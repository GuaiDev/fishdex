"""Stream gauge service — bridges WSC ingest to the agent.

Designed for future multi-provider support: USGS can be added here
without changing the agent tool signature.
"""

import importlib
import json
import math
from collections.abc import Sequence

from src.models.stream_gauge import NearestGauge, StreamGaugeSummary
from src.services.water_names import same_water
from src.storage.database import cleanup_old_gauge_readings, get_db

_wsc = importlib.import_module("src.ingest.global.wsc")


def get_stream_conditions_for_agent(
    lat: float, lng: float, radius_km: float = 50, max_stations: int = 3
) -> str:
    """Return JSON with current gauge readings for up to max_stations nearest stations."""
    stations = _wsc.fetch_nearby_stations(lat, lng, radius_km)
    if not stations:
        return json.dumps(
            {"stations": [], "note": f"No active WSC gauges found within {radius_km:.0f}km."}
        )

    # Annotate with distance and sort
    annotated = []
    for s in stations:
        s_lat = s.get("LATITUDE") or s.get("latitude")
        s_lng = s.get("LONGITUDE") or s.get("longitude")
        if s_lat is None or s_lng is None:
            continue
        dist = _haversine_km(lat, lng, float(s_lat), float(s_lng))
        annotated.append((dist, s))

    annotated.sort(key=lambda x: x[0])
    nearest = annotated[:max_stations]

    summaries = []
    for dist_km, station in nearest:
        station_id = station.get("STATION_NUMBER") or station.get("station_number")
        if not station_id:
            continue
        s_lat = float(station.get("LATITUDE") or station.get("latitude"))
        s_lng = float(station.get("LONGITUDE") or station.get("longitude"))

        reading = _wsc.fetch_station_reading(station_id, s_lat, s_lng)
        if reading is None:
            continue

        condition_note = _build_condition_note(reading)
        fishing_note = _build_fishing_note(reading, condition_note)

        summary = StreamGaugeSummary(
            station_id=reading.station_id,
            station_name=reading.station_name,
            river_name=reading.river_name,
            current_level_m=reading.water_level_m,
            current_discharge_cms=reading.discharge_cms,
            level_trend=reading.level_trend,
            discharge_trend=reading.discharge_trend,
            condition_note=condition_note,
            fishing_note=fishing_note,
            distance_km=round(dist_km, 1),
            reading_datetime=reading.reading_datetime,
            fetched_at=reading.fetched_at,
        )
        summaries.append(summary)

    if not summaries:
        return json.dumps(
            {"stations": [], "note": f"No gauge data available within {radius_km:.0f}km right now."}
        )

    return json.dumps(
        {
            "stations": [
                {
                    "station_id": s.station_id,
                    "station_name": s.station_name,
                    "river_name": s.river_name,
                    "distance_km": s.distance_km,
                    "current_level_m": s.current_level_m,
                    "current_discharge_cms": s.current_discharge_cms,
                    "level_trend": s.level_trend,
                    "discharge_trend": s.discharge_trend,
                    "condition_note": s.condition_note,
                    "fishing_note": s.fishing_note,
                    "reading_datetime": s.reading_datetime.isoformat(),
                    "fetched_at": s.fetched_at.isoformat(),
                }
                for s in summaries
            ]
        }
    )


# A gauge on the pin's own river is worth preferring over a nearer one on a
# neighbouring creek, but only up to a point: past this, the river has changed
# enough that its gauge no longer says what the water at the pin is doing.
OWN_WATER_MAX_KM = 25.0


def nearest_gauge(
    lat: float,
    lng: float,
    radius_km: float = 50,
    max_stations: int = 3,
    timeout: float = 30,
    own_water: Sequence[str] = (),
    own_water_max_km: float = OWN_WATER_MAX_KM,
) -> NearestGauge | None:
    """The best active station that has a current reading, with its distance.

    `own_water` is the name(s) of the watercourse the place is on. When given,
    a station on that same water (matched by name, see `src.services.water_names`) within
    `own_water_max_km` wins even if a gauge on another creek is closer, and the
    result says `same_water=True`. When none reports, the nearest station that
    does is returned with `same_water=False`: the caller must say it is on
    another water. With no `own_water`, `same_water` is None.

    Tries the closest `max_stations` candidates in order and takes the first
    that reports, because the closest gauge is sometimes offline. Returns None
    when no station in range reports anything; raises (httpx errors) when the
    network cannot be reached, so callers can tell "nothing here" from "try
    again". Stations are dicts from a gauge adapter: STATION_NUMBER, LATITUDE,
    LONGITUDE and, for river matching, RIVER_NAME.
    """
    stations = _wsc.fetch_nearby_stations(lat, lng, radius_km, timeout=timeout)
    ranked = []
    for s in stations:
        s_lat = s.get("LATITUDE") or s.get("latitude")
        s_lng = s.get("LONGITUDE") or s.get("longitude")
        station_id = s.get("STATION_NUMBER") or s.get("station_number")
        if s_lat is None or s_lng is None or not station_id:
            continue
        ranked.append((_haversine_km(lat, lng, float(s_lat), float(s_lng)), station_id, s))
    ranked.sort(key=lambda r: r[0])

    wanted = [n for n in own_water if n]
    if wanted:
        on_own = [
            r
            for r in ranked
            if r[0] <= own_water_max_km
            and any(same_water(r[2].get("RIVER_NAME"), name) for name in wanted)
        ]
        found = _first_reporting(on_own[:max_stations], timeout)
        if found is not None:
            return found.model_copy(update={"same_water": True})

    found = _first_reporting(ranked[:max_stations], timeout)
    if found is not None and wanted:
        # Still checked by name: the nearest reporting gauge can be on the
        # pin's river beyond the own-water cap.
        on_it = any(same_water(found.reading.river_name, name) for name in wanted)
        return found.model_copy(update={"same_water": on_it})
    return found


def _first_reporting(candidates: list, timeout: float) -> NearestGauge | None:
    for dist_km, station_id, s in candidates:
        s_lat = float(s.get("LATITUDE") or s.get("latitude"))
        s_lng = float(s.get("LONGITUDE") or s.get("longitude"))
        reading = _wsc.fetch_station_reading(station_id, s_lat, s_lng, timeout=timeout)
        if reading is not None:
            return NearestGauge(reading=reading, distance_km=round(dist_km, 1))
    return None


def fetch_and_store(lat: float, lng: float, radius_km: float = 50) -> int:
    """Fetch nearby gauge readings and store them. Called by the ingest CLI."""
    stations = _wsc.fetch_nearby_stations(lat, lng, radius_km)
    if not stations:
        return 0

    db = get_db()
    cleanup_old_gauge_readings(db)

    count = 0
    for station in stations:
        station_id = station.get("STATION_NUMBER") or station.get("station_number")
        s_lat = station.get("LATITUDE") or station.get("latitude")
        s_lng = station.get("LONGITUDE") or station.get("longitude")
        if not station_id or s_lat is None or s_lng is None:
            continue

        reading = _wsc.fetch_station_reading(station_id, float(s_lat), float(s_lng))
        if reading is None:
            continue

        try:
            db["stream_gauge_readings"].insert(
                {
                    "station_id": reading.station_id,
                    "station_name": reading.station_name,
                    "river_name": reading.river_name,
                    "lat": reading.lat,
                    "lng": reading.lng,
                    "jurisdiction": reading.jurisdiction,
                    "water_level_m": reading.water_level_m,
                    "discharge_cms": reading.discharge_cms,
                    "level_trend": reading.level_trend,
                    "discharge_trend": reading.discharge_trend,
                    "level_grade": reading.level_grade,
                    "reading_datetime": reading.reading_datetime.isoformat(),
                    "fetched_at": reading.fetched_at.isoformat(),
                },
                ignore=True,  # unique index on (station_id, reading_datetime) prevents dupes
            )
            count += 1
        except Exception:
            pass

    return count


def _build_condition_note(reading) -> str:
    trend = reading.discharge_trend or reading.level_trend

    # Classify level vs recent baseline
    level_class = _classify_level(reading)

    if level_class == "elevated" and trend == "rising":
        return "elevated and rising"
    if level_class == "elevated" and trend == "falling":
        return "elevated and falling"
    if level_class == "elevated" and trend == "stable":
        return "elevated and stable"
    if level_class == "low" and trend == "falling":
        return "low and falling"
    if level_class == "low" and trend == "rising":
        return "low and rising"
    if level_class == "low" and trend == "stable":
        return "low and stable"
    if trend == "rising":
        return "normal and rising"
    if trend == "falling":
        return "normal and falling"
    return "normal and stable"


def _classify_level(reading) -> str:
    """Classify current discharge (or level) as elevated/normal/low vs 24hr mean."""
    current = reading.discharge_cms
    mean = reading.discharge_24hr_mean_cms
    if current is not None and mean is not None and mean > 0:
        ratio = current / mean
        if ratio > 1.15:
            return "elevated"
        if ratio < 0.85:
            return "low"
        return "normal"
    # Fall back to level if discharge unavailable
    current = reading.water_level_m
    mean = reading.level_24hr_mean_m
    if current is not None and mean is not None and mean > 0:
        delta = current - mean
        if delta > 0.1:
            return "elevated"
        if delta < -0.1:
            return "low"
    return "normal"


def _build_fishing_note(reading, condition_note: str) -> str:
    trend = reading.discharge_trend or reading.level_trend
    level_class = _classify_level(reading)

    if level_class == "elevated" and trend == "rising":
        return (
            "Rising, elevated water — fish tight to structure, avoid main current, "
            "try slower backwater areas. Clarity likely reduced."
        )
    if level_class == "elevated" and trend == "falling":
        return (
            "Dropping from elevated — often a productive window as fish become "
            "active in cleaner water."
        )
    if level_class == "elevated":
        return (
            "High, stable flow — fish are pushed to edges and structure. "
            "Target slack water behind boulders or inside bends."
        )
    if level_class == "low" and trend == "falling":
        return (
            "Low and dropping — fish are stressed and concentrated in deeper pools. "
            "Pressure carefully."
        )
    if level_class == "low":
        return (
            "Low, stable flow — fish are holding in the deepest available water. "
            "Downsize gear and approach slowly."
        )
    if trend == "rising":
        return (
            "Rising but not yet elevated — fish may be starting to move. "
            "Worth monitoring; conditions can change quickly."
        )
    if trend == "falling":
        return (
            "Dropping from normal — conditions are stabilizing. "
            "Fishing is often improving as water clears."
        )
    return "Normal stable flow — standard conditions for this system."


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
