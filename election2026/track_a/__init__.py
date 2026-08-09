"""Track A — what other people already think.

Betting markets and poll averages. In v2 these were averaged into a single
`p_consensus` that Track B then nudged; in v3 (2026-08-08) they are two
separate columns on the board and nothing combines them. See polls.py for why.

The package stays structurally isolated from track_b — nothing here imports
from election2026.track_b, and its configuration lives in config.TRACK_A only.
"""

from .polls import margin_to_prob  # noqa: F401

# adapters.py was deleted on 2026-08-09. Its three classes had no callers
# left: board.py pulls Polymarket itself (it needs the whole midterms tag,
# not one race at a time), manual.py loads the poll workbook, and the Kalshi
# adapter never ran — its ticker map was empty from the start.
