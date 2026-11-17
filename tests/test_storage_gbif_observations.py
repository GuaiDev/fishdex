"""Tests for GBIF observation storage functions."""

from datetime import date

from src.models.gbif_observation import GBIFObservation
from src.storage.gbif_observations import oldest_gbif_record, upsert_gbif_observations


def _obs(**overrides) -> GBIFObservation:
    base = dict(
        gbif_key=1,
        species="Moxostoma duquesnii",
        common_name="Black Redhorse",
        taxon_key=2360285,
        lat=43.85,
        lng=-79.03,
        observed_on=date(2023, 6, 15),
        country_code="CA",
        dataset_name="Royal Ontario Museum Ichthyology (ROMI)",
        basis_of_record="PRESERVED_SPECIMEN",
        coordinate_uncertainty_m=None,
        jurisdiction="CA-ON",
    )
    base.update(overrides)
    return GBIFObservation(**base)


def test_oldest_returns_none_on_empty_db(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    assert oldest_gbif_record(db) is None


def test_oldest_returns_none_when_all_dates_null(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    upsert_gbif_observations(db, [_obs(gbif_key=1, observed_on=None)])
    assert oldest_gbif_record(db) is None


def test_oldest_returns_earliest_dated_record(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    upsert_gbif_observations(
        db,
        [
            _obs(gbif_key=1, species="Perca flavescens", observed_on=date(1985, 3, 10)),
            _obs(gbif_key=2, species="Esox lucius", observed_on=date(1972, 7, 4)),
            _obs(gbif_key=3, species="Salvelinus fontinalis", observed_on=date(2001, 9, 1)),
        ],
    )
    result = oldest_gbif_record(db)
    assert result is not None
    assert result.species == "Esox lucius"
    assert result.observed_on == date(1972, 7, 4)


def test_oldest_skips_null_dates(db_factory, tmp_path):
    db = db_factory(path=tmp_path / "test.db")
    upsert_gbif_observations(
        db,
        [
            _obs(gbif_key=1, species="Perca flavescens", observed_on=date(1990, 1, 1)),
            _obs(gbif_key=2, species="Esox lucius", observed_on=None),
        ],
    )
    result = oldest_gbif_record(db)
    assert result is not None
    assert result.species == "Perca flavescens"
