"""PWQMN water quality service — agent and CLI interface."""

import json
import logging
from datetime import date
from statistics import median

from src.services.ingest_runs import record_counts
from src.storage.database import get_db
from src.storage.water_quality import query_water_quality, upsert_water_quality_readings

logger = logging.getLogger(__name__)


def ingest_water_quality_data() -> int:
    """Download and upsert all available PWQMN field data. Returns total records stored."""
    from src.ingest.jurisdictions.ca_on.water_quality import (
        _get_field_data_resources,
        download_field_data,
        load_stations,
        parse_field_data,
    )

    stations = load_stations()
    resources = _get_field_data_resources()
    if not resources:
        record_counts(fetched=1, empty=1)
        logger.warning("No PWQMN field data resources discovered — check CKAN API")
        return 0

    db = get_db()
    total = 0
    for res in resources:
        record_counts(fetched=1)
        csv_path = download_field_data(res["name"], res["url"])
        if csv_path is None:
            record_counts(skipped=1)
            continue
        records = parse_field_data(csv_path, stations)
        record_counts(empty=int(not records))
        if records:
            upsert_water_quality_readings(db, records)
            record_counts(stored=len(records))
            total += len(records)
            logger.info("Upserted %d records from '%s'", len(records), res["name"])
    return total


def get_water_quality_for_agent(
    lat: float,
    lng: float,
    radius_km: float = 50,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """Return JSON water quality summary with habitat assessment for a location."""
    db = get_db()

    d_from = date.fromisoformat(date_from) if date_from else None
    d_to = date.fromisoformat(date_to) if date_to else None

    records = query_water_quality(
        db,
        lat=lat,
        lng=lng,
        radius_km=radius_km,
        date_from=d_from,
        date_to=d_to,
    )

    if not records:
        return json.dumps(
            {
                "query": {
                    "lat": lat,
                    "lng": lng,
                    "radius_km": radius_km,
                    "date_from": date_from,
                    "date_to": date_to,
                },
                "station_count": 0,
                "reading_count": 0,
                "stations": [],
                "summary": {},
                "habitat_assessment": {
                    "ruling_out": [],
                    "note": "No PWQMN water quality readings found within the query area. "
                    "Run `make ingest` to populate the database.",
                },
            }
        )

    # Per-station summary
    by_station: dict[str, list] = {}
    for r in records:
        by_station.setdefault(r.station_id, []).append(r)

    stations_out = []
    for sid, recs in by_station.items():
        first = recs[0]
        dates = sorted(r.sampled_at for r in recs)
        stations_out.append(
            {
                "station_id": sid,
                "station_name": first.station_name,
                "lat": first.lat,
                "lng": first.lng,
                "reading_count": len(recs),
                "date_range": [dates[0].isoformat(), dates[-1].isoformat()],
            }
        )

    # Aggregate parameter stats across all records in area
    do_vals = [r.do_mgl for r in records if r.do_mgl is not None]
    ph_vals = [r.ph for r in records if r.ph is not None]
    temp_vals = [r.temp_c for r in records if r.temp_c is not None]
    cond_vals = [r.conductivity_us_cm for r in records if r.conductivity_us_cm is not None]
    turb_vals = [r.turbidity_fnu for r in records if r.turbidity_fnu is not None]

    # Summer readings (June–August) for coldwater threshold
    summer_temps = [
        r.temp_c for r in records if r.temp_c is not None and r.sampled_at.month in (6, 7, 8)
    ]

    summary: dict = {}
    if do_vals:
        summary["dissolved_oxygen_mgl"] = _stats(do_vals)
    if ph_vals:
        summary["ph"] = _stats(ph_vals)
    if temp_vals:
        summary["temperature_c"] = _stats(temp_vals)
    if summer_temps:
        summary["summer_temperature_c"] = _stats(summer_temps)
    if cond_vals:
        summary["conductivity_us_cm"] = _stats(cond_vals)
    if turb_vals:
        summary["turbidity_fnu"] = _stats(turb_vals)

    habitat_assessment = _assess_habitat(do_vals, ph_vals, summer_temps, cond_vals)

    return json.dumps(
        {
            "query": {
                "lat": lat,
                "lng": lng,
                "radius_km": radius_km,
                "date_from": date_from,
                "date_to": date_to,
            },
            "station_count": len(by_station),
            "reading_count": len(records),
            "stations": stations_out,
            "summary": summary,
            "habitat_assessment": habitat_assessment,
        }
    )


def _stats(vals: list[float]) -> dict:
    return {
        "min": round(min(vals), 2),
        "median": round(median(vals), 2),
        "max": round(max(vals), 2),
        "n": len(vals),
    }


def _assess_habitat(
    do_vals: list[float],
    ph_vals: list[float],
    summer_temps: list[float],
    cond_vals: list[float],
) -> dict:
    ruling_out: list[str] = []
    notes: list[str] = []

    # Dissolved oxygen
    if do_vals:
        median_do = median(do_vals)
        if any(v < 3 for v in do_vals):
            ruling_out.append(
                f"DO below 3 mg/L recorded (min {min(do_vals):.1f} mg/L) — "
                "hypoxic conditions; most species implausible at these levels."
            )
        elif median_do < 5:
            ruling_out.append(
                f"Median DO {median_do:.1f} mg/L — coolwater and coldwater species implausible."
            )
        else:
            notes.append(f"Median DO {median_do:.1f} mg/L — adequate for most Ontario species.")

    # pH
    if ph_vals:
        median_ph = median(ph_vals)
        if median_ph < 5.5:
            ruling_out.append(
                f"Median pH {median_ph:.1f} — salmonid stress; "
                "acid-sensitive species (trout, many darters) likely absent."
            )
        if median_ph > 9.0:
            ruling_out.append(
                f"Median pH {median_ph:.1f} — alkaline stress; sensitive species likely excluded."
            )
        if 5.5 <= median_ph <= 9.0:
            notes.append(  # noqa: E501
                f"Median pH {median_ph:.1f} — within typical Ontario species tolerance range."
            )

    # Summer temperature
    if summer_temps:
        median_summer = median(summer_temps)
        if median_summer > 26:
            ruling_out.append(
                f"Summer median temperature {median_summer:.1f} °C — "
                "thermal stress for most Ontario species; even warmwater species stressed."
            )
        elif median_summer > 22:
            ruling_out.append(
                f"Summer median temperature {median_summer:.1f} °C — "
                "coldwater species implausible; warmwater species (bass, pike, carp) plausible."
            )
        else:
            notes.append(
                f"Summer median temperature {median_summer:.1f} °C — "
                "suitable for coolwater species; marginal for obligate coldwater."
            )

    # Conductivity (informational only — no hard threshold for exclusion)
    if cond_vals:
        median_cond = median(cond_vals)
        notes.append(f"Median conductivity {median_cond:.0f} μS/cm.")

    return {
        "ruling_out": ruling_out,
        "notes": notes,
        "data_caveat": (
            "Water quality parameters constrain which species CAN exist here — "
            "passing these thresholds means the site is habitable, not confirmed occupied. "
            "These are historical measurements; conditions vary seasonally and with flow events."
        ),
    }
