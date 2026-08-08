"""track_b/signals.py — turn raw Track B pulls into Dem-oriented z-readings.

For each variable the adapter reports a dem side and a rep side. Each side is
z-scored against ITS OWN baseline (per race, per source, per side channel),
then oriented:

    z_oriented = (z_dem - z_rep) / 2          (clipped to ±z_clip)

so "Democratic side unusually energized" is positive and "Republican side
unusually energized" is negative — never absolute levels. Sides with a
"baseline" array in the payload are scored against it; otherwise the
persistent BaselineStore is used and the new observation is appended for
future runs.

Two variables are intrinsically ONE contrast rather than two sides (a primary
turnout RATIO, a registration NET change). They ship an "oriented" block and
skip the differencing — splitting them into two sides would halve the reading
and invent a second baseline that does not exist.

Availability is four-valued. See adapters.unavailable().
"""

from __future__ import annotations

from typing import Optional

from .. import config
from ..baseline import BaselineStore, structural_residual, z_score
from ..signal import VariableReading
from .adapters import (EconClaimsAdapter, EconCoincidentAdapter,
                       EconUnemploymentAdapter,
                       FecBurnRate, FecInStateShare, FecRepeatDonorRate,
                       FecSmallDollarCount, FecUniqueDonors, GdeltAdapter,
                       PartyRegistrationAdapter, PrimaryTurnoutAdapter,
                       RedditAdapter, WikiEditCountAdapter,
                       WikiPageviewsShareAdapter, YouTubeAdapter,
                       is_unavailable)

ADAPTERS = {
    # FEC first: strongest behavioural proxy, and all five variables share
    # one set of API responses.
    "fec_in_state_share": FecInStateShare,
    "fec_small_dollar_count": FecSmallDollarCount,
    "fec_unique_donors": FecUniqueDonors,
    "fec_repeat_donor_rate": FecRepeatDonorRate,
    "fec_burn_rate": FecBurnRate,
    "econ_claims": EconClaimsAdapter,
    "econ_coincident": EconCoincidentAdapter,
    "econ_unemployment": EconUnemploymentAdapter,
    "primary_turnout_ratio": PrimaryTurnoutAdapter,
    "party_reg_net_change": PartyRegistrationAdapter,
    "gdelt": GdeltAdapter,
    "reddit": RedditAdapter,
    "youtube": YouTubeAdapter,
    "wiki_pageviews_share": WikiPageviewsShareAdapter,
    "wiki_edit_count": WikiEditCountAdapter,
}


def _min_obs_for(var: str) -> int:
    return config.TRACK_B["baseline_min_obs_overrides"].get(
        var, config.TRACK_B["baseline_min_obs"])


def residualize(raw_by_race: dict) -> dict:
    """Pre-pass: replace raw levels with structural residuals where covariates
    exist (config.STRUCTURAL_COVARIATES), before per-race z-scoring.

    This is where the FEC confounders are handled: small-dollar giving tracks
    ideological EXTREMITY as much as enthusiasm, so the level is regressed on
    district/state partisanship (the available ideology proxy) plus
    incumbency, media volume and population, and the z-score runs on what is
    left over. The cross-section is fitted across the MONITORED races only —
    i.e. other battlegrounds — which is also required, since competitive
    races attract disproportionate small-dollar money by construction.

    Only applies to sides scored against the persistent BaselineStore; sides
    carrying their own in-payload baseline keep the rolling path so residual
    observations are never compared against raw-level history. Returns
    {race_id: {variable: provenance}} for the output breakdown.
    """
    provenance: dict = {rid: {} for rid in raw_by_race}
    for var in config.TRACK_B["weights"]:
        for side in ("dem", "rep", "total"):
            obs = {}
            for rid, per_var in raw_by_race.items():
                payload = per_var.get(var)
                if is_unavailable(payload):
                    continue
                block = (payload or {}).get(side)
                if block and block.get("value") is not None \
                        and block.get("baseline") is None:
                    obs[rid] = float(block["value"])
            residuals, fitted = structural_residual(
                obs, config.STRUCTURAL_COVARIATES)
            for rid in obs:
                if fitted and rid in residuals:
                    raw_by_race[rid][var][side]["value"] = residuals[rid]
                    provenance[rid][var] = "structural"
                else:
                    provenance[rid].setdefault(var, "rolling")
    return provenance


def record_observation(store: Optional[BaselineStore], race_id: str,
                       payload: Optional[dict], attention_only: bool,
                       period: Optional[str] = None,
                       at: Optional[str] = None,
                       provenance: Optional[str] = None) -> bool:
    """Append one variable's observation to the rolling baseline, no z-score.

    THE SINGLE DEFINITION of which channels a variable writes: directional
    variables keep separate "dem"/"rep" windows, attention-only ones keep a
    single "total". Live runs (via compute_readings) and `backfill` both come
    through here, which is what makes a backfilled week and a live week
    directly comparable. Sides carrying their own in-payload `baseline` are
    skipped — they never use the persistent store, and neither do payloads
    that report an unavailability reason instead of a value.

    Returns True when something was written.
    """
    if store is None or payload is None or is_unavailable(payload):
        return False
    if payload.get("oriented") is not None:
        return False          # ships its own baseline; never uses the store
    if attention_only:
        total = payload.get("total")
        if total is not None and total.get("value") is not None:
            if total.get("baseline") is not None:
                return False
            store.append(race_id, float(total["value"]), channel="total",
                         period=period, at=at, provenance=provenance)
            return True
        values = [float(b["value"]) for b in
                  (payload.get("dem"), payload.get("rep"))
                  if b and b.get("value") is not None
                  and b.get("baseline") is None]
        if not values:
            return False
        store.append(race_id, sum(values), channel="total",
                     period=period, at=at, provenance=provenance)
        return True
    wrote = False
    for side in ("dem", "rep"):
        block = payload.get(side)
        if not block or block.get("value") is None:
            continue
        if block.get("baseline") is not None:
            continue
        store.append(race_id, float(block["value"]), channel=side,
                     period=period, at=at, provenance=provenance)
        wrote = True
    return wrote


def _block_z(store: Optional[BaselineStore], race_id: str, channel: str,
             block: Optional[dict], var: str) -> Optional[float]:
    if not block or block.get("value") is None:
        return None
    value = float(block["value"])
    if block.get("baseline") is not None:
        return z_score(value, block["baseline"],
                       min_obs=block.get("min_obs") or _min_obs_for(var))
    if store is None:
        return None
    return store.z_score(race_id, value, channel=channel)


def oriented_z(payload: Optional[dict], race_id: str,
               store: Optional[BaselineStore] = None,
               var: str = "") -> Optional[float]:
    """Dem-positive z for one variable's payload; None when not computable."""
    if payload is None or is_unavailable(payload):
        return None
    direct = payload.get("oriented")
    if direct is not None:
        # Already a Dem-positive contrast — no differencing, no halving.
        z = _block_z(store, race_id, "oriented", direct, var)
        if z is None:
            return None
        clip = config.TRACK_B["z_clip"]
        return max(-clip, min(clip, z))
    z_dem = _block_z(store, race_id, "dem", payload.get("dem"), var)
    z_rep = _block_z(store, race_id, "rep", payload.get("rep"), var)
    if z_dem is None and z_rep is None:
        return None
    clip = config.TRACK_B["z_clip"]
    z = ((z_dem or 0.0) - (z_rep or 0.0)) / 2.0
    return max(-clip, min(clip, z))


def attention_z(payload: Optional[dict], race_id: str,
                store: Optional[BaselineStore] = None,
                var: str = "") -> Optional[float]:
    """Attention-only z: deviation of the race's attention level vs its own
    baseline, NEVER oriented to a party. Direction is meaningless here — a
    pageview cannot distinguish positive from negative interest, exactly as a
    search query cannot.

    Uses the payload's explicit "total" block when present (some attention
    scalars are not simply dem + rep — pageview SHARES always sum to 1, so
    their total carries no information), otherwise the sum of the two sides.
    """
    if payload is None or is_unavailable(payload):
        return None
    clip = config.TRACK_B["z_clip"]
    total_block = payload.get("total")
    if total_block is not None and total_block.get("value") is not None:
        z = _block_z(store, race_id, "total", total_block, var)
        return None if z is None else max(-clip, min(clip, z))

    sides = [payload.get("dem"), payload.get("rep")]
    values = [float(b["value"]) for b in sides
              if b and b.get("value") is not None]
    if not values:
        return None
    total = sum(values)
    baselines = [b.get("baseline") for b in sides
                 if b and b.get("baseline") is not None]
    if baselines and all(b for b in baselines):
        base_total = [sum(pair) for pair in zip(*baselines)]
        z = z_score(total, base_total, min_obs=_min_obs_for(var))
    elif store is not None:
        z = store.z_score(race_id, total, channel="total")
    else:
        return None
    if z is None:
        return None
    return max(-clip, min(clip, z))


def compute_readings(race_id: str, raw: dict,
                     stores: Optional[dict] = None,
                     record: bool = False,
                     provenance: Optional[dict] = None,
                     periods: Optional[dict] = None) -> list:
    """VariableReading list for one race from its raw Track B pulls.

    `raw` maps variable name -> payload-or-None. `stores` maps variable name
    -> BaselineStore (omitted in tests/dry-run when payloads carry their own
    baselines). `record=True` appends observations to the rolling windows —
    the pipeline sets it once per run. `periods` maps variable -> the window
    label its payload covers, which keeps that append idempotent when a
    source has not advanced since the previous run.

    z-scores are computed BEFORE recording, so an observation is never
    compared against a baseline that already contains it.
    """
    stores = stores or {}
    provenance = provenance or {}
    periods = periods or {}
    attention_only = set(config.TRACK_B.get("attention_only", []))
    readings = []
    for var in config.TRACK_B["weights"]:
        payload = raw.get(var)
        is_attention = var in attention_only
        if is_attention:
            z = attention_z(payload, race_id, store=stores.get(var), var=var)
        else:
            z = oriented_z(payload, race_id, store=stores.get(var), var=var)
        if record:
            record_observation(stores.get(var), race_id, payload,
                               is_attention, period=periods.get(var),
                               provenance=provenance.get(var, "rolling"))

        # Four-valued availability. "Structurally unavailable" (Texas has no
        # party registration) and "missing this run" (the network was down)
        # are NOT the same fact and must not render the same.
        if z is not None:
            availability, reason = "available", None
        elif is_unavailable(payload):
            availability = payload["unavailable"]
            reason = payload.get("reason")
        elif payload is None:
            availability, reason = "missing", "소스가 아무 값도 반환하지 않았다"
        else:
            availability = "missing"
            reason = ("평소 수준을 계산할 관측치가 부족하다 "
                      "(%d개 필요) — backfill을 돌리거나 며칠 더 쌓이면 잡힌다"
                      % _min_obs_for(var))

        readings.append(VariableReading(
            variable=var, z=z, directional=not is_attention,
            provenance=provenance.get(var, "rolling"),
            availability=availability, reason=reason))
    return readings
