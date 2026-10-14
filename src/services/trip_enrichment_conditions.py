"""
Auto-enrichment service: fetches environmental conditions at time of a
fishing session and stores them in session_conditions.

Three data layers:
1. Open-Meteo archive API (weather at exact timestamp + coords), through the cache
2. Local PWQMN database (water quality at nearest station/date)
3. The nearest WSC gauge's current flow and level, snapshotted as it was at log time

Plus anomaly detection vs PWQMN historical baseline.
"""

import importlib
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlite_utils import Database

from src.models.freshness import SNAPSHOT_MAX_GAP_S, parse_observed_at

_weather = importlib.import_module("src.ingest.global.weather")

# Naive trip times are the angler's wall clock, which is what the archive
# request has always been made in.
_LOCAL_TZ = ZoneInfo("America/Toronto")


def enrich_session_conditions(
    db: Database,
    session_id: int,
    lat: float,
    lng: float,
    session_datetime: datetime,
    timeout_seconds: float = 3.0,
    *,
    user_id: int,
) -> dict:
    """
    Fetch and store environmental conditions for a session.
    Returns dict of what was fetched (or errors).
    Non-fatal: always returns, never raises.

    `user_id` is the owner of the session and is written to the row. It is
    required, not defaulted: the column's own default is user 1, and a caller
    that forgot it used to stamp every other user's conditions as user 1's.
    """
    result = {
        "session_id": session_id,
        "lat": lat,
        "lng": lng,
        "session_datetime": session_datetime.isoformat(),
    }

    weather = None
    pwqmn = None
    sources = []

    # Layer 1: Open-Meteo weather
    try:
        weather = _fetch_open_meteo(lat, lng, session_datetime, timeout_seconds)
        if weather:
            sources.append("open_meteo")
            result["weather"] = weather
    except Exception as e:
        result["weather_error"] = str(e)

    # Layer 2: PWQMN water quality
    try:
        pwqmn = _fetch_pwqmn_nearest(db, lat, lng, session_datetime)
        if pwqmn:
            sources.append("pwqmn")
            result["pwqmn"] = pwqmn
    except Exception as e:
        result["pwqmn_error"] = str(e)

    # Layer 3: nearest WSC gauge, as it reads now
    gauge = None
    try:
        gauge = _snapshot_gauge(lat, lng, session_datetime, timeout_seconds)
        if gauge:
            sources.append("wsc")
            result["gauge"] = gauge
    except Exception as e:
        result["gauge_error"] = str(e)

    # Anomaly detection
    anomalies = {}
    try:
        anomalies = _compute_anomalies(db, lat, lng, session_datetime, weather, pwqmn)
        result["anomalies"] = anomalies
    except Exception as e:
        result["anomaly_error"] = str(e)

    # Derived features
    season = _get_season(session_datetime.month)
    time_of_day = _get_time_of_day(session_datetime.hour)
    moon_phase = _compute_moon_phase(session_datetime)
    days_since_rain = None
    if weather and weather.get("precip_history"):
        days_since_rain = _compute_days_since_rain(weather["precip_history"])

    # Store in session_conditions
    try:
        row = {
            "session_id": session_id,
            "user_id": user_id,
            "enrichment_source": "+".join(sources) if sources else "none",
            "season": season,
            "time_of_day": time_of_day,
            "moon_phase": round(moon_phase, 3),
            "days_since_rain": days_since_rain,
        }

        if weather:
            row.update(
                {
                    "air_temp_c": weather.get("air_temp_c"),
                    "feels_like_c": weather.get("feels_like_c"),
                    "pressure_hpa": weather.get("pressure_hpa"),
                    "precip_mm": weather.get("precip_mm"),
                    "cloud_cover_pct": weather.get("cloud_cover_pct"),
                    "wind_speed_kmh": weather.get("wind_speed_kmh"),
                    "wind_direction_deg": weather.get("wind_direction_deg"),
                    "weather_code": weather.get("weather_code"),
                }
            )

        if pwqmn:
            row.update(
                {
                    "water_temp_c": pwqmn.get("temp_c"),
                    "do_mgl": pwqmn.get("do_mgl"),
                    "ph": pwqmn.get("ph"),
                    "conductivity_us_cm": pwqmn.get("conductivity_us_cm"),
                    "turbidity_fnu": pwqmn.get("turbidity_fnu"),
                    "pwqmn_station_name": pwqmn.get("station_name"),
                    "pwqmn_station_dist_km": pwqmn.get("dist_km"),
                    "pwqmn_sampled_at": pwqmn.get("sampled_at"),
                }
            )

        if gauge:
            row.update(
                {
                    "flow_cms": gauge["flow_cms"],
                    "flow_trend": gauge["flow_trend"],
                    "level_m": gauge["level_m"],
                    "gauge_station_id": gauge["station_id"],
                    "gauge_station_name": gauge["station_name"],
                    "gauge_dist_km": gauge["dist_km"],
                    "gauge_observed_at": gauge["observed_at"],
                }
            )

        if anomalies:
            row.update(
                {
                    "water_temp_anomaly_c": anomalies.get("water_temp_anomaly_c"),
                    "air_temp_anomaly_c": anomalies.get("air_temp_anomaly_c"),
                    "anomaly_flag": anomalies.get("anomaly_flag"),
                }
            )

        db["session_conditions"].upsert(row, pk="session_id")
        result["stored"] = True
    except Exception as e:
        result["store_error"] = str(e)

    return result


def _fetch_open_meteo(lat: float, lng: float, dt: datetime, timeout: float) -> dict | None:
    """
    Fetch hourly weather from Open-Meteo archive API for the session datetime.
    Also fetches 7 days prior for days_since_rain calculation.
    No API key required.
    """
    date_str = dt.strftime("%Y-%m-%d")
    start_date = (dt - timedelta(days=7)).strftime("%Y-%m-%d")

    variables = [
        "temperature_2m",
        "apparent_temperature",
        "surface_pressure",
        "precipitation",
        "cloud_cover",
        "wind_speed_10m",
        "wind_direction_10m",
        "weather_code",
    ]

    try:
        data = _weather.get_archive_hourly(
            lat,
            lng,
            start_date,
            date_str,
            variables,
            timeout=timeout,
            wind_speed_unit="kmh",
        )
    except Exception as e:
        raise RuntimeError(f"Open-Meteo fetch failed: {e}")

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return None

    # Find the index closest to session hour
    target = dt.strftime("%Y-%m-%dT%H:00")
    try:
        idx = times.index(target)
    except ValueError:
        target_dt = datetime.fromisoformat(target)
        idx = min(
            range(len(times)), key=lambda i: abs(datetime.fromisoformat(times[i]) - target_dt)
        )

    def val(key):
        arr = hourly.get(key, [])
        return arr[idx] if idx < len(arr) else None

    precip_arr = hourly.get("precipitation", [])

    return {
        "air_temp_c": val("temperature_2m"),
        "feels_like_c": val("apparent_temperature"),
        "pressure_hpa": val("surface_pressure"),
        "precip_mm": val("precipitation"),
        "cloud_cover_pct": val("cloud_cover"),
        "wind_speed_kmh": val("wind_speed_10m"),
        "wind_direction_deg": val("wind_direction_10m"),
        "weather_code": val("weather_code"),
        "precip_history": precip_arr,
    }


def _snapshot_gauge(lat: float, lng: float, dt: datetime, timeout: float) -> dict | None:
    """The nearest gauge's current reading, if it describes this trip's time.

    The WSC feed only gives "now". It is the trip's flow only when the trip is
    now-ish, so a reading further than SNAPSHOT_MAX_GAP_S from the trip is
    dropped rather than recorded as something it is not. Returns None when no
    gauge in range reports, or when the reading does not fit the trip.
    """
    from src.services.stream_gauge import nearest_gauge

    found = nearest_gauge(lat, lng, timeout=timeout)
    if found is None:
        return None
    r = found.reading
    trip = dt if dt.tzinfo else dt.replace(tzinfo=_LOCAL_TZ)
    observed = parse_observed_at(r.reading_datetime.isoformat())
    if observed is None or abs((observed - trip.astimezone(UTC)).total_seconds()) > (
        SNAPSHOT_MAX_GAP_S
    ):
        return None
    trend = r.discharge_trend or r.level_trend
    return {
        "flow_cms": r.discharge_cms,
        "flow_trend": {"stable": "steady"}.get(trend, trend),
        "level_m": r.water_level_m,
        "station_id": r.station_id,
        "station_name": r.station_name,
        "dist_km": found.distance_km,
        "observed_at": observed.isoformat(),
    }


def _fetch_pwqmn_nearest(
    db: Database,
    lat: float,
    lng: float,
    dt: datetime,
    radius_km: float = 50.0,
    days_tolerance: int = 45,
) -> dict | None:
    """
    Find the nearest PWQMN water quality reading to this location and datetime.
    Searches within radius_km and days_tolerance days of the session date.
    """
    start = (dt - timedelta(days=days_tolerance)).strftime("%Y-%m-%d")
    end = (dt + timedelta(days=days_tolerance)).strftime("%Y-%m-%d")

    rows = list(
        db.execute(
            """
        SELECT station_name, lat, lng, sampled_at,
               temp_c, do_mgl, ph, conductivity_us_cm, turbidity_fnu
        FROM water_quality_readings
        WHERE sampled_at BETWEEN ? AND ?
          AND lat IS NOT NULL AND lng IS NOT NULL
    """,
            [start, end],
        ).fetchall()
    )

    if not rows:
        return None

    best = None
    best_score = float("inf")

    for r in rows:
        dist = _haversine_km(lat, lng, r[1], r[2])
        if dist > radius_km:
            continue
        try:
            days_diff = abs((dt.date() - datetime.fromisoformat(r[3]).date()).days)
        except Exception:
            days_diff = 999
        score = dist + days_diff * 0.1
        if score < best_score:
            best_score = score
            best = r

    if not best:
        return None

    dist_km = _haversine_km(lat, lng, best[1], best[2])
    return {
        "station_name": best[0],
        "dist_km": round(dist_km, 1),
        "sampled_at": best[3],
        "temp_c": best[4],
        "do_mgl": best[5],
        "ph": best[6],
        "conductivity_us_cm": best[7],
        "turbidity_fnu": best[8],
    }


def _compute_anomalies(
    db: Database,
    lat: float,
    lng: float,
    dt: datetime,
    weather: dict | None,
    pwqmn: dict | None,
) -> dict:
    """
    Compare current conditions vs historical PWQMN baseline for same month.
    Flags anomalies > 2°C from baseline.
    """
    month = dt.month
    anomalies = {}

    # Historical water temp baseline: PWQMN average for this month within ~50km
    baseline_rows = list(
        db.execute(
            """
        SELECT AVG(temp_c), COUNT(*)
        FROM water_quality_readings
        WHERE strftime('%m', sampled_at) = ?
          AND temp_c IS NOT NULL
          AND lat BETWEEN ? AND ?
          AND lng BETWEEN ? AND ?
    """,
            [
                f"{month:02d}",
                lat - 0.45,
                lat + 0.45,
                lng - 0.45,
                lng + 0.45,
            ],
        ).fetchall()
    )

    if baseline_rows and baseline_rows[0][0] is not None:
        hist_water_temp = baseline_rows[0][0]
        current_water_temp = pwqmn.get("temp_c") if pwqmn else None

        if current_water_temp is not None:
            anomaly = current_water_temp - hist_water_temp
            anomalies["water_temp_anomaly_c"] = round(anomaly, 1)
            anomalies["historical_water_temp_c"] = round(hist_water_temp, 1)

    # Air temp anomaly: compare against same-week average from prior 3 years
    if weather and weather.get("air_temp_c") is not None:
        try:
            hist_air = _fetch_historical_air_temp_baseline(lat, lng, dt)
            if hist_air is not None:
                air_anomaly = weather["air_temp_c"] - hist_air
                anomalies["air_temp_anomaly_c"] = round(air_anomaly, 1)
                anomalies["historical_air_temp_c"] = round(hist_air, 1)
        except Exception:
            pass

    # Determine anomaly flag
    water_anom = anomalies.get("water_temp_anomaly_c")
    air_anom = anomalies.get("air_temp_anomaly_c")
    if water_anom is not None and water_anom < -2.0:
        anomalies["anomaly_flag"] = "cold_conditions"
        anomalies["anomaly_note"] = (
            f"Water temp {abs(water_anom):.1f}°C below historical average "
            f"for {dt.strftime('%B')} — expect delayed seasonal patterns"
        )
    elif water_anom is not None and water_anom > 2.0:
        anomalies["anomaly_flag"] = "warm_conditions"
        anomalies["anomaly_note"] = (
            f"Water temp {water_anom:.1f}°C above historical average for {dt.strftime('%B')}"
        )
    elif air_anom is not None and air_anom < -3.0:
        anomalies["anomaly_flag"] = "cold_air"
        anomalies["anomaly_note"] = (
            f"Air temp {abs(air_anom):.1f}°C below normal — post-cold-front likely"
        )
    else:
        anomalies["anomaly_flag"] = "normal"

    return anomalies


def _fetch_historical_air_temp_baseline(
    lat: float,
    lng: float,
    dt: datetime,
    timeout: float = 2.0,
) -> float | None:
    """
    Fetch average air temp for the same week across the prior 3 years.
    Used for anomaly detection.
    """
    temps = []
    for years_back in [1, 2, 3]:
        try:
            prior = dt.replace(year=dt.year - years_back)
            start = (prior - timedelta(days=3)).strftime("%Y-%m-%d")
            end = (prior + timedelta(days=3)).strftime("%Y-%m-%d")
            data = _weather.get_archive_hourly(
                lat, lng, start, end, ["temperature_2m"], timeout=timeout
            )
            arr = data.get("hourly", {}).get("temperature_2m", [])
            valid = [t for t in arr if t is not None]
            if valid:
                temps.append(sum(valid) / len(valid))
        except Exception:
            pass

    return sum(temps) / len(temps) if temps else None


def _compute_days_since_rain(precip_history: list, threshold_mm: float = 5.0) -> int:
    """Count days since last rain event > threshold from hourly precip array."""
    daily = []
    for i in range(0, len(precip_history), 24):
        chunk = [p for p in precip_history[i : i + 24] if p is not None]
        daily.append(sum(chunk))

    for i, total in enumerate(reversed(daily[:-1])):  # exclude current day
        if total >= threshold_mm:
            return i
    return len(daily)


def _compute_moon_phase(dt: datetime) -> float:
    """
    Compute moon phase (0=new, 0.5=full, 1=new) using a simple algorithm.
    Accurate to within ~1 day.
    """
    known_new = datetime(2000, 1, 6, 18, 14)
    cycle = 29.53058867
    days = (dt - known_new).total_seconds() / 86400
    phase = (days % cycle) / cycle
    return round(phase, 3)


def _get_season(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "fall"


def _get_time_of_day(hour: int) -> str:
    if hour < 6:
        return "night"
    if hour < 9:
        return "dawn"
    if hour < 12:
        return "morning"
    if hour < 14:
        return "midday"
    if hour < 17:
        return "afternoon"
    if hour < 20:
        return "evening"
    return "night"


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))
