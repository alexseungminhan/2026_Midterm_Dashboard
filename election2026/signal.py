"""signal.py — the per-variable Track B reading.

Reduced to a single dataclass in the v3 redesign (2026-08-08). This module
used to hold the machinery that collapsed fifteen variables into one number:
`combine()`, `agreement_of()`, `coverage_of()`, `trajectory_factor()`,
`reference_variable_count()`, `breakdown()`.

All of it existed to make ONE blended signal well-behaved — the agreement
factor cancelled conflicting evidence, and sqrt(n_available/n_reference)
equalized variance so that races with different coverage flagged at
comparable rates. Neither correction has anything to correct now: v3 does not
blend, does not flag, and reports each model's coverage as a plain "3/5" for
the reader to discount themselves (models.py).

What remains is the reading itself, produced by track_b/signals.py and grouped
into models by models.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class VariableReading:
    """One Track B variable's Democrat-oriented z-score for a race."""
    variable: str                 # config key: "fec_in_state_share", ...
    z: Optional[float]            # Dem-positive z-score; None = unavailable
    provenance: str = "rolling"   # "structural" residual | "rolling" mean
    directional: bool = True      # False => attention-only (never votes D/R)
    # available | missing | structural | pending — see track_b/adapters.py.
    # "structural" is permanent and expected (the FEC has no jurisdiction over
    # governor races); "missing" means this run failed and warrants a look.
    availability: str = "available"
    reason: Optional[str] = None
    # The two sides as the source reported them, before any contrast. The
    # detail view shows these rather than the derived number: "주내 기부 비중
    # 민주 30.5% · 공화 4.5%" is checkable, "+0.74" is not.
    dem_value: Optional[float] = None
    rep_value: Optional[float] = None
