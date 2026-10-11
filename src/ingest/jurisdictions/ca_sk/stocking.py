"""Saskatchewan fish stocking records — SK Fish Stocking History.

Source: SaskGIS Fisheries service (ArcGIS REST)
  FishStockingHistory (table)      : Fisheries/FeatureServer/4
  Species Info (waterbody points)  : Fisheries/FeatureServer/1
  https://gis.saskatchewan.ca/arcgis/rest/services/Fisheries/FeatureServer/

The stocking history is a geometry-free table. Each row carries an `NRCAN`
waterbody code that joins to the Species Info point layer's `LOCATIONID`, so
coordinates are resolved the same way as the fish observations adapter:

  1. Query the Species Info point layer within a bbox → waterbody coords.
  2. Query the stocking table for those `NRCAN` codes (chunked IN list).
  3. Map each row into stocking_records.

Field mapping (source → stocking_records):
  OBJECTID      → record_id ("SK_STOCK_<OBJECTID>")
  WATERBODY     → waterbody_name
  NRCAN         → waterbody_code
  SPECIES       → species
  SIZE_         → life_stage        (e.g. "Fry", "Fingerlings")
  NUMBER_       → quantity
  STOCKING_DATE → stocked_at / year / month  (epoch ms → UTC datetime; the
                  series reaches back before 1970, so negative epochs occur)

Rows whose `NRCAN` has no resolvable point are kept with NULL lat/lng (the
waterbody name and event are still real); the count is logged when material.

Cache TTL: 30 days.

Table: stocking_records (shared schema with MNRF / AB / MB stocking).
"""

import hashlib
import json
import logging
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

_POINTS_URL = "https://gis.saskatchewan.ca/arcgis/rest/services/Fisheries/FeatureServer/1/query"
_STOCKING_URL = "https://gis.saskatchewan.ca/arcgis/rest/services/Fisheries/FeatureServer/4/query"
_PAGE_SIZE = 1000
_CHUNK_SIZE = 200
_CACHE_DIR = Path("data/cache/sk_stocking")
_CACHE_TTL_SECONDS = 30 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

logger = logging.getLogger(__name__)


def fetch_stocking_records(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[dict]:
    """Fetch SK stocking records within radius_km of lat/lng. Cached 30 days."""
    locations = _fetch_waterbody_points(lat, lng, radius_km)
    if not locations:
        logger.info("SK stocking: no waterbody points in %.0fkm radius", radius_km)
        return []

    codes = list(locations.keys())
    logger.info("SK stocking: %d waterbodies in radius; querying stocking rows", len(codes))

    rows: list[dict] = []
    seen: set[int] = set()
    n_no_coords = 0
    n_no_species = 0
    for chunk in _chunks(codes, _CHUNK_SIZE):
        for attrs in _fetch_stocking_rows(chunk):
            fid = attrs.get("OBJECTID")
            if fid is None or fid in seen:
                continue
            species = (attrs.get("SPECIES") or "").strip()
            if not species:
                n_no_species += 1
                continue

            stocked_at, month = _epoch_ms_to_datetime(attrs.get("STOCKING_DATE"))
            year = stocked_at.year if stocked_at else None
            if year is None:
                n_no_species += 1
                continue

            code = (attrs.get("NRCAN") or "").strip()
            loc = locations.get(code)
            if loc is None:
                n_no_coords += 1

            seen.add(fid)
            rows.append(
                {
                    "record_id": f"SK_STOCK_{fid}",
                    "waterbody_name": (attrs.get("WATERBODY") or "").strip() or None,
                    "waterbody_code": code or None,
                    "municipality": None,
                    "county": None,
                    "lat": loc["lat"] if loc else None,
                    "lng": loc["lng"] if loc else None,
                    "jurisdiction": "CA-SK",
                    "species": species,
                    "species_code": None,
                    "year": year,
                    "month": month,
                    "quantity": _as_int(attrs.get("NUMBER_")),
                    "life_stage": (attrs.get("SIZE_") or "").strip() or None,
                    "stocking_purpose": None,
                    "stocked_at": stocked_at.isoformat() if stocked_at else None,
                }
            )

    if n_no_coords:
        logger.warning(
            "SK stocking: %d of %d records had no resolvable coordinates (kept with NULL lat/lng)",
            n_no_coords,
            len(rows) + n_no_species,
        )
    if n_no_species:
        logger.warning("SK stocking: %d records skipped (missing species/date)", n_no_species)
    logger.info("SK stocking fetch complete: %d records", len(rows))
    return rows


# ── step 1: waterbody points ────────────────────────────────────────────────────


def _fetch_waterbody_points(lat: float, lng: float, radius_km: float) -> dict[str, dict]:
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lng, radius_km)
    params = {
        "where": "1=1",
        "geometry": f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "LOCATIONID,NAME",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    locations: dict[str, dict] = {}
    for feat in _paginate(_POINTS_URL, params):
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        code = (props.get("LOCATIONID") or "").strip()
        if not code or geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        locations.setdefault(
            code,
            {
                "lat": float(coords[1]),
                "lng": float(coords[0]),
                "name": (props.get("NAME") or "").strip() or None,
            },
        )
    return locations


# ── step 2: stocking rows ───────────────────────────────────────────────────────


def _fetch_stocking_rows(codes: list[str]) -> list[dict]:
    quoted = ",".join("'" + c.replace("'", "''") + "'" for c in codes)
    params = {
        "where": f"NRCAN IN ({quoted})",
        "outFields": "OBJECTID,SPECIES,SIZE_,STOCKING_DATE,WATERBODY,NUMBER_,NRCAN",
        "returnGeometry": "false",
        "f": "json",
    }
    return [feat.get("attributes") or {} for feat in _paginate(_STOCKING_URL, params)]


def _paginate(url: str, base_params: dict) -> list[dict]:
    """Page through an ArcGIS query. Raises on any error so a partial read never looks complete."""
    features: list[dict] = []
    offset = 0
    while True:
        params = {**base_params, "resultOffset": offset, "resultRecordCount": _PAGE_SIZE}
        data = _cached_get(url, params)
        if "error" in data:
            raise RuntimeError(f"SK stocking: ArcGIS error at offset={offset}: {data['error']}")
        page = data.get("features", [])
        features.extend(page)
        if len(page) < _PAGE_SIZE:
            return features
        offset += _PAGE_SIZE


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


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _epoch_ms_to_datetime(value) -> tuple[datetime | None, int | None]:
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return None, None
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None, None
    return dt.replace(tzinfo=None), dt.month


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_deg = radius_km / 111.0
    lon_deg = radius_km / (111.320 * math.cos(math.radians(lat)))
    return (lon - lon_deg, lat - lat_deg, lon + lon_deg, lat + lat_deg)
