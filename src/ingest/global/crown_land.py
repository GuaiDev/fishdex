"""Ontario Crown Land boundaries — ESRI REST ingest.

Queries the LIO Open Data MapServer/5 layer by bounding box.
Returns simplified crown land polygons with LAND_USE_TYPE for access scoring.
Cache TTL: 30 days — crown land boundaries change rarely.

REST service:
  https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_OPEN_DATA/LIO_Open06/MapServer/5
"""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from shapely.geometry import shape
from shapely.wkt import dumps as wkt_dumps

_REST_URL = (
    "https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest"
    "/services/LIO_OPEN_DATA/LIO_Open06/MapServer/5/query"
)
_CACHE_DIR = Path("data/cache/crown_land")
_CACHE_TTL_SECONDS = 2_592_000  # 30 days
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"
_SIMPLIFY_TOLERANCE = 0.001  # ~100m — sufficient for access scoring

_TYPE_FIELD_CANDIDATES = [
    "LAND_USE_TYPE",
    "CROWN_LAND_USE_DESIGNATION",
    "DESIGNATION_TYPE",
    "LAND_CLASS",
    "CLASS",
]


def fetch_crown_land_near(lat: float, lng: float, radius_km: float = 100.0) -> list[dict]:
    """Fetch crown land polygons within radius_km of lat/lng.

    Returns list of dicts: {crown_id, land_use_type, geom_wkt, centroid_lat, centroid_lng,
    bbox_minx, bbox_miny, bbox_maxx, bbox_maxy, fetched_at}.
    Cached 30 days.
    """
    deg = radius_km / 111.0
    xmin, ymin = lng - deg, lat - deg
    xmax, ymax = lng + deg, lat + deg

    cache_key = f"{xmin:.3f},{ymin:.3f},{xmax:.3f},{ymax:.3f}"
    key_hash = hashlib.sha256(cache_key.encode()).hexdigest()[:16]
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{key_hash}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            return json.loads(cache_file.read_text())

    features = _query_all_pages(xmin, ymin, xmax, ymax)
    now = datetime.now().isoformat()
    parcels = []
    for feat in features:
        parsed = _parse_feature(feat, now)
        if parsed is not None:
            parcels.append(parsed)

    cache_file.write_text(json.dumps(parcels))
    return parcels


def fetch_and_store(db, lat: float, lng: float, radius_km: float = 100.0) -> int:
    """Fetch crown land polygons and upsert into the crown_land table. Returns count stored."""
    from src.services.ingest_runs import record_counts

    parcels = fetch_crown_land_near(lat, lng, radius_km)
    record_counts(fetched=1, empty=int(not parcels))
    if not parcels:
        return 0
    _ensure_table(db)
    db["crown_land"].upsert_all(parcels, pk="crown_id")
    record_counts(stored=len(parcels))
    return len(parcels)


# ── internal ──────────────────────────────────────────────────────────────────


def _query_all_pages(xmin: float, ymin: float, xmax: float, ymax: float) -> list[dict]:
    """Page through ESRI REST results (max 1000 per request) until exhausted."""
    all_features: list[dict] = []
    offset = 0
    page_size = 500  # conservative — crown land polygons can be complex

    while True:
        params = {
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
            "f": "geojson",
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
        }
        data = _rest_get(params)
        features = data.get("features", [])
        all_features.extend(features)
        if len(features) < page_size:
            break
        offset += page_size

    return all_features


def _rest_get(params: dict) -> dict:
    time.sleep(0.3)
    resp = httpx.get(
        _REST_URL,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_feature(feature: dict, fetched_at: str) -> dict | None:
    props = feature.get("properties") or feature.get("attributes") or {}
    geom_json = feature.get("geometry")
    if not geom_json:
        return None

    land_use_type = _resolve_field(props, _TYPE_FIELD_CANDIDATES) or "Crown Land"

    crown_id = None
    for id_field in ("OGF_ID", "OBJECTID", "FID", "GID"):
        if id_field in props and props[id_field] is not None:
            crown_id = str(props[id_field])
            break
    if crown_id is None:
        return None

    try:
        geom = shape(geom_json)
        if not geom.is_valid:
            geom = geom.buffer(0)
        simplified = geom.simplify(_SIMPLIFY_TOLERANCE, preserve_topology=True)
        if simplified.is_empty:
            return None

        bounds = simplified.bounds  # (minx, miny, maxx, maxy)
        centroid = simplified.centroid
        geom_wkt = wkt_dumps(simplified)
    except Exception:
        return None

    return {
        "crown_id": crown_id,
        "land_use_type": land_use_type,
        "geom_wkt": geom_wkt,
        "centroid_lat": centroid.y,
        "centroid_lng": centroid.x,
        "bbox_minx": bounds[0],
        "bbox_miny": bounds[1],
        "bbox_maxx": bounds[2],
        "bbox_maxy": bounds[3],
        "fetched_at": fetched_at,
    }


def _resolve_field(props: dict, candidates: list[str]) -> str | None:
    props_upper = {k.upper(): v for k, v in props.items()}
    for candidate in candidates:
        val = props_upper.get(candidate.upper())
        if val is not None:
            return str(val)
    return None


def _ensure_table(db) -> None:
    if "crown_land" not in db.table_names():
        db["crown_land"].create(
            {
                "crown_id": str,
                "land_use_type": str,
                "geom_wkt": str,
                "centroid_lat": float,
                "centroid_lng": float,
                "bbox_minx": float,
                "bbox_miny": float,
                "bbox_maxx": float,
                "bbox_maxy": float,
                "fetched_at": str,
            },
            pk="crown_id",
        )
