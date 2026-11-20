"""Tests for the sessions + stops schema: parse_session_from_text + log_session."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.trip_logger import log_session
from src.services.trip_parser import parse_session_from_text


def _mock_session_client(payload: dict) -> MagicMock:
    """Return a mock Anthropic client that returns payload as JSON text."""
    block = MagicMock()
    block.text = json.dumps(payload)
    msg = MagicMock()
    msg.content = [block]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


def _session_payload(**overrides) -> dict:
    base = {
        "date": "2025-06-01",
        "date_approx": None,
        "overall_notes": None,
        "stops": [
            {
                "location_text": "Bronte Creek at Burloak",
                "location_name": "Bronte Creek, Oakville",
                "species_caught": ["creek chub", "common shiner"],
                "was_productive": True,
                "technique": "worm under float",
                "gear": "2lb line",
                "water_level": "normal",
                "water_clarity": "clear",
                "weather_notes": None,
                "notes": None,
            }
        ],
    }
    base.update(overrides)
    return base


def _noop_resolve() -> dict:
    return {
        "lat": None,
        "lng": None,
        "method": "text_only",
        "confidence": None,
        "ohn_segment_id": None,
        "candidates": [],
    }


@pytest.fixture
def db_conn(db_factory, tmp_path):
    return db_factory(path=tmp_path / "test.db")


# ── parse + log tests ─────────────────────────────────────────────────────────


@patch("src.services.trip_parser.resolve_location")
@patch("src.llm.client.get_client")
def test_session_single_stop(mock_get_client, mock_resolve, db_conn):
    """Basic single-location session logs correctly."""
    mock_get_client.return_value = _mock_session_client(_session_payload())
    mock_resolve.return_value = _noop_resolve()

    text = "June 1 2025, Bronte Creek at Burloak. Caught creek chub and common shiner on worm."
    parsed = parse_session_from_text(text, db_conn, user_id=1)
    assert len(parsed["stops"]) == 1
    assert "creek chub" in parsed["stops"][0]["species_caught"]

    result = log_session(parsed, db_conn, user_id=1)
    assert result["stops_logged"] == 1
    assert isinstance(result["session_id"], int)

    stop = db_conn.execute(
        "SELECT * FROM stops WHERE session_id = ?", [result["session_id"]]
    ).fetchone()
    assert stop is not None
    assert stop[2] == "Bronte Creek at Burloak"  # location_text


@patch("src.services.trip_parser.resolve_location")
@patch("src.llm.client.get_client")
def test_session_multi_stop(mock_get_client, mock_resolve, db_conn):
    """Multi-location day creates multiple stops with per-stop productivity."""
    payload = _session_payload(
        date=None,
        date_approx="May 10 2025",
        stops=[
            {
                "location_text": "Milton stormwater pond",
                "location_name": "Milton Stormwater Pond",
                "species_caught": ["common carp"],
                "was_productive": True,
                "technique": None,
                "gear": "corn",
                "water_level": None,
                "water_clarity": None,
                "weather_notes": None,
                "notes": None,
            },
            {
                "location_text": "Osprey Marsh in Mississauga",
                "location_name": "Osprey Marsh, Mississauga",
                "species_caught": [],
                "was_productive": False,
                "technique": None,
                "gear": None,
                "water_level": None,
                "water_clarity": None,
                "weather_notes": None,
                "notes": "carp may have been spawning",
            },
        ],
    )
    mock_get_client.return_value = _mock_session_client(payload)
    mock_resolve.return_value = _noop_resolve()

    text = (
        "May 10. Started at a Milton stormwater pond, caught one carp. "
        "Then went to Osprey Marsh, caught nothing."
    )
    parsed = parse_session_from_text(text, db_conn, user_id=1)
    assert len(parsed["stops"]) == 2
    assert parsed["stops"][0]["was_productive"] is True
    assert parsed["stops"][1]["was_productive"] is False

    result = log_session(parsed, db_conn, user_id=1)
    assert result["stops_logged"] == 2


@patch("src.services.trip_parser.resolve_location")
@patch("src.llm.client.get_client")
def test_session_no_ohn_snap_still_logs(mock_get_client, mock_resolve, db_conn):
    """Lake Ontario location logs fine without OHN segment."""
    payload = _session_payload(
        date="2024-12-15",
        stops=[
            {
                "location_text": "Oakville pier on Lake Ontario",
                "location_name": "Oakville Pier, Lake Ontario",
                "species_caught": [],
                "was_productive": False,
                "technique": None,
                "gear": "spoons",
                "water_level": None,
                "water_clarity": None,
                "weather_notes": None,
                "notes": None,
            }
        ],
    )
    mock_get_client.return_value = _mock_session_client(payload)
    mock_resolve.return_value = _noop_resolve()

    text = "Fished Oakville pier on Lake Ontario. Cast spoons for pike. Caught nothing."
    parsed = parse_session_from_text(text, db_conn, user_id=1)
    result = log_session(parsed, db_conn, user_id=1)
    assert result["stops_logged"] == 1

    stop_row = db_conn.execute(
        "SELECT location_text, ohn_segment_id FROM stops WHERE session_id = ?",
        [result["session_id"]],
    ).fetchone()
    assert stop_row[0] is not None
    assert stop_row[1] is None


@patch("src.services.trip_parser.resolve_location")
@patch("src.llm.client.get_client")
def test_no_species_hallucination(mock_get_client, mock_resolve, db_conn):
    """Validation must not allow specific shiner species when user said only 'shiners'."""
    payload = _session_payload(
        stops=[
            {
                "location_text": "small creek near London",
                "location_name": None,
                "species_caught": ["unidentified shiner sp."],
                "was_productive": True,
                "technique": None,
                "gear": None,
                "water_level": None,
                "water_clarity": None,
                "weather_notes": None,
                "notes": None,
            }
        ]
    )
    mock_get_client.return_value = _mock_session_client(payload)
    mock_resolve.return_value = _noop_resolve()

    text = "Caught some shiners in a small creek near London"
    parsed = parse_session_from_text(text, db_conn, user_id=1)
    species = parsed["stops"][0]["species_caught"]
    specific = ["spotfin", "emerald", "brassy", "striped", "common shiner"]
    for s in species:
        assert not any(sp in s.lower() for sp in specific), f"Hallucinated specific shiner: {s}"


@patch("src.services.trip_parser.resolve_location")
@patch("src.llm.client.get_client")
def test_productivity_per_stop(mock_get_client, mock_resolve, db_conn):
    """Mixed day has productivity tracked independently per stop."""
    payload = _session_payload(
        stops=[
            {
                "location_text": "Credit River — blown out",
                "location_name": None,
                "species_caught": [],
                "was_productive": False,
                "technique": None,
                "gear": None,
                "water_level": "flooded",
                "water_clarity": "turbid",
                "weather_notes": None,
                "notes": "blown out",
            },
            {
                "location_text": "Bronte Creek",
                "location_name": "Bronte Creek",
                "species_caught": ["creek chub"],
                "was_productive": True,
                "technique": None,
                "gear": None,
                "water_level": "normal",
                "water_clarity": "clear",
                "weather_notes": None,
                "notes": None,
            },
        ]
    )
    mock_get_client.return_value = _mock_session_client(payload)
    mock_resolve.return_value = _noop_resolve()

    text = """
    Went to two spots today. First spot on the Credit River was blown out, caught nothing.
    Second spot on Bronte Creek was perfect, caught several creek chub.
    """
    parsed = parse_session_from_text(text, db_conn, user_id=1)
    assert len(parsed["stops"]) == 2
    productivities = [s["was_productive"] for s in parsed["stops"]]
    assert False in productivities
    assert True in productivities
