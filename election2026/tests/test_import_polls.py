"""NYT poll-sheet import: matchup-confidence weighting (Senate + House)."""

import pytest

from election2026 import import_polls, manual


def _row(race_id="senate_GA_2026", pollster="Some Pollster",
         end_date="2026-07-01", margin=3.0, status="확정", round_=None,
         sponsor=None, lean=None, dem="Ossoff", rep="King"):
    return {"race_id": race_id, "pollster": pollster, "sponsor": sponsor,
            "lean": lean, "end_date": end_date, "dem": dem, "rep": rep,
            "margin": margin, "status": status, "round": round_}


def test_race_id_maps_to_the_monitored_id():
    assert import_polls._senate_race_id("senate_GA_2026") == "sen-ga"
    assert import_polls._senate_race_id("senate_ZZ_2026") is None
    assert import_polls._senate_race_id("총 매치업 행수") is None   # footer
    assert import_polls._senate_race_id(None) is None


def test_matchup_status_maps_to_confidence_weight_not_exclusion():
    """The 2026-07-30 change: 가상/무효 rows are imported at reduced weight
    rather than dropped — dropping them left MI/NH/ME with no polls at all."""
    rows = [_row(status="확정"),
            _row(status="가상", end_date="2026-06-01"),
            _row(status="무효", end_date="2026-05-01")]
    polls, dropped = import_polls.convert_senate(rows)
    assert len(polls) == 3
    assert dropped == {}
    by_matchup = {p["matchup"]: p["weight"] for p in polls}
    assert by_matchup == {"confirmed": 1.0,
                          "hypothetical": manual.MATCHUP_WEIGHTS["hypothetical"],
                          "withdrawn": manual.MATCHUP_WEIGHTS["withdrawn"]}


def test_generic_ballot_hypotheticals_get_the_generic_weight():
    rows = [_row(status="가상", dem="Dem.", rep="Rep.")]
    polls, _ = import_polls.convert_senate(rows)
    assert polls[0]["matchup"] == "generic_ballot"
    assert polls[0]["weight"] == manual.MATCHUP_WEIGHTS["generic_ballot"]


def test_unknown_status_is_still_dropped():
    polls, dropped = import_polls.convert_senate([_row(status="???")])
    assert polls == []
    assert sum(dropped.values()) == 1


def test_rcv_rounds_collapse_to_the_final_round():
    """One poll reported in two rounds must count once, as the final round."""
    rows = [_row(race_id="senate_AK_2026", margin=4.0, round_="1st round"),
            _row(race_id="senate_AK_2026", margin=6.0, round_="Final round")]
    polls, dropped = import_polls.convert_senate(rows)
    assert len(polls) == 1
    assert polls[0]["margin_dem"] == 6.0
    assert any("RCV" in reason for reason in dropped)

    polls_reversed, _ = import_polls.convert_senate(list(reversed(rows)))
    assert polls_reversed[0]["margin_dem"] == 6.0


def test_independent_led_races_are_excluded_by_default():
    rows = [_row(race_id="senate_NE_2026", margin=5.0)]
    assert import_polls.convert_senate(rows)[0] == []
    included, _ = import_polls.convert_senate(rows, include_independents=True)
    assert len(included) == 1
    assert "independent" in included[0]["notes"]


def test_rows_without_a_margin_are_dropped():
    polls, _ = import_polls.convert_senate([_row(margin=None),
                                            _row(end_date=None)])
    assert polls == []


def test_output_matches_the_manual_polls_contract():
    polls, _ = import_polls.convert_senate([_row()])
    assert set(polls[0]) == set(manual.TEMPLATES["polls_template.xlsx"])
    assert polls[0]["sample_size"] is None      # NYT does not publish it


def test_missing_sheet_fails_with_a_readable_message(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "wrong.xlsx"
    Workbook().save(str(path))
    with pytest.raises(import_polls.PollImportError, match="sheet"):
        import_polls.read_senate_rows(str(path))


# ---------------------------------------------------------------------------
# House workbook specifics
# ---------------------------------------------------------------------------

def _hrow(label="Ohio 9", kind="본선거", pollster="P", field="April 18-20",
          sponsor=None, lean=None, c1="Merrin", c2="Kaptur",
          margin="Merrin +4"):
    return {"label": label, "kind": kind, "pollster": pollster,
            "field": field, "sponsor": sponsor or "", "lean": lean or "",
            "c1": c1, "c2": c2, "margin": margin}


def test_house_field_period_parsing():
    from datetime import date
    assert import_polls.parse_field_dates("June 8-11") == date(2026, 6, 11)
    assert import_polls.parse_field_dates("Nov. 15-17, 2025") \
        == date(2025, 11, 17)
    assert import_polls.parse_field_dates("June 27 - July 1") \
        == date(2026, 7, 1)
    assert import_polls.parse_field_dates("") is None


def test_house_margin_parsing():
    assert import_polls.parse_margin("Mendoza +2") == ("Mendoza", 2.0)
    assert import_polls.parse_margin("Democrat +12") == ("Democrat", 12.0)
    assert import_polls.parse_margin("Even") == (None, 0.0)
    assert import_polls.parse_margin("???") is None


def test_house_incumbent_seeds_party_resolution():
    """'Merrin +4' over the Democratic incumbent Kaptur must read D-4."""
    polls, dropped = import_polls.convert_house(
        [_hrow()], primary_held={"Ohio 9": True})
    assert dropped == {}
    assert polls[0]["race_id"] == "ho-oh9"
    assert polls[0]["margin_dem"] == -4.0
    assert polls[0]["matchup"] == "confirmed"


def test_house_suffixed_incumbent_still_resolves():
    """'Thomas Kean Jr.' vs a poll's bare 'Kean' — the generational suffix
    must not become the surname."""
    polls, dropped = import_polls.convert_house(
        [_hrow(label="N.J. 7", c1="Bennett", c2="Kean",
               margin="Bennett +4", field="May 24-26")],
        primary_held={"N.J. 7": False})
    assert dropped == {}
    assert polls[0]["margin_dem"] == 4.0
    assert polls[0]["matchup"] == "hypothetical"   # primary not yet held


def test_house_primary_polls_are_excluded_by_kind_not_downweighted():
    """An intra-party primary poll measures the wrong event entirely; no
    weight makes it belong in a D-vs-R consensus."""
    polls, dropped = import_polls.convert_house(
        [_hrow(label="Colo. 8", kind="민주당 예비선거", c1="Rutinel",
               c2="Bird", margin="Rutinel +13", field="June 11-14")],
        primary_held={"Colo. 8": False})
    assert polls == []
    assert any("예비선거" in r for r in dropped)


def test_house_unresolvable_party_is_dropped_loudly():
    polls, dropped = import_polls.convert_house(
        [_hrow(label="Ariz. 1", c1="Nobody", c2="Unknown",
               margin="Nobody +3", field="June 1-2")],
        primary_held={"Ariz. 1": True})
    assert polls == []
    assert any("which party" in r for r in dropped)


def test_house_trial_heats_of_one_poll_collapse_to_one_row():
    """One 500-person sample asked two head-to-heads is ONE poll. Emitting
    both handed that pollster double weight in aggregate_polls."""
    rows = [_hrow(label="Mich. 7", c1="Dem.", c2="Rep.", margin="Democrat +2",
                  field="June 24-30"),
            _hrow(label="Mich. 7", c1="Hertel", c2="Barrett",
                  margin="Barrett +6", field="June 24-30")]
    polls, dropped = import_polls.convert_house(rows,
                                                primary_held={"Mich. 7": False})
    assert len(polls) == 1
    # generic_ballot (0.60) outranks hypothetical (0.35), so the generic read
    # of that sample survives and the pre-primary named heat does not.
    assert polls[0]["matchup"] == "generic_ballot"
    assert polls[0]["margin_dem"] == 2.0
    assert any("trial heats" in r for r in dropped)


def test_trial_heats_at_the_same_confidence_are_averaged():
    heats = [
        {"race_id": "gov-wi", "pollster": "Marquette", "date": "2026-07-16",
         "sample_size": None, "margin_dem": m, "matchup": "hypothetical",
         "weight": 0.35, "_note_pre": [], "_heat": "h%+.0f" % m,
         "_source": "src"}
        for m in (4.0, -1.0, -2.0, 5.0, 0.0)]
    polls, absorbed = import_polls.collapse_trial_heats(heats)
    assert absorbed == 4
    assert len(polls) == 1
    assert polls[0]["margin_dem"] == 1.2          # mean of the five heats
    assert "5 trial heats in one poll" in polls[0]["notes"]
    assert not any(k.startswith("_") for k in polls[0])


# ---------------------------------------------------------------------------
# Governor workbook
# ---------------------------------------------------------------------------

def _grow(state="Ohio", pollster="P", field="June 15-28, 2026",
          end_date=None, dem="Acton", dem_pct=0.47, rep="Ramaswamy",
          rep_pct=0.47, margin="Even", leader=None, party="Tie",
          sponsor=None, lean=None):
    from datetime import datetime
    return {"state": state, "pollster": pollster, "sponsor": sponsor,
            "lean": lean, "field": field,
            "end_date": end_date or datetime(2026, 6, 28),
            "dem": dem, "dem_pct": dem_pct, "rep": rep, "rep_pct": rep_pct,
            "margin": margin, "leader": leader, "party": party}


def test_governor_state_name_maps_to_the_monitored_id():
    assert import_polls._governor_race_id("Wisconsin") == "gov-wi"
    assert import_polls._governor_race_id("Ohio") == "gov-oh"
    # Dropped from the board on 2026-08-02, so no longer importable.
    assert import_polls._governor_race_id("Michigan") is None
    assert import_polls._governor_race_id("Narnia") is None


def test_governor_margin_sign_comes_from_the_party_column():
    """NYT computes its margin on unrounded shares, so 43-43 can legitimately
    print as 'Jackson +1'. The published margin wins; the rounded toplines
    only sanity-check it."""
    polls, dropped = import_polls.convert_governor(
        [_grow(state="Georgia", dem="Bottoms", dem_pct=0.43, rep="Jackson",
               rep_pct=0.43, margin="Jackson +1", leader="Jackson",
               party="R")])
    assert dropped == {}
    assert polls[0]["race_id"] == "gov-ga"
    assert polls[0]["margin_dem"] == -1.0


def test_governor_margin_disagreeing_with_the_toplines_is_refused():
    polls, dropped = import_polls.convert_governor(
        [_grow(state="Georgia", dem="Bottoms", dem_pct=0.48, rep="Jackson",
               rep_pct=0.46, margin="Bottoms +12", leader="Bottoms",
               party="D")])
    assert polls == []
    assert any("transcription error" in r for r in dropped)


def test_governor_nominees_are_read_off_the_post_primary_polls():
    """Ohio's primary was 2026-05-05. A heat of the two people pollsters kept
    testing afterwards is confirmed; one naming a candidate who lost is
    withdrawn, not hypothetical."""
    from datetime import datetime
    rows = [
        _grow(end_date=datetime(2026, 6, 28), dem="Acton", rep="Ramaswamy",
              margin="Even", party="Tie"),
        _grow(end_date=datetime(2026, 6, 1), pollster="Q", dem="Acton",
              dem_pct=0.50, rep="Ramaswamy", rep_pct=0.49,
              margin="Acton +1", party="D"),
        _grow(end_date=datetime(2025, 2, 20), pollster="R", dem="Ryan",
              dem_pct=0.42, rep="Ramaswamy", rep_pct=0.48,
              margin="Ramaswamy +6", party="R"),
    ]
    polls, dropped = import_polls.convert_governor(rows)
    assert dropped == {}
    by_pollster = {p["pollster"]: p["matchup"] for p in polls}
    assert by_pollster == {"P": "confirmed", "Q": "confirmed",
                           "R": "withdrawn"}


def test_governor_pre_primary_state_is_all_hypothetical():
    """Wisconsin does not nominate until 2026-08-11, so no named matchup in
    that state can be confirmed however recent it is."""
    from datetime import datetime
    polls, _ = import_polls.convert_governor(
        [_grow(state="Wisconsin", dem="Rodriguez", dem_pct=0.47,
               rep="Tiffany", rep_pct=0.43, margin="Rodriguez +4",
               party="D", end_date=datetime(2026, 7, 16))],
        as_of=__import__("datetime").date(2026, 8, 2))
    assert polls[0]["matchup"] == "hypothetical"
    assert "2026-08-11" in polls[0]["notes"]


def test_governor_generic_ballot_rows_get_the_generic_weight():
    from datetime import datetime
    polls, _ = import_polls.convert_governor(
        [_grow(state="Wisconsin", dem="Democrat (generic)", dem_pct=0.50,
               rep="Republican (generic)", rep_pct=0.43,
               margin="Democrat +7", party="D",
               end_date=datetime(2026, 4, 14))])
    assert polls[0]["matchup"] == "generic_ballot"
    assert polls[0]["weight"] == manual.MATCHUP_WEIGHTS["generic_ballot"]


def test_governor_ambiguous_nominee_falls_back_to_hypothetical():
    """Two different Democrats polled equally often after the primary means
    the sheet is not telling us who won — promoting one to `confirmed` would
    be a guess."""
    from datetime import datetime
    rows = [
        _grow(end_date=datetime(2026, 6, 28), dem="Acton", rep="Ramaswamy",
              margin="Even", party="Tie"),
        _grow(end_date=datetime(2026, 6, 27), pollster="Q", dem="Ryan",
              rep="Ramaswamy", margin="Even", party="Tie"),
    ]
    polls, _ = import_polls.convert_governor(rows)
    assert {p["matchup"] for p in polls} == {"hypothetical"}
