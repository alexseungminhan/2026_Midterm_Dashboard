"""Chamber-level market reading and the four-model regrouping."""

import pytest

from election2026 import chambers, config, models, schema
from election2026.signal import VariableReading


def _seat_event(buckets):
    return {"markets": [
        {"groupItemTitle": label,
         "outcomes": '["Yes", "No"]',
         "outcomePrices": '["%s", "%s"]' % (p, 1 - p)}
        for label, p in buckets]}


# ---------------------------------------------------------------------------
# Bucket label parsing — six shapes across three markets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,low,high,mid", [
    ("190-194", 190, 194, 192.0),
    ("22–23", 22, 23, 22.5),            # EN DASH — the governor market's
    ("48", 48, 48, 48.0),
    # Open-ended buckets sit half a width past the edge (hint 10 -> 5).
    ("Below 190", None, 190, 185.0),
    ("<22", None, 22, 17.0),
    ("230+", 230, None, 235.0),
    ("57+", 57, None, 62.0),
])
def test_bucket_shapes(label, low, high, mid):
    assert chambers._parse_bucket(label, 10.0) == (low, high, mid)


def test_en_dash_and_hyphen_are_both_ranges():
    assert chambers._parse_bucket("22–23", 2)[2] == \
        chambers._parse_bucket("22-23", 2)[2]


def test_bucket_width_is_taken_from_the_closed_buckets():
    assert chambers._bucket_width(["Below 190", "190-194", "195-199",
                                   "230+"]) == 5.0
    assert chambers._bucket_width(["≤47", "48", "49", "57+"]) == 1.0


def test_seat_distribution_renormalizes_and_takes_an_expectation():
    """Polymarket's buckets sum to more than 1 — each leg carries its own
    spread — so the expectation is meaningless before renormalizing."""
    buckets, expected = chambers.read_seat_distribution(_seat_event([
        ("Below 190", 0.30), ("190-194", 0.30), ("195-199", 0.60)]))
    assert sum(b.prob for b in buckets) == pytest.approx(1.0, abs=1e-3)
    # width_hint comes from the CLOSED buckets (both are 5 wide), so
    # "Below 190" is placed at 190 - 2.5.
    assert expected == pytest.approx(
        0.25 * 187.5 + 0.25 * 192.0 + 0.50 * 197.0, abs=0.1)


def test_control_probability_is_two_party_normalized():
    assert chambers.read_control(_seat_event([
        ("Democratic Party", 0.455), ("Republican Party", 0.545)])) == \
        pytest.approx(0.455, abs=1e-3)


def test_balance_of_power_renormalizes():
    rows = chambers.read_balance_of_power(_seat_event([
        ("Democrats Sweep", 0.455), ("R Senate, D House", 0.405),
        ("Republicans Sweep", 0.125), ("D Senate, R House", 0.013)]))
    assert sum(p for _, p in rows) == pytest.approx(1.0, abs=1e-3)


def test_read_all_survives_a_chamber_with_no_control_market():
    """There is no 'which party wins the governorships' market, and that must
    not blank the seat distribution alongside it."""
    events = [{"slug": chambers.SEATS_SLUGS["governor"],
               "volume": 100, **_seat_event([("<22", 0.2), ("22–23", 0.3),
                                             ("24–25", 0.5)])}]
    readings, balance = chambers.read_all(events)
    gov = readings["governor"]
    assert gov.prob_dem_control is None
    assert gov.expected_rep_seats is not None
    assert gov.expected_dem_seats == pytest.approx(50 - gov.expected_rep_seats)
    assert balance == []


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def _readings(**zs):
    """One VariableReading per configured variable; named ones get a z."""
    out = []
    for var in config.TRACK_B["weights"]:
        z = zs.get(var)
        out.append(VariableReading(
            variable=var, z=z,
            directional=var not in config.TRACK_B["attention_only"],
            availability="available" if z is not None else "missing",
            reason=None if z is not None else "테스트: 값 없음"))
    return out


def test_every_variable_lands_in_exactly_one_model():
    assigned = [v for spec in models.MODELS.values() for v in spec["vars"]]
    assert sorted(assigned) == sorted(config.TRACK_B["weights"])
    assert len(assigned) == len(set(assigned))


def test_a_directional_model_reports_a_lean_and_a_shift():
    m = {x.key: x for x in models.build(_readings(
        econ_claims=-1.0, econ_coincident=-1.0, econ_unemployment=-1.0))}
    econ = m["economy"]
    assert econ.lean == "R" and econ.level is None
    assert econ.z == pytest.approx(-1.0)
    assert econ.shift_pp == pytest.approx(-1.0 * config.PP_PER_SIGMA)
    assert econ.n_available == 3


def test_weights_renormalize_over_the_available_variables_only():
    """One live variable out of five reports ITS value, not a value dragged
    four-fifths of the way to zero."""
    money = {x.key: x for x in models.build(
        _readings(fec_in_state_share=2.0))}["money"]
    assert money.z == pytest.approx(2.0)
    assert (money.n_available, money.n_total) == (1, 5)


def test_the_attention_model_never_leans():
    att = {x.key: x for x in models.build(
        _readings(wiki_pageviews_share=2.0, wiki_edit_count=2.0))}["attention"]
    assert att.lean is None            # a pageview cannot pick a party
    assert att.level == "high"
    assert att.shift_pp is None        # and cannot shift a probability


def test_a_near_zero_reading_is_neutral_not_a_lean():
    econ = {x.key: x for x in models.build(
        _readings(econ_claims=0.05))}["economy"]
    assert econ.lean == "N" and econ.strength == 0


def test_structural_absence_is_distinguished_from_a_failed_fetch():
    readings = _readings()
    for r in readings:
        if r.variable.startswith("fec_"):
            r.availability, r.reason = "structural", "주지사 선거는 FEC 관할이 아니다"
    by_key = {x.key: x for x in models.build(readings)}
    assert by_key["money"].unavailable == "structural"
    assert by_key["economy"].unavailable == "missing"


def test_an_empty_model_always_says_why():
    for m in models.build(_readings()):
        if m.n_available == 0:
            assert m.unavailable in schema.AVAILABILITY and m.reason


def test_strength_buckets_are_monotone():
    assert [models.strength_of(z) for z in (0.0, 0.5, 1.0, 2.0)] == [0, 1, 2, 3]
    assert models.strength_of(-2.0) == models.strength_of(2.0)


def test_the_structural_fit_stays_on_battlegrounds():
    """The FEC small-dollar confounder is controlled by fitting the
    cross-section over battlegrounds only. Since the board became
    volume-ranked, that guarantee rests on STRUCTURAL_COVARIATES' contents —
    every race with covariates must be one we deliberately curated, never an
    incidental safe seat the market happened to trade."""
    curated = set(config.RACES) | set(config.INACTIVE_RACES)
    stray = set(config.STRUCTURAL_COVARIATES) - curated
    assert not stray, (
        "these races carry structural covariates but are not in races.json, "
        "so they would enter the cross-sectional fit unvetted: %s" % sorted(stray))
