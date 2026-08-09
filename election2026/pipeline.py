"""pipeline.py — collect the three channels, write forecast.json, blend nothing.

    python3 -m election2026 run

Order of operations, and why:

 1. board.load()      The betting market defines WHICH races exist and in what
                      order. Everything downstream is a lookup keyed on what
                      the market is trading, not on a curated list.
 2. chambers.read_all Chamber totals come from the chamber-level markets, not
                      from aggregating our races — see chambers.py.
 3. polls             Our manual spreadsheet, per race, where we have it.
 4. Track B           Four models per race, where the underlying data exists.
 5. movers            What changed since yesterday's snapshot.

The three channels are written side by side and never combined. There is no
p_consensus, no p_alpha, no λ, and no flagging; if you are looking for the
divergence engine, it was deleted on 2026-08-08 and schema.py's docstring
explains what replaced it.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone
from typing import Optional

from . import board, chambers, config, manual, models, prediction_log, schema
from . import track_a, track_b
from .track_b.signals import (compute_readings, residualize,
                              attention_levels_across)

HISTORY_DIR = os.path.join(config.DATA_DIR, "history")
OUTPUT_PATH = os.path.join(config.DATA_DIR, "forecast.json")

CHAMBER_LABELS = {"senate": "상원", "house": "하원", "governor": "주지사"}

# Korean glosses for the Balance of Power market's outcome labels.
BALANCE_LABELS = {
    "Democrats Sweep": "민주당 상·하원 석권",
    "Republicans Sweep": "공화당 상·하원 석권",
    "R Senate, D House": "상원 공화 / 하원 민주",
    "D Senate, R House": "상원 민주 / 하원 공화",
    "Other": "기타",
}


# ---------------------------------------------------------------------------
# Reference lookup (races.json is no longer the universe — just enrichment)
# ---------------------------------------------------------------------------

def _reference_index() -> dict:
    """{race_id: races.json record}, including records marked inactive.

    Inactivity was a way of scoping the OLD hand-curated board. The board now
    comes from the market, so an inactive record is still perfectly good
    reference data (incumbent, rating, past results) if the market trades that
    seat.
    """
    index = dict(config.RACES)
    index.update(config.INACTIVE_RACES)
    return index


def _filter_board(ranked: dict, chamber: Optional[str],
                  races_filter: Optional[list]) -> dict:
    if chamber:
        ranked = {ch: rows for ch, rows in ranked.items() if ch == chamber}
    if races_filter:
        tokens = {t.strip().upper() for t in races_filter}
        ranked = {ch: [r for r in rows
                       if r.race_id.upper() in tokens or r.state in tokens]
                  for ch, rows in ranked.items()}
    return ranked


# ---------------------------------------------------------------------------
# Track B collection (unchanged in substance; scoped to the board)
# ---------------------------------------------------------------------------

def collect_track_b(races: dict, skip: Optional[set] = None) -> tuple:
    """({race_id: {var: payload}}, {race_id: {var: period}}).

    The period label rides along so the baseline append stays idempotent when
    a source has not advanced since the last run (the FEC frontier only moves
    on new filings).
    """
    skip = skip or set()
    adapters = [cls({}) for name, cls in track_b.ADAPTERS.items()
                if name not in skip]
    ttl = config.TRACK_B["cache_ttl_hours"]
    raw, periods = {}, {}
    for rid, meta in races.items():
        raw[rid] = {a.name: a.fetch(rid, meta, False, ttl_hours=ttl)
                    for a in adapters}
        periods[rid] = {}
        for a in adapters:
            if raw[rid].get(a.name) is None:
                continue
            # observation_period runs OUTSIDE Adapter.fetch's never-raise
            # wrapper and can itself hit the network (the FEC frontier walk),
            # so it needs the same guarantee.
            try:
                periods[rid][a.name] = a.observation_period(rid, meta)
            except Exception as exc:
                print("[track_b] %s: no observation period for %s: %s"
                      % (a.name, rid, exc))
    return raw, periods


# ---------------------------------------------------------------------------
# Change tracking
# ---------------------------------------------------------------------------

def _load_snapshots(limit: int = 10) -> list:
    """[(date, {race_id: prob_dem})] oldest -> newest, v3 documents only.

    v2 snapshots are skipped rather than translated: their headline number was
    p_consensus (a betting/polls blend), so treating it as a betting price
    would manufacture a day-over-day move on the day of the redesign.
    """
    snaps = []
    for path in sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json"))):
        date_ = os.path.basename(path).replace(".json", "")
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            if not str(doc.get("meta", {}).get("schema_version", "")) \
                    .startswith("3."):
                continue
            entry = {r["race_id"]: r["betting"]["prob_dem"]
                     for r in doc.get("races", [])
                     if r.get("betting", {}).get("prob_dem") is not None}
            if entry:
                snaps.append((date_, entry))
        except Exception:
            continue
    return snaps[-limit:]


def prior_gap_days(snapshots: list) -> Optional[int]:
    """Days between today and the snapshot `change_1d` is measured against.

    The run used to be daily, so "1d" was true by construction. It is every
    three days now (2026-08-09), and a three-day move labelled "어제보다" is
    simply a false statement — the dashboard prints this number instead of
    assuming.
    """
    today = datetime.now(timezone.utc).date()
    prior = [d for d, _ in snapshots
             if d != today.strftime("%Y-%m-%d")]
    if not prior:
        return None
    try:
        return max(1, (today - datetime.strptime(
            prior[-1], "%Y-%m-%d").date()).days)
    except ValueError:
        return None


def _changes(snapshots: list, race_id: str,
             current: Optional[float]) -> tuple:
    """(change_prev, change_older) in probability points, or (None, None)."""
    if current is None or not snapshots:
        return None, None
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prior = [(d, e) for d, e in snapshots if d != today]
    if not prior:
        return None, None

    def _delta(index: int) -> Optional[float]:
        if len(prior) < index:
            return None
        was = prior[-index][1].get(race_id)
        return None if was is None else round(current - was, 4)

    day = _delta(1)
    # "7d" is the oldest snapshot within the window we keep, not literally
    # seven days — runs are daily but skipped days happen.
    week = _delta(min(len(prior), 7))
    return day, (week if week != day else None)


def build_movers(races_out: list, limit: int = 8) -> list:
    """Biggest one-day market moves, largest first."""
    rows = [
        schema.Mover(race_id=r.race_id, label=r.label, chamber=r.chamber,
                     prob_dem=r.betting.prob_dem,
                     change=r.betting.change_1d, window="1d")
        for r in races_out
        if r.betting.change_1d is not None
        and abs(r.betting.change_1d) >= 0.005
    ]
    rows.sort(key=lambda m: abs(m.change), reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run(chamber: Optional[str] = None, races_filter: Optional[list] = None,
        output_path: str = OUTPUT_PATH, skip_sources: Optional[set] = None,
        rank_by: Optional[str] = None) -> dict:
    rank_by = rank_by or config.BOARD["rank_by"]

    events = board.fetch_events()
    if not events:
        raise SystemExit("Polymarket returned nothing and no cache exists — "
                         "cannot build a board")
    ranked = _filter_board(board.rank(board.build(events), rank_by),
                           chamber, races_filter)
    chamber_readings, balance = chambers.read_all(events)

    board_races = [r for rows in ranked.values() for r in rows]
    if not board_races:
        raise SystemExit("no traded races match the given scope")
    print("[pipeline] board: %s (%s 순)"
          % (", ".join("%s %d개" % (CHAMBER_LABELS[ch], len(rows))
                       for ch, rows in ranked.items()), rank_by))

    reference = _reference_index()
    poll_rows = manual.load_polls()          # loud on malformed data
    snapshots = _load_snapshots()

    # Track B needs a meta dict per race. Races the market trades but our
    # reference file has never heard of still get one, built from what the
    # market itself told us — the state-level economic variables only need a
    # state code, so those races are not left blank by default.
    metas = {}
    for r in board_races:
        ref = reference.get(r.race_id, {})
        # The MARKET's candidate names ride along. Track B otherwise builds
        # its Wikipedia roster from an FEC candidate search, which returns
        # everyone who filed rather than the two who are actually on the
        # ballot: Maine came back "Graham Platner, Janet Mills vs Patricia M.
        # Collins" for a race the market lists as Troy Jackson vs Susan
        # Collins. Those are different people, and the pageview comparison was
        # measuring them.
        #
        # This does not breach the track separation. The separation exists so
        # that Track B never inherits Track A's *estimates*; a candidate's
        # name is not an estimate, it is who is running.
        metas[r.race_id] = dict(ref, race_id=r.race_id, chamber=r.chamber,
                                state=r.state, district=r.district,
                                label=r.label,
                                market_candidates=dict(r.candidates))

    scoped = bool(chamber or races_filter)
    raw_b, _ = collect_track_b(metas, skip=skip_sources)
    provenance = residualize(raw_b)

    # Attention is graded against the rest of THIS board, not against an
    # accumulated per-race history, so the comparison set is built once here.
    attn_levels = attention_levels_across(raw_b)


    used, missing = {"polymarket"}, set()
    races_out = []
    for ch, rows in ranked.items():
        for rank, r in enumerate(rows, start=1):
            change_1d, change_7d = _changes(snapshots, r.race_id, r.prob_dem)

            betting = schema.Betting(
                prob_dem=r.prob_dem, volume=r.volume, volume1wk=r.volume1wk,
                liquidity=r.liquidity, slug=r.slug,
                unmapped_mass=r.unmapped_mass,
                trustworthy=r.two_party_trustworthy,
                change_1d=change_1d, change_7d=change_7d)

            polls = None
            rows_for_race = poll_rows.get(r.race_id)
            if rows_for_race:
                margin = manual.aggregate_polls(rows_for_race)
                prob = track_a.margin_to_prob(margin)
                if prob is not None:
                    used.add("polls")
                    polls = schema.Polls(
                        prob_dem=round(prob, 4), n_polls=len(rows_for_race),
                        margin_dem=round(margin, 2),
                        latest_date=max(
                            (str(row.get("date")) for row in rows_for_race
                             if row.get("date")), default=None))

            readings = compute_readings(
                r.race_id, raw_b[r.race_id],
                provenance=provenance.get(r.race_id),
                attention_levels=attn_levels)
            for name, payload in raw_b[r.race_id].items():
                (used if payload is not None else missing).add(name)

            ref = reference.get(r.race_id)
            races_out.append(schema.Race(
                race_id=r.race_id, chamber=ch, state=r.state,
                district=r.district, label=r.label, title=r.title,
                rank=rank, betting=betting, candidates=r.candidates,
                polls=polls, models=models.build(readings),
                reference=(schema.RaceReference(
                    incumbent=ref.get("incumbent"),
                    incumbent_party=ref.get("incumbent_party"),
                    seat_party=ref.get("seat_party"),
                    rating=ref.get("rating"),
                    history=ref.get("history", []),
                ) if ref else None)))

    chambers_out = {}
    for ch in schema.CHAMBERS:
        reading = chamber_readings[ch]
        chambers_out[ch] = schema.Chamber(
            label=CHAMBER_LABELS[ch],
            total_seats=reading.total_seats,
            n_races_shown=sum(1 for r in races_out if r.chamber == ch),
            prob_dem_control=reading.prob_dem_control,
            control_volume=reading.control_volume,
            expected_dem_seats=reading.expected_dem_seats,
            expected_rep_seats=reading.expected_rep_seats,
            favourite_dem_seats=reading.favourite_dem_seats,
            favourite_rep_seats=reading.favourite_rep_seats,
            seats_volume=reading.seats_volume,
            expectation_is_approximate=reading.expectation_is_approximate,
            seat_buckets=[schema.SeatBucket(
                label=b.label, prob=b.prob, rep_low=b.low, rep_high=b.high,
                midpoint=b.midpoint) for b in reading.buckets])

    doc = schema.document(
        meta=schema.Meta(
            generated_at=datetime.now(timezone.utc).isoformat(),
            schema_version=schema.SCHEMA_VERSION,
            sources_used=sorted(used),
            sources_missing=sorted(missing - used),
            rank_by=rank_by,
            pp_per_sigma=config.PP_PER_SIGMA,
            dry_run=False,
            calibration_validated=config.CALIBRATION_VALIDATED,
            calibration_note=(None if config.CALIBRATION_VALIDATED
                              else config.CALIBRATION_NOTE),
            change_window_days=prior_gap_days(snapshots),
        ),
        chambers=chambers_out,
        races=races_out,
        balance_of_power=[{"outcome": outcome,
                           "label": BALANCE_LABELS.get(outcome, outcome),
                           "prob": prob} for outcome, prob in balance],
        movers=build_movers(races_out),
    )

    schema.validate(doc)      # fail loudly BEFORE touching disk

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    # Dated snapshot — full runs only. A scoped run would put a partial board
    # into the timeline and manufacture "movers" out of races that were merely
    # absent yesterday.
    if not scoped:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with open(os.path.join(HISTORY_DIR, "%s.json" % stamp), "w",
                  encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)

    n_sealed = prediction_log.append_run(races_out,
                                         path=prediction_log.LOG_PATH)
    print("[pipeline] sealed %d predictions -> %s (config %s)"
          % (n_sealed, prediction_log.LOG_PATH, prediction_log.config_hash()))

    _report(doc, output_path)
    return doc


def _report(doc: dict, output_path: str) -> None:
    print("[pipeline] wrote %s — %d races" % (output_path, len(doc["races"])))
    for ch in schema.CHAMBERS:
        blk = doc["chambers"][ch]
        ctrl = ("민주당 %.0f%%" % (100 * blk["prob_dem_control"])
                if blk["prob_dem_control"] is not None else "장악 시장 없음")
        seats = ("D %.1f / R %.1f" % (blk["expected_dem_seats"],
                                      blk["expected_rep_seats"])
                 if blk["expected_dem_seats"] is not None else "의석 시장 없음")
        print("[pipeline]   %s: %s, 예상 의석 %s (%d개 레이스 표시)"
              % (blk["label"], ctrl, seats, blk["n_races_shown"]))

    thin = [r for r in doc["races"] if not r["betting"]["trustworthy"]]
    if thin:
        print("[pipeline] 두 정당 확률을 쓸 수 없는 레이스 %d곳 "
              "(무소속·미분류 비중 큼): %s"
              % (len(thin), ", ".join(r["label"] for r in thin)))

    for m in doc["movers"][:5]:
        print("[pipeline]   변동 %-14s %+.1f%%p (1일)"
              % (m["label"], 100 * m["change"]))
