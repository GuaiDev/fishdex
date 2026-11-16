"""Tests for recommendations CRUD."""

from src.models.recommendation import LureRecommendation
from src.storage.database import get_db
from src.storage.recommendations import (
    get_recommendation,
    insert_recommendation,
    mark_used,
    recent_recommendations,
)


def _rec(**overrides) -> LureRecommendation:
    defaults = dict(
        lure_type="spinnerbait",
        color="chartreuse/white",
        size_range="3/8 oz",
        technique="slow-roll",
        retrieve_speed="medium",
        target_depth_range="2-8 ft",
        conditions_matched=["stained water"],
        confidence="medium",
        reasoning="Stained water calls for high-visibility colors.",
    )
    defaults.update(overrides)
    return LureRecommendation(**defaults)


def _conditions() -> dict:
    return {
        "water_temp_c": 18.0,
        "water_clarity": "stained",
        "pressure_trend": "steady",
        "season": "fall",
    }


def test_insert_returns_int_id(tmp_path):
    db = fast_db
    rec_id = insert_recommendation(
        db, "smallmouth bass", 43.7, -79.4, "CA-ON", _conditions(), [_rec()]
    )
    assert isinstance(rec_id, int)
    assert rec_id > 0


def test_get_round_trips_fields(tmp_path):
    db = fast_db
    recs = [_rec(lure_type="jig"), _rec(lure_type="crankbait")]
    rec_id = insert_recommendation(db, "walleye", 44.0, -78.0, "CA-ON", _conditions(), recs)
    row = get_recommendation(db, rec_id)
    assert row is not None
    assert row["species"] == "walleye"
    assert row["lat"] == 44.0
    assert row["jurisdiction"] == "CA-ON"
    assert len(row["recommendations"]) == 2
    assert row["recommendations"][0]["lure_type"] == "jig"
    assert row["was_used"] == 0


def test_get_returns_none_for_missing(tmp_path):
    db = fast_db
    assert get_recommendation(db, 999) is None


def test_mark_used_sets_flag(tmp_path):
    db = fast_db
    rec_id = insert_recommendation(db, "pike", None, None, None, {}, [_rec()])
    mark_used(db, rec_id)
    row = get_recommendation(db, rec_id)
    assert row["was_used"] == 1
    assert row["trip_id"] is None


def test_mark_used_with_trip_id(tmp_path):
    db = fast_db
    rec_id = insert_recommendation(db, "pike", None, None, None, {}, [_rec()])
    mark_used(db, rec_id, trip_id=42)
    row = get_recommendation(db, rec_id)
    assert row["was_used"] == 1
    assert row["trip_id"] == 42


def test_recent_recommendations_reverse_chronological(tmp_path):
    db = fast_db
    insert_recommendation(db, "bass", None, None, None, {}, [_rec()])
    insert_recommendation(db, "trout", None, None, None, {}, [_rec()])
    insert_recommendation(db, "pike", None, None, None, {}, [_rec()])
    rows = recent_recommendations(db, limit=10)
    species = [r["species"] for r in rows]
    # Most recently inserted should appear first
    assert species[0] == "pike"
    assert species[-1] == "bass"


def test_recent_recommendations_respects_limit(tmp_path):
    db = fast_db
    for s in ("bass", "trout", "pike", "walleye", "perch"):
        insert_recommendation(db, s, None, None, None, {}, [_rec()])
    rows = recent_recommendations(db, limit=3)
    assert len(rows) == 3


def test_conditions_json_round_trips(tmp_path):
    db = fast_db
    cond = {"water_temp_c": 12.5, "season": "fall", "pressure_trend": "falling"}
    rec_id = insert_recommendation(db, "bass", None, None, None, cond, [_rec()])
    row = get_recommendation(db, rec_id)
    assert row["conditions"]["water_temp_c"] == 12.5
    assert row["conditions"]["pressure_trend"] == "falling"
