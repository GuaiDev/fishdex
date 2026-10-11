"""Manitoba fish stocking records via ArcGIS FeatureServer.

Source: Manitoba Government ArcGIS Online — FeatureServer layer 0
  "Manitoba Waterbody Stocking Records"
  https://services.arcgis.com/mMUesHYPkXjaFGfS/arcgis/rest/services/
  Manitoba_Waterbody_Stocking_Records/FeatureServer/0

This is the province's public stocking record — one point per stocking event,
with a real `DATE` (epoch ms), species, size class and number planted. It is
scoped to a bounding box derived from lat/lng/radius_km, paginated with the
standard ArcGIS resultOffset / resultRecordCount parameters.

Field mapping (source → stocking_records):
  OBJECTID         → record_id  ("MB_STOCK_<OBJECTID>")
  WATERBODY_NAME   → waterbody_name
  FISHING_DIVISION → county
  SPECIES          → species
  SIZE             → life_stage        (e.g. "Fingerling", "Adults")
  QUANTITY         → quantity
  YEAR             → year
  DATE             → stocked_at / month  (epoch milliseconds → UTC datetime)
  LAT_DD/LONG_DD   → lat/lng   (falls back to WATERBODY_LAT/LONG)

Some rows carry no usable coordinates (0/blank LAT_DD) — these are kept with a
NULL lat/lng rather than dropped, since the waterbody name is still real. The
count with no coordinates is logged at WARNING when it is material.

Cache TTL: 30 days (stocking records are added/updated through the season).

Table: stocking_records (shared schema with MNRF / AB stocking).
"""

import hashlib
import json
import logging
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

_QUERY_URL = (
    "https://services.arcgis.com/mMUesHYPkXjaFGfS/arcgis/rest/services/"
    "Manitoba_Waterbody_Stocking_Records/FeatureServer/0/query"
)
_PAGE_SIZE = 1000
_CACHE_DIR = Path("data/cache/mb_stocking")
_CACHE_TTL_SECONDS = 30 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

logger = logging.getLogger(__name__)


def fetch_stocking_records(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[dict]:
    """Fetch MB stocking records within radius_km of lat/lng. Cached 30 days.

    Returns a list of row dicts for the stocking_records table.
    """
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lng, radius_km)
    base_params = {
        "where": "1=1",
        "geometry": f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }
    features = _query_paginate(_QUERY_URL, base_params)
    logger.info("MB stocking: received %d raw records for %.0fkm radius", len(features), radius_km)

    rows: list[dict] = []
    seen: set[int] = set()
    n_no_coords = 0
    n_no_species = 0
    for feat in features:
        attrs = feat.get("attributes") or {}
        fid = attrs.get("OBJECTID")
        if fid is None or fid in seen:
            if fid is None:
                n_no_species += 1
            continue
        seen.add(fid)

        species = (attrs.get("SPECIES") or "").strip()
        if not species:
            n_no_species += 1
            continue

        lat_v = attrs.get("LAT_DD") if attrs.get("LAT_DD") else attrs.get("WATERBODY_LAT")
        lng_v = attrs.get("LONG_DD") if attrs.get("LONG_DD") else attrs.get("WATERBODY_LONG")
        lat_f = _as_float(lat_v)
        lng_f = _as_float(lng_v)
        if lat_f == 0 or lng_f == 0:
            lat_f = None
            lng_f = None
        if lat_f is None or lng_f is None:
            n_no_coords += 1

        stocked_at, month = _epoch_ms_to_datetime(attrs.get("DATE"))
        year = _as_int(attrs.get("YEAR")) or (stocked_at.year if stocked_at else None)
        if year is None:
            n_no_species += 1
            continue

        rows.append(
            {
                "record_id": f"MB_STOCK_{fid}",
                "waterbody_name": (attrs.get("WATERBODY_NAME") or "").strip() or None,
                "waterbody_code": _as_str(attrs.get("WATERBODY_ID")),
                "municipality": None,
                "county": (attrs.get("FISHING_DIVISION") or "").strip() or None,
                "lat": lat_f,
                "lng": lng_f,
                "jurisdiction": "CA-MB",
                "species": species,
                "species_code": None,
                "year": year,
                "month": month,
                "quantity": _as_int(attrs.get("QUANTITY")),
                "life_stage": (attrs.get("SIZE") or "").strip() or None,
                "stocking_purpose": None,
                "stocked_at": stocked_at.isoformat() if stocked_at else None,
            }
        )

    if n_no_coords:
        logger.warning(
            "MB stocking: %d of %d records had no usable coordinates (kept with NULL lat/lng)",
            n_no_coords,
            len(features),
        )
    if n_no_species:
        logger.warning(
            "MB stocking: %d of %d records skipped (missing species/year)",
            n_no_species,
            len(features),
        )
    logger.info("MB stocking fetch complete: %d records", len(rows))
    return rows


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
        data = _cached_get(url, params)
        if "error" in data:
            raise RuntimeError(f"MB stocking: ArcGIS error at offset={offset}: {data['error']}")
        page = data.get("features", [])
        features.extend(page)
        logger.debug("MB stocking: offset=%d → %d features", offset, len(page))
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


# ── helpers ────────────────────────────────────────────────────────────────────


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _epoch_ms_to_datetime(value) -> tuple[datetime | None, int | None]:
    """Convert ArcGIS epoch-milliseconds to (naive UTC datetime, month)."""
    ms = _as_float(value)
    if ms is None:
        return None, None
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None, None
    return dt.replace(tzinfo=None), dt.month


def _bbox(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_deg = radius_km / 111.0
    lon_deg = radius_km / (111.320 * math.cos(math.radians(lat)))
    return (lng - lon_deg, lat - lat_deg, lng + lon_deg, lat + lat_deg)
