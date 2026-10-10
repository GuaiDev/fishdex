"""Tests for FWMIS (Alberta) waterbody species-presence ingest module."""

import json
import shutil
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
_TEST_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache" / "test_tmp_ab_presence"
_HYDRO_HTTPX = "src.ingest.jurisdictions.ca_ab.hydro_network.httpx.get"
_HYDRO_CACHE = "src.ingest.jurisdictions.ca_ab.hydro_network._CACHE_DIR"

# 5 live features recorded from layer 1 (fwmis_hydro_polygons) 2026-10-10:
#   OBJECTID 79010 Elbow River (21 spp incl. UNKN — unmapped code)
#   OBJECTID 79015 Bow-Chestermere canal — named via COMMON_NM, OFFICIAL_NM="UNNAMED"
#   OBJECTID 79056 Bow River (43 spp incl. FNDC which the sample maps to Finescale Dace)
#   OBJECTID 98403 Kids Can Catch Pond (RNTR only)
#   OBJECTID 120756 UNNAMED lake — 'NO FISH SAMPLED TO DATE' sentinel


@pytest.fixture
def cache_dir():
    shutil.rmtree(_TEST_CACHE_DIR, ignore_errors=True)
    _TEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    yield _TEST_CACHE_DIR
    shutil.rmtree(_TEST_CACHE_DIR, ignore_errors=True)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def _mock_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


def _paged_side_effect(first_data: dict) -> list:
    """First HTTP call returns first_data; second returns empty to stop pagination."""
    return [
        _mock_response(first_data),
        _mock_response({"features": [], "exceededTransferLimit": False}),
    ]


# ── presence fetching ─────────────────────────────────────────────────────────


def test_fetch_returns_one_observation_per_waterbody_species(cache_dir):
    from src.ingest.jurisdictions.ca_ab.fish_observations import fetch_waterbody_presence

    fixture = _load_fixture("fwmis_fish_presence_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        obs = fetch_waterbody_presence(51.05, -114.07, radius_km=10)

    # 4 surveyed waterbodies; a "NO FISH SAMPLED" polygon is skipped.
    assert len(obs) == 21 + 21 + 43 + 1  # Elbow, canal, Bow, pond
    assert all(o.jurisdiction == "CA-AB" for o in obs)
    assert all(o.source == "FWMIS" for o in obs)
    assert all(o.quality_grade == "survey_data" for o in obs)
    assert all(o.observed_on == date(1900, 1, 1) for o in obs)
    assert all(o.geoprivacy == "open" and not o.is_obscured for o in obs)


def test_known_code_decoded_from_loadform(cache_dir):
    from src.ingest.jurisdictions.ca_ab.fish_observations import fetch_waterbody_presence

    fixture = _load_fixture("fwmis_fish_presence_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        obs = fetch_waterbody_presence(51.05, -114.07, radius_km=10)

    # Bow River ships no COMMON_NM, so OFFICIAL_NM ("BOW RIVER", upper case) is used.
    bow = [o for o in obs if o.place_guess == "BOW RIVER"]
    assert len(bow) == 43
    assert any(o.species == "Walleye" for o in bow)  # WALL
    assert any(o.species == "Sauger" for o in bow)  # SAUG
    assert any(o.species == "Lake Sturgeon" for o in bow)  # LKST
    assert any(o.species == "Bull Trout" for o in bow)  # BLTR


def test_named_waterbody_uses_common_name(cache_dir):
    from src.ingest.jurisdictions.ca_ab.fish_observations import fetch_waterbody_presence

    fixture = _load_fixture("fwmis_fish_presence_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        obs = fetch_waterbody_presence(51.05, -114.07, radius_km=10)

    # OFFICIAL_NM is "UNNAMED" for the canal; COMMON_NM is the useful name.
    canal = [o for o in obs if o.place_guess == "BOW-CHESTERMERE DIVERSION CANAL (WID)"]
    assert len(canal) == 21


def test_unmapped_code_kept_verbatim(cache_dir):
    from src.ingest.jurisdictions.ca_ab.fish_observations import fetch_waterbody_presence

    fixture = _load_fixture("fwmis_fish_presence_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        obs = fetch_waterbody_presence(51.05, -114.07, radius_km=10)

    # Elbow River ships UNKN, which is not in the loadform table — verify the
    # code survives verbatim instead of being dropped or guessed.
    assert any(o.species == "UNKN" for o in obs)


def test_no_survey_sentinel_skipped_not_absence(cache_dir):
    from src.ingest.jurisdictions.ca_ab.fish_observations import fetch_waterbody_presence

    fixture = _load_fixture("fwmis_fish_presence_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        obs = fetch_waterbody_presence(51.05, -114.07, radius_km=10)

    # The UNNAMED lake (OBJECTID 120756) had no survey — it must not appear.
    assert all(o.place_guess != "UNNAMED" for o in obs)


def test_centroid_anchors_observation_ll(cache_dir):
    from src.ingest.jurisdictions.ca_ab.fish_observations import fetch_waterbody_presence

    fixture = _load_fixture("fwmis_fish_presence_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        obs = fetch_waterbody_presence(51.05, -114.07, radius_km=10)

    pond = [o for o in obs if o.place_guess == "KIDS CAN CATCH POND"]
    assert len(pond) == 1
    # centroid from live response is WGS84 lon/lat, exposed as (lat, lng)
    assert abs(pond[0].lat - 51.04141962089342) < 1e-6
    assert abs(pond[0].lng - (-114.01769289350678)) < 1e-6


def test_observation_ids_namespaced(cache_dir):
    from src.ingest.jurisdictions.ca_ab.fish_observations import fetch_waterbody_presence

    fixture = _load_fixture("fwmis_fish_presence_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        obs = fetch_waterbody_presence(51.05, -114.07, radius_km=10)

    # ids live in the reserved 8e9 block, unique per (waterbody, species)
    assert len({o.observation_id for o in obs}) == len(obs)
    assert all(o.observation_id >= 8_000_000_000 for o in obs)


def test_empty_response_returns_empty_list(cache_dir):
    from src.ingest.jurisdictions.ca_ab.fish_observations import fetch_waterbody_presence

    empty = {"features": [], "exceededTransferLimit": False}
    with patch(_HYDRO_HTTPX, return_value=_mock_response(empty)), patch(_HYDRO_CACHE, cache_dir):
        obs = fetch_waterbody_presence(51.05, -114.07, radius_km=10)

    assert obs == []


# ── caching ───────────────────────────────────────────────────────────────────


def test_cache_hit_skips_http(cache_dir):
    from src.ingest.jurisdictions.ca_ab.fish_observations import fetch_waterbody_presence

    fixture = _load_fixture("fwmis_fish_presence_response.json")
    with (
        patch(_HYDRO_HTTPX, return_value=_mock_response(fixture)) as mock_get,
        patch(_HYDRO_CACHE, cache_dir),
    ):
        fetch_waterbody_presence(51.05, -114.07, radius_km=10)
        fetch_waterbody_presence(51.05, -114.07, radius_km=10)

    assert mock_get.call_count == 1


# ── parser ────────────────────────────────────────────────────────────────────


def _feature(wb_id, species, area, name=None, off_name="LAKE", centroid=(-114.0, 51.0)):
    return {
        "attributes": {
            "OBJECTID": wb_id,
            "WB_ID": wb_id,
            "OFFICIAL_NM": off_name,
            "COMMON_NM": name,
            "Feature_Type": "LAKE-PER",
            "SPECIES_PRES": species,
            "Shape__Area": area,
        },
        "centroid": {"x": centroid[0], "y": centroid[1]},
    }


def test_parse_aggregates_multi_polygon_waterbody():
    from src.ingest.jurisdictions.ca_ab.fish_observations import _parse_presence

    # Same WB_ID across two polygons (main lake + a bay); species union reported
    # once, anchored at the largest-area polygon's centroid.
    feats = [
        _feature(42, "RNTR, BLTR", 1000.0, off_name="MAIN LAKE", centroid=(-114.0, 51.0)),
        _feature(42, "WALL", 500.0, off_name="MAIN LAKE BAY", centroid=(-114.1, 51.1)),
    ]
    obs, unmapped, no_survey = _parse_presence(feats)

    assert len(obs) == 3  # RNTR + BLTR + WALL, no double-count
    for o in obs:
        assert o.lat == 51.0 and o.lng == -114.0  # largest-area anchor
        assert o.place_guess == "MAIN LAKE"
    # unique ids within the same waterbody
    assert len({o.observation_id for o in obs}) == 3
    assert unmapped == set()
    assert no_survey == 0


def test_parse_counts_no_survey_waterbody():
    from src.ingest.jurisdictions.ca_ab.fish_observations import _parse_presence

    obs, unmapped, no_survey = _parse_presence(
        [
            _feature(7, "NO FISH SAMPLED TO DATE", 100.0),
            _feature(8, "RNTR", 100.0),
        ]
    )
    assert len(obs) == 1  # only WB 8 stored
    assert no_survey == 1  # WB 7 reported as unsampled, not absent
    assert unmapped == set()


def test_parse_unmapped_code_reported():
    from src.ingest.jurisdictions.ca_ab.fish_observations import _parse_presence

    obs, unmapped, no_survey = _parse_presence([_feature(9, "RNTR, XYZZY", 100.0)])
    assert len(obs) == 2  # known + unmapped both stored
    assert unmapped == {"XYZZY"}
    assert any(o.species == "XYZZY" for o in obs)  # kept verbatim


def test_parse_skips_feature_without_wb_id():
    from src.ingest.jurisdictions.ca_ab.fish_observations import _parse_presence

    feat = _feature(10, "RNTR", 100.0)
    feat["attributes"]["WB_ID"] = None
    obs, _, _ = _parse_presence([feat])
    assert obs == []


def test_parse_skips_feature_without_centroid():
    from src.ingest.jurisdictions.ca_ab.fish_observations import _parse_presence

    obs, _, _ = _parse_presence([_feature(11, "RNTR", 100.0)])
    # feature with no "centroid" key
    no_centroid = {"attributes": dict(_feature(12, "RNTR", 100.0)["attributes"])}
    obs2, _, _ = _parse_presence([no_centroid])
    assert obs2 == []
