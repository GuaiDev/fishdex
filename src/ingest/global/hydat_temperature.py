"""Stream thermal regime derived from PWQMN water quality data.

Classifies monitoring stations as coldwater / coolwater / warmwater using
July+August temperature readings already in the app DB. No external downloads.

Run via: make ingest-hydat (after make ingest)
"""

from sqlite_utils import Database

from src.models.stream_temperature import StreamTemperatureSummary
from src.storage.stream_temperature import upsert_temperature_summaries

_SPECIES_NOTES: dict[str, str] = {
    "coldwater": (
        "Summer temps support brook trout, lake trout, and other salmonids. "
        "Darters and sensitive cyprinids plausible."
    ),
    "coolwater": (
        "Suitable for walleye, pike, bass. "
        "Marginal for salmonids — check for cold groundwater refugia."
    ),
    "warmwater": ("Carp, catfish, bass, sunfish. Too warm for sustained salmonid presence."),
    "unknown": "Insufficient temperature data to classify thermal regime.",
}


def derive_from_pwqmn(db: Database) -> int:
    """Classify thermal regime for PWQMN stations from existing water quality readings.

    Requires at least 3 July+August temp_c readings per station. Returns the count
    of station summaries upserted into stream_temperature_summaries.
    """
    from src.services.ingest_runs import record_counts

    if "water_quality_readings" not in db.table_names():
        record_counts(fetched=1, empty=1)
        return 0

    rows = db.execute(
        """
        SELECT
            station_id,
            MAX(station_name)                              AS station_name,
            MAX(lat)                                       AS lat,
            MAX(lng)                                       AS lng,
            AVG(temp_c)                                    AS summer_mean_c,
            MAX(temp_c)                                    AS summer_max_c,
            COUNT(DISTINCT strftime('%Y', sampled_at))     AS years_of_data
        FROM water_quality_readings
        WHERE temp_c IS NOT NULL
          AND CAST(strftime('%m', sampled_at) AS INTEGER) IN (7, 8)
        GROUP BY station_id
        HAVING COUNT(*) >= 3
        """
    ).fetchall()

    record_counts(fetched=len(rows) or 1, empty=int(not rows))
    summaries: list[StreamTemperatureSummary] = []
    for station_id, station_name, lat, lng, summer_mean_c, summer_max_c, years_of_data in rows:
        mean = round(summer_mean_c, 2) if summer_mean_c is not None else None
        max_c = round(summer_max_c, 2) if summer_max_c is not None else None
        regime = _classify_regime(mean)
        summaries.append(
            StreamTemperatureSummary(
                station_id=station_id,
                station_name=station_name,
                lat=lat,
                lng=lng,
                jurisdiction="CA-ON",
                summer_mean_c=mean,
                summer_max_c=max_c,
                thermal_regime=regime,
                years_of_data=years_of_data,
                species_notes=_species_notes(regime),
            )
        )

    if summaries:
        upsert_temperature_summaries(db, summaries)
    record_counts(stored=len(summaries))
    return len(summaries)


def _classify_regime(summer_mean_c: float | None) -> str:
    if summer_mean_c is None:
        return "unknown"
    if summer_mean_c < 18.0:
        return "coldwater"
    if summer_mean_c <= 23.0:
        return "coolwater"
    return "warmwater"


def _species_notes(regime: str) -> str:
    return _SPECIES_NOTES.get(regime, _SPECIES_NOTES["unknown"])
