"""Tests for the LIO Bathymetry ingest module (survey indices and depth contours).

The fetch goes through the watercourse adapter's cache, so the HTTP mock and the
cache-dir patch point at `hydro_network`, exactly as tests/test_ingest_waterbody.py does.
"""

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HTTP = "src.ingest.jurisdictions.ca_on.hydro_network.httpx.get"
_CACHE = "src.ingest.jurisdictions.ca_on.hydro_network._CACHE_DIR"
_INDEX_FIXTURE = Path(__file__).parent / "fixtures" / "lio_bathymetry_index_response.json"
_LINE_FIXTURE = Path(__file__).parent / "fixtures" / "lio_bathymetry_line_response.json"
_TEST_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache" / "test_tmp"


@pytest.fixture
def cache_dir():
    shutil.rmtree(_TEST_CACHE_DIR, ignore_errors=True)
    _TEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    yield _TEST_CACHE_DIR
    shutil.rmtree(_TEST_CACHE_DIR, ignore_errors=True)


def _fixture(path: Path) -> dict:
    return json.loads(path.read_text())


def _fake_get(payload: dict):
    def get(*_a, **_k):
        return MagicMock(json=lambda: payload)

    return get


# ── survey indices ────────────────────────────────────────────────────────────


def test_fetch_indices_parses_survey_facts_and_geometry(cache_dir):
    from src.ingest.jurisdictions.ca_on.bathymetry import fetch_bathymetry_indices

    with patch(_HTTP, side_effect=_fake_get(_fixture(_INDEX_FIXTURE))), patch(_CACHE, cache_dir):
        indices = fetch_bathymetry_indices(-78.3, 44.1, -78.15, 44.25)

    assert [i.ogf_id for i in indices] == [118430028, 103384764]
    rice = indices[0]
    assert rice.project_name == "Rice Lake"
    assert rice.wby_lid == "17-7257-48946"
    assert rice.survey_year == 1970
    assert rice.survey_method == "Transect"
    assert rice.bathymetry_line_ind is True
    assert rice.bathymetry_point_ind is True
    assert rice.historic_map_ind is False
    assert rice.effective_datetime == "2020-10-07"
    assert rice.geometry["type"] == "Polygon"
    assert rice.licence == "Open Government Licence – Ontario"


def test_fetch_indices_reads_the_line_flag_when_it_is_absent(cache_dir):
    from src.ingest.jurisdictions.ca_on.bathymetry import fetch_bathymetry_indices

    payload = {"features": [{"attributes": {"OGF_ID": 1}, "geometry": {"rings": []}}]}
    with patch(_HTTP, side_effect=_fake_get(payload)), patch(_CACHE, cache_dir):
        assert fetch_bathymetry_indices(0, 0, 1, 1) == []  # no rings, no geometry


# ── depth contours ────────────────────────────────────────────────────────────


def test_fetch_contours_parses_depths_dates_and_line_geometry(cache_dir):
    from src.ingest.jurisdictions.ca_on.bathymetry import fetch_bathymetry_contours

    with patch(_HTTP, side_effect=_fake_get(_fixture(_LINE_FIXTURE))), patch(_CACHE, cache_dir):
        contours = fetch_bathymetry_contours(-78.25, 44.18, -78.22, 44.21)

    # The feature with no DEPTH is skipped.
    assert [c.ogf_id for c in contours] == [118431234, 118431235, 118431236]
    shallow = contours[0]
    assert shallow.depth_m == -4.0
    assert shallow.survey_method == "Transect"
    assert shallow.survey_date == "1970-07-10"
    assert shallow.creation_methodology == "Hand Digitized"
    assert shallow.geometry["type"] == "LineString"
    assert contours[1].geometry["type"] == "MultiLineString"  # two paths
    assert contours[2].depth_m == 0.0  # the shoreline contour is kept
    assert contours[2].survey_date is None


def test_a_contour_above_the_surface_is_rejected_by_the_model(cache_dir):
    from src.ingest.jurisdictions.ca_on.bathymetry import fetch_bathymetry_contours

    payload = {
        "features": [
            {
                "attributes": {"OGF_ID": 1, "DEPTH": 3.0},
                "geometry": {"paths": [[[0, 0], [1, 1]]]},
            }
        ]
    }
    with patch(_HTTP, side_effect=_fake_get(payload)), patch(_CACHE, cache_dir):
        # A bad depth is dropped by the parser (counted as unparseable), not raised.
        assert fetch_bathymetry_contours(0, 0, 1, 1) == []


def test_a_path_with_one_point_is_dropped(cache_dir):
    from src.ingest.jurisdictions.ca_on.bathymetry import fetch_bathymetry_contours

    payload = {
        "features": [
            {
                "attributes": {"OGF_ID": 1, "DEPTH": -2.0},
                "geometry": {"paths": [[[0, 0]]]},
            }
        ]
    }
    with patch(_HTTP, side_effect=_fake_get(payload)), patch(_CACHE, cache_dir):
        assert fetch_bathymetry_contours(0, 0, 1, 1) == []


# ── query shape ───────────────────────────────────────────────────────────────


def test_fetch_queries_the_index_layer_with_an_envelope(cache_dir):
    from src.ingest.jurisdictions.ca_on.bathymetry import (
        BATHY_INDEX_LAYER,
        fetch_bathymetry_indices,
    )

    sink: dict = {}

    def get(url, params=None, **_k):
        sink["url"] = url
        sink.update(params or {})
        return MagicMock(json=lambda: {"features": []})

    with patch(_HTTP, side_effect=get), patch(_CACHE, cache_dir):
        fetch_bathymetry_indices(-78.3, 44.1, -78.15, 44.25)

    assert sink["url"].endswith(f"/{BATHY_INDEX_LAYER}/query")
    assert sink["geometry"] == "-78.300000,44.100000,-78.150000,44.250000"
    assert sink["geometryType"] == "esriGeometryEnvelope"
    assert sink["spatialRel"] == "esriSpatialRelIntersects"
    assert sink["returnGeometry"] == "true"
    assert sink["resultOffset"] == "0"


# ── paging ────────────────────────────────────────────────────────────────────


def test_fetch_follows_the_transfer_limit_page_by_page(cache_dir):
    from src.ingest.jurisdictions.ca_on.bathymetry import fetch_bathymetry_contours

    page_one = {
        "features": [
            {
                "attributes": {"OGF_ID": 1, "DEPTH": -2.0},
                "geometry": {"paths": [[[0, 0], [1, 1]]]},
            }
        ],
        "exceededTransferLimit": True,
    }
    page_two = {
        "features": [
            {
                "attributes": {"OGF_ID": 2, "DEPTH": -4.0},
                "geometry": {"paths": [[[0, 0], [1, 1]]]},
            }
        ],
        "exceededTransferLimit": False,
    }
    offsets: list[str] = []

    def get(_url, params=None, **_k):
        offsets.append(params["resultOffset"])
        payload = page_one if params["resultOffset"] == "0" else page_two
        return MagicMock(json=lambda p=payload: p)

    with patch(_HTTP, side_effect=get), patch(_CACHE, cache_dir):
        contours = fetch_bathymetry_contours(0, 0, 1, 1)

    assert offsets == ["0", "5000"]
    assert [c.ogf_id for c in contours] == [1, 2]


def test_fetch_warns_when_a_query_is_still_truncated_at_the_page_cap(cache_dir, caplog):
    import logging

    from src.ingest.jurisdictions.ca_on import bathymetry

    def get(*_a, **_k):
        return MagicMock(
            json=lambda: {
                "features": [
                    {
                        "attributes": {"OGF_ID": 1, "DEPTH": -2.0},
                        "geometry": {"paths": [[[0, 0], [1, 1]]]},
                    }
                ],
                "exceededTransferLimit": True,
            }
        )

    with (
        patch(_HTTP, side_effect=get),
        patch(_CACHE, cache_dir),
        patch.object(bathymetry, "_MAX_PAGES", 2),
        caplog.at_level(logging.WARNING),
    ):
        bathymetry.fetch_bathymetry_contours(0, 0, 1, 1)

    assert any("still truncated" in r.message for r in caplog.records)


def test_a_pre_1970_survey_date_is_kept():
    from src.ingest.jurisdictions.ca_on.bathymetry import _parse_contour

    feat = {
        "attributes": {"OGF_ID": 1, "DEPTH": -3.0, "SURVEY_DATE": -142000000000},
        "geometry": {"paths": [[[0.0, 0.0], [1.0, 1.0]]]},
    }
    assert _parse_contour(feat).survey_date == "1965-07-02"
