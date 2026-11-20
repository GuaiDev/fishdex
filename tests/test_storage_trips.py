"""Tests for trip CRUD."""

from datetime import date

from src.models.catch import Catch
from src.models.trip import Trip
from src.storage.trips import (
    get_parsed_trips,
    get_productive_segments,
    get_trip,
    insert_parsed_trip,
    insert_trip,
    recent_trips,
)


def _trip(d: str, location: str, status: str = "completed") -> Trip:
    return Trip(
        date=date.fromisoformat(d),
        jurisdiction="CA-ON",
        location_name=location,
        status=status,
        species_caught=[Catch(species="smallmouth bass", length_cm=30.0)],
    )


def test_insert_creates_id(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    trip_id = insert_trip(db, _trip("2026-05-01", "Lake Simcoe"), user_id=1)
    assert isinstance(trip_id, int)
    assert trip_id > 0


def test_get_round_trips_a_trip(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    trip_id = insert_trip(db, _trip("2026-05-01", "Credit River"), user_id=1)
    fetched = get_trip(db, trip_id, user_id=1)
    assert fetched is not None
    assert fetched.location_name == "Credit River"
    assert fetched.jurisdiction == "CA-ON"
    assert len(fetched.species_caught) == 1
    assert fetched.species_caught[0].species == "smallmouth bass"
    assert fetched.species_caught[0].length_cm == 30.0


def test_get_returns_none_for_missing(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    assert get_trip(db, 999, user_id=1) is None


def test_recent_trips_orders_by_date_desc(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    insert_trip(db, _trip("2026-05-01", "Old Trip"), user_id=1)
    insert_trip(db, _trip("2026-05-15", "New Trip"), user_id=1)
    insert_trip(db, _trip("2026-05-08", "Mid Trip"), user_id=1)
    trips = recent_trips(db, limit=10, user_id=1)
    assert [t.location_name for t in trips] == ["New Trip", "Mid Trip", "Old Trip"]


def test_recent_trips_respects_limit(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    for i in range(5):
        insert_trip(db, _trip(f"2026-05-{i + 1:02d}", f"Trip {i}"), user_id=1)
    assert len(recent_trips(db, limit=3, user_id=1)) == 3


def test_recent_excludes_planned_trips_by_default(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    insert_trip(db, _trip("2026-05-01", "Completed One"), user_id=1)
    insert_trip(db, _trip("2026-06-01", "Planned One", status="planned"), user_id=1)
    locations = [t.location_name for t in recent_trips(db, user_id=1)]
    assert "Completed One" in locations
    assert "Planned One" not in locations


# ── parsed_trips (NL logger) ──────────────────────────────────────────────────


def _parsed_trip(**overrides) -> dict:
    base = {
        "date": "2026-06-01",
        "location_description": "Bronte Creek near Burloak",
        "waterbody_name": "Bronte Creek",
        "lat": 43.45,
        "lng": -79.70,
        "ogf_id": 12345,
        "distance_to_segment_m": 80.0,
        "segment_stream_order": 3,
        "segment_watercourse_name": "Bronte Creek",
        "species_caught": ["Creek Chub", "White Sucker"],
        "species_observed": ["Rainbow Darter"],
        "species_targeted": "Creek Chub",
        "conditions": {
            "water_level": "low",
            "water_clarity": "clear",
            "water_temp_c": 14.5,
            "weather": "sunny",
            "flow_trend": "stable",
        },
        "habitat_notes": "riffle over cobble",
        "spot_type": "riffle",
        "fish_count": 5,
        "was_productive": True,
        "gear": "ultralight",
        "notes": "great spot",
        "raw_text": "test trip",
    }
    base.update(overrides)
    return base


def test_insert_parsed_trip_returns_id(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    trip_id = insert_parsed_trip(db, _parsed_trip(), user_id=1)
    assert isinstance(trip_id, int)
    assert trip_id > 0


def test_insert_and_retrieve_parsed_trip(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    trip_id = insert_parsed_trip(db, _parsed_trip(), user_id=1)
    trips = get_parsed_trips(db, limit=10, user_id=1)
    assert len(trips) == 1
    t = trips[0]
    assert t["trip_id"] == trip_id
    assert t["waterbody_name"] == "Bronte Creek"
    assert "Creek Chub" in t["species_caught"]
    assert t["was_productive"] is True


def test_get_parsed_trips_filter_by_species(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    insert_parsed_trip(db, _parsed_trip(species_caught=["Creek Chub"]), user_id=1)
    insert_parsed_trip(
        db, _parsed_trip(species_caught=["Rainbow Trout"], waterbody_name="Credit River"), user_id=1
    )
    chub_trips = get_parsed_trips(db, species="Creek Chub", user_id=1)
    assert len(chub_trips) == 1
    assert chub_trips[0]["waterbody_name"] == "Bronte Creek"


def test_get_productive_segments(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    insert_parsed_trip(db, _parsed_trip(ogf_id=111, was_productive=True), user_id=1)
    insert_parsed_trip(db, _parsed_trip(ogf_id=222, was_productive=False), user_id=1)
    insert_parsed_trip(db, _parsed_trip(ogf_id=333, was_productive=None), user_id=1)
    productive = get_productive_segments(db, user_id=1)
    assert 111 in productive
    assert 222 not in productive
    assert 333 not in productive


def test_get_productive_segments_deduplicates(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    insert_parsed_trip(db, _parsed_trip(ogf_id=42, was_productive=True), user_id=1)
    insert_parsed_trip(db, _parsed_trip(ogf_id=42, was_productive=True), user_id=1)
    assert get_productive_segments(db, user_id=1) == [42]
