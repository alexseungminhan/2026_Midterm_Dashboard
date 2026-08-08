"""track_b/quota.py — per-UTC-day API quota budget tracker.

Used by quota-limited sources (YouTube). `take()` refuses calls past the
configured ceiling and logs the remaining budget, so a long race list can
never burn the day's quota silently.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .. import cache


class QuotaBudget:
    def __init__(self, source: str, daily_budget: int):
        self.source = source
        self.daily_budget = daily_budget
        self.path = os.path.join(cache.RAW_DIR, "track_b",
                                 "_quota_%s.json" % source)

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def used(self) -> int:
        return int(self._load().get(self._today(), 0))

    def remaining(self) -> int:
        return max(0, self.daily_budget - self.used())

    def take(self, cost: int) -> bool:
        """Reserve `cost` units; False (and a log line) when over budget."""
        day = self._today()
        used = self.used()
        if used + cost > self.daily_budget:
            print("[track_b] %s: quota budget exhausted (%d/%d used, "
                  "call needs %d) — skipping"
                  % (self.source, used, self.daily_budget, cost))
            return False
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({day: used + cost}, fh)   # keep only today
        print("[track_b] %s: quota %d/%d used, %d remaining"
              % (self.source, used + cost, self.daily_budget,
                 self.daily_budget - used - cost))
        return True
