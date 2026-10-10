"""Saskatchewan water quality — Long Term Water Chemistry (Sask Water / WSA).

Source: Saskatchewan Water Security Agency via ArcGIS Online — FeatureServer/0
  "LongTermWaterChemistry"
  https://services3.arcgis.com/zcv98lgAl8xQ04cW/arcgis/rest/services/
  LongTermWaterChemistry/FeatureServer/0

A historical lake/stream chemistry dataset (roughly 1920s–1990s). Scoped to a
bounding box derived from lat/lng/radius_km, paginated with the standard ArcGIS
resultOffset / resultRecordCount parameters.

Field mapping (source → water_quality_readings):
  OBJECTID                       → record_id ("SK_WQ_<OBJECTID>")
  CGNDB_ID                       → station_id   (falls back to Geographical_Name)
  Geographical_Name              → station_name
  Latitude_WGS84/Longitude_WGS84 → lat/lng
  YYYY/MM/DD_collected           → sampled_at
  Oxygen_mgL                     → do_mgl
  pH  (fallback pH_in_field)     → ph
  Spec_conductivity_micromho_ppm → conductivity_us_cm  (µmho/cm == µS/cm)

The source stores most chemistry values as text and uses the sentinels "NA",
"ND", "Trace", "Nil" for non-detects and missing data; those become NULL.

Two deliberate omissions:
  - There is no water-temperature column, so `temp_c` stays NULL.
  - `Turbidity_Jackson_Turbidity` is in Jackson Turbidity Units (JTU), not
    Formazin Nephelometric Units (FNU); the two are not interchangeable, so
    `turbidity_fnu` is left NULL rather than silently mis-unit a reading.

Rows with no usable collection year are skipped (the schema requires a date);
the count is logged at WARNING when material.

Cache TTL: 90 days (historical data, effectively static).

Table: water_quality_readings (shared schema).
"""

import hashlib
import json
import logging
import math
import time
from datetime import date
from pathlib import Path

import httpx

from src.models.water_quality_reading import WaterQualityReading

_QUERY_URL = (
    "https://services3.arcgis.com/zcv98lgAl8xQ04cW/arcgis/rest/services/"
    "LongTermWaterChemistry/FeatureServer/0/query"
)
_PAGE_SIZE = 1000
_CACHE_DIR = Path("data/cache/sk_wq")
_CACHE_TTL_SECONDS = 90 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

_OUT_FIELDS = (
    "OBJECTID,CGNDB_ID,Geographical_Name,YYYY_collected,MM_collected,DD_collected,"
    "Latitude_WGS84,Longitude_WGS84,Oxygen_mgL,pH,pH_in_field,"
    "Spec_conductivity_micromho_ppm"
)

logger = logging.getLogger(__name__)


def fetch_water_quality_readings(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[WaterQualityReading]:
    """Fetch SK long-term water chemistry within radius_km of lat/lng. Cached 90 days."""
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lng, radius_km)
    base_params = {
        "where": "1=1",
        "geometry": f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": _OUT_FIELDS,
        "returnGeometry": "false",
        "f": "json",
    }
    features = _query_paginate(_QUERY_URL, base_params)
    logger.info(
        "SK water quality: received %d raw rows for %.0fkm radius", len(features), radius_km
    )

    readings: list[WaterQualityReading] = []
    seen: set[int] = set()
    n_no_date = 0
    for feat in features:
        attrs = feat.get("attributes") or {}
        fid = attrs.get("OBJECTID")
        if fid is None or fid in seen:
            continue

        sampled_at = _parse_date(
            attrs.get("YYYY_collected"),
            attrs.get("MM_collected"),
            attrs.get("DD_collected"),
        )
        if sampled_at is None:
            n_no_date += 1
            continue

        station_name = (attrs.get("Geographical_Name") or "").strip() or None
        cgndb = (attrs.get("CGNDB_ID") or "").strip()
        station_id = cgndb if cgndb and cgndb.upper() != "NA" else (station_name or f"SKWQ_{fid}")

        seen.add(fid)
        readings.append(
            WaterQualityReading(
                record_id=f"SK_WQ_{fid}",
                station_id=station_id,
                station_name=station_name,
                lat=_as_float(attrs.get("Latitude_WGS84")),
                lng=_as_float(attrs.get("Longitude_WGS84")),
                jurisdiction="CA-SK",
                sampled_at=sampled_at,
                do_mgl=_sanitize_do(attrs.get("Oxygen_mgL")),
                ph=_first_ph(attrs.get("pH"), attrs.get("pH_in_field")),
                temp_c=None,
                conductivity_us_cm=_as_float(attrs.get("Spec_conductivity_micromho_ppm")),
                turbidity_fnu=None,
            )
        )

    if n_no_date:
        logger.warning(
            "SK water quality: %d of %d rows skipped (no usable collection year)",
            n_no_date,
            len(features),
        )
    logger.info("SK water quality fetch complete: %d readings", len(readings))
    return readings


# ── ArcGIS pagination ───────────────────────────────────────────────────────────


def _query_paginate(url: str, base_params: dict) -> list[dict]:
    features: list[dict] = []
    offset = 0
    while True:
        params = {
            **base_params,
            "resultOffset": offset,
            "resultRecordCount": _PAGE_SIZE,
        }
        try:
            data = _cached_get(url, params)
        except Exception as exc:
            logger.error("SK water quality: ArcGIS request failed at offset=%d: %s", offset, exc)
            break
        if "error" in data:
            logger.error("SK water quality: ArcGIS error at offset=%d: %s", offset, data["error"])
            break
        page = data.get("features", [])
        features.extend(page)
        logger.debug("SK water quality: offset=%d → %d features", offset, len(page))
        if len(page) < _PAGE_SIZE or not data.get("exceededTransferLimit", False):
            break
        offset += _PAGE_SIZE
    return features


# ── HTTP + file cache ──────────────────────────────────────────────────────────


def _cached_get(url: str, params: dict) -> dict:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    raw_key = url + str(sorted(params.items()))
    key = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            return json.loads(cache_file.read_text())

    response = httpx.get(
        url,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data))
    return data


# ── value parsing ──────────────────────────────────────────────────────────────

# Non-numeric sentinels used throughout the source's text fields.
_NULL_SENTINELS = {"", "NA", "N/A", "ND", "TRACE", "NIL", "NONE", "-", "--"}


def _as_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in _NULL_SENTINELS:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _sanitize_ph(value) -> float | None:
    v = _as_float(value)
    if v is None or not (0.0 <= v <= 14.0):
        return None
    return v


def _first_ph(lab_value, field_value) -> float | None:
    """Prefer the lab pH; fall back to the field pH reading."""
    return _sanitize_ph(lab_value) or _sanitize_ph(field_value)


def _sanitize_do(value) -> float | None:
    v = _as_float(value)
    if v is None or v < 0:
        return None
    return v


def _parse_date(year, month, day) -> date | None:
    y = _as_int_str(year)
    if y is None or y < 1800 or y > 2100:
        return None
    m = _as_int_str(month) or 1
    d = _as_int_str(day) or 1
    if not (1 <= m <= 12) or not (1 <= d <= 31):
        m, d = 1, 1
    try:
        return date(y, m, d)
    except ValueError:
        try:
            return date(y, 1, 1)
        except ValueError:
            return None


def _as_int_str(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in _NULL_SENTINELS:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_deg = radius_km / 111.0
    lon_deg = radius_km / (111.320 * math.cos(math.radians(lat)))
    return (lon - lon_deg, lat - lat_deg, lon + lon_deg, lat + lat_deg)
