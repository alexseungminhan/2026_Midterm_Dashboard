"""backfill.py — reconstruct Track B baseline history in a single pass.

    python -m election2026 backfill                  # 8 weeks, every source
    python -m election2026 backfill --weeks 12
    python -m election2026 backfill --chamber senate # start small
    python -m election2026 backfill --skip trends    # skip a slow source

THE PROBLEM. Every Track B variable enters the pipeline as a z-score against
its OWN rolling baseline, and `baseline_min_obs` (4) suppresses the z-score
until that baseline holds enough observations. A baseline built the ordinary
way — one observation per run — is therefore blind for its first four weekly
runs. Standing up the monitor 100 days before the election and waiting a
month for Track B to switch on is not an option.

WHAT THIS DOES. For every source whose API can answer questions about the
past, pull the last N weekly windows in one go and write them into the same
rolling baselines a live run writes, with the same period labels and the same
channel layout (track_b.signals.record_observation is the single definition of
both). After a backfill, the next `run` produces real z-scores immediately.

WHAT IT DELIBERATELY DOES NOT DO.
  * youtube — the statistics endpoint only reports CURRENT counters, so a
    past week cannot be recovered, only invented. Left to accumulate live.
  * reddit — search only reaches the recent past reliably; accumulates live.
  * primary_turnout_ratio / party_reg_net_change — their baselines are prior
    ELECTION CYCLES carried inside the manual payload, not rolling weeks.
  * It never writes the window the next live run will observe. Backfill fills
    the history BEHIND today's observation; overlapping the two would make a
    race's newest reading get z-scored against a baseline containing itself.

ALIGNMENT. Sources advance on different clocks — GDELT and Wikipedia are
current to yesterday, while the FEC only exposes weeks that have been filed
and loaded (see FecBase). Backfilled weeks are therefore aligned per source by
RECENCY INDEX (1 = the week before that source's live window) rather than by
calendar date, and the structural-residual cross-section is fitted within one
source and one index. The per-race FEC frontier is logged so the spread is
visible rather than silent.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from . import config, track_b
from .baseline import BaselineStore
from .track_b.signals import record_observation, residualize


def _backfillable(skip: Optional[set] = None) -> dict:
    """{name: adapter class} for sources that can reconstruct the past."""
    skip = skip or set()
    return {name: cls for name, cls in track_b.ADAPTERS.items()
            if getattr(cls, "supports_backfill", False) and name not in skip}


def collect(races: dict, weeks: int, sources: dict) -> dict:
    """{source: {race_id: [(period, payload)] newest-first}}."""
    out: dict = {}
    for name, cls in sources.items():
        adapter = cls()
        if not adapter.is_available():
            print("[backfill] %s: unavailable (missing key or dependency) "
                  "— skipped" % name)
            continue
        per_race = {}
        for rid, meta in races.items():
            try:
                series = adapter.backfill_series(rid, meta, weeks)
            except Exception as exc:      # one bad race must not kill the run
                print("[backfill] %s %s: %s" % (name, rid, exc))
                continue
            if not series:
                continue
            per_race[rid] = sorted(series.items(), reverse=True)
            print("[backfill] %-8s %-8s %d weeks (%s .. %s)"
                  % (name, rid, len(per_race[rid]),
                     per_race[rid][-1][0], per_race[rid][0][0]))
        if per_race:
            out[name] = per_race
        else:
            print("[backfill] %s: no race returned usable history" % name)
    return out


def apply(collected: dict, stores: dict) -> dict:
    """Write collected history into the baseline stores. {source: n_written}.

    Weeks are applied OLDEST FIRST so each store's rolling window keeps the
    most recent `baseline_window` observations when the series is longer.
    Within one source and one recency index the cross-section is passed
    through the same structural-residual layer a live run uses, so backfilled
    values and live values are on the same scale.
    """
    attention_only = set(config.TRACK_B.get("attention_only", []))
    written = {}
    for source, per_race in collected.items():
        store = stores.get(source)
        if store is None:
            continue
        depth = max(len(v) for v in per_race.values())
        count = 0
        for index in range(depth - 1, -1, -1):        # oldest index first
            cross = {}
            periods = {}
            for rid, series in per_race.items():
                if index >= len(series):
                    continue
                period, payload = series[index]
                cross[rid] = {source: payload}
                periods[rid] = period
            if not cross:
                continue
            # Same structural layer as a live run, fitted within this source
            # and this recency index.
            provenance = residualize(cross)
            for rid, per_var in cross.items():
                if record_observation(
                        store, rid, per_var[source],
                        source in attention_only,
                        period=periods[rid],
                        at="%sT23:59:59+00:00" % periods[rid],
                        provenance=provenance.get(rid, {}).get(source,
                                                               "rolling")):
                    count += 1
        written[source] = count
    return written


def warn_on_mixed_provenance(stores: dict, races: dict) -> list:
    """Name every window that mixes structural residuals with raw levels.

    Such a window z-scores a value against a baseline on a different scale.
    It can happen legitimately — the structural fit needs a minimum number of
    races with covariates, so a week where too few sources reported falls
    back to raw levels — which is exactly why it has to be said out loud.
    """
    mixed = []
    for source, store in sorted(stores.items()):
        for rid in races:
            for channel in ("dem", "rep", "total"):
                if len(store.provenance_mix(rid, channel)) > 1:
                    mixed.append("%s/%s/%s" % (source, rid, channel))
    if mixed:
        print("[backfill] WARNING: %d window(s) mix structural residuals with "
              "raw levels — their z-scores compare different scales: %s"
              % (len(mixed), ", ".join(mixed[:8])
                 + (" ..." if len(mixed) > 8 else "")))
    return mixed


def report(races: dict) -> None:
    """Print how close each source now is to producing live z-scores.

    Reads the stores fresh from disk so the report reflects everything on
    disk — including sources this particular invocation did not touch.
    """
    min_obs = config.TRACK_B["baseline_min_obs"]
    print("\n[backfill] baseline depth (need >= %d observations for a "
          "z-score):" % min_obs)
    for source in sorted(config.TRACK_B["weights"]):
        store = BaselineStore(source)
        ready = total = 0
        for rid in races:
            for channel in ("dem", "rep", "total"):
                n = len(store.observations(rid, channel))
                if n:
                    total += 1
                    ready += n >= min_obs
        if total:
            print("[backfill]   %-8s %d/%d channels ready"
                  % (source, ready, total))
        else:
            print("[backfill]   %-8s no history (builds up one run at a time)"
                  % source)


def run(weeks: Optional[int] = None, chamber: Optional[str] = None,
        races_filter: Optional[list] = None,
        skip_sources: Optional[set] = None) -> dict:
    from .pipeline import _scoped_races

    weeks = weeks or config.TRACK_B["backfill_weeks"]
    races = _scoped_races(chamber, races_filter)
    if not races:
        raise SystemExit("no monitored races match the given scope")
    sources = _backfillable(skip_sources)
    if not sources:
        raise SystemExit("no backfillable sources selected")

    window = config.TRACK_B["baseline_window"]
    if weeks > window:
        print("[backfill] NOTE: --weeks %d exceeds baseline_window=%d; only "
              "the newest %d weeks will be retained." % (weeks, window, window))
    if chamber or races_filter:
        print("[backfill] NOTE: scoped to %d of %d races. The structural "
              "residual is a CROSS-SECTIONAL fit, so a scoped backfill fits "
              "it on a smaller panel than a full `run` will — re-run "
              "unscoped before relying on the numbers."
              % (len(races), len(config.RACES)))

    print("[backfill] %d races x %d weeks from %s (as of %s)"
          % (len(races), weeks, ", ".join(sorted(sources)), date.today()))
    collected = collect(races, weeks, sources)

    # Stores are opened AFTER collection and only for sources that actually
    # returned history. Opening every store up front and saving them all back
    # would let a backfill of one source overwrite another source's file with
    # the copy it happened to read at startup — which matters because these
    # runs are slow enough that two of them overlap in practice.
    stores = {name: BaselineStore(name) for name in collected}
    written = apply(collected, stores)
    for name, store in stores.items():
        if written.get(name):
            store.save()

    for source in sorted(written):
        print("[backfill] %s: wrote %d observations" % (source, written[source]))
    warn_on_mixed_provenance(stores, races)
    report(races)
    print("[backfill] done — run `python3 -m election2026 run` to use them.")
    return written
