"""Tests for behavioral insight conflict detection."""

from sqlite_utils import Database

from src.models.behavioral_insight import BehavioralInsight
from src.storage.database import ensure_schema, migrate_behavioral_insights
from src.storage.insights import _haversine_km, check_conflicts, insert_insight


def _make_db(tmp_path) -> Database:
    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    migrate_behavioral_insights(db)
    return db


def _catfish(**overrides) -> BehavioralInsight:
    base = dict(
        user_id=1,
        species="channel catfish",
        condition_type="temporal",
        condition_context="midday-summer",
        conclusion="Fish midday in deep holes",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
    )
    base.update(overrides)
    return BehavioralInsight(**base)


def test_no_conflicts_when_empty(tmp_path):
    db = _make_db(tmp_path)
    result = check_conflicts(db, "channel catfish", user_id=1)
    assert result == []


def test_conflict_detected_by_species(tmp_path):
    db = _make_db(tmp_path)
    insert_insight(db, _catfish(condition_season="summer"))
    conflicts = check_conflicts(db, "channel catfish", condition_season="summer", user_id=1)
    assert len(conflicts) >= 1


def test_no_conflict_different_season(tmp_path):
    db = _make_db(tmp_path)
    insert_insight(db, _catfish(condition_season="summer"))
    conflicts = check_conflicts(db, "channel catfish", condition_season="winter", user_id=1)
    assert len(conflicts) == 0


def test_no_conflict_different_species(tmp_path):
    db = _make_db(tmp_path)
    insert_insight(db, _catfish())
    conflicts = check_conflicts(db, "rainbow trout", user_id=1)
    assert len(conflicts) == 0


def test_conflict_detected_by_proximity(tmp_path):
    db = _make_db(tmp_path)
    insert_insight(db, _catfish(
        condition_type="habitat",
        condition_context="deep-pool",
        conclusion="Good midday spot",
        lat=42.917,
        lng=-79.774,
    ))
    conflicts = check_conflicts(db, "channel catfish", lat=42.918, lng=-79.775, user_id=1)
    assert len(conflicts) >= 1


def test_no_conflict_far_location(tmp_path):
    db = _make_db(tmp_path)
    insert_insight(db, _catfish(
        condition_type="habitat",
        condition_context="deep-pool",
        conclusion="Good midday spot",
        lat=42.917,
        lng=-79.774,
    ))
    conflicts = check_conflicts(db, "channel catfish", lat=43.5, lng=-80.5, user_id=1)
    assert len(conflicts) == 0


def test_general_insight_included_regardless_of_location(tmp_path):
    """Insight with no lat/lng is general — included for any location query."""
    db = _make_db(tmp_path)
    insert_insight(db, _catfish())  # no lat/lng
    conflicts = check_conflicts(db, "channel catfish", lat=43.5, lng=-80.5, user_id=1)
    assert len(conflicts) == 1


def test_season_any_always_conflicts(tmp_path):
    db = _make_db(tmp_path)
    insert_insight(db, _catfish(condition_season="any"))
    conflicts = check_conflicts(db, "channel catfish", condition_season="winter", user_id=1)
    assert len(conflicts) == 1


def test_haversine_distance():
    # Willoway Park area to ~3-4km away
    dist = _haversine_km(42.917, -79.774, 42.900, -79.740)
    assert 2.0 < dist < 6.0


def test_haversine_same_point():
    dist = _haversine_km(43.0, -79.0, 43.0, -79.0)
    assert dist < 0.001


def test_new_fields_round_trip(tmp_path):
    db = _make_db(tmp_path)
    insight = _catfish(
        lat=42.917,
        lng=-79.774,
        recommendation="Fish the deep pool midday with cut bait.",
        condition_season="summer",
        location_name="Willoway Park, Grand River",
    )
    new_id = insert_insight(db, insight)
    from src.storage.insights import get_insight
    fetched = get_insight(db, new_id)
    assert fetched is not None
    assert fetched.lat == 42.917
    assert fetched.lng == -79.774
    assert fetched.recommendation == "Fish the deep pool midday with cut bait."
    assert fetched.condition_season == "summer"
    assert fetched.location_name == "Willoway Park, Grand River"
