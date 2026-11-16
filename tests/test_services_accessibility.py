"""Tests for access score computation. All use synthetic data — no live calls."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.services.accessibility import (
    _PARK_MODIFIERS,
    _build_crown_index,
    _build_park_index,
    _distance_modifier,
    _road_proximity_modifier,
    _vectorized_crown_flag,
    _vectorized_park_modifier,
    compute_access_scores,
)
from src.services.untapped_potential import _access_note
from src.storage.database import get_db

# ── synthetic helpers ─────────────────────────────────────────────────────────


def _make_feature_matrix(n: int = 20, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    lats = np.linspace(43.5, 44.0, n)
    lngs = np.linspace(-80.0, -79.5, n)
    return pd.DataFrame(
        {
            "ogf_id": list(range(1, n + 1)),
            "centroid_lat": lats,
            "centroid_lng": lngs,
            "stream_order": rng.integers(1, 5, n),
        }
    )


def _insert_park(db, park_id: str, park_type: str, lat: float, lng: float) -> None:
    """Insert a park polygon that contains the given point."""
    # Small square polygon centred on lat/lng (~1km radius)
    d = 0.01
    rings = [
        [
            [lng - d, lat - d],
            [lng + d, lat - d],
            [lng + d, lat + d],
            [lng - d, lat + d],
            [lng - d, lat - d],
        ]
    ]
    if "provincial_parks" not in db.table_names():
        db["provincial_parks"].create(
            {
                "park_id": str,
                "name": str,
                "park_type": str,
                "centroid_lat": float,
                "centroid_lng": float,
                "polygon_json": str,
                "fetched_at": str,
            },
            pk="park_id",
        )
    db["provincial_parks"].insert(
        {
            "park_id": park_id,
            "name": f"Test {park_type} Park",
            "park_type": park_type,
            "centroid_lat": lat,
            "centroid_lng": lng,
            "polygon_json": json.dumps(rings),
            "fetched_at": "2026-01-01T00:00:00",
        },
        replace=True,
    )


def _insert_access_point(db, osm_id: str, access_type: str, lat: float, lng: float) -> None:
    if "access_points" not in db.table_names():
        db["access_points"].create(
            {
                "osm_id": str,
                "access_type": str,
                "name": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "tags": str,
                "fetched_at": str,
            },
            pk="osm_id",
        )
    db["access_points"].insert(
        {
            "osm_id": osm_id,
            "access_type": access_type,
            "name": None,
            "lat": lat,
            "lng": lng,
            "jurisdiction": "CA-ON",
            "tags": "{}",
            "fetched_at": "2026-01-01T00:00:00",
        },
        replace=True,
    )


# ── park modifier tests ───────────────────────────────────────────────────────


def test_park_modifier_values():
    assert _PARK_MODIFIERS["Recreational"] == pytest.approx(0.3)
    assert _PARK_MODIFIERS["Waterway"] == pytest.approx(0.2)
    assert _PARK_MODIFIERS["Natural Environment"] == pytest.approx(0.1)
    assert _PARK_MODIFIERS["Cultural Heritage"] == pytest.approx(0.0)
    assert _PARK_MODIFIERS["Nature Reserve"] == pytest.approx(-0.3)
    assert _PARK_MODIFIERS["Wilderness"] == pytest.approx(-0.4)


def test_park_index_empty_db(tmp_path: Path):
    db = fast_db
    tree, data = _build_park_index(db)
    assert tree is None
    assert data == []


def test_park_containment_recreational(tmp_path: Path):
    db = fast_db
    # Segment inside a Recreational park
    seg_lat, seg_lng = 43.65, -79.38
    _insert_park(db, "park_rec", "Recreational", seg_lat, seg_lng)

    tree, park_data = _build_park_index(db)
    coords = np.array([[seg_lat, seg_lng]])
    mods = _vectorized_park_modifier(tree, park_data, coords)
    assert mods[0] == pytest.approx(0.3)


def test_park_containment_wilderness(tmp_path: Path):
    db = fast_db
    seg_lat, seg_lng = 43.70, -79.40
    _insert_park(db, "park_wild", "Wilderness", seg_lat, seg_lng)

    tree, park_data = _build_park_index(db)
    coords = np.array([[seg_lat, seg_lng]])
    mods = _vectorized_park_modifier(tree, park_data, coords)
    assert mods[0] == pytest.approx(-0.4)


def test_park_outside_polygon_gets_zero(tmp_path: Path):
    db = fast_db
    # Park far from segment
    _insert_park(db, "park_far", "Recreational", 45.0, -75.0)

    tree, park_data = _build_park_index(db)
    coords = np.array([[43.65, -79.38]])
    mods = _vectorized_park_modifier(tree, park_data, coords)
    assert mods[0] == pytest.approx(0.0)


# ── distance modifier tests ───────────────────────────────────────────────────


def test_distance_modifier_within_threshold():
    pts = [(43.65, -79.38)]
    coords = np.array([[43.651, -79.381]])  # ~0.15km away
    mods = _distance_modifier(pts, coords, threshold_km=0.5, bonus=0.3)
    assert mods[0] == pytest.approx(0.3)


def test_distance_modifier_outside_threshold():
    pts = [(43.65, -79.38)]
    coords = np.array([[43.80, -79.38]])  # ~17km away
    mods = _distance_modifier(pts, coords, threshold_km=0.5, bonus=0.3)
    assert mods[0] == pytest.approx(0.0)


def test_distance_modifier_empty_pts():
    coords = np.array([[43.65, -79.38], [43.70, -79.40]])
    mods = _distance_modifier([], coords, threshold_km=1.0, bonus=0.2)
    assert np.all(mods == 0.0)


# ── road proximity tests ──────────────────────────────────────────────────────


def test_road_proximity_within_200m():
    road_pts = [(43.65, -79.38)]
    coords = np.array([[43.651, -79.381]])  # ~0.15km
    mods = _road_proximity_modifier({"road": road_pts}, coords)
    assert mods[0] == pytest.approx(0.2)


def test_road_proximity_within_500m():
    road_pts = [(43.65, -79.38)]
    coords = np.array([[43.653, -79.38]])  # ~0.33km
    mods = _road_proximity_modifier({"road": road_pts}, coords)
    assert mods[0] == pytest.approx(0.1)


def test_road_proximity_no_road_within_1km():
    road_pts = [(43.65, -79.38)]
    coords = np.array([[43.66, -79.50]])  # ~8km away
    mods = _road_proximity_modifier({"road": road_pts}, coords)
    assert mods[0] == pytest.approx(-0.2)


def test_road_proximity_outside_ingest_area_is_neutral():
    # Empty road and parking → no data available → 0.0 (not penalized)
    mods = _road_proximity_modifier({}, np.array([[43.65, -79.38]]))
    assert mods[0] == pytest.approx(0.0)


def test_road_proximity_uses_parking_as_proxy():
    # No road entries, but parking within 200m → should get +0.2
    parking_pts = [(43.65, -79.38)]
    coords = np.array([[43.651, -79.381]])
    mods = _road_proximity_modifier({"parking": parking_pts}, coords)
    assert mods[0] == pytest.approx(0.2)


# ── full compute_access_scores test ──────────────────────────────────────────


def test_compute_access_scores_output_range(tmp_path: Path, monkeypatch):
    import src.services.accessibility as acc_mod

    monkeypatch.setattr(acc_mod, "_PARQUET_PATH", tmp_path / "access_scores.parquet")
    db = fast_db
    fm = _make_feature_matrix(30)
    scores = compute_access_scores(db, fm)
    assert len(scores) == 30
    assert scores.min() >= 0.0
    assert scores.max() <= 1.0


def test_compute_access_scores_fishing_spot_beats_no_access(tmp_path: Path, monkeypatch):
    import src.services.accessibility as acc_mod

    monkeypatch.setattr(acc_mod, "_PARQUET_PATH", tmp_path / "access_scores.parquet")
    db = fast_db
    fm = _make_feature_matrix(10)

    # Put a fishing spot right next to segment 1 (43.5, -80.0)
    seg1_lat, seg1_lng = fm.iloc[0]["centroid_lat"], fm.iloc[0]["centroid_lng"]
    _insert_access_point(db, "fs1", "fishing_spot", seg1_lat + 0.001, seg1_lng + 0.001)

    scores = compute_access_scores(db, fm)
    # Segment 1 (with nearby fishing spot) should outscore segment 10 (no access)
    assert scores.iloc[0] > scores.iloc[9]


def test_compute_access_scores_nature_reserve_penalizes(tmp_path: Path, monkeypatch):
    import src.services.accessibility as acc_mod

    monkeypatch.setattr(acc_mod, "_PARQUET_PATH", tmp_path / "access_scores.parquet")
    db = fast_db
    fm = _make_feature_matrix(5)

    seg0_lat, seg0_lng = fm.iloc[0]["centroid_lat"], fm.iloc[0]["centroid_lng"]
    seg1_lat, seg1_lng = fm.iloc[1]["centroid_lat"], fm.iloc[1]["centroid_lng"]

    # Segment 0: inside Nature Reserve (-0.3)
    _insert_park(db, "p_nr", "Nature Reserve", seg0_lat, seg0_lng)
    # Segment 1: inside Recreational park (+0.3)
    _insert_park(db, "p_rec", "Recreational", seg1_lat, seg1_lng)

    scores = compute_access_scores(db, fm)
    assert scores.iloc[1] > scores.iloc[0]


def test_compute_access_scores_writes_parquet(tmp_path: Path, monkeypatch):
    import src.services.accessibility as acc_mod

    monkeypatch.setattr(acc_mod, "_PARQUET_PATH", tmp_path / "access_scores.parquet")
    db = fast_db
    fm = _make_feature_matrix(5)
    compute_access_scores(db, fm)
    assert (tmp_path / "access_scores.parquet").exists()


# ── Phase 2f: Crown land tests ────────────────────────────────────────────────


def _insert_crown_land(db, crown_id: str, lat: float, lng: float) -> None:
    """Insert a crown land polygon that contains the given point."""
    from shapely.geometry import Polygon
    from shapely.wkt import dumps as wkt_dumps

    d = 0.01  # ~1km square
    poly = Polygon([
        (lng - d, lat - d), (lng + d, lat - d),
        (lng + d, lat + d), (lng - d, lat + d),
        (lng - d, lat - d),
    ])
    bounds = poly.bounds
    centroid = poly.centroid
    if "crown_land" not in db.table_names():
        db["crown_land"].create(
            {
                "crown_id": str, "land_use_type": str, "geom_wkt": str,
                "centroid_lat": float, "centroid_lng": float,
                "bbox_minx": float, "bbox_miny": float,
                "bbox_maxx": float, "bbox_maxy": float,
                "fetched_at": str,
            },
            pk="crown_id",
        )
    db["crown_land"].insert(
        {
            "crown_id": crown_id,
            "land_use_type": "Crown Land",
            "geom_wkt": wkt_dumps(poly),
            "centroid_lat": centroid.y,
            "centroid_lng": centroid.x,
            "bbox_minx": bounds[0], "bbox_miny": bounds[1],
            "bbox_maxx": bounds[2], "bbox_maxy": bounds[3],
            "fetched_at": "2026-01-01T00:00:00",
        },
        replace=True,
    )


def test_crown_land_pip(tmp_path: Path):
    """Segment centroid inside a crown land polygon gets is_crown_land=True."""
    db = fast_db
    inside_lat, inside_lng = 43.75, -79.75
    outside_lat, outside_lng = 43.50, -79.50
    _insert_crown_land(db, "cl_1", inside_lat, inside_lng)

    tree, geoms = _build_crown_index(db)
    assert tree is not None

    coords = np.array([
        [inside_lat, inside_lng],
        [outside_lat, outside_lng],
    ])
    flags = _vectorized_crown_flag(tree, geoms, coords)
    assert flags[0] is True or flags[0] == 1   # inside → True
    assert not flags[1]                          # outside → False


def test_access_note_crown_land():
    """Crown land segment gets positive access note."""
    note = _access_note(True, 0.1)
    assert "Crown land" in note
    assert "public access" in note


def test_access_note_private_warning():
    """Low access + not crown land produces trespassing warning."""
    note = _access_note(False, 0.2)
    assert "⚠️" in note
    assert "private land" in note or "trespassing" in note


def test_access_note_road_nearby():
    """Moderate access + not crown land gives neutral road note."""
    note = _access_note(False, 0.5)
    assert "Road" in note or "park" in note
    assert "⚠️" not in note
