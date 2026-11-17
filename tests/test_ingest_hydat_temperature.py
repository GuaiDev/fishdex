"""Tests for PWQMN-derived stream thermal regime classification."""

import importlib
from pathlib import Path

import pytest
from sqlite_utils import Database

from src.models.stream_temperature import StreamTemperatureSummary
from src.storage.stream_temperature import (
    is_data_loaded,
    query_temperature_summaries,
)

_hydat = importlib.import_module("src.ingest.global.hydat_temperature")
_derive_from_pwqmn = _hydat.derive_from_pwqmn
_classify_regime = _hydat._classify_regime
_species_notes = _hydat._species_notes

_LAT = 43.6532
_LNG = -79.3832

# Station coordinates mirror the original HYDAT fixture positions so distance
# assertions remain meaningful:
#   PWQMN001 (Cold Creek)  → 43.805, -79.420  (~17 km from Toronto centre)
#   PWQMN002 (Cool River)  → 43.700, -79.515  (~11 km)
#   PWQMN003 (Warm Creek)  → 43.877, -79.260  (~27 km)
#   PWQMN004 (Sparse Creek)→ 43.600, -79.300  (only 2 readings — excluded)


def _insert_pwqmn_rows(db: Database) -> None:
    rows = []

    # PWQMN001: 6 summer readings (3 Jul + 3 Aug), mean=14°C → coldwater
    for year in range(2020, 2023):
        for month in (7, 8):
            rows.append(
                {
                    "record_id": f"001_{year}_{month}",
                    "station_id": "PWQMN001",
                    "station_name": "Cold Creek",
                    "lat": 43.805,
                    "lng": -79.420,
                    "jurisdiction": "CA-ON",
                    "sampled_at": f"{year}-{month:02d}-15",
                    "temp_c": 14.0,
                }
            )

    # PWQMN002: 6 summer readings, mean=21°C → coolwater
    for year in range(2020, 2023):
        for month in (7, 8):
            rows.append(
                {
                    "record_id": f"002_{year}_{month}",
                    "station_id": "PWQMN002",
                    "station_name": "Cool River",
                    "lat": 43.700,
                    "lng": -79.515,
                    "jurisdiction": "CA-ON",
                    "sampled_at": f"{year}-{month:02d}-15",
                    "temp_c": 21.0,
                }
            )

    # PWQMN003: 6 summer readings, mean=25°C → warmwater
    for year in range(2020, 2023):
        for month in (7, 8):
            rows.append(
                {
                    "record_id": f"003_{year}_{month}",
                    "station_id": "PWQMN003",
                    "station_name": "Warm Creek",
                    "lat": 43.877,
                    "lng": -79.260,
                    "jurisdiction": "CA-ON",
                    "sampled_at": f"{year}-{month:02d}-15",
                    "temp_c": 25.0,
                }
            )

    # PWQMN004: only 2 summer readings → insufficient, must be excluded
    for month in (7, 8):
        rows.append(
            {
                "record_id": f"004_2022_{month}",
                "station_id": "PWQMN004",
                "station_name": "Sparse Creek",
                "lat": 43.600,
                "lng": -79.300,
                "jurisdiction": "CA-ON",
                "sampled_at": f"2022-{month:02d}-15",
                "temp_c": 20.0,
            }
        )

    db["water_quality_readings"].insert_all(rows)


@pytest.fixture()
def app_db(db_factory, tmp_path: Path):
    return db_factory(path=tmp_path / "fishing_test.db")


@pytest.fixture()
def populated_db(db_factory, tmp_path: Path):
    db = db_factory(path=tmp_path / "fishing_test.db")
    _insert_pwqmn_rows(db)
    _derive_from_pwqmn(db)
    return db


# ---------------------------------------------------------------------------
# Unit: _classify_regime
# ---------------------------------------------------------------------------


def test_classify_regime_coldwater():
    assert _classify_regime(14.0) == "coldwater"


def test_classify_regime_coldwater_boundary():
    assert _classify_regime(17.9) == "coldwater"


def test_classify_regime_coolwater_lower():
    assert _classify_regime(18.0) == "coolwater"


def test_classify_regime_coolwater_upper():
    assert _classify_regime(23.0) == "coolwater"


def test_classify_regime_warmwater():
    assert _classify_regime(23.1) == "warmwater"


def test_classify_regime_unknown_when_none():
    assert _classify_regime(None) == "unknown"


# ---------------------------------------------------------------------------
# Unit: _species_notes
# ---------------------------------------------------------------------------


def test_species_notes_coldwater_mentions_salmonids():
    notes = _species_notes("coldwater")
    assert "salmonid" in notes.lower() or "trout" in notes.lower()


def test_species_notes_coolwater_mentions_walleye():
    notes = _species_notes("coolwater")
    assert "walleye" in notes.lower()


def test_species_notes_warmwater_excludes_salmonids():
    notes = _species_notes("warmwater")
    assert "salmonid" in notes.lower()  # mentioned as "too warm for"


def test_species_notes_warmwater_mentions_bass():
    notes = _species_notes("warmwater")
    assert "bass" in notes.lower()


def test_species_notes_unknown_returns_string():
    notes = _species_notes("unknown")
    assert len(notes) > 0


# ---------------------------------------------------------------------------
# Unit: derive_from_pwqmn — return values and filtering
# ---------------------------------------------------------------------------


def test_derive_returns_three_qualifying_stations(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "t.db")
    _insert_pwqmn_rows(db)
    assert _derive_from_pwqmn(db) == 3


def test_derive_excludes_insufficient_data(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "t.db")
    _insert_pwqmn_rows(db)
    _derive_from_pwqmn(db)
    ids = {row["station_id"] for row in db["stream_temperature_summaries"].rows}
    assert "PWQMN004" not in ids


def test_derive_returns_zero_when_no_table(tmp_path):
    db = Database(tmp_path / "empty.db")
    assert _derive_from_pwqmn(db) == 0


def test_derive_returns_zero_when_no_temp_data(app_db):
    # Table exists but no rows with temp_c values
    assert _derive_from_pwqmn(app_db) == 0


def test_derive_is_idempotent(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "t.db")
    _insert_pwqmn_rows(db)
    _derive_from_pwqmn(db)
    _derive_from_pwqmn(db)
    count = db.execute("SELECT COUNT(*) FROM stream_temperature_summaries").fetchone()[0]
    assert count == 3


# ---------------------------------------------------------------------------
# Unit: derive_from_pwqmn — classification correctness
# ---------------------------------------------------------------------------


def test_derive_classifies_coldwater(populated_db):
    row = populated_db.execute(
        "SELECT thermal_regime FROM stream_temperature_summaries WHERE station_id = 'PWQMN001'"
    ).fetchone()
    assert row[0] == "coldwater"


def test_derive_classifies_coolwater(populated_db):
    row = populated_db.execute(
        "SELECT thermal_regime FROM stream_temperature_summaries WHERE station_id = 'PWQMN002'"
    ).fetchone()
    assert row[0] == "coolwater"


def test_derive_classifies_warmwater(populated_db):
    row = populated_db.execute(
        "SELECT thermal_regime FROM stream_temperature_summaries WHERE station_id = 'PWQMN003'"
    ).fetchone()
    assert row[0] == "warmwater"


def test_derive_summer_mean_coldwater(populated_db):
    row = populated_db.execute(
        "SELECT summer_mean_c FROM stream_temperature_summaries WHERE station_id = 'PWQMN001'"
    ).fetchone()
    assert row[0] == pytest.approx(14.0)


def test_derive_summer_max_coldwater(populated_db):
    row = populated_db.execute(
        "SELECT summer_max_c FROM stream_temperature_summaries WHERE station_id = 'PWQMN001'"
    ).fetchone()
    assert row[0] == pytest.approx(14.0)


def test_derive_years_of_data(populated_db):
    row = populated_db.execute(
        "SELECT years_of_data FROM stream_temperature_summaries WHERE station_id = 'PWQMN001'"
    ).fetchone()
    assert row[0] == 3


def test_derive_summaries_are_summary_models(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "t.db")
    _insert_pwqmn_rows(db)
    _derive_from_pwqmn(db)
    rows = list(db["stream_temperature_summaries"].rows)
    assert len(rows) == 3
    # Validate each row round-trips through the Pydantic model
    for r in rows:
        s = StreamTemperatureSummary(**r)
        assert s.thermal_regime in ("coldwater", "coolwater", "warmwater", "unknown")


def test_derive_species_notes_populated(populated_db):
    rows = list(populated_db["stream_temperature_summaries"].rows)
    assert all(len(r["species_notes"]) > 0 for r in rows)


# ---------------------------------------------------------------------------
# Integration: storage / query
# ---------------------------------------------------------------------------


def test_is_data_loaded_false_when_empty(app_db):
    assert is_data_loaded(app_db) is False


def test_is_data_loaded_true_after_derive(populated_db):
    assert is_data_loaded(populated_db) is True


def test_query_summaries_returns_correct_count(populated_db):
    results = query_temperature_summaries(populated_db, _LAT, _LNG, radius_km=100)
    assert len(results) == 3


def test_query_summaries_radius_filter(populated_db):
    # PWQMN003 (Warm Creek) at ~27km — excluded at 20km
    results = query_temperature_summaries(populated_db, _LAT, _LNG, radius_km=20)
    ids = {s.station_id for s in results}
    assert "PWQMN003" not in ids
    assert "PWQMN001" in ids
    assert "PWQMN002" in ids


def test_query_summaries_sorted_by_distance(populated_db):
    results = query_temperature_summaries(populated_db, _LAT, _LNG, radius_km=100)
    # Cool River (~11km) is closer than Cold Creek (~17km)
    assert results[0].station_id == "PWQMN002"
