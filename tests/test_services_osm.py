"""Tests for OSM service layer — waterfowl dispersal flag."""

import json
from datetime import date

import sqlite_utils

from src.models.water_feature import WaterFeature
from src.services import osm as osm_svc


def _make_db(tmp_path, *, stream_wkt: str | None = None, bird_lat: float | None = None):
    """Build a minimal test database.

    stream_wkt: WKT for a stream_segments row (None = no rows).
    bird_lat: latitude of a bird_observation row (None = no rows).
    """
    db = sqlite_utils.Database(tmp_path / "test.db")
    if stream_wkt is not None:
        db["stream_segments"].insert({"ogf_id": 1, "geom_wkt": stream_wkt})
    if bird_lat is not None:
        db["bird_observations"].insert(
            {
                "obs_id": "test_grbher3",
                "species_code": "grbher3",
                "common_name": "Great Blue Heron",
                "lat": bird_lat,
                "lng": -79.38,
                "observed_on": date.today().isoformat(),
                "piscivore_significance": "test",
                "jurisdiction": "CA-ON",
                "fetched_at": "2026-05-26T00:00:00",
            }
        )
    return db


def _make_pond(lat: float = 43.65, lng: float = -79.38) -> WaterFeature:
    return WaterFeature(
        osm_id="way/99999",
        feature_type="pond",
        name="Test Stormwater Pond",
        lat=lat,
        lng=lng,
        jurisdiction="CA-ON",
        area_m2=5000.0,
        tags={},
    )


def test_dispersal_flag_present_for_isolated_pond_with_birds(tmp_path, monkeypatch):
    """Pond with no nearby stream and recent bird obs gets waterfowl_dispersal_flag."""
    # Stream is far away (lat 50, lng -90 — not near the pond at 43.65, -79.38)
    db = _make_db(
        tmp_path,
        stream_wkt="LINESTRING (-90.0 50.0, -90.1 50.0)",
        bird_lat=43.651,  # within 500m of the pond
    )
    monkeypatch.setattr(osm_svc, "get_db", lambda: db)
    pond = _make_pond()
    monkeypatch.setattr(osm_svc._osm, "fetch_water_features", lambda *a, **kw: [pond])

    result = json.loads(osm_svc.get_nearby_water_for_agent(43.65, -79.38, user_id=1))
    bodies = result["water_bodies"]
    assert len(bodies) == 1
    assert bodies[0].get("waterfowl_dispersal_flag") is True
    assert "dispersal_note" in bodies[0]
    assert "PNAS" in bodies[0]["dispersal_note"]


def test_dispersal_flag_absent_when_stream_nearby(tmp_path, monkeypatch):
    """Pond with a nearby stream segment does not get the dispersal flag."""
    # Stream is RIGHT next to the pond at 43.65, -79.38
    db = _make_db(
        tmp_path,
        stream_wkt="LINESTRING (-79.380 43.650, -79.381 43.651)",
        bird_lat=43.651,
    )
    monkeypatch.setattr(osm_svc, "get_db", lambda: db)
    pond = _make_pond()
    monkeypatch.setattr(osm_svc._osm, "fetch_water_features", lambda *a, **kw: [pond])

    result = json.loads(osm_svc.get_nearby_water_for_agent(43.65, -79.38, user_id=1))
    bodies = result["water_bodies"]
    assert len(bodies) == 1
    assert "waterfowl_dispersal_flag" not in bodies[0]
