"""Tests for the GBIF ingest module. No real API calls — uses a fixture."""

import hashlib
import importlib
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# "global" is a Python keyword — use importlib to reach the module
_gbif = importlib.import_module("src.ingest.global.gbif")
fetch_gbif_observations = _gbif.fetch_gbif_observations
_cached_get = _gbif._cached_get

FIXTURE = Path(__file__).parent / "fixtures" / "gbif_response.json"


def _fixture_data() -> dict:
    return json.loads(FIXTURE.read_text())


def _mock_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = data
    mock.raise_for_status.return_value = None
    return mock


def test_fetch_returns_observations(tmp_path: Path):
    cache = tmp_path / "cache" / "gbif"
    fixture = _fixture_data()

    with (
        patch("src.ingest.global.gbif._CACHE_DIR", cache),
        patch("httpx.get", return_value=_mock_response(fixture)),
    ):
        observations = fetch_gbif_observations(lat=43.65, lng=-79.38, radius_km=50)

    # Mock returns the same 3-record fixture for every orderKey query; total = 3 × num_orders
    assert len(observations) == 3 * len(_gbif._FISH_ORDER_KEYS)
    species = {o.species for o in observations}
    assert "Moxostoma duquesnii" in species
    assert "Percina caprodes" in species
    assert "Etheostoma caeruleum" in species


def test_null_date_handling(tmp_path: Path):
    cache = tmp_path / "cache" / "gbif"
    fixture = _fixture_data()

    with (
        patch("src.ingest.global.gbif._CACHE_DIR", cache),
        patch("httpx.get", return_value=_mock_response(fixture)),
    ):
        observations = fetch_gbif_observations(lat=43.65, lng=-79.38, radius_km=50)

    by_species = {o.species: o for o in observations}
    specimen = by_species["Percina caprodes"]
    assert specimen.observed_on is None
    assert specimen.basis_of_record == "PRESERVED_SPECIMEN"


def test_datetime_date_parsed(tmp_path: Path):
    """eventDate with full ISO datetime (e.g. "2024-05-22T00:00:00") parses to date only."""
    cache = tmp_path / "cache" / "gbif"
    fixture = _fixture_data()

    with (
        patch("src.ingest.global.gbif._CACHE_DIR", cache),
        patch("httpx.get", return_value=_mock_response(fixture)),
    ):
        observations = fetch_gbif_observations(lat=43.65, lng=-79.38, radius_km=50)

    by_species = {o.species: o for o in observations}
    from datetime import date

    assert by_species["Etheostoma caeruleum"].observed_on == date(2024, 5, 22)


def test_jurisdiction_tagged(tmp_path: Path):
    cache = tmp_path / "cache" / "gbif"
    fixture = _fixture_data()

    with (
        patch("src.ingest.global.gbif._CACHE_DIR", cache),
        patch("httpx.get", return_value=_mock_response(fixture)),
    ):
        observations = fetch_gbif_observations(lat=43.65, lng=-79.38, radius_km=50)

    for obs in observations:
        assert obs.jurisdiction == "CA-ON"


def test_basis_of_record_excludes_human_observation(tmp_path: Path):
    """basisOfRecord sent as a list (repeated params); HUMAN_OBSERVATION absent."""
    cache = tmp_path / "cache" / "gbif"
    fixture = _fixture_data()

    with (
        patch("src.ingest.global.gbif._CACHE_DIR", cache),
        patch("httpx.get", return_value=_mock_response(fixture)) as mock_http,
    ):
        fetch_gbif_observations(lat=43.65, lng=-79.38, radius_km=50)

    _, kwargs = mock_http.call_args
    sent_params = kwargs["params"]
    if isinstance(sent_params, dict):
        basis = sent_params["basisOfRecord"]
    else:
        basis = [v for k, v in sent_params if k == "basisOfRecord"]
    assert isinstance(basis, list)
    assert "HUMAN_OBSERVATION" not in basis
    assert "PRESERVED_SPECIMEN" in basis
    assert "MATERIAL_SAMPLE" in basis


def test_cache_hit_skips_http(tmp_path: Path):
    cache = tmp_path / "cache" / "gbif"
    cache.mkdir(parents=True)
    fixture = _fixture_data()

    params = {"taxonKey": 186, "decimalLatitude": "43.0,44.0", "offset": 0}
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    cache_file = cache / f"{key}.json"
    cache_file.write_text(json.dumps(fixture))

    with (
        patch("src.ingest.global.gbif._CACHE_DIR", cache),
        patch("httpx.get") as mock_http,
    ):
        result = _cached_get(params)

    mock_http.assert_not_called()
    assert result["count"] == 3


def test_cache_miss_writes_file(tmp_path: Path):
    cache = tmp_path / "cache" / "gbif"
    fixture = _fixture_data()
    params = {"taxonKey": 186, "decimalLatitude": "42.0,43.0", "offset": 0}

    with (
        patch("src.ingest.global.gbif._CACHE_DIR", cache),
        patch("httpx.get", return_value=_mock_response(fixture)),
    ):
        _cached_get(params)

    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    cache_file = cache / f"{key}.json"
    assert cache_file.exists()
    assert json.loads(cache_file.read_text())["count"] == 3


def test_stale_cache_triggers_refetch(tmp_path: Path):
    cache = tmp_path / "cache" / "gbif"
    cache.mkdir(parents=True)
    fixture = _fixture_data()
    params = {"taxonKey": 186, "decimalLatitude": "41.0,42.0", "offset": 0}

    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    cache_file = cache / f"{key}.json"
    cache_file.write_text(json.dumps(fixture))

    old_time = time.time() - (25 * 3600)
    os.utime(cache_file, (old_time, old_time))

    with (
        patch("src.ingest.global.gbif._CACHE_DIR", cache),
        patch("httpx.get", return_value=_mock_response(fixture)) as mock_http,
    ):
        _cached_get(params)

    mock_http.assert_called_once()


# ── licensing and attribution ─────────────────────────────────────────────────


def test_licence_uri_normalises_to_the_shared_vocabulary():
    """GBIF publishes legalcode URIs; iNaturalist publishes short codes. Both
    corpora normalise to one vocabulary so a filter spans them."""
    import importlib

    normalise_licence = importlib.import_module("src.ingest.global.gbif").normalise_licence

    assert normalise_licence("http://creativecommons.org/licenses/by/4.0/legalcode") == "cc-by"
    assert normalise_licence("https://creativecommons.org/publicdomain/zero/1.0/legalcode") == "cc0"


def test_by_nc_is_not_swallowed_by_the_by_prefix():
    """'licenses/by' is a prefix of 'licenses/by-nc' — order matters, and
    mistaking NC for BY would licence-launder a non-commercial record."""
    import importlib

    normalise_licence = importlib.import_module("src.ingest.global.gbif").normalise_licence

    assert (
        normalise_licence("http://creativecommons.org/licenses/by-nc/4.0/legalcode") == "cc-by-nc"
    )


def test_unstated_or_unrecognised_licence_is_none_not_guessed():
    import importlib

    normalise_licence = importlib.import_module("src.ingest.global.gbif").normalise_licence

    assert normalise_licence(None) is None
    assert normalise_licence("") is None
    assert normalise_licence("http://example.com/some-bespoke-terms") is None


# ── survey provenance ─────────────────────────────────────────────────────────
# What separates a standardised survey record from a casual photo. These fields
# arrive from GBIF on gear-based records and were dropped at parse until now, so
# a boat-electrofishing haul was stored indistinguishably from a phone upload.

SURVEY_FIXTURE = Path(__file__).parent / "fixtures" / "gbif_survey_response.json"

_parse_observation = _gbif._parse_observation


def _survey_records() -> list[dict]:
    return json.loads(SURVEY_FIXTURE.read_text(encoding="utf-8"))["results"]


def test_sampling_protocol_is_captured():
    """Recorded from a real GBIF query for boat-electrofisher records."""
    obs = [_parse_observation(r) for r in _survey_records()]
    protocols = {o.sampling_protocol for o in obs}
    assert protocols, "no sampling_protocol captured"
    assert all("electrofisher" in (p or "").lower() for p in protocols)


def test_event_id_is_captured_and_groups_records():
    """Records sharing an event_id came from one haul.

    This is the whole point of keeping the field: a group of records from a
    single sampling event is the only route to an effort-corrected absence,
    which presence-only data cannot produce. The fixture contains two species
    from ROM event ROMI-E69589.
    """
    obs = [_parse_observation(r) for r in _survey_records()]
    ids = [o.event_id for o in obs if o.event_id]
    assert ids, "no event_id captured"
    assert len(ids) > len(set(ids)), "fixture should contain a shared sampling event"


def test_sampling_protocol_accepts_a_list_from_gbif():
    """GBIF returns samplingProtocol as a string on some records, a list on
    others. A list must not be stringified into the column."""
    raw = dict(_survey_records()[0])
    raw["samplingProtocol"] = ["Backpack electrofisher", "dip net"]
    obs = _parse_observation(raw)
    assert obs.sampling_protocol == "Backpack electrofisher"


def test_absent_survey_fields_stay_none():
    """Absent is not 'no gear was used'. Defaulting these would invent a survey."""
    raw = dict(_survey_records()[0])
    for k in ("samplingProtocol", "eventID", "samplingEffort", "sampleSizeValue", "sampleSizeUnit"):
        raw.pop(k, None)
    obs = _parse_observation(raw)
    assert obs.sampling_protocol is None
    assert obs.event_id is None
    assert obs.sampling_effort is None
    assert obs.sample_size_value is None
    assert obs.sample_size_unit is None


def test_unparseable_sample_size_does_not_raise():
    """A publisher can put anything in sampleSizeValue. One bad value must not
    discard the record -- that is the iNaturalist observed_on bug's shape."""
    raw = dict(_survey_records()[0])
    raw["sampleSizeValue"] = "about 100"
    obs = _parse_observation(raw)
    assert obs.sample_size_value is None
    assert obs.species  # the rest of the record survived


def test_survey_fields_round_trip_through_storage(tmp_path: Path):
    from src.storage.database import get_db
    from src.storage.gbif_observations import (
        query_gbif_observations,
        upsert_gbif_observations,
    )

    db = get_db(tmp_path / "t.db")
    obs = [_parse_observation(r) for r in _survey_records()]
    upsert_gbif_observations(db, obs)

    back = query_gbif_observations(db, obs[0].lat, obs[0].lng, radius_km=50)
    assert back, "nothing round-tripped"
    assert any(o.sampling_protocol for o in back), "sampling_protocol lost in storage"
    assert any(o.event_id for o in back), "event_id lost in storage"
