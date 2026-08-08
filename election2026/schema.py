"""schema.py — the frozen data contract for forecast.json.

Single interface between the pipeline and the dashboard. Every run validates
its output against JSON_SCHEMA and fails loudly on mismatch. Any change to the
shape of forecast.json must bump SCHEMA_VERSION.

v3.0.0 (2026-08-08) — THE BLEND IS GONE.
----------------------------------------
v2 carried one headline probability per race, `p_alpha = p_consensus +
λ·signal`, with λ hand-set and never fitted. v3 carries the channels side by
side and never combines them:

    betting   the market's price, two-party normalized     (a quote)
    polls     our manual poll aggregate                    (a measurement)
    models    four Track B models, each a lean + strength  (an indicator)

There is no p_alpha, no delta, no flag, and no λ. A reader compares three
numbers themselves; the document does not decide for them. `p_consensus` is
likewise gone — betting and polls are no longer averaged into one number,
because averaging them is what made Nebraska produce 0.215, a value neither
channel believed.

The race universe now comes from the betting market (board.py) ranked by
volume, not from a curated list. `rank` and `volume` are therefore part of the
contract: they are why a race is on the board at all.

`betting.trustworthy` is false when a large share of market probability sits
on candidates who are neither D nor R (Nebraska's independent, 26%). The
two-party number is still reported, so the dashboard can show it struck
through rather than silently dropping the race.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

SCHEMA_VERSION = "3.1.0"

CHAMBERS = ("senate", "house", "governor")
PARTIES = ("D", "R")
# Hand-maintained expert ratings. In v2 these were a probability FALLBACK when
# both channels were missing; in v3 they are reference context only — a race
# with no market price is simply not on the board, so nothing is ever imputed
# from a rating. `manual.load_ratings` still validates against this list.
RATINGS = ("Tossup", "Lean D", "Lean R", "Likely D", "Likely R",
           "Safe D", "Safe R")
LEANS = ("D", "R", "N")
LEVELS = ("high", "normal", "low")
AVAILABILITY = ("available", "missing", "structural", "pending")
MODEL_KEYS = ("economy", "money", "grassroots", "attention")


# ---------------------------------------------------------------------------
# Dataclasses (the in-memory shape the pipeline builds)
# ---------------------------------------------------------------------------

@dataclass
class Betting:
    """The market's price for this race."""
    prob_dem: Optional[float]         # two-party normalized, D / (D + R)
    volume: float                     # cumulative USD since listing
    volume1wk: float
    liquidity: float
    slug: str
    # Share of probability on non-D/R candidates. Above board.UNMAPPED_WARN the
    # two-party number is answering a different question than "who wins".
    unmapped_mass: float = 0.0
    trustworthy: bool = True
    change_1d: Optional[float] = None    # prob_dem change vs the last snapshot
    change_7d: Optional[float] = None


@dataclass
class Polls:
    """Our manually maintained poll aggregate. None when we have no polls."""
    prob_dem: float
    n_polls: int
    latest_date: Optional[str] = None
    margin_dem: Optional[float] = None    # aggregate margin in points


@dataclass
class RaceReference:
    """Context from data/reference/races.json, when the race is in it.

    Optional by design: the board is built from the market, so most races have
    no entry here and must still render.
    """
    incumbent: Optional[str] = None
    incumbent_party: Optional[str] = None
    seat_party: Optional[str] = None
    rating: Optional[str] = None
    history: list = field(default_factory=list)


@dataclass
class Race:
    race_id: str
    chamber: str
    state: str
    district: Optional[str]
    label: str                        # "미시간 상원", "테네시 5지구"
    title: str                        # the market's own title
    rank: int                         # 1-based, within the chamber
    betting: Betting
    candidates: dict = field(default_factory=dict)     # {"D": name, "R": name}
    polls: Optional[Polls] = None
    models: list = field(default_factory=list)         # [models.ModelReading]
    reference: Optional[RaceReference] = None


@dataclass
class SeatBucket:
    label: str
    prob: float
    rep_low: Optional[float]
    rep_high: Optional[float]
    midpoint: float


@dataclass
class Chamber:
    label: str
    total_seats: int
    n_races_shown: int
    prob_dem_control: Optional[float] = None
    control_volume: float = 0.0
    expected_dem_seats: Optional[float] = None
    expected_rep_seats: Optional[float] = None
    seats_volume: float = 0.0
    # True whenever the seat distribution has open-ended end buckets, which it
    # always does. The expectation then rests on an assumed midpoint for
    # "Below 190" / "≤47", so the distribution is the honest object and the
    # point estimate is a gloss on it.
    expectation_is_approximate: bool = True
    seat_buckets: list = field(default_factory=list)   # [SeatBucket]


@dataclass
class Mover:
    """A race whose market moved most since the previous snapshot."""
    race_id: str
    label: str
    chamber: str
    prob_dem: Optional[float]
    change: float
    window: str                       # "1d" | "7d"


@dataclass
class Meta:
    generated_at: str
    schema_version: str
    sources_used: list
    sources_missing: list
    rank_by: str                      # "volume1wk" | "volume"
    pp_per_sigma: float
    dry_run: bool = False
    # False means the model scale factors are hand-set, not fitted. Rides in
    # the document so anyone reading a forecast.json sees it without going to
    # config.py. Nothing in v3 blends on it — see the module docstring.
    calibration_validated: bool = True
    calibration_note: Optional[str] = None


def document(meta: Meta, chambers: dict, races: list,
             balance_of_power: list, movers: list) -> dict:
    """Assemble the final forecast.json document from dataclasses."""
    return {
        "meta": asdict(meta),
        "balance_of_power": [asdict(b) if hasattr(b, "__dataclass_fields__")
                             else b for b in balance_of_power],
        "chambers": {k: asdict(v) for k, v in chambers.items()},
        "races": [asdict(r) for r in races],
        "movers": [asdict(m) for m in movers],
    }


# ---------------------------------------------------------------------------
# JSON schema (validated on every run)
# ---------------------------------------------------------------------------

def _nullable(t):
    return {"type": [t, "null"]}


_PROB = {"type": "number", "minimum": 0.0, "maximum": 1.0}
_NULLABLE_PROB = {"type": ["number", "null"], "minimum": 0.0, "maximum": 1.0}

_VARIABLE_SCHEMA = {
    "type": "object",
    "required": ["variable", "label", "z", "weight", "availability"],
    "properties": {
        "variable": {"type": "string"},
        "label": {"type": "string"},
        "z": _nullable("number"),
        "weight": {"type": "number", "minimum": 0},
        "availability": {"enum": list(AVAILABILITY)},
        "reason": _nullable("string"),
    },
}

_MODEL_SCHEMA = {
    "type": "object",
    "required": ["key", "label", "question", "directional", "n_available",
                 "n_total", "variables"],
    "properties": {
        "key": {"enum": list(MODEL_KEYS)},
        "label": {"type": "string"},
        "question": {"type": "string"},
        "detail": {"type": "string"},
        "directional": {"type": "boolean"},
        "lean": {"type": ["string", "null"], "enum": list(LEANS) + [None]},
        "level": {"type": ["string", "null"], "enum": list(LEVELS) + [None]},
        "z": _nullable("number"),
        "strength": {"type": "integer", "minimum": 0, "maximum": 3},
        "shift_pp": _nullable("number"),
        "n_available": {"type": "integer", "minimum": 0},
        "n_total": {"type": "integer", "minimum": 0},
        "variables": {"type": "array", "items": _VARIABLE_SCHEMA},
        "unavailable": {"type": ["string", "null"],
                        "enum": list(AVAILABILITY) + [None]},
        "reason": _nullable("string"),
    },
}

_BETTING_SCHEMA = {
    "type": "object",
    "required": ["prob_dem", "volume", "volume1wk", "slug", "trustworthy"],
    "properties": {
        "prob_dem": _NULLABLE_PROB,
        "volume": {"type": "number", "minimum": 0},
        "volume1wk": {"type": "number", "minimum": 0},
        "liquidity": {"type": "number", "minimum": 0},
        "slug": {"type": "string"},
        "unmapped_mass": {"type": "number", "minimum": 0, "maximum": 1},
        "trustworthy": {"type": "boolean"},
        "change_1d": _nullable("number"),
        "change_7d": _nullable("number"),
    },
}

_POLLS_SCHEMA = {
    "type": ["object", "null"],
    "required": ["prob_dem", "n_polls"],
    "properties": {
        "prob_dem": _PROB,
        "n_polls": {"type": "integer", "minimum": 0},
        "latest_date": _nullable("string"),
        "margin_dem": _nullable("number"),
    },
}

_RACE_SCHEMA = {
    "type": "object",
    "required": ["race_id", "chamber", "state", "label", "rank", "betting",
                 "models"],
    "properties": {
        "race_id": {"type": "string", "minLength": 1},
        "chamber": {"enum": list(CHAMBERS)},
        "state": {"type": "string", "minLength": 2, "maxLength": 2},
        "district": _nullable("string"),
        "label": {"type": "string", "minLength": 1},
        "title": {"type": "string"},
        "rank": {"type": "integer", "minimum": 1},
        "betting": _BETTING_SCHEMA,
        "candidates": {"type": "object"},
        "polls": _POLLS_SCHEMA,
        "models": {"type": "array", "items": _MODEL_SCHEMA},
        "reference": {
            "type": ["object", "null"],
            "properties": {
                "incumbent": _nullable("string"),
                "incumbent_party": {"type": ["string", "null"],
                                    "enum": list(PARTIES) + [None]},
                "seat_party": {"type": ["string", "null"],
                               "enum": list(PARTIES) + [None]},
                "rating": {"type": ["string", "null"],
                           "enum": list(RATINGS) + [None]},
                "history": {"type": "array"},
            },
        },
    },
}

_BUCKET_SCHEMA = {
    "type": "object",
    "required": ["label", "prob", "midpoint"],
    "properties": {
        "label": {"type": "string"},
        "prob": _PROB,
        "rep_low": _nullable("number"),
        "rep_high": _nullable("number"),
        "midpoint": {"type": "number", "minimum": 0},
    },
}

_CHAMBER_SCHEMA = {
    "type": "object",
    "required": ["label", "total_seats", "n_races_shown"],
    "properties": {
        "label": {"type": "string"},
        "total_seats": {"type": "integer", "minimum": 1},
        "n_races_shown": {"type": "integer", "minimum": 0},
        "prob_dem_control": _NULLABLE_PROB,
        "control_volume": {"type": "number", "minimum": 0},
        "expected_dem_seats": _nullable("number"),
        "expected_rep_seats": _nullable("number"),
        "seats_volume": {"type": "number", "minimum": 0},
        "expectation_is_approximate": {"type": "boolean"},
        "seat_buckets": {"type": "array", "items": _BUCKET_SCHEMA},
    },
}

_MOVER_SCHEMA = {
    "type": "object",
    "required": ["race_id", "label", "chamber", "change", "window"],
    "properties": {
        "race_id": {"type": "string"},
        "label": {"type": "string"},
        "chamber": {"enum": list(CHAMBERS)},
        "prob_dem": _NULLABLE_PROB,
        "change": {"type": "number"},
        "window": {"enum": ["1d", "7d"]},
    },
}

JSON_SCHEMA = {
    "type": "object",
    "required": ["meta", "chambers", "races", "balance_of_power", "movers"],
    "properties": {
        "meta": {
            "type": "object",
            "required": ["generated_at", "schema_version", "sources_used",
                         "sources_missing", "rank_by", "pp_per_sigma"],
            "properties": {
                "generated_at": {"type": "string", "minLength": 10},
                "schema_version": {"type": "string"},
                "sources_used": {"type": "array", "items": {"type": "string"}},
                "sources_missing": {"type": "array",
                                    "items": {"type": "string"}},
                "rank_by": {"enum": ["volume1wk", "volume"]},
                "pp_per_sigma": {"type": "number", "minimum": 0},
                "dry_run": {"type": "boolean"},
                "calibration_validated": {"type": "boolean"},
                "calibration_note": _nullable("string"),
            },
        },
        "balance_of_power": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["outcome", "prob"],
                "properties": {"outcome": {"type": "string"},
                               "label": {"type": "string"},
                               "prob": _PROB},
            },
        },
        "chambers": {
            "type": "object",
            "required": list(CHAMBERS),
            "properties": {ch: _CHAMBER_SCHEMA for ch in CHAMBERS},
        },
        "races": {"type": "array", "items": _RACE_SCHEMA},
        "movers": {"type": "array", "items": _MOVER_SCHEMA},
    },
}


# ---------------------------------------------------------------------------
# Validation (dependency-free; jsonschema is not required at runtime)
# ---------------------------------------------------------------------------

class SchemaError(ValueError):
    """Raised when a document does not match the frozen contract."""


def _check(cond: bool, path: str, msg: str, errors: list):
    if not cond:
        errors.append("%s: %s" % (path, msg))


def _validate_node(value, schema: dict, path: str, errors: list):
    if "enum" in schema and "type" not in schema:
        _check(value in schema["enum"], path,
               "value %r not in %r" % (value, schema["enum"]), errors)
        return
    types = schema.get("type")
    if types is not None:
        tlist = types if isinstance(types, list) else [types]
        pymap = {"object": dict, "array": list, "string": str,
                 "number": (int, float), "integer": int, "boolean": bool,
                 "null": type(None)}
        ok = False
        for t in tlist:
            expected = pymap[t]
            if t in ("number", "integer") and isinstance(value, bool):
                continue
            if isinstance(value, expected):
                ok = True
                break
        if not ok:
            _check(False, path, "expected %s, got %s (%r)"
                   % (tlist, type(value).__name__, value), errors)
            return
        if "enum" in schema:
            _check(value in schema["enum"], path,
                   "value %r not in %r" % (value, schema["enum"]), errors)
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if "minimum" in schema:
            _check(value >= schema["minimum"], path,
                   "%r < minimum %r" % (value, schema["minimum"]), errors)
        if "maximum" in schema:
            _check(value <= schema["maximum"], path,
                   "%r > maximum %r" % (value, schema["maximum"]), errors)
    if isinstance(value, str):
        if "minLength" in schema:
            _check(len(value) >= schema["minLength"], path, "string too short",
                   errors)
        if "maxLength" in schema:
            _check(len(value) <= schema["maxLength"], path, "string too long",
                   errors)
    if isinstance(value, dict):
        for req in schema.get("required", []):
            _check(req in value, path, "missing required key %r" % req, errors)
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                _validate_node(value[key], sub, "%s.%s" % (path, key), errors)
    if isinstance(value, list) and "items" in schema:
        for idx, item in enumerate(value):
            _validate_node(item, schema["items"], "%s[%d]" % (path, idx),
                           errors)


def validate(doc: dict) -> None:
    """Validate a forecast document, listing every violation in one shot."""
    errors: list = []
    _validate_node(doc, JSON_SCHEMA, "$", errors)

    if not errors:
        meta = doc["meta"]
        _check(meta["schema_version"] == SCHEMA_VERSION, "$.meta.schema_version",
               "expected %s, got %s" % (SCHEMA_VERSION, meta["schema_version"]),
               errors)

        seen = set()
        per_chamber: dict = {ch: 0 for ch in CHAMBERS}
        ranks: dict = {ch: [] for ch in CHAMBERS}
        for i, race in enumerate(doc["races"]):
            p = "$.races[%d](%s)" % (i, race.get("race_id"))
            rid = race["race_id"]
            _check(rid not in seen, p, "duplicate race_id", errors)
            seen.add(rid)
            per_chamber[race["chamber"]] += 1
            ranks[race["chamber"]].append(race["rank"])

            betting = race["betting"]
            # trustworthy must follow from unmapped_mass, not be set freely —
            # otherwise a Nebraska can be published as a clean two-party quote.
            from . import board
            expected = (betting["prob_dem"] is not None
                        and betting.get("unmapped_mass", 0.0)
                        < board.UNMAPPED_WARN)
            _check(betting["trustworthy"] == expected, p,
                   "betting.trustworthy=%r contradicts unmapped_mass=%r"
                   % (betting["trustworthy"], betting.get("unmapped_mass")),
                   errors)

            for m in race["models"]:
                mp = "%s.models(%s)" % (p, m.get("key"))
                _check(m["n_available"] <= m["n_total"], mp,
                       "n_available=%d exceeds n_total=%d"
                       % (m["n_available"], m["n_total"]), errors)
                # A directionless model must never carry a party lean, and a
                # directional one must never carry an attention level.
                if m["directional"]:
                    _check(m.get("level") is None, mp,
                           "directional model carries an attention level",
                           errors)
                else:
                    _check(m.get("lean") is None, mp,
                           "attention model carries a party lean", errors)
                    _check(m.get("shift_pp") is None, mp,
                           "attention model carries shift_pp", errors)
                if m["n_available"] == 0:
                    _check(m.get("unavailable") is not None, mp,
                           "no variables available but no unavailable reason",
                           errors)

        for ch in CHAMBERS:
            blk = doc["chambers"][ch]
            _check(blk["n_races_shown"] == per_chamber[ch],
                   "$.chambers.%s" % ch,
                   "n_races_shown=%d but races[] carries %d"
                   % (blk["n_races_shown"], per_chamber[ch]), errors)
            expected_ranks = list(range(1, per_chamber[ch] + 1))
            _check(sorted(ranks[ch]) == expected_ranks, "$.chambers.%s" % ch,
                   "ranks are not 1..n contiguous: %s" % sorted(ranks[ch]),
                   errors)

    if errors:
        raise SchemaError(
            "forecast.json violates schema v%s (%d problem%s):\n  %s"
            % (SCHEMA_VERSION, len(errors), "s" if len(errors) != 1 else "",
               "\n  ".join(errors)))


def write_json_schema(path: str) -> None:
    """Dump the machine-readable JSON schema (for external validators)."""
    import json
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(JSON_SCHEMA, fh, ensure_ascii=False, indent=2)
