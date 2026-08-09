"""The 2026-07-30 Track B variable swap: FEC five-way expansion, primary
turnout, party registration, Wikipedia attention, Reddit — normalization
paths, structural-null vs missing-run distinction, and title resolution.

Network-free: HTTP and manual loaders are faked; what is under test is each
adapter's computation and its unavailability semantics.
"""

import json
from datetime import date

import pytest

from election2026 import config
from election2026.track_b import adapters as A
from election2026.track_b.signals import (attention_level,
                                          attention_z_across,
                                          compute_readings,
                                          oriented_z, side_contrast)


# ---------------------------------------------------------------------------
# FEC: in-state share / unique donors / repeat rate against a small fixture
# ---------------------------------------------------------------------------

def _receipt(name, state, zip_, amount, ytd, conduit=True):
    return {"contributor_name": name, "contributor_state": state,
            "contributor_zip": zip_, "contribution_receipt_amount": amount,
            "contributor_aggregate_ytd": ytd,
            "contributor_id": "C00401224" if conduit else None,
            "receipt_type_desc": ("EARMARKED CONTRIBUTION" if conduit
                                  else "RECEIPT")}


FIXTURE = [
    _receipt("KIM, A", "GA", "30301", 25.0, 25.0),          # in-state, first
    _receipt("LEE, B", "GA", "30302", 10.0, 60.0),          # in-state, repeat
    _receipt("PARK, C", "CA", "90001", 25.0, 25.0),         # out-of-state
    _receipt("CHOI, D", "NY", "10001", 50.0, 150.0),        # out, repeat
]


class FixtureFec(A.FecBase):
    name = "fec_test"

    def __init__(self, rows, count=40, burn=1.5):
        super().__init__()
        self._rows_fixture = rows
        self._count_fixture = count
        self._burn_fixture = burn

    def _receipts(self, committee_id, start, end):
        return self._count_fixture, list(self._rows_fixture)

    def _burn_rate(self, committee_id):
        return self._burn_fixture


def test_fec_in_state_share_and_repeat_rate_from_fixture():
    fec = FixtureFec(FIXTURE)
    block = fec._party_metrics(["C1"], {"state": "GA"},
                               date(2026, 4, 1), date(2026, 4, 7))
    assert block["small_dollar_count"] == 40.0          # exact, from count
    assert block["in_state_share"] == pytest.approx(2 / 4)
    assert block["repeat_donor_rate"] == pytest.approx(2 / 4)
    # 4 distinct donors in a 4-row sample -> estimated uniques = count * 1.0
    assert block["unique_donors"] == pytest.approx(40.0)
    assert block["burn_rate"] == pytest.approx(1.5)
    assert block["conduit_share"] == 1.0


def test_fec_duplicate_donor_lowers_unique_estimate():
    rows = FIXTURE + [_receipt("KIM, A", "GA", "30301", 5.0, 30.0)]
    fec = FixtureFec(rows, count=50)
    block = fec._party_metrics(["C1"], {"state": "GA"},
                               date(2026, 4, 1), date(2026, 4, 7))
    assert block["unique_donors"] == pytest.approx(50 * 4 / 5)


def test_fec_conduit_rows_preferred_when_share_is_high():
    """One non-conduit outlier among conduit rows must not enter the ratios
    when the conduit share clears the threshold."""
    rows = FIXTURE + [_receipt("OUT, X", "TX", "77001", 5.0, 5.0,
                               conduit=False)]
    fec = FixtureFec(rows)
    block = fec._party_metrics(["C1"], {"state": "GA"},
                               date(2026, 4, 1), date(2026, 4, 7))
    assert block["conduit_share"] == pytest.approx(4 / 5)
    assert block["n_sampled"] == 4                      # conduit rows only
    assert block["in_state_share"] == pytest.approx(2 / 4)


def test_fec_falls_back_to_all_rows_when_conduit_share_is_low():
    rows = [_receipt("A, A", "GA", "1", 5, 5, conduit=False),
            _receipt("B, B", "CA", "2", 5, 5, conduit=False),
            _receipt("C, C", "GA", "3", 5, 5, conduit=False),
            _receipt("D, D", "CA", "4", 5, 5, conduit=False),
            _receipt("E, E", "GA", "5", 5, 5, conduit=True)]
    fec = FixtureFec(rows)
    block = fec._party_metrics(["C1"], {"state": "GA"},
                               date(2026, 4, 1), date(2026, 4, 7))
    assert block["conduit_share"] == pytest.approx(1 / 5)   # < min_share 0.25
    assert block["n_sampled"] == 5                      # fell back to all
    assert block["in_state_share"] == pytest.approx(3 / 5)


def test_fec_governor_races_are_structurally_unavailable():
    fec = FixtureFec(FIXTURE)
    payload = fec._pull_live("gov-ga", {"chamber": "governor", "state": "GA"})
    assert A.is_unavailable(payload)
    assert payload["unavailable"] == "structural"
    assert "jurisdiction" in payload["reason"]


def test_repeat_donor_detection_uses_the_fec_ytd_aggregate():
    assert A._is_repeat_donor(_receipt("X", "GA", "1", 25.0, 75.0))
    assert not A._is_repeat_donor(_receipt("X", "GA", "1", 25.0, 25.0))
    assert not A._is_repeat_donor({"contributor_name": "X"})   # missing data


# ---------------------------------------------------------------------------
# Primary turnout: AK jungle-primary null, uncontested null, pending MI
# ---------------------------------------------------------------------------

def _turnout_adapter(monkeypatch, table):
    adapter = A.PrimaryTurnoutAdapter()
    monkeypatch.setattr(adapter, "_rows", lambda: table)
    return adapter


_GA_TABLE = {"GA": {
    2018: {"ratio": 0.91, "contested_dem": True, "contested_rep": True},
    2022: {"ratio": 0.60, "contested_dem": True, "contested_rep": True},
    2026: {"ratio": 0.63, "contested_dem": True, "contested_rep": True,
           "dem_votes": 1, "rep_votes": 1},
}}


def test_primary_turnout_emits_log_ratio_vs_prior_cycles(monkeypatch):
    adapter = _turnout_adapter(monkeypatch, _GA_TABLE)
    payload = adapter._pull_live("sen-ga", {"state": "GA"})
    import math
    assert payload["oriented"]["value"] == pytest.approx(math.log(0.63))
    assert payload["oriented"]["baseline"] == pytest.approx(
        [math.log(0.91), math.log(0.60)])
    assert payload["oriented"]["min_obs"] == \
        config.TRACK_B["baseline_min_obs_overrides"]["primary_turnout_ratio"]


def test_primary_turnout_alaska_is_structural_with_a_reason(monkeypatch):
    adapter = _turnout_adapter(monkeypatch, {})
    payload = adapter._pull_live("sen-ak", {"state": "AK"})
    assert payload["unavailable"] == "structural"
    assert "jungle" in payload["reason"] or "ranked-choice" in payload["reason"]


def test_primary_turnout_uncontested_side_is_structural(monkeypatch):
    table = {"GA": dict(_GA_TABLE["GA"])}
    table["GA"][2026] = dict(table["GA"][2026], contested_rep=False)
    adapter = _turnout_adapter(monkeypatch, table)
    payload = adapter._pull_live("sen-ga", {"state": "GA"})
    assert payload["unavailable"] == "structural"
    assert "uncontested" in payload["reason"]


def test_primary_turnout_michigan_pending_until_primary_day(monkeypatch):
    adapter = _turnout_adapter(monkeypatch, {})
    monkeypatch.setattr(A, "date", _FakeDate)
    payload = adapter._pull_live("sen-mi", {"state": "MI"})
    assert payload["unavailable"] == "pending"
    assert "2026-08-04" in payload["reason"]


class _FakeDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 30)


# ---------------------------------------------------------------------------
# Party registration: structural-null vs missing-run — never the same
# ---------------------------------------------------------------------------

def test_party_reg_structural_for_states_without_registration(monkeypatch):
    adapter = A.PartyRegistrationAdapter()
    monkeypatch.setattr(adapter, "_rows", lambda: {})
    payload = adapter._pull_live("sen-tx", {"state": "TX"})
    assert payload["unavailable"] == "structural"
    assert "TX" in payload["reason"]


def test_party_reg_missing_when_data_is_thin_not_structural(monkeypatch):
    adapter = A.PartyRegistrationAdapter()
    monkeypatch.setattr(adapter, "_rows", lambda: {"NC": [
        {"report_date": "2026-07-01", "dem_registered": 10, "rep_registered": 9},
    ]})
    assert adapter._pull_live("sen-nc", {"state": "NC"}) is None   # missing


def test_party_reg_net_change_per_day_rates(monkeypatch):
    rows = [
        {"report_date": "2026-06-01", "dem_registered": 1000, "rep_registered": 900},
        {"report_date": "2026-06-11", "dem_registered": 1030, "rep_registered": 910},
        {"report_date": "2026-06-21", "dem_registered": 1050, "rep_registered": 930},
        {"report_date": "2026-07-01", "dem_registered": 1100, "rep_registered": 940},
    ]
    adapter = A.PartyRegistrationAdapter()
    monkeypatch.setattr(adapter, "_rows", lambda: {"NC": rows})
    payload = adapter._pull_live("sen-nc", {"state": "NC"})
    # nets: 100, 120, 120, 160 -> per-day rates over 10-day gaps: 2, 0, 4
    assert payload["oriented"]["baseline"] == pytest.approx([2.0, 0.0])
    assert payload["oriented"]["value"] == pytest.approx(4.0)


def test_structural_and_missing_render_differently_in_readings():
    raw = {var: None for var in config.TRACK_B["weights"]}
    raw["party_reg_net_change"] = A.unavailable("structural", "no party reg")
    raw["primary_turnout_ratio"] = A.unavailable("pending", "primary is 8/4")
    readings = {r.variable: r for r in compute_readings("sen-mi", raw)}
    assert readings["party_reg_net_change"].availability == "structural"
    assert readings["primary_turnout_ratio"].availability == "pending"
    # gdelt/reddit/youtube were removed on 2026-08-09 — all three were
    # blocked at the source and carried weight 0. wiki stands in for "the
    # source returned nothing this run".
    assert readings["wiki_edit_count"].availability == "missing"
    for r in readings.values():
        assert r.z is None


# ---------------------------------------------------------------------------
# Wikipedia: title resolution (redirect, unresolvable), share reduction
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


def test_wiki_title_resolution_follows_redirects(monkeypatch, tmp_path):
    _isolate_cache(monkeypatch, tmp_path)

    def fake_get(url, params=None, **kwargs):
        if params and params.get("titles") == "Jon Osoff":   # misspelled
            return _Resp({"query": {"redirects": [
                {"from": "Jon Osoff", "to": "Jon Ossoff"}],
                "pages": [{"title": "Jon Ossoff"}]}})
        return _Resp({"query": {"pages": [{"missing": True}]}})

    monkeypatch.setattr(A.cache, "http_get", fake_get)
    adapter = A.WikiPageviewsShareAdapter()
    assert adapter._resolve("Jon Osoff", {"state": "GA"}) == "Jon Ossoff"


def test_wiki_unresolvable_candidate_fails_loudly_not_zero(monkeypatch,
                                                           tmp_path, capsys):
    _isolate_cache(monkeypatch, tmp_path)

    def fake_get(url, params=None, **kwargs):
        if params and "titles" in params:
            return _Resp({"query": {"pages": [{"missing": True}]}})
        return _Resp({"query": {"search": []}})           # search finds nothing

    monkeypatch.setattr(A.cache, "http_get", fake_get)
    monkeypatch.setattr(A, "candidate_roster",
                        lambda rid, meta: {"dem": ["Nobody Realname"],
                                           "rep": ["Someone Else"]})
    adapter = A.WikiPageviewsShareAdapter()
    # Nobody on either side resolves, so neither side has a denominator.
    assert adapter._titles("sen-xx", {"state": "GA"}) is None
    out = capsys.readouterr().out
    assert "no Wikipedia article" in out
    assert "Nobody Realname" in out


def test_wiki_unresolvable_minor_candidate_does_not_blank_the_race(
        monkeypatch, tmp_path):
    """One unknown name must not cost the whole contest.

    Ohio Senate was dropped on 2026-08-08 because 'Frederick J Ode' has no
    article — while Sherrod Brown and Jon Husted both resolve. 28 of 40 races
    were blanked this way. The unresolved name is SKIPPED, which is not the
    same as scoring it zero: zero would say nobody looked them up and inflate
    the other side.
    """
    _isolate_cache(monkeypatch, tmp_path)
    known = {"Sherrod Brown": "Sherrod Brown", "Jon Husted": "Jon Husted"}
    monkeypatch.setattr(A.WikiPageviewsShareAdapter, "_resolve",
                        lambda self, name, meta: known.get(name))
    monkeypatch.setattr(A, "candidate_roster",
                        lambda rid, meta: {
                            "dem": ["Sherrod Brown", "Frederick J Ode"],
                            "rep": ["Jon Husted"]})
    titles = A.WikiPageviewsShareAdapter()._titles("sen-oh", {"state": "OH"})
    assert titles == {"dem": ["Sherrod Brown"], "rep": ["Jon Husted"]}


def test_wiki_hand_override_wins(monkeypatch):
    monkeypatch.setitem(config.TRACK_B, "wiki_titles",
                        {"Jon Ossoff": "Jon Ossoff (politician)"})
    adapter = A.WikiPageviewsShareAdapter()
    assert adapter._resolve("Jon Ossoff", {"state": "GA"}) \
        == "Jon Ossoff (politician)"


def test_wiki_share_reduction_is_proportional():
    adapter = A.WikiPageviewsShareAdapter()
    series = {"dem": {date(2026, 7, 20): (300.0, 2.0)},
              "rep": {date(2026, 7, 20): (100.0, 1.0)}}
    payload = adapter._reduce(series, date(2026, 7, 14), date(2026, 7, 20))
    assert payload["dem"]["value"] == pytest.approx(0.75)
    assert payload["rep"]["value"] == pytest.approx(0.25)
    # The recorded scalar is CONCENTRATION, not direction.
    assert payload["total"]["value"] == pytest.approx(0.5)
    # No longer attention-only: the wiki variables became directional on
    # 2026-08-09, comparing the two candidates' pageviews to each other.
    assert config.TRACK_B["attention_only"] == []


def test_wiki_zero_views_yield_no_payload():
    adapter = A.WikiPageviewsShareAdapter()
    series = {"dem": {date(2026, 7, 20): (0.0, 0.0)},
              "rep": {date(2026, 7, 20): (0.0, 0.0)}}
    assert adapter._reduce(series, date(2026, 7, 14), date(2026, 7, 20)) is None


# ---------------------------------------------------------------------------
# Reddit: sentiment lexicon + throttle bookkeeping (no live API)
# ---------------------------------------------------------------------------

def test_oriented_payload_skips_differencing_and_store():
    payload = {"oriented": {"value": 4.0, "baseline": [1.0, 2.0, 3.0],
                            "min_obs": 3}}
    z = oriented_z(payload, var="primary_turnout_ratio")
    assert z is not None and z > 0


def test_attention_total_block_is_used_directly():
    """A pageview SHARE sums to 1, so the adapter ships a separate total."""
    payload = {"dem": {"value": 0.9}, "rep": {"value": 0.1},
               "total": {"value": 0.8}}
    assert attention_level(payload) == 0.8


def test_attention_is_graded_against_the_rest_of_the_board():
    """Cross-sectional, so no accumulated history is needed."""
    others = [0.1, 0.2, 0.15, 0.25, 0.2]
    z = attention_z_across(0.8, others, var="wiki_pageviews_share")
    assert z is not None and z > 0
    # A race sitting mid-pack is not "busy".
    mid = attention_z_across(0.18, others, var="wiki_pageviews_share")
    assert mid is not None and abs(mid) < 1.0


def test_side_contrast_needs_both_sides_present_and_positive():
    """A zero side is a candidate with no filing, not maximal dominance.

    Delaware Senate reported a Republican in-state share of exactly 0.0;
    letting that saturate to +1.0 would manufacture the strongest possible
    Democratic reading out of missing data. 17 of 83 races on 2026-08-08.
    """
    assert side_contrast({"dem": {"value": 1086.0},
                          "rep": {"value": 397.0}}) == pytest.approx(0.4646,
                                                                     abs=1e-4)
    assert side_contrast({"dem": {"value": 0.735},
                          "rep": {"value": 0.0}}) is None
    assert side_contrast({"dem": {"value": 5.0}}) is None


def test_unavailable_payloads_never_reach_a_store_or_a_z():
    payload = A.unavailable("structural", "because")
    assert oriented_z(payload) is None
    assert attention_level(payload) is None

def _isolate_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(A.cache, "RAW_DIR", str(tmp_path))


# ---------------------------------------------------------------------------
# State economics (FRED) — added 2026-08-02
# ---------------------------------------------------------------------------

class _FakeEcon:
    """A FredBase with a hand-built series, so the test needs no network."""


def _econ(cls, series, meta):
    a = cls()
    a._series = lambda m: series
    return a._pull_live("x", meta)


def test_econ_credits_improvement_to_the_party_in_power():
    from datetime import date as _d
    from election2026.track_b.adapters import EconUnemploymentAdapter as U
    # Unemployment falling steadily = improvement.
    series = {_d(2024, m // 12 + 1, m % 12 + 1): 6.0 for m in range(0)}
    series = {}
    val = 8.0
    for i in range(40):
        y, m = 2023 + i // 12, i % 12 + 1
        series[_d(y, m, 1)] = val
        val -= 0.1
    gov_r = {"chamber": "governor", "state": "OH", "seat_party": "R"}
    gov_d = {"chamber": "governor", "state": "OH", "seat_party": "D"}
    r = _econ(U, series, gov_r)["oriented"]["value"]
    d = _econ(U, series, gov_d)["oriented"]["value"]
    # Same economy, opposite sign depending on who owns the seat.
    assert r < 0 < d
    assert r == -d


def test_econ_federal_races_credit_the_president_not_the_seat():
    from datetime import date as _d
    from election2026 import config
    from election2026.track_b.adapters import EconUnemploymentAdapter as U
    series = {}
    val = 8.0
    for i in range(40):
        series[_d(2023 + i // 12, i % 12 + 1, 1)] = val
        val -= 0.1
    # A Democratic-held Senate seat still credits the White House party.
    sen = {"chamber": "senate", "state": "OH", "seat_party": "D"}
    gov = {"chamber": "governor", "state": "OH", "seat_party": "D"}
    assert config.PRESIDENT_PARTY == "R"
    assert _econ(U, series, sen)["oriented"]["value"] < 0
    assert _econ(U, series, gov)["oriented"]["value"] > 0


def test_econ_needs_enough_history_before_it_speaks():
    from datetime import date as _d
    from election2026.track_b.adapters import EconUnemploymentAdapter as U
    short = {_d(2026, m, 1): 5.0 for m in range(1, 6)}
    meta = {"chamber": "governor", "state": "OH", "seat_party": "R"}
    assert _econ(U, short, meta) is None


def test_econ_claims_removes_seasonality_year_over_year():
    """Raw state claims spike every July when auto plants retool. A summer
    reading must not register as an economic collapse."""
    from datetime import date as _d, timedelta
    from election2026.track_b.adapters import EconClaimsAdapter as C
    series, week = {}, _d(2022, 1, 2)
    while week < _d(2026, 8, 1):
        # Flat trend, +60% every July: pure seasonality, no real change.
        seasonal = 1.6 if week.month == 7 else 1.0
        series[week] = 4000 * seasonal
        week += timedelta(days=7)
    meta = {"chamber": "governor", "state": "OH", "seat_party": "R"}
    a = C()
    a._series = lambda m: series
    out = a._pull_live("x", meta)
    # Same July as last July => no year-over-year change at all.
    assert abs(out["oriented"]["value"]) < 1e-6


def test_econ_claims_reads_a_real_deterioration():
    from datetime import date as _d, timedelta
    from election2026.track_b.adapters import EconClaimsAdapter as C
    series, week = {}, _d(2022, 1, 2)
    while week < _d(2026, 8, 1):
        base = 4000 * (1.5 if week >= _d(2026, 1, 1) else 1.0)
        series[week] = base
        week += timedelta(days=7)
    a = C()
    a._series = lambda m: series
    r = a._pull_live("x", {"chamber": "governor", "state": "OH",
                           "seat_party": "R"})["oriented"]["value"]
    d = a._pull_live("x", {"chamber": "governor", "state": "OH",
                           "seat_party": "D"})["oriented"]["value"]
    # Claims up 50% => bad for whoever holds the office, good for the
    # challenger's party.
    assert r > 0 > d


def test_fec_name_puts_the_generational_suffix_behind_the_surname():
    """'KEAN, THOMAS H JR' must not become 'Thomas H Jr Kean' — the FEC files
    the suffix in the given-name field, and naive reassembly produces a
    string no Wikipedia title or search index matches."""
    from election2026.track_b.adapters import _humanize_fec_name as h
    assert h("KEAN, THOMAS H JR") == "Thomas H Kean Jr"
    assert h("SMITH, JOHN III") == "John Smith III"     # roman stays upper
    assert h("OSSOFF, JON") == "Jon Ossoff"
    assert h("PAUTSCH, DAVID ALFRED MR.") == "David Alfred Pautsch"


# ---------------------------------------------------------------------------
# Primary turnout, relative form (added 2026-08-02)
# ---------------------------------------------------------------------------

def _write_change(tmp_path, rows):
    from openpyxl import Workbook
    from election2026 import manual
    wb = Workbook(); ws = wb.active
    ws.append(manual.TEMPLATES["primary_turnout_change_template.xlsx"])
    for st, d, r in rows:
        ws.append([st, 2026, 2022, d, r, "gov", "http://x", ""])
    p = tmp_path / "primary_turnout_change.xlsx"
    wb.save(str(p))
    return str(tmp_path)


def test_relative_turnout_equals_the_ratio_shift(tmp_path):
    """log(D% / R%) is algebraically the move in the D/R turnout ratio, which
    is the same deviation the vote-count path builds from levels."""
    import math
    from election2026 import manual
    d = _write_change(tmp_path, [("IA", 122, 108), ("OH", 154, 76),
                                 ("GA", 148, 78), ("NV", 107, 83),
                                 ("TX", 216, 111), ("PA", 87, 48)])
    rows = manual.load_primary_turnout_change(directory=d)
    assert rows["IA"]["dem_pct"] == 122
    # Iowa SOS certified: D 194,710/158,745 = 1.227; R 217,075/198,858 = 1.092
    assert abs(math.log(122 / 108) - math.log(1.227 / 1.092)) < 0.02


def test_relative_turnout_compares_the_two_parties_in_the_state(tmp_path):
    """The two parties' growth, against each other, in this state only.

    It replaced a cross-state z-score on 2026-08-09. That version answered
    "unusual among the states reporting this cycle", which is a real question
    but not the one the card asks — and it meant Maine's reading moved when
    Ohio's numbers arrived.
    """
    from election2026 import manual, track_b
    from election2026.track_b.signals import side_contrast
    d = _write_change(tmp_path, [("IA", 122, 108), ("OH", 154, 76),
                                 ("GA", 148, 78), ("NV", 107, 83),
                                 ("TX", 216, 111), ("PA", 87, 48)])
    a = track_b.ADAPTERS["primary_turnout_ratio"]()
    a._changes = manual.load_primary_turnout_change(directory=d)
    a._loaded = {}          # no vote-count history, so the relative path runs
    payload = a._pull_live("gov-oh", {"state": "OH", "chamber": "governor"})
    # Ohio's D side grew to 154% of 2022 while its R side fell to 76%.
    assert payload["dem"]["value"] == 154 and payload["rep"]["value"] == 76
    assert side_contrast(payload) == pytest.approx((154 - 76) / (154 + 76))
    # No other state appears anywhere in the reading.
    assert "baseline" not in payload and "oriented" not in payload


def test_relative_turnout_rejects_an_implausible_percentage(tmp_path):
    from election2026 import manual
    d = _write_change(tmp_path, [("IA", 122, 0.0)])
    try:
        manual.load_primary_turnout_change(directory=d)
    except manual.ManualDataError as exc:
        assert "rep_pct_of_prior" in str(exc)
    else:
        raise AssertionError("a 0% turnout ratio must be refused")


def test_two_sided_variables_are_not_residualized():
    """Residualizing the sides blanks the money model.

    `residualize` replaces a level with a SIGNED structural residual. That was
    correct while each side was z-scored against its own history, but the
    snapshot contrast (d-r)/(d+r) needs levels: residuals can both be
    negative, and `side_contrast` rejects non-positive values. Maine, Texas,
    Michigan and Georgia all lost their money reading this way on the first
    snapshot run — every one of them has healthy FEC data.
    """
    from election2026.track_b.signals import residualize
    raw = {
        "sen-me": {"fec_small_dollar_count": {"dem": {"value": 2122.0},
                                              "rep": {"value": 399.0}}},
        "sen-tx": {"fec_small_dollar_count": {"dem": {"value": 5470.0},
                                              "rep": {"value": 799.0}}},
        "sen-ga": {"fec_small_dollar_count": {"dem": {"value": 10195.0},
                                              "rep": {"value": 728.0}}},
    }
    residualize(raw)
    for rid, payload in raw.items():
        block = payload["fec_small_dollar_count"]
        assert block["dem"]["value"] > 0 and block["rep"]["value"] > 0, rid
        assert side_contrast(block) is not None, rid


def test_disambiguation_pages_are_not_people(monkeypatch, tmp_path):
    """"Susan Collins (disambiguation)" is not Susan Collins.

    It resolves, so nothing errors, and it carries almost no traffic — Maine's
    Republican side scored ~0 against the real article's 15,914 weekly views,
    putting the Democratic pageview share at 99.8% in a 67.8% race.
    """
    _isolate_cache(monkeypatch, tmp_path)
    assert A.WikiPageviewsShareAdapter._NOT_A_PERSON.search(
        "Susan Collins (disambiguation)")
    assert not A.WikiPageviewsShareAdapter._NOT_A_PERSON.search("Susan Collins")
