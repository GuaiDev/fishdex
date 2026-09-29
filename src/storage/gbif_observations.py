"""GBIF observation CRUD via sqlite-utils."""

from datetime import date, datetime, timedelta
from typing import Any

from sqlite_utils.db import Database

from src.models.gbif_observation import GBIFObservation

_KM_PER_DEGREE = 111.0


def upsert_gbif_observations(db: Database, observations: list[GBIFObservation]) -> None:
    rows = [_obs_to_row(o) for o in observations]
    db["gbif_observations"].upsert_all(rows, pk="gbif_key")


def query_gbif_observations(
    db: Database,
    lat: float,
    lng: float,
    radius_km: float,
    days_back: int | None = None,
    species_filter: str | None = None,
) -> list[GBIFObservation]:
    deg = radius_km / _KM_PER_DEGREE

    where = "lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?"
    params: list[Any] = [lat - deg, lat + deg, lng - deg, lng + deg]

    if days_back is not None:
        since = (date.today() - timedelta(days=days_back)).isoformat()
        # Include records with no date — museum specimens are always relevant
        where += " AND (observed_on IS NULL OR observed_on >= ?)"
        params.append(since)

    if species_filter:
        where += " AND (LOWER(species) LIKE ? OR LOWER(common_name) LIKE ?)"
        pattern = f"%{species_filter.lower()}%"
        params += [pattern, pattern]

    rows = db["gbif_observations"].rows_where(where, params, order_by="observed_on desc")
    return [_row_to_obs(r) for r in rows]


def oldest_gbif_record(db: Database) -> GBIFObservation | None:
    rows = list(
        db["gbif_observations"].rows_where(
            "observed_on IS NOT NULL",
            order_by="observed_on asc",
            limit=1,
        )
    )
    return _row_to_obs(rows[0]) if rows else None


def _obs_to_row(obs: GBIFObservation) -> dict[str, Any]:
    return {
        "gbif_key": obs.gbif_key,
        "species": obs.species,
        "common_name": obs.common_name,
        "taxon_key": obs.taxon_key,
        "lat": obs.lat,
        "lng": obs.lng,
        "observed_on": obs.observed_on.isoformat() if obs.observed_on else None,
        "country_code": obs.country_code,
        "dataset_name": obs.dataset_name,
        "basis_of_record": obs.basis_of_record,
        "coordinate_uncertainty_m": obs.coordinate_uncertainty_m,
        "jurisdiction": obs.jurisdiction,
        "ingested_at": obs.ingested_at.isoformat(),
        "license_code": obs.license_code,
        "license_uri": obs.license_uri,
        "dataset_key": obs.dataset_key,
        "rights_holder": obs.rights_holder,
        "recorded_by": obs.recorded_by,
        "sampling_protocol": obs.sampling_protocol,
        "event_id": obs.event_id,
        "sampling_effort": obs.sampling_effort,
        "sample_size_value": obs.sample_size_value,
        "sample_size_unit": obs.sample_size_unit,
    }


def _row_to_obs(row: dict[str, Any]) -> GBIFObservation:
    decoded = dict(row)
    decoded["observed_on"] = (
        date.fromisoformat(decoded["observed_on"]) if decoded.get("observed_on") else None
    )
    decoded["ingested_at"] = datetime.fromisoformat(decoded["ingested_at"])
    return GBIFObservation.model_validate(decoded)
