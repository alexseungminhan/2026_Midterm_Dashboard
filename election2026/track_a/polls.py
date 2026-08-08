"""track_a/polls.py — the polling channel, on its own.

Replaces consensus.py (deleted 2026-08-08). That module's job was to average
betting and polls into a single `p_consensus`, and v3 does not average them:
the two channels are shown side by side because they can legitimately
disagree, and collapsing them destroys exactly the information a reader wants.

The Nebraska case is why. Polymarket priced "do the Democrats win Nebraska" at
0.01 while the polls priced Osborn (an independent) against Ricketts at 0.795.
The blend produced 0.215 — a number neither channel believed, describing no
event anyone had traded or polled. v3 shows 시장 1% and 여론조사 79.5% next to
each other, plus a warning that 26% of the market sits on a candidate who is
neither D nor R.

What survives from consensus.py is the margin-to-probability conversion, which
is a property of polling and not of blending.
"""

from __future__ import annotations

from typing import Optional

from scipy.stats import norm

from .. import config


def _clamp(p: float) -> float:
    return max(0.01, min(0.99, float(p)))


def margin_to_prob(margin: Optional[float],
                   sigma: Optional[float] = None) -> Optional[float]:
    """P(Dem win) = Phi(margin / sigma).

    `sigma` is the standard deviation of the final-margin error, i.e. how far
    polls at this distance from election day have historically landed from the
    result — not the sampling error of any single poll.
    """
    if margin is None:
        return None
    sigma = sigma or config.TRACK_A["poll_sigma"]
    if sigma <= 0:
        raise ValueError("poll_sigma must be positive")
    return _clamp(float(norm.cdf(margin / sigma)))
