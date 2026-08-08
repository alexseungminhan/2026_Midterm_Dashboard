"""election2026 — 2026 미국 중간선거 모니터.

Three channels, shown side by side, never combined:

  betting  Polymarket. Defines WHICH races appear and in what order — the
           board is the market's own universe ranked by trading volume
           (board.py), not a hand-curated list. Chamber totals and seat
           counts are read straight off the chamber-level markets
           (chambers.py) rather than aggregated from our races.
  polls    A manually maintained spreadsheet; no free per-race polling API
           exists for 2026. Margin -> probability via Phi(margin/sigma).
  models   Four models over the Track B variables (models.py): 경제, 정치자금,
           풀뿌리, 관심도. Each reports a lean and a strength, not a win
           probability, because no fitted mapping from indicator to outcome
           exists here.

v2 blended all of this into `p_alpha = p_consensus + λ·signal` with λ hand-set
and unvalidated. v3 (2026-08-08) deleted the blend. See schema.py's docstring
for the contract and models.py's for the reasoning.

Output: data/forecast.json (schema.py is the frozen contract) + a dated
snapshot in data/history/, consumed by dashboard.html.
"""

import os


def _load_dotenv(path: str = None) -> None:
    """Load .env from the project root so API keys need no shell export."""
    path = path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()
