"""Tests for trip_enrichment_conditions service."""

import pytest

from src.storage.database import get_db


@pytest.fixture
def db_conn(tmp_path):
    return get_db(path=tmp_path / "test.db")


def test_moon_phase_known_date():
    from datetime import datetime

    from src.services.trip_enrichment_conditions import _compute_moon_phase

    # Known full moon: Jan 17, 2022
    full = _compute_moon_phase(datetime(2022, 1, 17))
    assert 0.45 < full < 0.55


def test_days_since_rain_no_rain():
    from src.services.trip_enrichment_conditions import _compute_days_since_rain

    precip = [0.0] * 168  # 7 days, no rain
    result = _compute_days_since_rain(precip)
    assert result == 7


def test_days_since_rain_recent():
    from src.services.trip_enrichment_conditions import _compute_days_since_rain

    # 168h (7 days): rain on day 5 (hours 96-119), days 6-7 dry
    # Function excludes day 7 (today); day 5 = 2 days before yesterday → result=1
    precip = [0.0] * 96 + [10.0] + [0.0] * 71
    result = _compute_days_since_rain(precip)
    assert result <= 2


def test_get_season():
    from src.services.trip_enrichment_conditions import _get_season

    assert _get_season(6) == "summer"
    assert _get_season(4) == "spring"
    assert _get_season(11) == "fall"
    assert _get_season(1) == "winter"


def test_haversine():
    from src.services.trip_enrichment_conditions import _haversine_km

    # Willoway to Byng — actual haversine ~10.6 km for these coordinates
    d = _haversine_km(42.919, -79.767, 42.897, -79.640)
    assert 8 < d < 14


def test_pwqmn_nearest_no_data(db_conn):
    from datetime import datetime

    from src.services.trip_enrichment_conditions import _fetch_pwqmn_nearest

    result = _fetch_pwqmn_nearest(db_conn, 43.0, -80.0, datetime(2025, 6, 20))
    # No data in test DB — should return None gracefully
    assert result is None


def test_session_conditions_stored(db_conn):
    """enrich_session_conditions stores a row even with no API data."""
    from datetime import datetime

    from src.services.trip_enrichment_conditions import enrich_session_conditions

    db_conn["sessions"].insert({"date": "2025-06-20", "overall_notes": None})
    sid = db_conn.execute("SELECT MAX(id) FROM sessions").fetchone()[0]

    # Very short timeout forces API calls to fail, but derived fields still store
    result = enrich_session_conditions(
        db_conn, sid, 42.919, -79.767,
        datetime(2025, 6, 20, 10, 0, 0),
        timeout_seconds=0.01,
        user_id=1,
    )

    assert isinstance(result, dict)
    assert result["session_id"] == sid
