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


class BaselineStore:
    """Rolling per-(race, source, channel) observation windows on disk."""

    def __init__(self, source: str, directory: str = BASELINE_DIR,
                 window: Optional[int] = None,
                 min_obs: Optional[int] = None):
        self.source = source
        self.window = window or config.TRACK_B["baseline_window"]
        self.min_obs = min_obs or config.TRACK_B["baseline_min_obs"]
        self.path = os.path.join(directory, "%s.json" % source)
        self._data = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=1)

    def observations(self, race_id: str, channel: Optional[str] = None,
                     provenance: Optional[str] = None) -> list:
        """Stored values, oldest -> newest.

        `provenance` restricts to observations collected the same way. A
        window that mixes structural residuals with raw levels holds two
        different QUANTITIES — measured on sen-ga 2026-08-03, residuals near
        -0.15 sat alongside raw shares near +0.85 — and averaging them
        produces a mean that describes neither, so every z-score off that
        window is wrong rather than merely noisy.
        """
        rows = self._data.get(_key(race_id, channel), [])
        if provenance is not None:
            rows = [o for o in rows if o.get("provenance") == provenance]
        return [o["value"] for o in rows]

    def latest_provenance(self, race_id: str,
                          channel: Optional[str] = None) -> Optional[str]:
        """How the NEWEST stored observation was collected."""
        rows = self._data.get(_key(race_id, channel), [])
        return rows[-1].get("provenance") if rows else None

    def periods(self, race_id: str, channel: Optional[str] = None) -> list:
        """Observation period labels, oldest -> newest (None where absent)."""
        return [o.get("period") for o in self._data.get(_key(race_id, channel), [])]

    def provenance_mix(self, race_id: str,
                       channel: Optional[str] = None) -> set:
        """Distinct provenances in one window. More than one means the window
        mixes structural residuals with raw levels and its z-score is not
        trustworthy — callers surface this rather than quietly using it."""
        return {o.get("provenance") for o in
                self._data.get(_key(race_id, channel), [])
                if o.get("provenance")}

    def append(self, race_id: str, value: float,
               channel: Optional[str] = None,
               period: Optional[str] = None,
               at: Optional[str] = None,
               provenance: Optional[str] = None) -> None:
        """Record an observation into the rolling window.

        `period` labels the window the observation covers (YYYY-MM-DD, the
        window's end date). Supplying it makes the append IDEMPOTENT: writing
        the same period again replaces that entry instead of stacking a
        duplicate. This matters because sources advance on their own schedule
        — the FEC frontier only moves when a new filing is loaded, so two runs
        a day apart legitimately observe the same week, and duplicating it
        would shrink the window's variance and inflate every later z-score.

        Entries are kept sorted oldest -> newest by period so backfilled
        history and live observations interleave correctly.

        `provenance` ("structural" residual vs "rolling" raw level) is stored
        alongside the value. The two are on different scales, so a window
        that mixes them is comparing apples to oranges — recording it makes
        that visible (see `provenance_mix`) instead of silently wrong.
        """
        k = _key(race_id, channel)
        window = self._data.setdefault(k, [])
        entry = {
            "value": float(value),
            "at": at or datetime.now(timezone.utc).isoformat(),
        }
        if provenance is not None:
            entry["provenance"] = provenance
        if period is not None:
            entry["period"] = period
            window[:] = [o for o in window if o.get("period") != period]
        window.append(entry)
        window.sort(key=lambda o: (o.get("period") or "", o.get("at") or ""))
        del window[:-self.window]

    def z_score(self, race_id: str, value: float,
                channel: Optional[str] = None) -> Optional[float]:
        """z of `value` against the stored baseline (excluding `value` itself).

        Returns None — never a wild number — when the history is too thin or
        degenerate (zero variance). Result is clipped to ±z_clip so a single
        anomalous observation cannot dominate the combined signal.
        """
        # Compare like with like: score against the observations gathered the
        # same way as the newest one, not against a mixture of scales. When
        # that leaves too few, z_score returns None — an honest gap, which is
        # the right outcome. The alternative on a mixed window is a confident
        # number computed from a baseline that describes nothing.
        mix = self.provenance_mix(race_id, channel)
        if len(mix) > 1:
            history = self.observations(
                race_id, channel,
                provenance=self.latest_provenance(race_id, channel))
        else:
            history = self.observations(race_id, channel)
        return z_score(value, history, min_obs=self.min_obs)


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
