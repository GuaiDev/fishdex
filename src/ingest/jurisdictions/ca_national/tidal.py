"""CHS (Canadian Hydrographic Service) Tidal API — tide predictions.

Fetches tide station metadata and high/low tide predictions from DFO's
CHS IWLS API. Relevant for coastal provinces: BC, NS, NB, PEI, NL.

API:
  Base: https://api-iwls.dfo-mpo.gc.ca
  Stations: GET /api/v1/stations
  Data:     GET /api/v1/stations/{id}/data?time-series-code=wlp-hilo

No API key required — public endpoint.

Table: tidal_readings
  record_id (PK), station_id, station_name, lat, lng, jurisdiction,
  prediction_datetime, water_level_m, data_type, tide_type, fetched_at

Cache TTL: Stations 30 days. Predictions 24 hours (they change based on
  calculation date).
"""

import hashlib
import json
import logging
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from src.jurisdictions.geo import jurisdiction_for_coords
from src.services.ingest_runs import record_counts, record_error

_BASE_URL = "https://api-iwls.dfo-mpo.gc.ca"
_STATIONS_URL = f"{_BASE_URL}/api/v1/stations"
_CACHE_DIR = Path("data/cache/chs_tidal")
_STATIONS_TTL = 30 * 86400
_PREDICTIONS_TTL = 24 * 3600
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

logger = logging.getLogger(__name__)


def fetch_tidal_readings(
    lat: float,
    lng: float,
    radius_km: float = 100.0,
) -> list[dict]:
    """Fetch tidal predictions for stations within radius_km of lat/lng.

    Returns list of row dicts ready for upsert into tidal_readings table.
    """
    stations = _fetch_nearby_stations(lat, lng, radius_km)
    if not stations:
        logger.info(
            "CHS tidal: no stations found within %.0fkm of (%.4f, %.4f)",
            radius_km,
            lat,
            lng,
        )
        return []

    logger.info("CHS tidal: %d stations found near (%.4f, %.4f)", len(stations), lat, lng)
    rows: list[dict] = []
    for station in stations:
        record_counts(fetched=1)
        try:
            preds = _fetch_predictions(station["id"])
            parsed = _parse_predictions(station, preds)
            record_counts(empty=int(not parsed))
            rows.extend(parsed)
        except Exception as exc:
            record_counts(skipped=1)
            record_error(exc)
            logger.warning(
                "CHS tidal: failed to fetch predictions for station %s: %s", station["id"], exc
            )

    logger.info("CHS tidal: %d tidal reading records fetched", len(rows))
    return rows


def _fetch_nearby_stations(lat: float, lng: float, radius_km: float) -> list[dict]:
    """Return CHS tidal stations within radius_km."""
    try:
        data = _cached_get(_STATIONS_URL, {}, _STATIONS_TTL)
    except Exception as exc:
        record_counts(fetched=1, skipped=1)
        record_error(exc)
        logger.error("CHS tidal: stations fetch failed: %s", exc)
        return []

    # The API may return a list or a dict with a 'data' key
    if isinstance(data, list):
        all_stations = data
    else:
        all_stations = data.get("data", data.get("stations", []))

    nearby = []
    for s in all_stations:
        slat = _float_field(s, ["latitude", "lat"])
        slng = _float_field(s, ["longitude", "lng", "lon"])
        if slat is None or slng is None:
            continue
        dist = _haversine_km(lat, lng, slat, slng)
        if dist <= radius_km:
            nearby.append(
                {
                    "id": str(s.get("id") or s.get("station_id") or ""),
                    "name": str(s.get("officialName") or s.get("name") or "Unknown"),
                    "lat": slat,
                    "lng": slng,
                }
            )

    if not nearby:
        record_counts(fetched=1, empty=1)
    return nearby


def _fetch_predictions(station_id: str) -> list[dict]:
    """Fetch high/low tide predictions for a station."""
    url = f"{_BASE_URL}/api/v1/stations/{station_id}/data"
    # Round to start of today UTC so the cache key is stable within a day
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    from_dt = today.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_dt = (today + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "time-series-code": "wlp-hilo",
        "from": from_dt,
        "to": to_dt,
    }
    data = _cached_get(url, params, _PREDICTIONS_TTL)
    if isinstance(data, list):
        return data
    return data.get("data", [])


def _parse_predictions(station: dict, predictions: list[dict]) -> list[dict]:
    """Convert raw prediction dicts to tidal_readings rows."""
    now = datetime.utcnow().isoformat()
    jur = jurisdiction_for_coords(station["lat"], station["lng"]) or "CA"
    rows = []
    for pred in predictions:
        iso_dt = str(pred.get("eventDate") or pred.get("datetime") or pred.get("time") or "")
        if not iso_dt:
            continue
        level = _float_field(pred, ["value", "water_level", "wl"])
        tide_type_raw = (pred.get("type") or pred.get("tideType") or "").lower()
        if "high" in tide_type_raw:
            tide_type = "high"
        elif "low" in tide_type_raw:
            tide_type = "low"
        else:
            tide_type = None
        record_id = f"{station['id']}_{iso_dt}"
        rows.append({
            "record_id": record_id,
            "station_id": station["id"],
            "station_name": station["name"],
            "lat": station["lat"],
            "lng": station["lng"],
            "jurisdiction": jur,
            "prediction_datetime": iso_dt,
            "water_level_m": level,
            "data_type": "prediction",
            "tide_type": tide_type,
            "fetched_at": now,
        })
    return rows


def _float_field(obj: dict, keys: list[str]) -> float | None:
    for k in keys:
        v = obj.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                continue
    return None


# ── HTTP + file cache ──────────────────────────────────────────────────────────


def _cached_get(url: str, params: dict, ttl: int) -> dict | list:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    raw_key = url + str(sorted(params.items()))
    key = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < ttl:
            return json.loads(cache_file.read_text())

    response = httpx.get(
        url,
        params=params or None,
        headers={"User-Agent": _USER_AGENT},
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error("CHS tidal HTTP %d for %s", response.status_code, url)
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data))
    return data


# ── geometry helpers ───────────────────────────────────────────────────────────


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))
