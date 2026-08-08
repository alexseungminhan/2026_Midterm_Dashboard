"""backfill: reconstructing baseline history without corrupting it.

Network-free — the adapters are replaced by fakes, so what is under test is
the contract between backfill and the baseline store, not the APIs.
"""

import pytest

from election2026 import backfill, config
from election2026.baseline import BaselineStore
from election2026.track_b.adapters import TrackBAdapter


class FakeAdapter(TrackBAdapter):
    """Reports a known weekly series; never touches the network."""

    name = "gdelt"
    supports_backfill = True
    anchor = "2026-05-01"

    def is_available(self):
        return True

    def observation_period(self, race_id, meta):
        return self.anchor

    def _pull_historical(self, race_id, meta, start, end):
        # A deterministic but varying value so the baseline has real variance.
        return {"dem": {"value": 100.0 + end.day},
                "rep": {"value": 50.0 + end.day}}


class DeadAdapter(FakeAdapter):
    name = "fec_small_dollar_count"

    def is_available(self):
        return False


RACES = {"sen-ga": config.RACES["sen-ga"], "sen-nc": config.RACES["sen-nc"]}


@pytest.fixture
def stores(tmp_path):
    return {name: BaselineStore(name, directory=str(tmp_path))
            for name in config.TRACK_B["weights"]}


def test_backfill_never_writes_the_live_observation_window():
    """The window the next run will observe must stay out of its own baseline.

    If backfill wrote it, that run would z-score the observation against a
    history containing itself — a guaranteed pull toward z = 0.
    """
    series = FakeAdapter().backfill_series("sen-ga", RACES["sen-ga"], 8)
    assert FakeAdapter.anchor not in series
    assert max(series) < FakeAdapter.anchor
    assert len(series) == 8


def test_backfilled_weeks_are_contiguous_and_weekly():
    from datetime import date, timedelta

    series = FakeAdapter().backfill_series("sen-ga", RACES["sen-ga"], 4)
    days = sorted(date.fromisoformat(p) for p in series)
    assert [b - a for a, b in zip(days, days[1:])] == [timedelta(days=7)] * 3


def test_apply_fills_the_baseline_deep_enough_to_produce_a_z_score(stores):
    collected = backfill.collect(RACES, 8, {"gdelt": FakeAdapter})
    written = backfill.apply(collected, stores)

    assert written["gdelt"] == 8 * len(RACES)    # one per race-week
    store = stores["gdelt"]
    for rid in RACES:
        for channel in ("dem", "rep"):
            obs = store.observations(rid, channel)
            assert len(obs) >= config.TRACK_B["baseline_min_obs"]
            assert store.z_score(rid, obs[-1] + 50, channel=channel) is not None


def test_backfill_is_idempotent(stores):
    """Re-running must refresh the same weeks, not stack duplicates.

    Duplicates would shrink the window's variance and inflate every later
    z-score — the failure mode is silent, so it is pinned here.
    """
    collected = backfill.collect(RACES, 8, {"gdelt": FakeAdapter})
    backfill.apply(collected, stores)
    first = stores["gdelt"].observations("sen-ga", "dem")

    backfill.apply(collected, stores)
    assert stores["gdelt"].observations("sen-ga", "dem") == first


def test_rolling_window_caps_an_overlong_backfill(stores):
    window = config.TRACK_B["baseline_window"]
    collected = backfill.collect(RACES, window + 5, {"gdelt": FakeAdapter})
    backfill.apply(collected, stores)
    obs = stores["gdelt"].observations("sen-ga", "dem")
    assert len(obs) == window
    # The newest weeks are the ones kept.
    periods = stores["gdelt"].periods("sen-ga", "dem")
    assert periods == sorted(periods)
    assert max(periods) < FakeAdapter.anchor


def test_unavailable_source_is_skipped_not_fatal(stores, capsys):
    collected = backfill.collect(RACES, 8, {"fec_small_dollar_count": DeadAdapter})
    assert collected == {}
    assert backfill.apply(collected, stores) == {}
    assert "unavailable" in capsys.readouterr().out


def test_only_backfillable_sources_are_selected():
    selected = backfill._backfillable()
    assert {"fec_small_dollar_count", "fec_unique_donors",
            "fec_in_state_share", "fec_repeat_donor_rate", "fec_burn_rate",
            "gdelt", "wiki_pageviews_share", "wiki_edit_count"} <= set(selected)
    # youtube reports only current statistics; reddit search only reaches the
    # recent past; the two manual sources carry their own cycle baselines.
    assert not {"youtube", "reddit", "primary_turnout_ratio",
                "party_reg_net_change"} & set(selected)


def test_skip_is_honoured():
    assert "gdelt" not in backfill._backfillable(skip={"gdelt"})
