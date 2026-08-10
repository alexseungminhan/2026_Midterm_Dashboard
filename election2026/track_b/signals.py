"""track_b/signals.py — turn raw Track B pulls into Dem-oriented z-readings.

Nothing is carried between runs (2026-08-09). Each variable is read one of
two ways, both of which finish inside a single run:

  * two sides (dem/rep) -> the snapshot contrast (dem - rep) / (dem + rep)
  * an "oriented" block -> already one signed reading, carrying its own
    baseline from the source (FRED returns the state's whole series)

Availability is four-valued. See adapters.unavailable().
"""

from __future__ import annotations

from typing import Optional

from .. import config
from ..baseline import structural_residual, z_score
from ..signal import VariableReading
from .adapters import (EconClaimsAdapter, EconCoincidentAdapter,
                       EconUnemploymentAdapter,
                       FecBurnRate, FecInStateShare, FecRepeatDonorRate,
                       FecSmallDollarCount, FecUniqueDonors,
                       PartyRegistrationAdapter, PrimaryTurnoutAdapter,
                       WikiEditCountAdapter, WikiPageviewsShareAdapter,
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

    Returns {race_id: {variable: provenance}} for the output breakdown.
    """
    provenance: dict = {rid: {} for rid in raw_by_race}
    for var in config.TRACK_B["weights"]:
        # Only "total" — the attention scalar, which IS compared across races
        # and so still benefits from having population and media volume
        # regressed out.
        #
        # The "dem"/"rep" sides are deliberately left raw (2026-08-08). They
        # are compared to each other WITHIN one race now, where the structural
        # covariates cancel on their own: both candidates run in the same
        # district, before the same population. Residualizing them would be
        # actively wrong — it replaces levels with signed residuals, and
        # (d−r)/(d+r) on residuals is meaningless because both can be
        # negative. That is what blanked the money model on Maine, Texas,
        # Michigan and Georgia in the first snapshot run.
        for side in ("total",):
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


def _block_z(block: Optional[dict], var: str) -> Optional[float]:
    """z against the baseline the payload carries with it.

    There is no other baseline any more. The rolling BaselineStore was removed
    on 2026-08-09: every remaining `oriented` payload ships its own history
    from the source (FRED hands back the state's whole series), and the
    two-sided variables stopped being z-scored entirely.
    """
    if not block or block.get("value") is None:
        return None
    if block.get("baseline") is None:
        return None
    return z_score(float(block["value"]), block["baseline"],
                   min_obs=block.get("min_obs") or _min_obs_for(var))


def side_contrast(payload: Optional[dict]) -> Optional[float]:
    """(dem - rep) / (dem + rep) for a two-sided variable. -1..+1, unit-free.

    THE SNAPSHOT RULE (2026-08-08). The two sides are compared to EACH OTHER
    today, not each to its own accumulated past. That drops the rolling
    baseline store entirely: nothing has to be carried between runs, so a run
    on a fresh machine gives the same answer as one on a machine that has been
    running for weeks.

    What it costs, stated plainly: this measures LEVEL, not CHANGE. A district
    where the Republican always outraises 3:1 reads "R" even in a year the
    Democrat tripled their haul. That trade was made deliberately.

    Both sides must be present and positive. A zero side is not "maximal
    dominance" — it is a candidate with no committee filing yet, and letting
    it saturate to ±1 would manufacture the strongest possible reading out of
    missing data. 17 of 83 races carried a zero side on 2026-08-08.
    """
    if payload is None or is_unavailable(payload):
        return None
    dem, rep = payload.get("dem"), payload.get("rep")
    if not dem or not rep:
        return None
    d, r = dem.get("value"), rep.get("value")
    if d is None or r is None:
        return None
    d, r = float(d), float(r)
    if d <= 0 or r <= 0:
        return None
    return (d - r) / (d + r)


def oriented_z(payload: Optional[dict], var: str = "") -> Optional[float]:
    """Dem-positive reading for one variable's payload; None when unavailable.

    Two paths, and which one applies is a property of the payload:

    * `oriented` block — the variable is ALREADY one contrast and ships its
      own baseline with it, straight from the source (FRED hands back the
      state's whole series; the primary-turnout sheet carries the other
      states). Those are z-scored against that in-payload history and were
      never in the rolling store — their baseline files were empty.
    * `dem`/`rep` blocks — the snapshot contrast above.
    """
    if payload is None or is_unavailable(payload):
        return None
    direct = payload.get("oriented")
    if direct is not None:
        # `direct` payloads are already a signed, oriented reading in their
        # own units (the economy's year-over-year change). They are NOT
        # z-scored: the sign is the whole signal, and models.py tallies signs
        # rather than averaging quantities that share no unit.
        if direct.get("direct") and direct.get("value") is not None:
            return float(direct["value"])
        # Already a Dem-positive contrast — no differencing, no halving.
        z = _block_z(direct, var)
        if z is None:
            return None
        clip = config.TRACK_B["z_clip"]
        return max(-clip, min(clip, z))
    return side_contrast(payload)


def attention_level(payload: Optional[dict]) -> Optional[float]:
    """This race's raw attention total. NEVER oriented to a party.

    Direction is meaningless here — a pageview cannot distinguish support from
    disgust, exactly as a search query cannot. The number is turned into
    high/normal/low by comparing races to each other in `attention_z_across`.

    Uses the explicit "total" block when present, otherwise the sum of sides.
    A pageview SHARE sums to 1 by construction, so its total carries no
    information and the adapter ships a separate total for that reason.
    """
    if payload is None or is_unavailable(payload):
        return None
    total_block = payload.get("total")
    if total_block is not None and total_block.get("value") is not None:
        return float(total_block["value"])
    values = [float(b["value"]) for b in
              (payload.get("dem"), payload.get("rep"))
              if b and b.get("value") is not None]
    return sum(values) if values else None


def attention_z_across(level: Optional[float],
                       all_levels: list, var: str = "") -> Optional[float]:
    """Where this race's attention sits among THIS RUN's other races.

    Cross-sectional, so it needs no accumulated history — the comparison set
    is the board itself. It answers "busier than the other races on the board"
    rather than "busier than this race's own past", which is a different
    question but an answerable one from a single snapshot. The primary-turnout
    variable already used a cross-sectional baseline for the same reason.
    """
    if level is None:
        return None
    others = [v for v in all_levels if v is not None]
    z = z_score(level, others, min_obs=_min_obs_for(var))
    if z is None:
        return None
    clip = config.TRACK_B["z_clip"]
    return max(-clip, min(clip, z))


def attention_levels_across(raw_by_race: dict) -> dict:
    """{variable: [level per race]} for the attention-only variables.

    Built once per run and handed to every race, because "is this race busier
    than usual" is now answered against the rest of the board rather than
    against an accumulated history of this race.
    """
    out = {}
    for var in config.TRACK_B.get("attention_only", []):
        out[var] = [attention_level(raw.get(var))
                    for raw in raw_by_race.values()]
    return out


def compute_readings(race_id: str, raw: dict,
                     provenance: Optional[dict] = None,
                     attention_levels: Optional[dict] = None) -> list:
    """VariableReading list for one race from its raw Track B pulls.

    `raw` maps variable name -> payload-or-None. Nothing is persisted: every
    reading is computed from this run's own pulls (2026-08-09).
    """
    provenance = provenance or {}
    attention_only = set(config.TRACK_B.get("attention_only", []))
    readings = []
    for var in config.TRACK_B["weights"]:
        payload = raw.get(var)
        is_attention = var in attention_only
        if is_attention:
            z = attention_z_across(attention_level(payload),
                                   (attention_levels or {}).get(var, []), var)
        else:
            z = oriented_z(payload, var)

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

        sides = payload if isinstance(payload, dict) else {}
        dv = (sides.get("dem") or {}).get("value") if not is_unavailable(payload) else None
        rv = (sides.get("rep") or {}).get("value") if not is_unavailable(payload) else None
        raw_v = (sides.get("oriented") or {}).get("raw_change") \
            if isinstance(sides.get("oriented"), dict) else None
        readings.append(VariableReading(
            variable=var, z=z, directional=not is_attention,
            dem_value=dv, rep_value=rv, raw_value=raw_v,
            provenance=provenance.get(var, "rolling"),
            availability=availability, reason=reason))
    return readings
