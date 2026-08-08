"""Track A — what other people already think.

Betting markets and poll averages. In v2 these were averaged into a single
`p_consensus` that Track B then nudged; in v3 (2026-08-08) they are two
separate columns on the board and nothing combines them. See polls.py for why.

The package stays structurally isolated from track_b — nothing here imports
from election2026.track_b, and its configuration lives in config.TRACK_A only.
"""

from .adapters import KalshiAdapter, ManualPollsAdapter, PolymarketAdapter  # noqa: F401
from .polls import margin_to_prob  # noqa: F401

ADAPTERS = {
    "polymarket": PolymarketAdapter,
    "kalshi": KalshiAdapter,
    "polls": ManualPollsAdapter,
}
