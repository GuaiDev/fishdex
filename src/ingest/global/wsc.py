"""Water Survey of Canada hydrometric data. Free, no API key required."""

import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from src.jurisdictions.geo import jurisdiction_for_coords
from src.models.stream_gauge import StreamGaugeReading

_STATIONS_URL = "https://api.weather.gc.ca/collections/hydrometric-stations/items"
_REALTIME_URL = "https://api.weather.gc.ca/collections/hydrometric-realtime/items"
_CACHE_DIR = Path("data/cache/wsc")
_TTL_STATIONS = 86_400  # 24 hours — stations don't move
_TTL_REALTIME = 3_600  # 1 hour — data refreshes every 5 minutes at source
_KM_PER_DEGREE = 111.0
_USER_AGENT = "fishbot/1.0 (personal fishing assistant)"
_HTTP_TIMEOUT_S = 30

# Trend thresholds
_LEVEL_TREND_THRESHOLD_M = 0.05  # 0.05m change over 3hr
_DISCHARGE_TREND_THRESHOLD_PCT = 2.0  # 2% change over 3hr


def fetch_nearby_stations(
    lat: float, lng: float, radius_km: float = 50, timeout: float = _HTTP_TIMEOUT_S
) -> list[dict]:
    """Return active WSC stations within radius_km of lat/lng. Cached 24hr.

    Returns a list of normalised dicts with keys:
    STATION_NUMBER, STATION_NAME, PROV_TERR_STATE_LOC, LATITUDE, LONGITUDE.
    """
    deg = radius_km / _KM_PER_DEGREE
    params = {
        "f": "json",
        "bbox": f"{lng - deg},{lat - deg},{lng + deg},{lat + deg}",
        "limit": 500,
    }
    data = _cached_get(_STATIONS_URL, params, _TTL_STATIONS, timeout)

    stations = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        # API returns STATUS_EN:"Active" / "Inactive" — STATUS_CD param has no effect
        if props.get("STATUS_EN") != "Active":
            continue
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates")  # GeoJSON order: [longitude, latitude]
        if not coords or len(coords) < 2:
            continue
        stations.append(
            {
                "STATION_NUMBER": props.get("STATION_NUMBER") or props.get("IDENTIFIER", ""),
                "STATION_NAME": props.get("STATION_NAME", ""),
                "PROV_TERR_STATE_LOC": props.get("PROV_TERR_STATE_LOC", ""),
                "LATITUDE": coords[1],
                "LONGITUDE": coords[0],
                "REAL_TIME": props.get("REAL_TIME", 0),
            }
        )
    return stations


def fetch_station_reading(
    station_id: str, lat: float, lng: float, timeout: float = _HTTP_TIMEOUT_S
) -> StreamGaugeReading | None:
    """Fetch current reading + trend for a single WSC station. Cached 1hr.

    Returns None if the station has no recent data or is offline.
    """
    params = {
        "f": "json",
        "STATION_NUMBER": station_id,
        "sortby": "-DATETIME",
        "limit": 500,
    }
    data = _cached_get(_REALTIME_URL, params, _TTL_REALTIME, timeout)
    features = data.get("features", [])
    if not features:
        return None

    readings = _extract_readings(features)
    if not readings:
        return None

    # API returns descending by DATETIME; sort defensively
    readings.sort(key=lambda r: r[0], reverse=True)
    current_dt, current_level, current_discharge, level_grade = readings[0]

    if current_level is None and current_discharge is None:
        return None  # gauge offline

    # Find reading closest to 3 hours before current for trend
    target_3hr_ago = current_dt - timedelta(hours=3)
    ref = min(
        readings[1:],
        key=lambda r: abs((r[0] - target_3hr_ago).total_seconds()),
        default=None,
    )

    level_trend = _compute_level_trend(current_level, ref[1] if ref else None)
    discharge_trend = _compute_discharge_trend(current_discharge, ref[2] if ref else None)

    # 24hr means for condition classification in service layer
    level_vals = [r[1] for r in readings if r[1] is not None]
    discharge_vals = [r[2] for r in readings if r[2] is not None]
    level_mean = sum(level_vals) / len(level_vals) if level_vals else None
    discharge_mean = sum(discharge_vals) / len(discharge_vals) if discharge_vals else None

    station_name = ""
    for f in features:
        name = f.get("properties", {}).get("STATION_NAME")
        if name:
            station_name = name
            break

    return StreamGaugeReading(
        station_id=station_id,
        station_name=station_name,
        river_name=_extract_river_name(station_name),
        lat=lat,
        lng=lng,
        jurisdiction=jurisdiction_for_coords(lat, lng),
        water_level_m=current_level,
        discharge_cms=current_discharge,
        level_trend=level_trend,
        discharge_trend=discharge_trend,
        level_grade=level_grade,
        reading_datetime=current_dt,
        level_24hr_mean_m=level_mean,
        discharge_24hr_mean_cms=discharge_mean,
    )


def _extract_readings(
    features: list[dict],
) -> list[tuple[datetime, float | None, float | None, str | None]]:
    result = []
    for f in features:
        props = f.get("properties", {})
        # Real-time API uses "DATETIME" (not "DATE_TIME")
        dt_str = props.get("DATETIME") or props.get("DATE_TIME")
        if not dt_str:
            continue
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        level = props.get("LEVEL")
        discharge = props.get("DISCHARGE")
        # Grade field: real API uses LEVEL_SYMBOL_EN; fixture fallback uses LEVEL_GRADE
        grade = props.get("LEVEL_SYMBOL_EN") or props.get("LEVEL_GRADE")
        result.append((dt, _as_float(level), _as_float(discharge), grade))
    return result


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _compute_level_trend(current: float | None, reference: float | None) -> str | None:
    if current is None or reference is None:
        return None
    delta = current - reference
    if delta > _LEVEL_TREND_THRESHOLD_M:
        return "rising"
    if delta < -_LEVEL_TREND_THRESHOLD_M:
        return "falling"
    return "stable"


def _compute_discharge_trend(current: float | None, reference: float | None) -> str | None:
    if current is None or reference is None or reference == 0:
        return None
    pct_change = (current - reference) / abs(reference) * 100
    if pct_change > _DISCHARGE_TREND_THRESHOLD_PCT:
        return "rising"
    if pct_change < -_DISCHARGE_TREND_THRESHOLD_PCT:
        return "falling"
    return "stable"


def _extract_river_name(station_name: str) -> str | None:
    """Extract river name from WSC station name format 'RIVER NAME AT/NEAR LOCATION'."""
    upper = station_name.upper()
    for sep in (" AT ", " NEAR ", " BELOW ", " ABOVE "):
        if sep in upper:
            idx = upper.index(sep)
            return station_name[:idx].title()
    return None


def _cached_get(url: str, params: dict, ttl_seconds: int, timeout: float = _HTTP_TIMEOUT_S) -> dict:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key_data = url + json.dumps(params, sort_keys=True)
    key = hashlib.sha256(key_data.encode()).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < ttl_seconds:
            return json.loads(cache_file.read_text())

    response = httpx.get(
        url,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data))
    return data
