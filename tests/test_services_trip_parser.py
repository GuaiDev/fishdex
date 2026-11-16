"""Tests for trip_parser service.

Claude API calls are mocked via the anthropic client. Snap-to-segment is
tested via direct mock of snap_to_segment. resolve_location tests use a
seeded in-memory test DB.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.trip_parser import (
    parse_session_from_text,
    parse_trip_from_text,
    resolve_location,
)
from src.storage.database import get_db


def _mock_client(payload: dict) -> MagicMock:
    """Return a mock Anthropic client whose messages.create returns payload as JSON."""
    block = MagicMock()
    block.text = json.dumps(payload)
    msg = MagicMock()
    msg.content = [block]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


def _api_payload(**overrides) -> dict:
    base = {
        "date": "2026-05-20",
        "location_description": "Bronte Creek near Burloak Drive",
        "waterbody_name": "Bronte Creek",
        "lat": 43.45,
        "lng": -79.70,
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
        "habitat_notes": "riffle over cobble under a road bridge",
        "spot_type": "riffle",
        "fish_count": 5,
        "was_productive": True,
        "gear": "2lb tippet, size 14 nymph",
        "notes": "Best spot on the creek",
    }
    base.update(overrides)
    return base


@patch(
    "src.services.trip_parser.snap_to_segment",
    return_value={
        "ogf_id": 12345,
        "distance_to_segment_m": 45.0,
        "segment_stream_order": 3,
        "segment_watercourse_name": "Bronte Creek",
    },
)
@patch("src.llm.client.get_client")
def test_parse_complete_trip(mock_get_client, mock_snap):
    mock_get_client.return_value = _mock_client(_api_payload())

    result = parse_trip_from_text(
        "Fished Bronte Creek near Burloak this morning, caught creek chubs and suckers "
        "near a riffle under the bridge, water low and clear"
    )

    assert result["date"] == "2026-05-20"
    assert "Creek Chub" in result["species_caught"]
    assert result["conditions"]["water_level"] == "low"
    assert result["was_productive"] is True
    assert result["spot_type"] == "riffle"
    assert result["ogf_id"] == 12345
    assert result["segment_watercourse_name"] == "Bronte Creek"


@patch("src.services.trip_parser.snap_to_segment", return_value={})
@patch("src.llm.client.get_client")
def test_parse_minimal_trip(mock_get_client, mock_snap):
    mock_get_client.return_value = _mock_client(
        _api_payload(
            date=None,
            species_caught=["Creek Chub"],
            species_observed=[],
            conditions={
                "water_level": None,
                "water_clarity": None,
                "water_temp_c": None,
                "weather": None,
                "flow_trend": None,
            },
            habitat_notes=None,
            spot_type=None,
            fish_count=None,
            was_productive=True,
            gear=None,
            notes=None,
        )
    )

    result = parse_trip_from_text("fished Bronte Creek, caught chubs")
    assert result["waterbody_name"] == "Bronte Creek"
    assert "Creek Chub" in result["species_caught"]
    assert result["date"] is None


@patch("src.services.trip_parser.snap_to_segment", return_value={})
@patch("src.llm.client.get_client")
def test_parse_negative_trip(mock_get_client, mock_snap):
    mock_get_client.return_value = _mock_client(
        _api_payload(
            species_caught=[],
            species_observed=[],
            conditions={
                "water_level": "high",
                "water_clarity": "turbid",
                "water_temp_c": None,
                "weather": "rainy",
                "flow_trend": "rising",
            },
            fish_count=0,
            was_productive=False,
            notes="blown out from rain",
        )
    )

    result = parse_trip_from_text("nothing at the Credit today, blown out")
    assert result["was_productive"] is False
    assert result["conditions"]["water_clarity"] == "turbid"
    assert result["fish_count"] == 0


@patch("src.llm.client.get_client")
def test_snap_to_segment_called_with_parsed_coords(mock_get_client):
    """When Claude returns lat/lng, snap_to_segment is called with them."""
    mock_get_client.return_value = _mock_client(_api_payload())

    with patch("src.services.trip_parser.snap_to_segment") as mock_snap:
        mock_snap.return_value = {
            "ogf_id": 999,
            "distance_to_segment_m": 120.0,
            "segment_stream_order": 3,
            "segment_watercourse_name": "Bronte Creek",
        }
        result = parse_trip_from_text("fished Bronte Creek, caught chubs")

    mock_snap.assert_called_once_with(43.45, -79.70)
    assert result["ogf_id"] == 999
    assert result["distance_to_segment_m"] == 120.0


# ── resolve_location tests ────────────────────────────────────────────────────


@pytest.fixture
def db_conn(tmp_path):
    """Test DB seeded with a handful of realistic Ontario stream segments."""
    db = get_db(path=tmp_path / "test.db")
    db["stream_segments"].insert_all(
        [
            {  # Credit River near Streetsville
                "ogf_id": 1001,
                "watercourse_type": "River",
                "name": "Credit River",
                "flow_verified": 1,
                "permanency": "perennial",
                "flow_classification": "regular",
                "length_m": 500.0,
                "geom_wkt": "POINT (-79.7200 43.5800)",
                "start_node": "A1",
                "end_node": "A2",
                "jurisdiction": "CA-ON",
                "ingested_at": "2026-01-01",
            },
            {  # Credit River — upstream segment (Brampton area)
                "ogf_id": 1002,
                "watercourse_type": "River",
                "name": "Credit River",
                "flow_verified": 1,
                "permanency": "perennial",
                "flow_classification": "regular",
                "length_m": 450.0,
                "geom_wkt": "POINT (-79.8000 43.6500)",
                "start_node": "B1",
                "end_node": "B2",
                "jurisdiction": "CA-ON",
                "ingested_at": "2026-01-01",
            },
            {  # Bronte Creek near Britannia Rd / west Oakville
                "ogf_id": 2001,
                "watercourse_type": "Creek",
                "name": "Bronte Creek",
                "flow_verified": 1,
                "permanency": "perennial",
                "flow_classification": "regular",
                "length_m": 300.0,
                "geom_wkt": "POINT (-79.6700 43.4750)",
                "start_node": "C1",
                "end_node": "C2",
                "jurisdiction": "CA-ON",
                "ingested_at": "2026-01-01",
            },
        ],
        pk="ogf_id",
        ignore=True,
    )
    db["stream_segments"].create_index(["name"], if_not_exists=True)
    return db


def test_resolve_location_single_name_match(db_conn):
    """Single matching segment returns immediately with high confidence."""
    result = resolve_location("Bronte Creek near the bridge", db_conn)
    assert result["lat"] is not None
    assert result["method"] in ("name_match", "landmark", "geocode_fallback")
    assert result["confidence"] >= 0.75
    assert result.get("ohn_segment_id") is not None


@patch("src.services.trip_parser._nominatim_geocode")
def test_resolve_location_by_name(mock_geo, db_conn):
    """Credit River (2 candidates) narrows via sub-location qualifier."""
    mock_geo.return_value = (43.58, -79.72)

    result = resolve_location("Credit River near Streetsville", db_conn)

    assert result["lat"] is not None
    assert result["method"] in ("name_match", "landmark", "geocode_fallback")
    assert result["confidence"] > 0.5


@patch("src.services.trip_parser._nominatim_geocode")
def test_resolve_location_landmark_bridge(mock_geo, db_conn):
    """Bridge keyword + single segment produces high-confidence result."""
    mock_geo.return_value = None  # Nominatim not needed for single match

    result = resolve_location("Bronte Creek at Britannia Rd bridge", db_conn)

    assert result["lat"] is not None
    assert result["confidence"] >= 0.75


@patch("src.services.trip_parser._nominatim_geocode", return_value=None)
def test_resolve_location_unresolved(mock_geo, db_conn):
    """Completely unrecognisable location returns text_only — never blocks."""
    result = resolve_location("some random ditch I found somewhere", db_conn)
    assert result["lat"] is None
    assert result["method"] == "text_only"
    assert result["confidence"] is None
    assert result["ohn_segment_id"] is None


@patch("src.services.trip_parser.snap_to_segment")
@patch("src.services.trip_parser.resolve_location")
@patch("src.llm.client.get_client")
def test_parse_trip_with_fuzzy_location(
    mock_get_client, mock_resolve, mock_snap, db_conn
):
    """Location without Claude-inferred coords falls through to resolve_location."""
    mock_get_client.return_value = _mock_client(
        _api_payload(
            lat=None,
            lng=None,
            location_description="Bronte Creek at the 5 Side Rd bridge",
            waterbody_name="Bronte Creek",
            species_caught=["Rainbow Darter", "Creek Chub"],
            conditions={
                "water_level": None,
                "water_clarity": "clear",
                "water_temp_c": None,
                "weather": None,
                "flow_trend": None,
            },
        )
    )
    mock_resolve.return_value = {
        "lat": 43.4750,
        "lng": -79.6700,
        "method": "name_match",
        "confidence": 0.85,
        "ohn_segment_id": 2001,
        "candidates": [2001],
    }
    mock_snap.return_value = {
        "ogf_id": 2001,
        "distance_to_segment_m": 55.0,
        "segment_stream_order": 3,
        "segment_watercourse_name": "Bronte Creek",
    }

    result = parse_trip_from_text(
        "Caught darters on Bronte Creek at the 5 Side Rd bridge, clear water",
        db=db_conn,
    )

    assert result.get("status") != "needs_location"
    assert result.get("ogf_id") == 2001
    mock_resolve.assert_called_once()


@patch("src.services.trip_parser.snap_to_segment", return_value={})
@patch("src.services.trip_parser.resolve_location")
@patch("src.llm.client.get_client")
def test_parse_trip_unresolved_location_still_returns_result(
    mock_get_client, mock_resolve, mock_snap, db_conn
):
    """Unresolved location returns parsed data with no coordinates — never blocks."""
    mock_get_client.return_value = _mock_client(
        _api_payload(
            lat=None, lng=None, location_description="that one random ditch", waterbody_name=None
        )
    )
    mock_resolve.return_value = {
        "lat": None,
        "lng": None,
        "method": "text_only",
        "confidence": None,
        "ohn_segment_id": None,
        "candidates": [],
    }

    result = parse_trip_from_text("caught something in a ditch", db=db_conn)

    assert result.get("status") != "needs_location"
    assert result.get("lat") is None


# ── parse_session_from_text tests ─────────────────────────────────────────────


def _session_payload(**stop_overrides) -> dict:
    """Base session payload for parse_session_from_text mocks."""
    stop = {
        "location_text": "Byng Island, Grand River",
        "location_name": "Byng Island",
        "species_caught": ["channel catfish"],
        "party_species_caught": ["channel catfish"],
        "was_productive": True,
        "technique": None,
        "gear": None,
        "water_level": None,
        "water_clarity": None,
        "weather_notes": None,
        "notes": None,
    }
    stop.update(stop_overrides)
    return {"date": "2026-06-15", "date_approx": None, "overall_notes": None, "stops": [stop]}


@patch(
    "src.services.trip_parser.resolve_location",
    return_value={
        "lat": None,
        "lng": None,
        "method": "text_only",
        "confidence": None,
        "ohn_segment_id": None,
        "candidates": [],
    },
)
@patch("src.llm.client.get_client")
def test_user_vs_party_distinction(mock_get_client, mock_resolve, db_conn):
    """Parser keeps friend catches out of species_caught but puts them in party_species_caught."""
    payload = _session_payload(
        species_caught=["channel catfish"],
        party_species_caught=["channel catfish", "freshwater drum"],
    )
    mock_get_client.return_value = _mock_client(payload)

    result = parse_session_from_text(
        "I caught a channel catfish at Byng Island. My friend caught a freshwater drum on a worm.",
        db_conn,
        user_id=1,
    )

    stop = result["stops"][0]
    assert "channel catfish" in stop["species_caught"]
    assert "freshwater drum" not in stop["species_caught"]
    assert "freshwater drum" in stop.get("party_species_caught", [])


@patch(
    "src.services.trip_parser.resolve_location",
    return_value={
        "lat": None,
        "lng": None,
        "method": "text_only",
        "confidence": None,
        "ohn_segment_id": None,
        "candidates": [],
    },
)
@patch("src.llm.client.get_client")
def test_blank_user_productive_party(mock_get_client, mock_resolve, db_conn):
    """User blank = was_productive false even if friend caught fish."""
    payload = _session_payload(
        species_caught=[],
        party_species_caught=["common carp"],
        was_productive=False,
    )
    mock_get_client.return_value = _mock_client(payload)

    result = parse_session_from_text(
        "My friend caught a nice carp but I blanked all day at Osprey Marsh.",
        db_conn,
        user_id=1,
    )

    stop = result["stops"][0]
    assert stop["was_productive"] is False
    assert stop["species_caught"] == []
    assert "common carp" in stop.get("party_species_caught", [])
