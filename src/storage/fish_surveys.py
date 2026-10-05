"""Fish community survey CRUD via sqlite-utils.

Queries here are keyed on (station_name, visit_date) as much as on coordinates,
because a sampling event is the unit that carries effort. A station's visit is
what makes an absence readable: the species in that group were caught, and the
ones missing from it were not caught at known effort.
"""

from datetime import date, datetime
from typing import Any

from sqlite_utils.db import Database

from src.models.fish_survey import FishSurveyRecord

_KM_PER_DEGREE = 111.0


def upsert_fish_surveys(db: Database, records: list[FishSurveyRecord]) -> int:
    """Write survey records, replacing any with the same record_id.

    Returns the number of rows actually in the table afterwards that were not
    there before, read back rather than assumed. A caller that reports
    len(records) is reporting its intent, not the outcome — which is how a run
    that stored nothing read as "stored 420 survey records".

    insert_all(replace=True), NOT upsert_all. sqlite_utils implements upsert as
    `INSERT OR IGNORE` of the primary key alone followed by an `UPDATE` of the
    rest. This table declares survey_program, station_name, jurisdiction and
    species_common_name NOT NULL, so that PK-only insert violates four
    constraints, OR IGNORE swallows the violation, the UPDATE then matches no
    row, and every record is dropped without an error. replace=True writes the
    whole row in one statement and respects the constraints.
    """
    if not records:
        return 0
    before = db["fish_surveys"].count if "fish_surveys" in db.table_names() else 0
    rows = [_rec_to_row(r) for r in records]
    db["fish_surveys"].insert_all(rows, replace=True)
    db.conn.commit()
    return db["fish_surveys"].count - before


def query_fish_surveys(
    db: Database,
    lat: float,
    lng: float,
    radius_km: float,
    species_filter: str | None = None,
) -> list[FishSurveyRecord]:
    if "fish_surveys" not in db.table_names():
        return []
    deg = radius_km / _KM_PER_DEGREE
    where = "lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?"
    params: list[Any] = [lat - deg, lat + deg, lng - deg, lng + deg]
    if species_filter:
        where += " AND (LOWER(species_common_name) LIKE ? OR LOWER(species_scientific_name) LIKE ?)"
        pattern = f"%{species_filter.lower()}%"
        params += [pattern, pattern]
    rows = db["fish_surveys"].rows_where(where, params)
    return [_row_to_rec(r) for r in rows]


def survey_events_near(
    db: Database,
    lat: float,
    lng: float,
    radius_km: float,
) -> list[dict[str, Any]]:
    """Sampling events near a point, each with the full species list caught.

    The species list is the point: it is a complete catch at known effort, so
    the species absent from it are real negatives at that location. Returns
    newest first.
    """
    if "fish_surveys" not in db.table_names():
        return []
    deg = radius_km / _KM_PER_DEGREE
    rows = db.execute(
        """
        SELECT station_name, visit_date, sample_year, watershed,
               AVG(lat) AS lat, AVG(lng) AS lng,
               COUNT(DISTINCT species_common_name) AS n_species,
               SUM(COALESCE(total_count, 0))       AS n_fish,
               GROUP_CONCAT(DISTINCT species_common_name) AS species
        FROM fish_surveys
        WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?
        GROUP BY station_name, visit_date
        ORDER BY COALESCE(visit_date, '') DESC, station_name
        """,
        [lat - deg, lat + deg, lng - deg, lng + deg],
    ).fetchall()
    return [
        {
            "station_name": r[0],
            "visit_date": r[1],
            "sample_year": r[2],
            "watershed": r[3],
            "lat": r[4],
            "lng": r[5],
            "n_species": r[6],
            "n_fish": r[7],
            "species": sorted((r[8] or "").split(",")) if r[8] else [],
        }
        for r in rows
    ]


def _rec_to_row(rec: FishSurveyRecord) -> dict[str, Any]:
    return {
        "record_id": rec.record_id,
        "survey_program": rec.survey_program,
        "station_name": rec.station_name,
        "visit_date": rec.visit_date.isoformat() if rec.visit_date else None,
        "sample_year": rec.sample_year,
        "watershed": rec.watershed,
        "subwatershed": rec.subwatershed,
        "lat": rec.lat,
        "lng": rec.lng,
        "jurisdiction": rec.jurisdiction,
        "species_common_name": rec.species_common_name,
        "species_scientific_name": rec.species_scientific_name,
        "total_count": rec.total_count,
        "total_weight_g": rec.total_weight_g,
        "sampling_protocol": rec.sampling_protocol,
        "source_url": rec.source_url,
        "ingested_at": rec.ingested_at.isoformat(),
    }


def _row_to_rec(row: dict[str, Any]) -> FishSurveyRecord:
    decoded = dict(row)
    decoded["visit_date"] = (
        date.fromisoformat(decoded["visit_date"]) if decoded.get("visit_date") else None
    )
    decoded["ingested_at"] = datetime.fromisoformat(decoded["ingested_at"])
    return FishSurveyRecord.model_validate(decoded)
