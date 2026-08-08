"""prediction_log.py — sealed, append-only forecast log.

Every run appends one JSON line per race (timestamp, race_id, p_consensus,
p_alpha, delta, flagged, config_hash). Lines are chained with a rolling
SHA-256 (line_hash = sha256(prev_hash + payload)), so any later edit or
deletion of a prior line breaks the chain. `verify()` checks timestamp
monotonicity and the full chain. This exists to make forecasts falsifiable
after the fact — treat it as load-bearing and never rewrite the file.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from . import config

LOG_PATH = os.path.join(config.DATA_DIR, "prediction_log.jsonl")

_GENESIS = "0" * 64


def config_hash() -> str:
    """Hash of the parameters a forecast depends on.

    λ and the flag thresholds left this hash on 2026-08-08 along with the
    blending engine itself. What remains are the inputs that still shape a
    published number: the Track B weights (which set each model's internal
    mix), the z scaling, the board's ranking metric and size, and the
    sigma-to-points factor the detail view quotes.
    """
    payload = json.dumps({
        "track_b_weights": config.TRACK_B["weights"],
        "z_norm": config.TRACK_B["z_norm"],
        "z_clip": config.TRACK_B["z_clip"],
        "pp_per_sigma": config.PP_PER_SIGMA,
        "board": config.BOARD,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _last_hash(path: str) -> str:
    last = _GENESIS
    if not os.path.exists(path):
        return last
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            last = json.loads(line)["line_hash"]
    return last


def _seal(record: dict, prev_hash: str) -> dict:
    body = json.dumps(record, sort_keys=True, ensure_ascii=False)
    record = dict(record)
    record["prev_hash"] = prev_hash
    record["line_hash"] = hashlib.sha256(
        (prev_hash + body).encode("utf-8")).hexdigest()
    return record


def append_run(races: list, path: str = LOG_PATH,
               dry_run: bool = False) -> int:
    """Append one sealed line per race. Never rewrites prior lines.

    v3 (2026-08-08) seals the CHANNELS rather than the blend. p_consensus,
    p_alpha, delta and flagged are gone, because none of them are computed any
    more; what is sealed is what was actually published — the market price,
    the poll aggregate, and each model's lean. Lines written before this date
    carry the old keys, and `verify()` checks the chain, not the shape, so the
    history stays continuous and readable across the change.

    `dry_run` is sealed into every record so the log can tell a real forecast
    from one generated off mock data.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    chash = config_hash()
    prev = _last_hash(path)
    n = 0
    with open(path, "a", encoding="utf-8") as fh:   # append-only
        for r in races:
            record = {
                "ts": ts,
                "race_id": r.race_id,
                "rank": r.rank,
                "betting": r.betting.prob_dem,
                "betting_trustworthy": r.betting.trustworthy,
                "volume1wk": r.betting.volume1wk,
                "polls": r.polls.prob_dem if r.polls else None,
                "models": {m.key: m.z for m in r.models},
                "config_hash": chash,
                "dry_run": bool(dry_run),
            }
            sealed = _seal(record, prev)
            fh.write(json.dumps(sealed, ensure_ascii=False, sort_keys=True)
                     + "\n")
            prev = sealed["line_hash"]
            n += 1
    return n


def verify(path: str = LOG_PATH) -> tuple:
    """Check timestamp monotonicity + hash chain. Returns (ok, problems)."""
    if not os.path.exists(path):
        return True, ["log is empty (nothing to verify)"]
    problems = []
    prev_hash = _GENESIS
    prev_ts = None
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                problems.append("line %d: unparseable" % i)
                break
            body = {k: v for k, v in rec.items()
                    if k not in ("prev_hash", "line_hash")}
            expect = hashlib.sha256(
                (prev_hash + json.dumps(body, sort_keys=True,
                                        ensure_ascii=False)).encode("utf-8")
            ).hexdigest()
            if rec.get("prev_hash") != prev_hash:
                problems.append("line %d: prev_hash broken (log altered?)" % i)
            if rec.get("line_hash") != expect:
                problems.append("line %d: line_hash mismatch (line altered?)"
                                % i)
            if prev_ts is not None and rec.get("ts", "") < prev_ts:
                problems.append("line %d: timestamp went backwards" % i)
            prev_hash = rec.get("line_hash", expect)
            prev_ts = rec.get("ts", prev_ts)
    return (not problems), problems


def summarize(path: str = LOG_PATH) -> dict:
    """{"real": n, "dry_run": n, "unlabelled": n, "runs": [(ts, kind, n)]}.

    A sealed log is only useful if you can tell which lines were forecasts
    and which were mock output, so this is what `verify-log` reports.
    """
    counts = {"real": 0, "dry_run": 0, "unlabelled": 0}
    runs: dict = {}
    if not os.path.exists(path):
        return dict(counts, runs=[])
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            flag = rec.get("dry_run")
            kind = ("unlabelled" if flag is None
                    else "dry_run" if flag else "real")
            counts[kind] += 1
            key = (rec.get("ts", "?"), kind)
            runs[key] = runs.get(key, 0) + 1
    return dict(counts,
                runs=[(ts, kind, n) for (ts, kind), n in sorted(runs.items())])
