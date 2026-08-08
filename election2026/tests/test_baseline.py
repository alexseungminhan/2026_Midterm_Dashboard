"""Baseline z-scoring: guards, clipping, rolling windows."""

import pytest

from election2026.baseline import BaselineStore, z_score


def test_z_score_basic():
    history = [90.0, 100.0, 110.0, 100.0]
    z = z_score(120.0, history, min_obs=4)
    assert z == pytest.approx((120 - 100) / 7.0710678, rel=1e-4)


def test_thin_history_returns_none_not_wild():
    assert z_score(500.0, [100.0, 101.0], min_obs=4) is None


def test_zero_variance_returns_none():
    assert z_score(105.0, [100.0] * 8, min_obs=4) is None


def test_extreme_values_clipped():
    history = [99.0, 100.0, 101.0, 100.0]
    assert z_score(1e6, history, min_obs=4, clip=3.0) == 3.0
    assert z_score(-1e6, history, min_obs=4, clip=3.0) == -3.0


def test_store_rolls_window_and_scopes_channels(tmp_path):
    store = BaselineStore("testsrc", directory=str(tmp_path),
                          window=4, min_obs=3)
    for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
        store.append("race-1", v, channel="dem")
    assert store.observations("race-1", "dem") == [20.0, 30.0, 40.0, 50.0]
    # channels are independent baselines
    assert store.observations("race-1", "rep") == []
    assert store.z_score("race-1", 60.0, channel="rep") is None
    z = store.z_score("race-1", 60.0, channel="dem")
    assert z is not None and z > 1.5


def test_store_persists(tmp_path):
    store = BaselineStore("persist", directory=str(tmp_path), window=8)
    store.append("race-1", 1.0)
    store.save()
    again = BaselineStore("persist", directory=str(tmp_path), window=8)
    assert again.observations("race-1") == [1.0]


def test_mixed_provenance_window_scores_like_with_like(tmp_path):
    """A window holding both structural residuals and raw levels holds two
    different quantities. Scoring against the mixture inverted sen-ga's wiki
    signal in production (z -0.61 where the correct answer was +1.51)."""
    from election2026.baseline import BaselineStore
    store = BaselineStore("test_mixed", directory=str(tmp_path))
    # Residuals sit near -0.15; raw shares near +0.85.
    for i, (v, prov) in enumerate([(-0.20, "structural"), (0.80, "rolling"),
                                   (-0.14, "structural"), (0.85, "rolling"),
                                   (-0.17, "structural"), (0.88, "rolling"),
                                   (-0.11, "structural"), (-0.06, "structural")]):
        store.append("r1", v, channel="total", period="2026-07-%02d" % (i + 1),
                     provenance=prov)
    assert store.provenance_mix("r1", "total") == {"structural", "rolling"}
    assert store.latest_provenance("r1", "total") == "structural"
    # Only the structural observations are comparable to a structural value.
    assert store.observations("r1", "total", provenance="structural") \
        == [-0.20, -0.14, -0.17, -0.11, -0.06]
    z = store.z_score("r1", -0.06, channel="total")
    assert z is not None and z > 0, "a value above the residual mean must be +z"


def test_unmixed_window_is_unaffected(tmp_path):
    from election2026.baseline import BaselineStore
    store = BaselineStore("test_clean", directory=str(tmp_path))
    for i, v in enumerate([1.0, 2.0, 3.0, 4.0]):
        store.append("r1", v, channel="total", period="2026-07-%02d" % (i + 1),
                     provenance="rolling")
    assert store.observations("r1", "total") == [1.0, 2.0, 3.0, 4.0]
    assert store.z_score("r1", 5.0, channel="total") is not None
