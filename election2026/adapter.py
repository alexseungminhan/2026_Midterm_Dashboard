"""adapter.py — the uniform source-adapter interface.

Every data source (both tracks) implements:

    class SomeAdapter(Adapter):
        name  = "polymarket"
        track = "track_a"           # or "track_b" — sets the cache namespace

        def is_available(self) -> bool: ...        # keys present, not stubbed
        def _pull_live(self, race_id, meta) -> Optional[dict]: ...
        def _pull_mock(self, race_id, meta) -> Optional[dict]: ...

Guarantees enforced here, not by convention:
  * fetch() NEVER raises into the pipeline — any failure returns None.
  * every successful pull is cached to disk with a timestamp; on live failure
    the newest cache is used.
  * a missing source yields None so downstream weight renormalization can
    drop it — it must not silently bias anything toward zero.
"""

from __future__ import annotations

from typing import Optional

from . import cache


class Adapter:
    name = "base"
    track = "track_a"

    def is_available(self) -> bool:
        """Can this source be queried live right now (keys, not a stub)?"""
        return True

    # -- subclasses implement -------------------------------------------------
    def _pull_live(self, race_id: str, meta: dict) -> Optional[dict]:
        raise NotImplementedError

    def _pull_mock(self, race_id: str, meta: dict) -> Optional[dict]:
        raise NotImplementedError

    # -- uniform entry point --------------------------------------------------
    def fetch(self, race_id: str, meta: dict, dry_run: bool = False,
              ttl_hours: Optional[float] = None) -> Optional[dict]:
        try:
            if dry_run:
                return self._pull_mock(race_id, meta)
            if not self.is_available():
                return self._cached(race_id)
            fresh = cache.read(self.track, self.name, race_id,
                               max_age_hours=ttl_hours)
            if fresh is not None:
                return fresh
            payload = self._pull_live(race_id, meta)
            if payload is not None:
                cache.write(self.track, self.name, race_id, payload)
                return payload
        except Exception as exc:  # graceful failure — never crash the run
            print("[%s] %s failed for %s: %s"
                  % (self.track, self.name, race_id, exc))
        return self._cached(race_id)

    def _cached(self, race_id: str) -> Optional[dict]:
        payload = cache.read(self.track, self.name, race_id)
        if payload is not None:
            print("[%s] %s: using cached data for %s"
                  % (self.track, self.name, race_id))
        return payload
