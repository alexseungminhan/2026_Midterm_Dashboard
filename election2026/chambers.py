"""chambers.py — chamber-level numbers, read straight off the market.

"상원은 민주당 몇 석?" does not need a model. Polymarket prices it directly:

    Which party will win the Senate in 2026?          $3.8M
    Republican Senate seats after the 2026 midterms?  $2.8M   (bucketed)
    Balance of Power: 2026 Midterms                   $9.5M   (joint outcome)

So this module READS rather than aggregates. That is deliberate. Aggregating
our own board into a seat count is not possible and never was: the board holds
at most a few dozen races, the House has 435 seats, and the races we do not
show are exactly the ones whose outcomes we would have to assume.

The control probability comes from the "which party" market and NOT from the
seat distribution, because chamber control is not a clean function of the seat
count — a 50-50 Senate is decided by the Vice President. Traders price the
tiebreak rules; a midpoint sum does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import board

TOTAL_SEATS = {"senate": 100, "house": 435, "governor": 50}

CONTROL_SLUGS = {
    "senate": "which-party-will-win-the-senate-in-2026",
    "house": "which-party-will-win-the-house-in-2026",
}
SEATS_SLUGS = {
    "senate": "republican-senate-seats-after-the-2026-midterm-elections-927",
    "house": "republican-house-seats-after-the-2026-midterm-elections",
    "governor": "how-many-republican-governors-after-the-2026-midterm-elections",
}
BALANCE_SLUG = "balance-of-power-2026-midterms"

# Bucket labels come in six shapes across the three markets, including an
# EN DASH in the governor market ("22–23") where the others use a hyphen.
_RANGE = re.compile(r"^(\d+)\s*[-–—]\s*(\d+)$")
_BELOW = re.compile(r"^(?:below|under|<|≤|<=)\s*(\d+)$", re.I)
_ABOVE = re.compile(r"^(?:above|over|>|≥|>=)?\s*(\d+)\s*\+$", re.I)
_EXACT = re.compile(r"^(\d+)$")


@dataclass
class SeatBucket:
    label: str
    prob: float                 # renormalized across the market's buckets
    low: Optional[float]        # None = open-ended below
    high: Optional[float]       # None = open-ended above
    midpoint: float


@dataclass
class ChamberReading:
    chamber: str
    total_seats: int
    prob_dem_control: Optional[float] = None      # from the "which party" market
    control_volume: float = 0.0
    expected_dem_seats: Optional[float] = None    # from the seat distribution
    expected_rep_seats: Optional[float] = None
    seats_volume: float = 0.0
    buckets: list = field(default_factory=list)   # [SeatBucket] in R-seat terms
    # True when the extreme buckets are open-ended, which they always are here.
    # The expectation then depends on an assumed value for "Below 190"/"230+",
    # so it is an estimate, not a quote.
    expectation_is_approximate: bool = True


def _parse_bucket(label: str, width_hint: float) -> Optional[tuple]:
    """(low, high, midpoint) in R seats, or None when the label is not a bucket.

    Open-ended buckets ("Below 190", "230+") have no midpoint of their own, so
    they are assigned one half-width past the edge. That is a modelling choice
    and it is the reason `expectation_is_approximate` exists: "Below 190"
    carries 23.5% of the House distribution, and where its mass actually sits
    is not something the market tells us.
    """
    text = str(label or "").strip()

    m = _RANGE.match(text)
    if m:
        low, high = float(m.group(1)), float(m.group(2))
        return low, high, (low + high) / 2.0

    m = _BELOW.match(text)
    if m:
        edge = float(m.group(1))
        return None, edge, edge - width_hint / 2.0

    m = _ABOVE.match(text)
    if m:
        edge = float(m.group(1))
        return edge, None, edge + width_hint / 2.0

    m = _EXACT.match(text)
    if m:
        v = float(m.group(1))
        return v, v, v

    return None


def _bucket_width(labels: list) -> float:
    """Typical width of the CLOSED buckets, used to place the open ones."""
    widths = []
    for label in labels:
        m = _RANGE.match(str(label).strip())
        if m:
            widths.append(float(m.group(2)) - float(m.group(1)) + 1)
        elif _EXACT.match(str(label).strip()):
            widths.append(1.0)
    return sorted(widths)[len(widths) // 2] if widths else 1.0


def read_seat_distribution(event: dict) -> tuple:
    """([SeatBucket], expected_rep_seats). Probabilities are renormalized.

    Polymarket's buckets sum to slightly over 1 (the House market summed to
    1.089 on 2026-08-08) because each leg carries its own spread. Renormalizing
    is required before the expectation means anything.
    """
    legs = []
    for m in event.get("markets", []):
        price = board._yes_price(m)
        if price is None:
            continue
        legs.append((str(m.get("groupItemTitle") or "").strip(), price))

    width = _bucket_width([label for label, _ in legs])
    parsed, total = [], 0.0
    for label, price in legs:
        got = _parse_bucket(label, width)
        if got is None:
            continue
        low, high, mid = got
        parsed.append((label, price, low, high, mid))
        total += price

    if not parsed or total <= 0:
        return [], None

    buckets = [SeatBucket(label=label, prob=round(price / total, 4),
                          low=low, high=high, midpoint=mid)
               for label, price, low, high, mid in parsed]
    expected = sum(b.prob * b.midpoint for b in buckets)
    return buckets, round(expected, 1)


def read_control(event: dict) -> Optional[float]:
    """P(Democrats win the chamber), two-party normalized."""
    prob_dem, _names, _unmapped = board._two_party(event.get("markets", []))
    return prob_dem


def read_balance_of_power(event: dict) -> list:
    """[(outcome, prob)] for the joint House+Senate market, renormalized.

    The single most-traded midterm market ($9.5M on 2026-08-08) and the most
    legible summary of the whole election, so it heads the dashboard.
    """
    legs = []
    for m in event.get("markets", []):
        price = board._yes_price(m)
        if price is None:
            continue
        legs.append([str(m.get("groupItemTitle") or "").strip(), price])
    total = sum(p for _, p in legs)
    if total <= 0:
        return []
    return [(label, round(price / total, 4)) for label, price in legs]


def read_all(events: list) -> tuple:
    """({chamber: ChamberReading}, [(outcome, prob)]) from the raw event list."""
    by_slug = {e.get("slug"): e for e in events}
    readings = {}

    for chamber, total in TOTAL_SEATS.items():
        reading = ChamberReading(chamber=chamber, total_seats=total)

        event = by_slug.get(CONTROL_SLUGS.get(chamber, ""))
        if event is not None:
            reading.prob_dem_control = read_control(event)
            reading.control_volume = float(event.get("volume") or 0)

        event = by_slug.get(SEATS_SLUGS.get(chamber, ""))
        if event is not None:
            buckets, expected_rep = read_seat_distribution(event)
            reading.buckets = buckets
            reading.seats_volume = float(event.get("volume") or 0)
            if expected_rep is not None:
                reading.expected_rep_seats = expected_rep
                reading.expected_dem_seats = round(total - expected_rep, 1)

        readings[chamber] = reading

    balance_event = by_slug.get(BALANCE_SLUG)
    balance = read_balance_of_power(balance_event) if balance_event else []
    return readings, balance
