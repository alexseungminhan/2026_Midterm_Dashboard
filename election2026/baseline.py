"""baseline.py — rolling baselines and z-scores for Track B variables.

PRINCIPLE: absolute sentiment is never used. Every Track B observation enters
the pipeline as a deviation from ITS OWN baseline — per (race, source,
channel). A conservative outlet being pro-Republican is not signal; that
outlet being unusually energized is.

Baselines persist in data/baselines/<source>.json as rolling windows of past
observations, appended once per run.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import config

BASELINE_DIR = os.path.join(config.DATA_DIR, "baselines")


def _key(race_id: str, channel: Optional[str]) -> str:
    return race_id if channel is None else "%s::%s" % (race_id, channel)


def week_windows(anchor_end: date, n: int) -> list:
    """`n` consecutive 7-day windows ending at `anchor_end`, NEWEST FIRST.

    Returns [(start, end)] where index 0 is the week ending on `anchor_end`
    (inclusive, 7 days: end-6 .. end) and index k is k weeks earlier. Live
    pulls and backfill share this convention so a backfilled observation and
    a live one for the same week carry the same `period` label — which is
    what makes BaselineStore.append idempotent across the two paths.
    """
    out = []
    for k in range(n):
        end = anchor_end - timedelta(days=7 * k)
        out.append((end - timedelta(days=6), end))
    return out


# BaselineStore was deleted on 2026-08-09 with the rolling-window design.
# Nothing is carried between runs any more; what survives here is the pure
# maths that a single run still needs.

def structural_residual(observations: dict, covariates: dict):
    """Cross-sectional structural layer before z-scoring.

    Fit a linear regression of the raw variable level on structural
    covariates (incumbency, state/district partisanship, mainstream media
    volume, race population) across races, and return residuals
    (actual − predicted). Races missing covariates are excluded — callers
    fall back to the rolling-mean baseline for those and mark provenance.

    observations: {race_id: value}; covariates: {race_id: {name: float}}.
    Returns ({race_id: residual}, fitted: bool).
    """
    import numpy as np

    keys = ("incumbency", "partisanship", "media_volume", "population")
    rids = [r for r in observations
            if r in covariates
            and all(covariates[r].get(k) is not None for k in keys)]
    if len(rids) < len(keys) + 2:      # too few points to fit sanely
        return {}, False
    X = np.array([[1.0] + [float(covariates[r][k]) for k in keys]
                  for r in rids])
    y = np.array([float(observations[r]) for r in rids])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    return {r: float(y[i] - pred[i]) for i, r in enumerate(rids)}, True


def z_score(value: float, history: list,
            min_obs: Optional[int] = None,
            clip: Optional[float] = None) -> Optional[float]:
    """Pure z-score helper: deviation of `value` from its own history.

    Guards: len(history) < min_obs -> None; zero/near-zero std -> None.
    """
    min_obs = min_obs if min_obs is not None else config.TRACK_B["baseline_min_obs"]
    clip = clip if clip is not None else config.TRACK_B["z_clip"]
    if history is None or len(history) < min_obs:
        return None
    mean = statistics.fmean(history)
    std = statistics.pstdev(history)
    if std < 1e-9:
        return None
    z = (float(value) - mean) / std
    return max(-clip, min(clip, z))
