"""board.py — the board is whatever the betting market trades, ranked by volume.

This replaces the curated `data/reference/races.json` universe as the SOURCE OF
TRUTH for which races appear. races.json is demoted to an enrichment lookup
(incumbent, rating, our poll coverage); a race no longer has to be in it to be
shown, and being in it is no longer enough to be shown.

The universe is the UNION of four Polymarket tags, not the `midterms` tag
alone. The `midterms` tag is incomplete and silently so: the California
governor race — $40.7M, thirty-seven times the next-biggest governor market
and the largest single race market of the cycle — carries `governor-midterms`
but NOT `midterms`, so pulling one tag left the board's governor tab headed by
Alaska and missing California entirely (found 2026-08-11). Pulling all four
costs three extra events and closes that hole: 35 Senate, 36 governor (the
full 2026 slate), 434 House.

Of the ~790 events, ~505 are individual races ("Texas Senate Election Winner",
"CA-28 House Election Winner"); the rest are chamber-level, primary, and
novelty markets, handled in chambers.py or dropped here.

RANKING CAVEAT — read before changing `rank_by`. Cumulative `volume` measures
how long a market has been listed and how much has churned through it, NOT how
contested the seat is. Polymarket lists 443 House districts, and the top of the
cumulative list is safe seats (FL-01, VA-06, MS-01) where volume accreted from
cheap speculation. Measured 2026-08-08: every one of the 18 hand-picked
battleground districts sits BELOW the House median of $23.7K. The board is
ranked on it anyway, so the order matches what polymarket.com shows — the
user's explicit decision (2026-08-08) after being shown these numbers.

Matching polymarket.com also needs config.BOARD["off_board"]; see the note
there. It is applied in rank(), not in build(), because chambers.py counts
seats from every race the market prices, on and off the board.

The recency fields are NOT rankable. Polymarket omits `volume1wk` and
`volume24hr` from any event that has not traded in the window (435 of 697 on
2026-08-08), so they arrive as absent, not as zero. `volume1wk` is carried as
Optional for reference only; nothing sorts or displays it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from . import cache, config

EVENTS_URL = "https://gamma-api.polymarket.com/events"
PAGE = 100
MAX_PAGES = 12          # 640 events per tag today; 1,200 is generous headroom

# Pulled in order and merged on event id. `midterms` carries almost everything;
# the chamber tags are the safety net for races Polymarket forgot to tag, which
# is not hypothetical — see the module docstring on California governor.
TAG_SLUGS = ("midterms", "senate-midterms", "governor-midterms",
             "house-elections")

_CODE_BY_NAME = {name.lower(): code for code, name in config.STATE_NAMES.items()}

# Exact title shapes. Anything that does not match one of these is not an
# individual race and must not reach the board — the midterms tag also carries
# "Balance of Power", seat-count markets, and conditionals like "ACA credits
# extended & House Winner 2026?".
_SENATE = re.compile(r"^(?P<state>[A-Za-z .]+) Senate Election Winner$")
_GOVERNOR = re.compile(r"^(?P<state>[A-Za-z .]+) Governor Election Winner$")
_HOUSE = re.compile(r"^(?P<code>[A-Z]{2})-(?P<district>\d{1,2}|AL) "
                    r"House Election Winner$")

# Above this share of probability on non-D/R candidates, the two-party
# renormalization stops describing the race and the dashboard says so instead
# of printing a number. 10% is a judgement call, not a fitted threshold.
UNMAPPED_WARN = 0.10


@dataclass
class RaceMarket:
    """One tradeable race, as the market defines it."""
    race_id: str
    chamber: str                    # senate | house | governor
    state: str                      # two-letter code
    district: Optional[str]         # "07", "AL", or None
    slug: str
    title: str
    prob_dem: Optional[float]       # two-party normalized, D / (D + R)
    volume: float                   # cumulative USD since listing
    volume1wk: Optional[float]     # None = 최근 거래 없어 폴리마켓이 안 보냄
    liquidity: float
    candidates: dict = field(default_factory=dict)   # {"D": name, "R": name}
    # Share of market probability on candidates who are neither D nor R. Above
    # UNMAPPED_WARN the two-party number is not describing the race.
    unmapped_mass: float = 0.0

    @property
    def two_party_trustworthy(self) -> bool:
        return self.prob_dem is not None and self.unmapped_mass < UNMAPPED_WARN

    @property
    def label(self) -> str:
        """Human label for the dashboard, in Korean where we have one."""
        state = config.STATE_NAMES_KO.get(
            self.state, config.STATE_NAMES.get(self.state, self.state))
        if self.chamber == "house":
            return "%s %s 선거구" % (state, self.district.lstrip("0") or "AL")
        return "%s %s" % (state, "주지사" if self.chamber == "governor" else "상원")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_tag(tag: str) -> list:
    """Every open event on one tag, paged out."""
    events, offset = [], 0
    for _ in range(MAX_PAGES):
        resp = cache.http_get(EVENTS_URL, params={
            "tag_slug": tag, "closed": "false", "limit": PAGE,
            "offset": offset, "order": "volume", "ascending": "false"})
        if resp is None:
            break
        try:
            resp.raise_for_status()
            batch = resp.json()
        except Exception:
            break
        events.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return events


def fetch_events(ttl_hours: float = 6.0) -> list:
    """All open events across TAG_SLUGS, newest prices, disk-cached.

    De-duplicated on event id, first tag wins — the tags overlap heavily and
    the payloads are identical where they do.

    Returns [] rather than raising when Polymarket is unreachable — the daily
    run then falls back to the previous snapshot instead of dying.
    """
    cached = cache.read("track_a", "polymarket", "midterms_universe", ttl_hours)
    if cached is not None:
        return cached.get("events", [])

    seen, events = set(), []
    for tag in TAG_SLUGS:
        for event in _fetch_tag(tag):
            key = event.get("id")
            if key in seen:
                continue
            seen.add(key)
            events.append(event)

    if events:
        cache.write("track_a", "polymarket", "midterms_universe",
                    {"events": events})
    else:                                    # keep serving the stale copy
        stale = cache.read("track_a", "polymarket", "midterms_universe")
        if stale is not None:
            print("[board] Polymarket unreachable — using the cached universe")
            return stale.get("events", [])
    return events


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def _as_list(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return value or []


def _yes_price(market: dict) -> Optional[float]:
    outcomes = _as_list(market.get("outcomes"))
    prices = _as_list(market.get("outcomePrices"))
    for label, price in zip(outcomes, prices):
        if str(label).lower() == "yes":
            try:
                return float(price)
            except (TypeError, ValueError):
                return None
    return None


def _party_of(label: str) -> Optional[str]:
    """D/R from a market leg's label, across both shapes Polymarket uses.

    House races name parties ("Democratic Party"); Senate and governor races
    name candidates ("James Talarico (D)"). Note the .strip(): Georgia Senate
    ships "Jon Ossoff (D) " with a trailing space, and an unstripped
    endswith() silently drops the only Democratic leg in the race.

    Returns None for unlabeled legs — Alaska's jungle primary lists 22 bare
    candidate names with no party marker at all. Those are resolved against
    CANDIDATE_PARTIES, and whatever stays unresolved is reported as unmapped
    mass rather than quietly excluded.
    """
    title = str(label or "").strip().lower()
    if "democrat" in title or title.endswith("(d)"):
        return "D"
    if "republican" in title or title.endswith("(r)"):
        return "R"
    return None


def _load_candidate_parties() -> dict:
    path = os.path.join(config.REFERENCE_DIR, "candidate_parties.json")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return {}
    return {k.strip().lower(): v for k, v in raw.get("candidates", {}).items()}


CANDIDATE_PARTIES = _load_candidate_parties()


def _two_party(markets: list) -> tuple:
    """(prob_dem, names, unmapped_mass) with the two-party normalization.

    Three corrections over reading the Democratic leg directly:

    1. BOTH legs are read and renormalized to D/(D+R). Polymarket prices each
       leg independently and they do not sum to 1 — Texas Senate quoted
       D 0.495 / R 0.515 on 2026-08-08. Reading D alone inherits that spread.
    2. Legs are SUMMED per party, not taken first-match. Alaska's governor race
       lists a dozen Republicans and four Democrats; the leading Republican's
       0.335 is not the Republican probability.
    3. Probability sitting on candidates who are neither D nor R is returned as
       `unmapped_mass`. When that is large, D/(D+R) is answering a different
       question than "who wins" — the Nebraska/Osborn failure, where a
       three-way race renormalized to a number neither channel believed.
    """
    totals = {"D": 0.0, "R": 0.0}
    best = {"D": (-1.0, None), "R": (-1.0, None)}   # -1 so a 0.0 leg counts
    quoted = set()
    unmapped = 0.0

    for m in markets or []:
        price = _yes_price(m)
        if price is None:
            continue
        label = str(m.get("groupItemTitle") or "").strip()
        party = _party_of(label) or CANDIDATE_PARTIES.get(label.lower())
        if party not in ("D", "R"):
            unmapped += price
            continue
        quoted.add(party)
        totals[party] += price
        if price > best[party][0]:                  # leading candidate's name
            best[party] = (price, label)

    names = {p: n for p, (_, n) in best.items() if n}
    denom = totals["D"] + totals["R"]
    unmapped_share = (unmapped / (denom + unmapped)
                      if (denom + unmapped) else 0.0)

    # BOTH parties must actually be quoted. With only a Republican leg the
    # ratio evaluates to a clean 0.0 — "the Democrat has no chance" — when the
    # truth is that the market is not pricing a D-vs-R contest at all.
    # Nebraska is the live example: Ricketts plus an independent, no
    # Democratic leg. A safe seat is NOT this case; Polymarket quotes both
    # legs there (CA-28 carries a Democratic leg at 0.9695 and a Republican
    # one at 0.0125), and a leg quoted at exactly 0 is still a quote —
    # `quoted` tracks presence separately from price for that reason.
    if not quoted.issuperset({"D", "R"}) or denom <= 0:
        return None, names, round(unmapped_share, 4)

    return round(totals["D"] / denom, 4), names, round(unmapped_share, 4)


def parse_event(event: dict) -> Optional[RaceMarket]:
    """A RaceMarket, or None when the event is not an individual race."""
    title = str(event.get("title") or "").strip()

    chamber = state = district = None
    m = _HOUSE.match(title)
    if m:
        chamber, state = "house", m.group("code")
        district = m.group("district")
        if district != "AL":
            district = district.zfill(2)
    else:
        m = _SENATE.match(title)
        if m:
            chamber = "senate"
        else:
            m = _GOVERNOR.match(title)
            if m:
                chamber = "governor"
        if m:
            state = _CODE_BY_NAME.get(m.group("state").strip().lower())

    if chamber is None or state is None:
        return None

    prob_dem, names, unmapped = _two_party(event.get("markets", []))
    suffix = (district.lstrip("0") or "AL").lower() if district else ""
    prefix = {"senate": "sen", "house": "ho", "governor": "gov"}[chamber]

    def _f(key):
        try:
            return float(event.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _opt(key):
        """None when the key is absent — absent is not zero.

        Polymarket ships `volume1wk` only on events that traded in the window.
        Coercing the missing key to 0.0 printed "$0 this week" next to
        $118,980 cumulative on South Carolina Senate, which reads as a dead
        market rather than as no data. 435 of 697 events on 2026-08-08.
        """
        if event.get(key) is None:
            return None
        try:
            return float(event[key])
        except (TypeError, ValueError):
            return None

    return RaceMarket(
        race_id="%s-%s%s" % (prefix, state.lower(), suffix),
        chamber=chamber, state=state, district=district,
        slug=str(event.get("slug") or ""), title=title,
        prob_dem=prob_dem, volume=_f("volume"), volume1wk=_opt("volume1wk"),
        liquidity=_f("liquidity"), candidates=names, unmapped_mass=unmapped,
    )


def build(events: list) -> list:
    """Parse, drop non-races, and de-duplicate on race_id keeping the deepest
    market. Polymarket occasionally lists a seat twice (a special election
    alongside the regular one); the thinner book is the stale one."""
    best = {}
    for event in events:
        race = parse_event(event)
        if race is None:
            continue
        prior = best.get(race.race_id)
        if prior is None or race.volume > prior.volume:
            best[race.race_id] = race
    return list(best.values())


# ---------------------------------------------------------------------------
# Rank
# ---------------------------------------------------------------------------

def rank(races: list, metric: Optional[str] = None,
         top_n: Optional[dict] = None,
         off_board: Optional[set] = None) -> dict:
    """{chamber: [RaceMarket, ...]} sorted by `metric`, truncated per chamber.

    Cumulative volume is the only ranking the board offers (2026-08-08). The
    recency metrics cannot be ranked on: Polymarket OMITS `volume1wk` and
    `volume24hr` from an event that has not traded recently — 435 of 697
    events on 2026-08-08 — so an absent key is "unknown", not "zero", and
    ranking on it would sort two thirds of the board on a fabricated 0.

    `off_board` races are dropped before sorting. They are still parsed, still
    priced, and still counted by chambers.py — they are only kept off the
    displayed board. See config.BOARD["off_board"].
    """
    metric = metric or config.BOARD["rank_by"]
    top_n = top_n or config.BOARD["top_n"]
    if off_board is None:
        off_board = set(config.BOARD["off_board"])

    out = {}
    for chamber in ("senate", "house", "governor"):
        rows = [r for r in races
                if r.chamber == chamber and r.race_id not in off_board]
        rows.sort(key=lambda r: (getattr(r, metric), r.volume), reverse=True)
        limit = top_n.get(chamber)
        out[chamber] = rows[:limit] if limit else rows
    return out


def load(ttl_hours: float = 6.0, metric: Optional[str] = None) -> dict:
    """Everything above, in one call: {chamber: [RaceMarket, ...]}."""
    return rank(build(fetch_events(ttl_hours)), metric)
