"""Market parsing — the layer the whole v3 board rests on.

Every fixture here is a shape observed on the live gamma API on 2026-08-08,
including the two that silently broke the first implementation: Georgia's
trailing space and Alaska's unlabeled jungle-primary field.
"""

import pytest

from election2026 import board


def _event(title, legs, slug="x", volume=1000.0, volume1wk=10.0):
    """legs: [(groupItemTitle, yes_price_or_None)]"""
    return {
        "title": title, "slug": slug, "volume": volume,
        "volume1wk": volume1wk, "liquidity": 5.0,
        "markets": [
            {"groupItemTitle": name,
             "outcomes": '["Yes", "No"]',
             "outcomePrices": (None if price is None
                               else '["%s", "%s"]' % (price, 1 - price))}
            for name, price in legs],
    }


# ---------------------------------------------------------------------------
# Title parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,chamber,state,district,race_id", [
    ("Texas Senate Election Winner", "senate", "TX", None, "sen-tx"),
    ("New Hampshire Senate Election Winner", "senate", "NH", None, "sen-nh"),
    ("Alaska Governor Election Winner", "governor", "AK", None, "gov-ak"),
    ("CA-28 House Election Winner", "house", "CA", "28", "ho-ca28"),
    ("NJ-07 House Election Winner", "house", "NJ", "07", "ho-nj7"),
    ("ND-AL House Election Winner", "house", "ND", "AL", "ho-ndal"),
])
def test_race_titles_parse(title, chamber, state, district, race_id):
    r = board.parse_event(_event(title, [("Democratic Party", 0.5),
                                         ("Republican Party", 0.5)]))
    assert (r.chamber, r.state, r.district, r.race_id) == \
        (chamber, state, district, race_id)


def test_house_race_id_matches_the_curated_convention():
    """ho-nj7, not ho-nj07 — reference lookups key on races.json's ids."""
    from election2026 import config
    r = board.parse_event(_event("NJ-07 House Election Winner",
                                 [("Democratic Party", 0.5),
                                  ("Republican Party", 0.5)]))
    assert r.race_id in config.RACES or r.race_id in config.INACTIVE_RACES


@pytest.mark.parametrize("title", [
    "Balance of Power: 2026 Midterms",
    "Which party will win the House in 2026?",
    "Republican Senate seats after the 2026 midterm elections?",
    "ACA credits extended & House Winner 2026?",
    "Which states will use new congressional maps in the midterms?",
    "New Virginia congressional map used in the midterms?",
    "Next Senate Majority Leader?",
])
def test_non_race_markets_are_rejected(title):
    assert board.parse_event(_event(title, [("Yes", 0.5)])) is None


# ---------------------------------------------------------------------------
# Two-party normalization
# ---------------------------------------------------------------------------

def test_both_legs_are_read_and_renormalized():
    """Texas quoted D 0.495 / R 0.515 — the legs do not sum to 1."""
    r = board.parse_event(_event("Texas Senate Election Winner", [
        ("James Talarico (D)", 0.495), ("Ken Paxton (R)", 0.515),
        ("Person A", None)]))
    assert r.prob_dem == pytest.approx(0.495 / 1.010, abs=1e-4)
    assert r.prob_dem != 0.495          # reading the D leg alone is the bug
    assert r.candidates == {"D": "James Talarico (D)", "R": "Ken Paxton (R)"}


def test_trailing_space_after_the_party_marker():
    """Georgia ships 'Jon Ossoff (D) '. An unstripped endswith() drops it,
    leaving the race with no Democratic leg and a null probability."""
    r = board.parse_event(_event("Georgia Senate Election Winner", [
        ("Jon Ossoff (D) ", 0.925), ("Mike Collins (R)", 0.082)]))
    assert r.prob_dem == pytest.approx(0.925 / 1.007, abs=1e-4)


def test_legs_are_summed_per_party_not_first_match():
    """Alaska's governor field lists many candidates per party. The leading
    Republican's price is not the Republican probability."""
    r = board.parse_event(_event("Alaska Governor Election Winner", [
        ("Bernadette Wilson", 0.335), ("Treg Taylor", 0.128),
        ("Tom Begich", 0.290), ("Jonathan Kreiss-Tomkins", 0.101)]))
    dem, rep = 0.290 + 0.101, 0.335 + 0.128
    assert r.prob_dem == pytest.approx(dem / (dem + rep), abs=1e-4)
    # The NAME shown is still the leading candidate of each party.
    assert r.candidates == {"D": "Tom Begich", "R": "Bernadette Wilson"}


def test_independent_mass_is_reported_not_swallowed():
    """Nebraska: Ricketts 0.745 vs an independent at 0.265, no Democratic leg
    at all. v2 hardcoded an exclusion for this race; v3 must detect it."""
    r = board.parse_event(_event("Nebraska Senate Election Winner", [
        ("Pete Ricketts (R)", 0.745), ("Independent", 0.265)]))
    assert r.unmapped_mass == pytest.approx(0.265 / 1.010, abs=1e-3)
    assert r.unmapped_mass >= board.UNMAPPED_WARN
    assert r.two_party_trustworthy is False
    # And no fabricated 0% for the absent Democrat.
    assert r.prob_dem is None


def test_three_way_race_with_both_parties_still_quotes_but_warns():
    """Montana: D 0.007 / R 0.830 / independent 0.170. Both major parties are
    priced, so a two-party ratio exists — but 17% of the market sits outside
    it, so the number must not be presented as clean."""
    r = board.parse_event(_event("Montana Senate Election Winner", [
        ("Democrat", 0.007), ("Republican", 0.830), ("Independent", 0.170)]))
    assert r.prob_dem == pytest.approx(0.007 / 0.837, abs=1e-4)
    assert r.unmapped_mass >= board.UNMAPPED_WARN
    assert r.two_party_trustworthy is False


def test_a_safe_seat_is_not_mistaken_for_a_missing_leg():
    """CA-28 quotes D 0.9695 / R 0.0125. A tiny leg is still a leg."""
    r = board.parse_event(_event("CA-28 House Election Winner", [
        ("Democratic Party", 0.9695), ("Republican Party", 0.0125)]))
    assert r.prob_dem == pytest.approx(0.9695 / 0.982, abs=1e-4)
    assert r.two_party_trustworthy is True


def test_a_clean_two_party_race_is_trustworthy():
    r = board.parse_event(_event("Michigan Senate Election Winner", [
        ("Abdul El-Sayed (D)", 0.60), ("Mike Rogers (R)", 0.41)]))
    assert r.unmapped_mass == 0.0 and r.two_party_trustworthy is True


def test_one_sided_quote_yields_no_probability():
    r = board.parse_event(_event("Ohio Senate Election Winner",
                                 [("Some Republican (R)", 0.9)]))
    assert r.prob_dem is None and r.two_party_trustworthy is False


def test_unmapped_candidates_resolve_through_the_party_file():
    """Alaska's Senate legs carry no party marker; the reference file supplies
    it, and the race stops being a null."""
    assert board.CANDIDATE_PARTIES.get("mary peltola") == "D"
    r = board.parse_event(_event("Alaska Senate Election Winner", [
        ("Dan Sullivan", 0.485), ("Mary Peltola", 0.525),
        ("Ann Diener", 0.001)]))
    assert r.prob_dem == pytest.approx(0.525 / 1.010, abs=1e-3)
    assert r.two_party_trustworthy is True


# ---------------------------------------------------------------------------
# Board assembly
# ---------------------------------------------------------------------------

def test_duplicate_seats_keep_the_deeper_market():
    legs = [("Democratic Party", 0.5), ("Republican Party", 0.5)]
    races = board.build([
        _event("Iowa Senate Election Winner", legs, slug="thin", volume=10),
        _event("Iowa Senate Election Winner", legs, slug="deep", volume=999),
    ])
    assert len(races) == 1 and races[0].slug == "deep"


def test_ranking_uses_the_requested_metric_and_breaks_ties_on_volume():
    legs = [("Democratic Party", 0.5), ("Republican Party", 0.5)]
    rows = board.build([
        _event("Texas Senate Election Winner", legs, "a", volume=100, volume1wk=5),
        _event("Iowa Senate Election Winner", legs, "b", volume=900, volume1wk=5),
        _event("Ohio Senate Election Winner", legs, "c", volume=1, volume1wk=50),
    ])
    by_day = [r.race_id for r in board.rank(rows, "volume1wk")["senate"]]
    assert by_day == ["sen-oh", "sen-ia", "sen-tx"]   # tie broken by volume
    by_total = [r.race_id for r in board.rank(rows, "volume")["senate"]]
    assert by_total == ["sen-ia", "sen-tx", "sen-oh"]


def test_ranking_is_per_chamber_and_capped():
    legs = [("Democratic Party", 0.5), ("Republican Party", 0.5)]
    rows = board.build([
        _event("CA-%02d House Election Winner" % i, legs, slug=str(i),
               volume=1000.0 * i)
        for i in range(1, 12)])

    ranked = board.rank(rows, "volume", top_n={"house": 3})
    assert [r.race_id for r in ranked["house"]] == \
        ["ho-ca11", "ho-ca10", "ho-ca9"]
    assert ranked["senate"] == [] and ranked["governor"] == []


def test_absent_weekly_volume_is_none_not_zero():
    """Polymarket omits volume1wk on markets with no trades in the window.

    Reading the missing key as 0.0 put "$0 this week" beside $118,980
    cumulative on South Carolina Senate — which reads as a dead market rather
    than as no data. 435 of 697 events carried no such key on 2026-08-08.
    """
    legs = [("Democratic Party", 0.5), ("Republican Party", 0.5)]
    quiet = _event("South Carolina Senate Election Winner", legs,
                   volume=118980.0)
    del quiet["volume1wk"]
    assert board.parse_event(quiet).volume1wk is None
    # A real figure still comes through untouched.
    busy = _event("Michigan Senate Election Winner", legs, volume=270523.0,
                  volume1wk=125623.6)
    assert board.parse_event(busy).volume1wk == 125623.6


def test_ranking_without_a_cap_keeps_everything():
    legs = [("Democratic Party", 0.5), ("Republican Party", 0.5)]
    rows = board.build([
        _event("CA-%02d House Election Winner" % i, legs, slug=str(i),
               volume=1000.0 * i)
        for i in range(1, 6)])
    assert len(board.rank(rows, "volume", top_n={"house": None})["house"]) == 5


def test_labels_are_korean_and_name_the_office():
    legs = [("Democratic Party", 0.5), ("Republican Party", 0.5)]
    assert board.parse_event(
        _event("Michigan Senate Election Winner", legs)).label == "미시간 상원"
    assert board.parse_event(
        _event("Ohio Governor Election Winner", legs)).label == "오하이오 주지사"
    assert board.parse_event(
        _event("NJ-07 House Election Winner", legs)).label == "뉴저지 7 선거구"


def test_a_leg_quoted_at_zero_is_still_a_leg():
    """A hopeless party can be quoted at exactly 0. That is a price, not an
    absent market, and it must not be confused with Nebraska's missing leg."""
    r = board.parse_event(_event("Wyoming Senate Election Winner", [
        ("Democratic Party", 0.0), ("Republican Party", 0.98)]))
    assert r.prob_dem == 0.0
    assert r.two_party_trustworthy is True
    assert r.candidates["D"] == "Democratic Party"


def test_zero_on_both_sides_yields_no_probability():
    r = board.parse_event(_event("Utah Senate Election Winner", [
        ("Democratic Party", 0.0), ("Republican Party", 0.0)]))
    assert r.prob_dem is None
