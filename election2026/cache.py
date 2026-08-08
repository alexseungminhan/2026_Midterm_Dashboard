"""cache.py — shared plumbing: disk cache, retrying HTTP, rate limiting.

Pure infrastructure. Both tracks may use these helpers — they carry no data
and therefore cannot leak signal between Track A and Track B.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import requests
except Exception:  # requests is a hard dep, but stay import-safe
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")

HTTP_TIMEOUT = 10
RETRIES = 3
BACKOFF_BASE = 1.5  # seconds; grows 1.5, 3.0, 6.0 ...
# 429 means "you are going too fast", so it gets its own, much longer ladder.
# GDELT in particular keeps refusing for a while after a burst, and retrying
# on the ordinary backoff just spends the attempts without waiting out the
# cooldown.
THROTTLE_BACKOFF = 20.0  # seconds; grows 20, 40, 80 ...

SESSION = requests.Session() if requests is not None else None
if SESSION is not None:
    SESSION.headers.update(
        {"User-Agent": "election2026/3.0 (midterm monitor)"})


class RateLimiter:
    """Enforce a minimum interval between calls to a rate-limited host."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            gap = time.time() - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.time()


def http_get(url: str, *, params=None, headers=None, timeout=HTTP_TIMEOUT,
             retries=RETRIES, limiter: Optional[RateLimiter] = None):
    """GET with retry + exponential backoff. Returns a Response or None.

    Never raises: adapters treat None as "source unavailable right now".
    4xx responses (except 429) are not retried — they won't get better.
    """
    if SESSION is None:
        return None
    last_exc = None
    for attempt in range(retries):
        throttled = False
        try:
            if limiter is not None:
                limiter.wait()
            resp = SESSION.get(url, params=params, headers=headers,
                               timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                throttled = resp.status_code == 429
                raise RuntimeError("HTTP %d" % resp.status_code)
            return resp
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                base = THROTTLE_BACKOFF if throttled else BACKOFF_BASE
                time.sleep(base * (2 ** attempt))
    print("[cache] GET %s failed after %d tries: %s" % (url, retries, last_exc))
    return None


# ---------------------------------------------------------------------------
# Disk cache — data/raw/<track>/<source>/<key>.json with a fetch timestamp
# ---------------------------------------------------------------------------

def _path(track: str, source: str, key: str) -> str:
    d = os.path.join(RAW_DIR, track, source)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s.json" % key)


def write(track: str, source: str, key: str, payload: dict) -> None:
    record = {
        "source": source,
        "key": key,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    with open(_path(track, source, key), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)


def read(track: str, source: str, key: str,
         max_age_hours: Optional[float] = None) -> Optional[dict]:
    """Return the cached payload, or None if absent / unreadable / too old."""
    path = _path(track, source, key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        if max_age_hours is not None:
            fetched = datetime.fromisoformat(record["fetched_at"])
            age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
            if age_h > max_age_hours:
                return None
        return record.get("payload")
    except Exception:
        return None
