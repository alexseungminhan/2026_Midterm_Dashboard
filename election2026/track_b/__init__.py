"""Track B — base signals (the alpha track).

Signals polls miss: five FEC donation variables, primary turnout by party,
party-registration net change, media exposure, state-subreddit sentiment,
channel enthusiasm, and Wikipedia attention. Every variable enters as a
DEVIATION from its own rolling baseline (per race, per source, per channel)
— absolute levels are never used.

STRUCTURALLY isolated from Track A: nothing here imports from
election2026.track_a, and its config lives in config.TRACK_B only. Track B
output is a single signal per race, consumed only by the divergence engine.
"""

from .signals import compute_readings, ADAPTERS  # noqa: F401
