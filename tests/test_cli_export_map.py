"""Tests for src/cli/export_map.py."""

import json
from unittest.mock import patch

import pandas as pd
import pytest

from src.cli.export_map import HOME_LAT, HOME_LNG, _haversine_km, export_map_data

# ── fixtures ──────────────────────────────────────────────────────────────────

def _make_untapped_df():
    """Minimal untapped_potential DataFrame with two nearby segments."""
    return pd.DataFrame({
        "ogf_id": [1001, 1002],
        "centroid_lat": [43.50, 43.55],
        "centroid_lng": [-79.70, -79.65],
        "stream_order": [3, 4],
        "watercourse_name": ["Test Creek", None],
        "watercourse_type": ["Stream", "Stream"],
        "observation_density_25km": [100, 50],
        "is_confluence_segment": [True, False],
        "distance_to_nearest_confluence_km": [0.1, 5.0],
        "nearest_waterbody_distance_m": [50.0, 500.0],
        "connected_to_waterbody": [True, False],
        "access_score": [0.6, 0.8],
        "observation_pressure": [0.3, 0.1],
        "untapped_score": [0.35, 0.20],
    })


# ── unit test: haversine ──────────────────────────────────────────────────────

def test_haversine_same_point():
    assert _haversine_km(HOME_LAT, HOME_LNG, HOME_LAT, HOME_LNG) == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_distance():
    # Toronto to Mississauga City Hall ≈ 22 km
    d = _haversine_km(43.65, -79.38, 43.589, -79.6441)
    assert 19 < d < 25


# ── integration test: export produces valid GeoJSON ───────────────────────────

def test_export_map_generates_geojson(tmp_path):
    out = tmp_path / "map_data.json"
    untapped = _make_untapped_df()

    with patch("src.cli.export_map.pd.read_parquet") as mock_parquet:
        mock_parquet.return_value = untapped

        stats = export_map_data(
            output_path=out,
            html_output_path=tmp_path / "map_index.html",
        )

    assert out.exists(), "Output file should be created"
    assert stats["segments"] == 2

    data = json.loads(out.read_text(encoding="utf-8"))

    # Top-level GeoJSON structure
    assert data["type"] == "FeatureCollection"
    assert "features" in data
    assert "metadata" in data
    assert data["metadata"]["segment_count"] == 2

    for feat in data["features"]:
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "Point"
        assert len(feat["geometry"]["coordinates"]) == 2

        props = feat["properties"]
        # Required properties present
        for key in ("untapped_score_balanced", "untapped_score_easy", "untapped_score_adventure",
                    "access_score", "stream_order",
                    "is_confluence_segment", "connected_to_waterbody",
                    "google_maps_url", "swoop_url"):
            assert key in props, f"Missing property: {key}"

        # Scores are numeric and non-negative
        assert props["untapped_score_balanced"] >= 0.0
        assert props["untapped_score_easy"] >= 0.0
        assert props["untapped_score_adventure"] >= 0.0
        assert "habitat_score" not in props, "SDM habitat score must not reach the map"
        assert 0.0 <= props["access_score"] <= 1.0

        # Links are plausible URLs
        assert props["google_maps_url"].startswith("https://www.google.com/maps/")
        assert props["swoop_url"].startswith("https://maps.ontario.ca/swoop/")

    # Named segment preserved
    named = next(f for f in data["features"] if f["properties"]["watercourse_name"] == "Test Creek")
    assert named["properties"]["is_confluence_segment"] is True


def test_export_emits_no_sdm_prediction_fields(tmp_path):
    """The SDM is retired from the map path — no species probabilities ship."""
    out = tmp_path / "map_data.json"
    with patch("src.cli.export_map.pd.read_parquet") as mock_parquet:
        mock_parquet.return_value = _make_untapped_df()
        export_map_data(output_path=out, html_output_path=tmp_path / "map.html")

    data = json.loads(out.read_text(encoding="utf-8"))
    for feat in data["features"]:
        for field in ("top1_species", "top1_prob", "top2_species", "top2_prob"):
            assert field not in feat["properties"], field
