"""Open-Meteo weather and pressure data. Free, no API key required."""

import hashlib
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from src.jurisdictions.geo import jurisdiction_for_coords
from src.models.weather import (
    CurrentConditions,
    ForecastDay,
    PressureTrend,
    WeatherForecast,
)

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_CACHE_DIR = Path("data/cache/open-meteo")
_TTL_CURRENT = 3_600  # 1 hour
_TTL_FORECAST = 21_600  # 6 hours
_TTL_HISTORY = 86_400  # 24 hours
_USER_AGENT = "fishbot/1.0 (personal fishing assistant)"


def get_current_conditions(lat: float, lng: float) -> CurrentConditions:
    params = {
        "latitude": lat,
        "longitude": lng,
        "current": (
            "temperature_2m,relative_humidity_2m,precipitation,"
            "wind_speed_10m,surface_pressure,cloud_cover,weather_code"
        ),
        "wind_speed_unit": "kmh",
    }
    data = _cached_get(_FORECAST_URL, params, _TTL_CURRENT)
    c = data["current"]
    return CurrentConditions(
        lat=lat,
        lng=lng,
        jurisdiction=jurisdiction_for_coords(lat, lng),
        time=datetime.fromisoformat(c["time"]),
        temperature_c=c["temperature_2m"],
        humidity_pct=float(c["relative_humidity_2m"]),
        precipitation_mm=c["precipitation"],
        wind_speed_kmh=c["wind_speed_10m"],
        pressure_hpa=c["surface_pressure"],
        cloud_cover_pct=float(c["cloud_cover"]),
        weather_code=c["weather_code"],
    )


def get_forecast(lat: float, lng: float, days: int = 10) -> WeatherForecast:
    params = {
        "latitude": lat,
        "longitude": lng,
        "daily": (
            "temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,wind_speed_10m_max,weather_code"
        ),
        "forecast_days": days,
        "wind_speed_unit": "kmh",
    }
    data = _cached_get(_FORECAST_URL, params, _TTL_FORECAST)
    d = data["daily"]
    forecast_days = [
        ForecastDay(
            date=date.fromisoformat(d["time"][i]),
            temp_max_c=d["temperature_2m_max"][i],
            temp_min_c=d["temperature_2m_min"][i],
            precipitation_sum_mm=d["precipitation_sum"][i],
            wind_speed_max_kmh=d["wind_speed_10m_max"][i],
            weather_code=d["weather_code"][i],
        )
        for i in range(len(d["time"]))
    ]
    return WeatherForecast(
        lat=lat,
        lng=lng,
        jurisdiction=jurisdiction_for_coords(lat, lng),
        days=forecast_days,
    )


def get_recent_history(lat: float, lng: float, days_back: int = 7) -> list[tuple[datetime, float]]:
    today = date.today()
    start = (today - timedelta(days=days_back)).isoformat()
    end = (today - timedelta(days=1)).isoformat()
    params = {
        "latitude": lat,
        "longitude": lng,
        "hourly": "surface_pressure",
        "start_date": start,
        "end_date": end,
    }
    data = _cached_get(_ARCHIVE_URL, params, _TTL_HISTORY)
    h = data["hourly"]
    return [
        (datetime.fromisoformat(h["time"][i]), float(h["surface_pressure"][i]))
        for i in range(len(h["time"]))
        if h["surface_pressure"][i] is not None
    ]


def compute_pressure_trend(lat: float, lng: float) -> PressureTrend:
    current = get_current_conditions(lat, lng)
    readings = get_recent_history(lat, lng, days_back=3)
    return _compute_trend_from_readings(lat, lng, current, readings)


def _compute_trend_from_readings(
    lat: float,
    lng: float,
    current: CurrentConditions,
    readings: list[tuple[datetime, float]],
) -> PressureTrend:
    now = current.time

    def _closest_pressure(target: datetime) -> float:
        if not readings:
            return current.pressure_hpa
        return min(readings, key=lambda r: abs((r[0] - target).total_seconds()))[1]

    p24 = _closest_pressure(now - timedelta(hours=24))
    p48 = _closest_pressure(now - timedelta(hours=48))
    delta_24h = round(current.pressure_hpa - p24, 1)
    delta_48h = round(current.pressure_hpa - p48, 1)

    if delta_24h > 1.5:
        trend = "rising"
    elif delta_24h < -1.5:
        trend = "falling"
    else:
        trend = "steady"

    return PressureTrend(
        lat=lat,
        lng=lng,
        jurisdiction=current.jurisdiction,
        trend=trend,
        current_hpa=current.pressure_hpa,
        delta_24h_hpa=delta_24h,
        delta_48h_hpa=delta_48h,
    )


def get_archive_hourly(
    lat: float,
    lng: float,
    start_date: str,
    end_date: str,
    variables: list[str],
    timeout: float = 30,
    wind_speed_unit: str | None = None,
) -> dict:
    """Hourly archive weather for a date range, through the cache (24 h).

    Used when a trip is logged, where the caller has a short budget: the
    politeness delay is skipped and the timeout is the caller's. Times come back
    in America/Toronto, as the trip enrichment has always read them.
    """
    params = {
        "latitude": lat,
        "longitude": lng,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(variables),
        "timezone": "America/Toronto",
    }
    if wind_speed_unit:
        params["wind_speed_unit"] = wind_speed_unit
    return _cached_get(_ARCHIVE_URL, params, _TTL_HISTORY, timeout=timeout, delay=0.0)


def _cached_get(
    url: str, params: dict, ttl_seconds: int, timeout: float = 30, delay: float = 0.5
) -> dict:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key_data = url + json.dumps(params, sort_keys=True)
    key = hashlib.sha256(key_data.encode()).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < ttl_seconds:
            return json.loads(cache_file.read_text())

    if delay:
        time.sleep(delay)
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
