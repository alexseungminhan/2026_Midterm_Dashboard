"""The v3 contract, and the invariants it refuses to publish without."""

import copy

import pytest

from election2026 import schema


def _doc():
    betting = schema.Betting(prob_dem=0.6, volume=100.0, volume1wk=10.0,
                             liquidity=5.0, slug="s", unmapped_mass=0.0,
                             trustworthy=True)
    econ = {
        "key": "economy", "label": "경제 모델", "question": "?", "detail": "",
        "directional": True, "lean": "D", "level": None, "z": 0.8,
        "strength": 2, "shift_pp": 4.0, "n_available": 3, "n_total": 3,
        "variables": [], "unavailable": None, "reason": None,
    }
    attention = dict(econ, key="attention", label="관심도", directional=False,
                     lean=None, level="high", shift_pp=None)
    race = schema.Race(
        race_id="sen-mi", chamber="senate", state="MI", district=None,
        label="미시간 상원", title="Michigan Senate Election Winner", rank=1,
        betting=betting, candidates={"D": "A", "R": "B"},
        polls=schema.Polls(prob_dem=0.53, n_polls=17), models=[econ, attention],
        reference=None)
    chambers = {ch: schema.Chamber(label=ch, total_seats=100,
                                   n_races_shown=(1 if ch == "senate" else 0))
                for ch in schema.CHAMBERS}
    return schema.document(
        meta=schema.Meta(
            generated_at="2026-08-08T00:00:00+00:00",
            schema_version=schema.SCHEMA_VERSION,
            sources_used=["polymarket"], sources_missing=[],
            rank_by="volume1wk", pp_per_sigma=5.0,
            calibration_validated=False, calibration_note="테스트"),
        chambers=chambers, races=[race],
        balance_of_power=[{"outcome": "Democrats Sweep", "label": "민주 석권",
                           "prob": 1.0}],
        movers=[])


def test_a_well_formed_document_validates():
    schema.validate(_doc())            # must not raise


def test_the_blend_fields_are_gone_from_the_contract():
    """If any of these come back, something re-introduced the λ engine."""
    race = _doc()["races"][0]
    for dead in ("p_consensus", "p_alpha", "delta", "flagged"):
        assert dead not in race
    assert "lambda" not in _doc()["meta"]
    assert "flag_history" not in _doc()


def test_trustworthy_cannot_contradict_the_independent_mass():
    """The Nebraska guard. A race where a quarter of the market sits on an
    independent must not be published as a clean two-party quote."""
    bad = _doc()
    bad["races"][0]["betting"]["unmapped_mass"] = 0.26
    with pytest.raises(schema.SchemaError, match="trustworthy"):
        schema.validate(bad)


def test_a_null_price_cannot_be_trustworthy():
    bad = _doc()
    bad["races"][0]["betting"]["prob_dem"] = None
    with pytest.raises(schema.SchemaError, match="trustworthy"):
        schema.validate(bad)


def test_an_attention_model_may_not_carry_a_party_lean():
    bad = _doc()
    bad["races"][0]["models"][1]["lean"] = "D"
    with pytest.raises(schema.SchemaError, match="attention model"):
        schema.validate(bad)


def test_an_attention_model_may_not_carry_a_shift():
    bad = _doc()
    bad["races"][0]["models"][1]["shift_pp"] = 3.0
    with pytest.raises(schema.SchemaError, match="shift_pp"):
        schema.validate(bad)


def test_an_empty_model_must_say_why():
    bad = _doc()
    bad["races"][0]["models"][0]["n_available"] = 0
    with pytest.raises(schema.SchemaError, match="unavailable reason"):
        schema.validate(bad)


def test_ranks_must_be_contiguous_within_a_chamber():
    bad = _doc()
    bad["races"][0]["rank"] = 4
    with pytest.raises(schema.SchemaError, match="contiguous"):
        schema.validate(bad)


def test_race_count_must_match_the_chamber_block():
    bad = _doc()
    bad["chambers"]["senate"]["n_races_shown"] = 9
    with pytest.raises(schema.SchemaError, match="n_races_shown"):
        schema.validate(bad)


def test_duplicate_race_ids_are_rejected():
    bad = _doc()
    bad["races"].append(copy.deepcopy(bad["races"][0]))
    bad["races"][1]["rank"] = 2
    bad["chambers"]["senate"]["n_races_shown"] = 2
    with pytest.raises(schema.SchemaError, match="duplicate race_id"):
        schema.validate(bad)


def test_the_version_is_pinned():
    bad = _doc()
    bad["meta"]["schema_version"] = "2.2.0"
    with pytest.raises(schema.SchemaError, match="schema_version"):
        schema.validate(bad)


def test_an_unknown_rating_is_rejected():
    bad = _doc()
    bad["races"][0]["reference"] = {"rating": "Probably fine"}
    with pytest.raises(schema.SchemaError, match="rating"):
        schema.validate(bad)
