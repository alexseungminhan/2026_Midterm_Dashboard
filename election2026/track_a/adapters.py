"""track_a/adapters.py — consensus sources: Polymarket, Kalshi, manual polls.

Each adapter returns {"prob_dem": float} (markets) or {"margin_dem": float}
(polls), or None when unavailable. Ratings are not an adapter — they are the
last-resort fallback applied in consensus.py when both channels are missing.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .. import cache, config, manual
from ..adapter import Adapter


class PolymarketAdapter(Adapter):
    """Primary betting source. gamma-api.polymarket.com, no auth."""

    name = "polymarket"
    track = "track_a"
    EVENTS = "https://gamma-api.polymarket.com/events"

    def __init__(self, mock_table: Optional[dict] = None):
        self._mock = mock_table or {}

    def _pull_mock(self, race_id, meta):
        prob = self._mock.get(race_id)
        return None if prob is None else {"prob_dem": prob, "mock": True}

    def _pull_live(self, race_id, meta):
        slug = meta.get("polymarket_slug")
        if not slug:
            return None
        resp = cache.http_get(self.EVENTS, params={"slug": slug})
        if resp is None:
            return None
        resp.raise_for_status()
        events = resp.json()
        if not events:
            return None                      # seat not traded on Polymarket
        markets = events[0].get("markets", [])
        prob = self._extract_dem_prob(markets)
        if prob is None:
            return None
        return {"prob_dem": round(prob, 4), "slug": slug,
                "volume": float(events[0].get("liquidity", 0) or 0)}

    # -- market parsing (proven against the live gamma API) -------------------
    @staticmethod
    def _yes_price(market: dict) -> Optional[float]:
        try:
            outcomes = market.get("outcomes")
            prices = market.get("outcomePrices")
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            if isinstance(prices, str):
                prices = json.loads(prices)
            for label, price in zip(outcomes or [], prices or []):
                if str(label).lower() == "yes":
                    return float(price)
        except Exception:
            return None
        return None

    @classmethod
    def _extract_dem_prob(cls, markets: list) -> Optional[float]:
        for m in markets or []:
            gi = str(m.get("groupItemTitle") or "").lower()
            q = str(m.get("question") or "").lower()
            if ("democrat" in gi or "(d)" in gi
                    or "democrats win" in q or "democratic party" in q):
                price = cls._yes_price(m)
                if price is not None:
                    return price
        return None


class KalshiAdapter(Adapter):
    """Secondary betting source. Public elections read API; an API key from
    the environment is attached only for higher rate limits and the adapter
    skips cleanly (None) when no verified ticker exists for a race."""

    name = "kalshi"
    track = "track_a"
    BASE = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, mock_table: Optional[dict] = None):
        self._mock = mock_table or {}

    def is_available(self) -> bool:
        return bool(config.TRACK_A["kalshi_tickers"])

    def _pull_mock(self, race_id, meta):
        prob = self._mock.get(race_id)
        return None if prob is None else {"prob_dem": prob, "mock": True}

    def _pull_live(self, race_id, meta):
        ticker = config.TRACK_A["kalshi_tickers"].get(race_id)
        if not ticker:
            return None
        headers = {}
        key = os.environ.get("KALSHI_API_KEY")
        if key:
            headers["Authorization"] = "Bearer %s" % key
        resp = cache.http_get("%s/markets" % self.BASE,
                              params={"event_ticker": ticker},
                              headers=headers)
        if resp is None:
            return None
        resp.raise_for_status()
        markets = resp.json().get("markets", [])
        dem = self._pick_dem_market(markets)
        if dem is None:
            return None
        cents = dem.get("last_price")
        if not cents:
            yb, ya = dem.get("yes_bid") or 0, dem.get("yes_ask") or 0
            cents = (yb + ya) / 2 if (yb or ya) else None
        if not cents:
            return None
        return {"prob_dem": round(float(cents) / 100.0, 4)}

    @staticmethod
    def _pick_dem_market(markets: list):
        for m in markets or []:
            label = str(m.get("yes_sub_title") or m.get("subtitle")
                        or m.get("ticker") or "").lower()
            if "democrat" in label or label.endswith("-d") or "(d)" in label:
                return m
        return None


class ManualPollsAdapter(Adapter):
    """Polls from the data/manual/polls spreadsheet (see manual.py).

    No free per-race polling API exists in 2026, so the spreadsheet is the
    authoritative source. Aggregation (recency + sample-size weighting)
    happens here so downstream sees one margin per race.
    """

    name = "polls"
    track = "track_a"

    def __init__(self, mock_table: Optional[dict] = None):
        self._mock = mock_table or {}
        self._loaded: Optional[dict] = None

    def _polls(self) -> dict:
        if self._loaded is None:
            self._loaded = manual.load_polls()   # raises ManualDataError loudly
        return self._loaded

    def is_available(self) -> bool:
        try:
            return bool(self._polls())
        except manual.ManualDataError:
            raise          # malformed manual data must fail loudly, not skip
        except Exception:
            return False

    def _pull_mock(self, race_id, meta):
        margin = self._mock.get(race_id)
        return None if margin is None else {"margin_dem": margin, "mock": True}

    def _pull_live(self, race_id, meta):
        rows = self._polls().get(race_id)
        if not rows:
            return None
        margin = manual.aggregate_polls(rows)
        if margin is None:
            return None
        # matchup_confidence rides along so the UI and the run log can tell a
        # margin built from settled nominees apart from one carried by
        # pre-primary hypotheticals. It does NOT scale p_consensus — the
        # down-weighting already happened inside aggregate_polls.
        return {"margin_dem": round(margin, 2), "n_polls": len(rows),
                "matchup_confidence": manual.poll_confidence(rows)}
