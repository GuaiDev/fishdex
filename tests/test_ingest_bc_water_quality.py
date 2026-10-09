"""Tests for BC EnMoDS water quality ingest — no live downloads.

The fixture is real rows from the current-tier file (Chilliwack River, Sapperton
Bar, Vancouver and Fraser-area sites) plus a few hand-edited negatives: waste
water, replicate QC, a non-detect, pH 99, DO as % saturation, an empty value, a
mS/cm conductivity, and a location in Ontario.
"""

import csv
import gzip
import logging
import shutil
from pathlib import Path

import httpx
import pytest

from src.ingest.jurisdictions.ca_bc import water_quality as wq
from src.services.bc_ingest import ingest_bc_water_quality
from src.storage.database import get_db
from src.storage.water_quality import query_water_quality

FIXTURE = Path(__file__).parent / "fixtures" / "enmods_results_sample.csv"
FRASER = (49.15, -122.6)


@pytest.fixture
def gz_fixture(tmp_path):
    out = tmp_path / "enmods_current.csv.gz"
    with FIXTURE.open("rb") as src, gzip.open(out, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return out


def _fixture_rows():
    with FIXTURE.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _river_row(**overrides):
    template = next(r for r in _fixture_rows() if r["Location_ID"] == "E339144")
    row = {
        **template,
        "Location_ID": "E900001",
        "Observed_Date_Time": "2025-10-01T09:00-08:00",
        "Depth_Upper": "",
    }
    row.update(overrides)
    return row


def _by_station(readings):
    out = {}
    for r in readings:
        out.setdefault(r.station_id, []).append(r)
    return out


def test_parse_reads_gzip_and_plain_identically(gz_fixture):
    plain, _ = wq.parse_results(FIXTURE, *FRASER, radius_km=100)
    gz, _ = wq.parse_results(gz_fixture, *FRASER, radius_km=100)
    assert sorted(r.record_id for r in plain) == sorted(r.record_id for r in gz)
    assert plain


def test_parameters_from_one_visit_fold_into_one_reading():
    readings, _ = wq.parse_results(FIXTURE, *FRASER, radius_km=100)
    visit = [
        r
        for r in readings
        if r.station_id == "E339144" and r.sampled_at.isoformat() == "2025-12-02"
    ]
    assert len(visit) == 1
    assert visit[0].temp_c == 6.4
    assert visit[0].conductivity_us_cm == 90.3
    assert visit[0].jurisdiction == "CA-BC"
    assert visit[0].record_id.startswith("CA-BC:E339144:2025-12-02T08:06")


def test_conductivity_in_ms_per_cm_is_converted():
    readings, _ = wq.parse_results(FIXTURE, *FRASER, radius_km=100)
    nov1 = [r for r in readings if r.sampled_at.isoformat() == "2025-11-01"]
    assert [r.conductivity_us_cm for r in nov1] == [200.0]


def test_unwanted_rows_are_dropped_and_counted():
    readings, stats = wq.parse_results(FIXTURE, *FRASER, radius_km=100)
    dates = {r.sampled_at.isoformat() for r in readings}
    # waste water (11-02), replicate (11-03), non-detect (11-04), % saturation (11-06)
    assert not dates & {"2025-11-02", "2025-11-03", "2025-11-04", "2025-11-06"}
    assert stats.rows_not_wanted >= 4
    assert stats.unit_counts == {}  # % saturation is a known skip, not a surprise
    # empty Result_Value (11-07) cannot be parsed
    assert stats.rows_unusable == 1


def test_validator_rejection_is_counted_not_silent():
    readings, stats = wq.parse_results(FIXTURE, *FRASER, radius_km=100)
    assert stats.rows_rejected == 1  # pH 99 on 2025-11-05
    assert "2025-11-05" not in {r.sampled_at.isoformat() for r in readings}


def test_compliance_location_types_never_reach_stored_readings(tmp_path, monkeypatch, gz_fixture):
    _, stats = wq.parse_results(FIXTURE, *FRASER, radius_km=100)
    assert stats.rows_not_ambient == 4

    db_path = tmp_path / "t.db"
    monkeypatch.setattr(wq, "download_results", lambda: gz_fixture)
    monkeypatch.setattr("src.services.bc_ingest.get_db", lambda: get_db(db_path))
    ingest_bc_water_quality(49.10, -123.02, radius_km=30)
    stored = query_water_quality(get_db(db_path), lat=49.10, lng=-123.02, radius_km=30)
    # 0301336 is the 'Ditch or Culvert' station with DO 1.08 mg/L and pH 3.98.
    assert stored
    assert "0301336" not in {r.station_id for r in stored}
    assert 1.08 not in {r.do_mgl for r in stored}


def test_visit_fold_keeps_shallowest_sample_and_prefers_field_ph(tmp_path):
    rows = [
        _river_row(
            Observed_Property_Name="0014", Result_Unit="mg/L", Result_Value="2.0", Depth_Upper="12"
        ),
        _river_row(Observed_Property_Name="0014", Result_Unit="mg/L", Result_Value="9.0"),
        _river_row(
            Observed_Property_Name="0014", Result_Unit="mg/L", Result_Value="7.0", Depth_Upper="5"
        ),
        _river_row(Observed_Property_Name="0004", Result_Unit="pH units", Result_Value="6.5"),
        _river_row(Observed_Property_Name="PH-F", Result_Unit="pH units", Result_Value="7.2"),
        _river_row(Observed_Property_Name="0004", Result_Unit="pH units", Result_Value="6.0"),
    ]
    for ordering in (rows, rows[::-1]):
        readings, _ = wq.parse_results(_write_rows(tmp_path / "f.csv", ordering), *FRASER, 100)
        assert len(readings) == 1
        assert readings[0].do_mgl == 9.0
        assert readings[0].ph == 7.2


def test_field_do_is_read_and_preferred_over_lab_do(tmp_path):
    # DO-F carries most ambient BC dissolved oxygen; its % saturation rows are a known skip.
    field_only = [
        _river_row(Observed_Property_Name="DO-F", Result_Unit="mg/L", Result_Value="10.4"),
        _river_row(Observed_Property_Name="DO-F", Result_Unit="%", Result_Value="96"),
    ]
    readings, stats = wq.parse_results(_write_rows(tmp_path / "f.csv", field_only), *FRASER, 100)
    assert [r.do_mgl for r in readings] == [10.4]
    assert stats.unit_counts == {}

    both = [
        _river_row(Observed_Property_Name="0014", Result_Unit="mg/L", Result_Value="8.0"),
        _river_row(Observed_Property_Name="DO-F", Result_Unit="mg/L", Result_Value="10.4"),
    ]
    for ordering in (both, both[::-1]):
        readings, _ = wq.parse_results(_write_rows(tmp_path / "f.csv", ordering), *FRASER, 100)
        assert [r.do_mgl for r in readings] == [10.4]


def test_rejected_parameter_keeps_the_rest_of_its_visit(tmp_path):
    rows = [
        _river_row(Observed_Property_Name="TEMF", Result_Unit="degC", Result_Value="45"),
        _river_row(Observed_Property_Name="PH-F", Result_Unit="pH units", Result_Value="7.4"),
    ]
    readings, stats = wq.parse_results(_write_rows(tmp_path / "f.csv", rows), *FRASER, 100)
    assert stats.rows_rejected == 1
    assert [(r.ph, r.temp_c) for r in readings] == [(7.4, None)]


def test_unexpected_unit_is_counted_and_logged_at_warning(tmp_path, monkeypatch, caplog):
    rows = [
        _river_row(Observed_Property_Name="SC-F", Result_Unit="uS/cm", Result_Value="90"),
        _river_row(Observed_Property_Name="PH-F", Result_Unit="pH units", Result_Value="7.4"),
    ]
    path = _write_rows(tmp_path / "f.csv", rows)
    monkeypatch.setattr(wq, "download_results", lambda: path)
    with caplog.at_level(logging.INFO, logger=wq.__name__):
        readings = wq.fetch_water_quality_readings(*FRASER, radius_km=100)
    assert [r.conductivity_us_cm for r in readings] == [None]
    [record] = [r for r in caplog.records if "BC EnMoDS: scanned" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "uS/cm" in record.getMessage()


def test_rows_without_coordinates_are_counted_apart_and_do_not_warn(tmp_path, monkeypatch, caplog):
    rows = [
        _river_row(Location_Latitude="", Location_Longitude=""),
        _river_row(Observed_Property_Name="PH-F", Result_Unit="pH units", Result_Value="7.4"),
    ]
    path = _write_rows(tmp_path / "f.csv", rows)
    _, stats = wq.parse_results(path, *FRASER, 100)
    assert (stats.rows_no_coords, stats.rows_unusable) == (1, 0)

    monkeypatch.setattr(wq, "download_results", lambda: path)
    with caplog.at_level(logging.INFO, logger=wq.__name__):
        wq.fetch_water_quality_readings(*FRASER, radius_km=100)
    [record] = [r for r in caplog.records if "BC EnMoDS: scanned" in r.getMessage()]
    assert record.levelno == logging.INFO


def test_radius_excludes_distant_locations():
    near, _ = wq.parse_results(FIXTURE, *FRASER, radius_km=100)
    assert "E000001" not in {r.station_id for r in near}
    ontario, _ = wq.parse_results(FIXTURE, 43.4, -79.7, radius_km=20)
    assert {r.station_id for r in ontario} == {"E000001"}


def test_small_radius_filters_by_distance_not_just_box():
    # Chilliwack River is ~50 km from the Fraser test point.
    tight, _ = wq.parse_results(FIXTURE, 49.0, -121.95, radius_km=5)
    assert {r.station_id for r in tight} <= {"E339144"}


def test_download_streams_follows_redirect_and_is_cached(tmp_path, monkeypatch, gz_fixture):
    monkeypatch.setattr(wq, "_CACHE_DIR", tmp_path / "cache")
    payload = gz_fixture.read_bytes()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "coms.api" in str(request.url):
            return httpx.Response(302, headers={"location": "https://store.example/f.csv.gz"})
        return httpx.Response(200, content=payload)

    def stream(method, url, **kw):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url, follow_redirects=kw.get("follow_redirects", False))

    monkeypatch.setattr(httpx, "stream", stream)
    first = wq.download_results()
    assert first.read_bytes() == payload
    assert len(calls) == 2
    assert not first.with_suffix(".part").exists()

    wq.download_results()  # fresh -> no second download
    assert len(calls) == 2


def test_failed_download_raises_and_leaves_no_cache_file(tmp_path, monkeypatch):
    monkeypatch.setattr(wq, "_CACHE_DIR", tmp_path / "cache")
    transport = httpx.MockTransport(lambda r: httpx.Response(503))

    def stream(method, url, **kw):
        return httpx.Client(transport=transport).stream(method, url)

    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(httpx.HTTPStatusError):
        wq.download_results()
    assert not (tmp_path / "cache" / wq._CACHE_FILE).exists()


def test_ingest_stores_readings_and_returns_count(tmp_path, monkeypatch, gz_fixture):
    db_path = tmp_path / "t.db"
    monkeypatch.setattr(wq, "download_results", lambda: gz_fixture)
    monkeypatch.setattr("src.services.bc_ingest.get_db", lambda: get_db(db_path))

    n = ingest_bc_water_quality(*FRASER, radius_km=100)
    db = get_db(db_path)
    stored = query_water_quality(db, lat=FRASER[0], lng=FRASER[1], radius_km=100)
    assert n == len(stored) > 0
    assert {r.jurisdiction for r in stored} == {"CA-BC"}

    # Re-running replaces, never duplicates.
    assert ingest_bc_water_quality(*FRASER, radius_km=100) == n
    assert db["water_quality_readings"].count == n


def test_bc_readings_reach_the_water_slice_with_their_own_source(tmp_path, monkeypatch, gz_fixture):
    from src.services.context import describe

    db_path = tmp_path / "t.db"
    monkeypatch.setattr(wq, "download_results", lambda: gz_fixture)
    monkeypatch.setattr("src.services.bc_ingest.get_db", lambda: get_db(db_path))
    ingest_bc_water_quality(49.10, -123.02, radius_km=30)

    water = describe(get_db(db_path), lat=49.10, lng=-123.02, caller="map_tap").water
    assert not water.dissolved_oxygen.is_empty
    assert "BC EnMoDS" in water.dissolved_oxygen.provenance.source
    assert "PWQMN" not in water.dissolved_oxygen.provenance.source
