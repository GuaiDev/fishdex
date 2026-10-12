"""Tests for Saskatchewan Fish Atlas observations — no live downloads.

The source is a two-step join: waterbody points (Species Info, FeatureServer/1)
resolve coordinates for the species rows (FishAtlasHabiSask, FeatureServer/3)
via NRCANCODE→LOCATIONID. The fixture covers a blank-species row that must be
dropped.
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ingest.jurisdictions.ca_sk import fish_observations
from src.services.sk_ingest import ingest_sk_fish_observations
from src.storage.database import get_db
from src.storage.observations import query_observations

FIXTURES = Path(__file__).parent / "fixtures"
_POINTS = json.loads((FIXTURES / "sk_fish_atlas_points.json").read_text())
_SPECIES = json.loads((FIXTURES / "sk_fish_atlas_species.json").read_text())
_HTTPX = "src.ingest.jurisdictions.ca_sk.fish_observations.httpx.get"
_CACHE = "src.ingest.jurisdictions.ca_sk.fish_observations._CACHE_DIR"


def _mock_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


def _server(url, **kwargs):
    if "/1/query" in url:
        return _mock_response(_POINTS)
    return _mock_response(_SPECIES)


def test_fetch_returns_observations_and_skips_blank_species(tmp_path):
    with patch(_HTTPX, side_effect=_server), patch(_CACHE, tmp_path):
        observations = fish_observations.fetch_observations(52.13, -106.67, radius_km=50)

    assert len(observations) == 3
    assert {o.observation_id for o in observations} == {
        fish_observations.SK_OBSERVATION_ID_OFFSET + i for i in (9001, 9002, 9003)
    }


def test_observation_fields_mapped(tmp_path):
    with patch(_HTTPX, side_effect=_server), patch(_CACHE, tmp_path):
        observations = fish_observations.fetch_observations(52.13, -106.67, radius_km=50)

    obs = next(
        o
        for o in observations
        if o.observation_id == fish_observations.SK_OBSERVATION_ID_OFFSET + 9001
    )
    assert obs.species == "Northern Pike"
    assert obs.lat == 52.13
    assert obs.lng == -106.67
    assert obs.place_guess == "Lake A"
    assert obs.jurisdiction == "CA-SK"
    assert obs.source == "SKFishAtlas"
    assert obs.quality_grade == "survey_data"


def test_missing_date_uses_sentinel(tmp_path):
    with patch(_HTTPX, side_effect=_server), patch(_CACHE, tmp_path):
        observations = fish_observations.fetch_observations(52.13, -106.67, radius_km=50)

    assert {o.observed_on for o in observations} == {date(1900, 1, 1)}


def test_cache_hit_skips_http(tmp_path):
    with patch(_HTTPX, side_effect=_server) as mock_get, patch(_CACHE, tmp_path):
        fish_observations.fetch_observations(52.13, -106.67, radius_km=50)
        fish_observations.fetch_observations(52.13, -106.67, radius_km=50)

    # points + species on the first fetch; both cached for the second
    assert mock_get.call_count == 2


def test_service_stores_observations(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    with patch(_HTTPX, side_effect=_server), patch(_CACHE, tmp_path):
        monkeypatch.setattr("src.services.sk_ingest.get_db", lambda: get_db(db_path))
        n = ingest_sk_fish_observations(52.13, -106.67, radius_km=50)

    assert n == 3
    stored, _ = query_observations(
        get_db(db_path), lat=52.13, lng=-106.67, radius_km=50, days_back=90
    )
    assert {o.source for o in stored} == {"SKFishAtlas"}
    assert {o.jurisdiction for o in stored} == {"CA-SK"}


def test_sk_and_bc_rows_with_same_raw_id_both_survive(tmp_path):
    from src.storage.observations import upsert_observations

    with patch(_HTTPX, side_effect=_server), patch(_CACHE, tmp_path):
        sk_obs = fish_observations.fetch_observations(52.13, -106.67, radius_km=50)
    sk = next(o for o in sk_obs if o.species == "Northern Pike")
    raw_id = sk.observation_id - fish_observations.SK_OBSERVATION_ID_OFFSET
    bc = sk.model_copy(update={"observation_id": raw_id, "jurisdiction": "CA-BC", "source": "FISS"})

    db = get_db(tmp_path / "t.db")
    upsert_observations(db, [bc])
    upsert_observations(db, sk_obs)

    rows = {r["observation_id"]: r["source"] for r in db["observations"].rows}
    assert rows[raw_id] == "FISS"
    assert rows[sk.observation_id] == "SKFishAtlas"
