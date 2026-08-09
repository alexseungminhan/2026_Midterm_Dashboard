"""Manual ingestion: templates, row-level validation, poll aggregation,
and the two once-per-cycle Track B sources (primary turnout, party reg)."""

import csv
from datetime import date

import pytest

from election2026 import manual


def _write_csv(path, headers, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        w.writerows(rows)


def test_load_polls_happy_path(tmp_path):
    _write_csv(tmp_path / "polls.csv",
               ["race_id", "pollster", "date", "sample_size", "margin_dem"],
               [["sen-ga", "UGA", "2026-07-20", 800, 2.5]])
    polls = manual.load_polls(str(tmp_path))
    assert polls["sen-ga"][0]["margin_dem"] == 2.5
    # Legacy sheets without matchup/weight columns read as confirmed @ 1.0.
    assert polls["sen-ga"][0]["matchup"] == "confirmed"
    assert polls["sen-ga"][0]["weight"] == 1.0


def test_unknown_race_id_names_the_row(tmp_path):
    _write_csv(tmp_path / "polls.csv",
               ["race_id", "pollster", "date", "sample_size", "margin_dem"],
               [["sen-zz", "X", "2026-07-20", 800, 2.5]])
    with pytest.raises(manual.ManualDataError) as err:
        manual.load_polls(str(tmp_path))
    assert "row 2" in str(err.value) and "sen-zz" in str(err.value)


def test_malformed_date_and_margin_rejected(tmp_path):
    _write_csv(tmp_path / "polls.csv",
               ["race_id", "pollster", "date", "sample_size", "margin_dem"],
               [["sen-ga", "X", "July 20", 800, 2.5]])
    with pytest.raises(manual.ManualDataError, match="YYYY-MM-DD"):
        manual.load_polls(str(tmp_path))

    _write_csv(tmp_path / "polls.csv",
               ["race_id", "pollster", "date", "sample_size", "margin_dem"],
               [["sen-ga", "X", "2026-07-20", 800, 95.0]])
    with pytest.raises(manual.ManualDataError, match="range"):
        manual.load_polls(str(tmp_path))


def test_unknown_matchup_kind_rejected(tmp_path):
    _write_csv(tmp_path / "polls.csv",
               ["race_id", "pollster", "date", "sample_size", "margin_dem",
                "matchup"],
               [["sen-ga", "X", "2026-07-20", 800, 2.5, "maybe"]])
    with pytest.raises(manual.ManualDataError, match="matchup"):
        manual.load_polls(str(tmp_path))


def test_missing_file_means_unavailable_not_error(tmp_path):
    assert manual.load_polls(str(tmp_path)) == {}


def test_poll_aggregation_recency_and_size_weighting():
    as_of = date(2026, 7, 26)
    polls = [
        {"date": "2026-07-25", "sample_size": 800, "margin_dem": 4.0},
        {"date": "2026-05-01", "sample_size": 800, "margin_dem": -4.0},
    ]
    # The stale poll should be nearly ignored at a 14-day half-life.
    m = manual.aggregate_polls(polls, as_of=as_of, halflife_days=14.0)
    assert m > 3.5

    big = [{"date": "2026-07-25", "sample_size": 4000, "margin_dem": 4.0},
           {"date": "2026-07-25", "sample_size": 250, "margin_dem": -4.0}]
    assert manual.aggregate_polls(big, as_of=as_of) > 2.0

    assert manual.aggregate_polls([], as_of=as_of) is None


def test_matchup_weight_downweights_hypotheticals():
    """A confirmed poll must dominate an equal-and-opposite hypothetical."""
    as_of = date(2026, 7, 26)
    polls = [
        {"date": "2026-07-25", "sample_size": 800, "margin_dem": 4.0,
         "matchup": "confirmed", "weight": 1.0},
        {"date": "2026-07-25", "sample_size": 800, "margin_dem": -4.0,
         "matchup": "hypothetical", "weight": 0.35},
    ]
    m = manual.aggregate_polls(polls, as_of=as_of)
    assert m > 1.5                      # pulled toward the confirmed poll
    conf = manual.poll_confidence(polls, as_of=as_of)
    assert 0.5 < conf < 1.0             # and the mix is reported honestly


def test_ratings_validation(tmp_path):
    _write_csv(tmp_path / "ratings.csv",
               ["race_id", "rating", "as_of"],
               [["sen-ga", "Leans Blue", "2026-07-20"]])
    with pytest.raises(manual.ManualDataError, match="rating"):
        manual.load_ratings(str(tmp_path))


# ---------------------------------------------------------------------------
# Primary turnout (Track B manual, once per cycle)
# ---------------------------------------------------------------------------

_PT_HEAD = ["state", "cycle", "dem_votes", "rep_votes", "contested_dem",
            "contested_rep", "certified_date", "source_url"]


def test_primary_turnout_happy_path(tmp_path):
    _write_csv(tmp_path / "primary_turnout.csv", _PT_HEAD, [
        ["GA", 2018, 555000, 607000, "TRUE", "TRUE", "2018-06-10", ""],
        ["GA", 2022, 708000, 1188000, "TRUE", "TRUE", "2022-06-10", ""],
        ["GA", 2026, 742000, 1180000, "TRUE", "TRUE", "2026-06-16", ""],
    ])
    rows = manual.load_primary_turnout(str(tmp_path))
    assert rows["GA"][2026]["ratio"] == pytest.approx(742000 / 1180000)
    assert rows["GA"][2018]["contested_dem"] is True


def test_primary_turnout_unknown_state_names_the_row(tmp_path):
    _write_csv(tmp_path / "primary_turnout.csv", _PT_HEAD, [
        ["ZZ", 2026, 1000, 1000, "TRUE", "TRUE", "", ""]])
    with pytest.raises(manual.ManualDataError, match="row 2"):
        manual.load_primary_turnout(str(tmp_path))


def test_primary_turnout_missing_cycle_and_bad_votes(tmp_path):
    _write_csv(tmp_path / "primary_turnout.csv", _PT_HEAD, [
        ["GA", "", 1000, 1000, "TRUE", "TRUE", "", ""]])
    with pytest.raises(manual.ManualDataError, match="cycle"):
        manual.load_primary_turnout(str(tmp_path))

    _write_csv(tmp_path / "primary_turnout.csv", _PT_HEAD, [
        ["GA", 2026, "many", 1000, "TRUE", "TRUE", "", ""]])
    with pytest.raises(manual.ManualDataError, match="not a number"):
        manual.load_primary_turnout(str(tmp_path))


def test_primary_turnout_ratio_sanity_band(tmp_path):
    # A dropped digit turns 742000 into 74200 — the band catches it.
    _write_csv(tmp_path / "primary_turnout.csv", _PT_HEAD, [
        ["GA", 2026, 742000, 7420, "TRUE", "TRUE", "", ""]])
    with pytest.raises(manual.ManualDataError, match="plausible range"):
        manual.load_primary_turnout(str(tmp_path))


def test_primary_turnout_rejects_alaska_rows(tmp_path):
    """Alaska's jungle primary has no party-partitioned electorate; a typed-in
    number would be an invention, so the loader refuses the row outright."""
    _write_csv(tmp_path / "primary_turnout.csv", _PT_HEAD, [
        ["AK", 2026, 1000, 1000, "TRUE", "TRUE", "", ""]])
    with pytest.raises(manual.ManualDataError, match="jungle|party-partitioned"):
        manual.load_primary_turnout(str(tmp_path))


# ---------------------------------------------------------------------------
# Party registration (Track B manual)
# ---------------------------------------------------------------------------

_PR_HEAD = ["state", "report_date", "dem_registered", "rep_registered",
            "source_url"]


def test_party_registration_happy_path_sorted_by_date(tmp_path):
    _write_csv(tmp_path / "party_registration.csv", _PR_HEAD, [
        ["NC", "2026-07-25", 2380000, 2290000, ""],
        ["NC", "2026-07-11", 2378000, 2289000, ""],
    ])
    rows = manual.load_party_registration(str(tmp_path))
    assert [r["report_date"] for r in rows["NC"]] \
        == ["2026-07-11", "2026-07-25"]


def test_party_registration_rejects_states_without_it(tmp_path):
    """Texas has no party registration AT ALL — the loader must say so
    rather than accept a number that cannot exist."""
    _write_csv(tmp_path / "party_registration.csv", _PR_HEAD, [
        ["TX", "2026-07-25", 1, 1, ""]])
    with pytest.raises(manual.ManualDataError, match="no party registration"):
        manual.load_party_registration(str(tmp_path))


def test_party_registration_duplicate_report_date(tmp_path):
    _write_csv(tmp_path / "party_registration.csv", _PR_HEAD, [
        ["NC", "2026-07-25", 1000, 900, ""],
        ["NC", "2026-07-25", 1001, 901, ""]])
    with pytest.raises(manual.ManualDataError, match="duplicate"):
        manual.load_party_registration(str(tmp_path))
