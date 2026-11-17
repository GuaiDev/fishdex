"""Ontario Provincial Parks + Conservation Reserves — ESRI REST ingest.

Queries the LIO Open Data MapServer/4 layer by bounding box.
Returns park polygons with PROTECTED_AREA_TYPE for access scoring.
Cache TTL: 30 days — park boundaries change rarely.

REST service:
  https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_OPEN_DATA/LIO_Open03/MapServer/4
"""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

_REST_URL = (
    "https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest"
    "/services/LIO_OPEN_DATA/LIO_Open03/MapServer/4/query"
)
_CACHE_DIR = Path("data/cache/provincial_parks")
_CACHE_TTL_SECONDS = 2_592_000  # 30 days
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

# Field names confirmed from LIO MapServer/4 (as of 2026-05)
_TYPE_FIELD_CANDIDATES = [
    "PROVINCIAL_PARK_CLASS_ENG",
    "PROTECTED_AREA_TYPE",
    "PARK_CLASS",
    "CLASS",
]
_NAME_FIELD_CANDIDATES = [
    "PROTECTED_AREA_NAME_ENG",
    "COMMON_SHORT_NAME",
    "REGULATED_AREA_NAME",
    "AREA_NAME",
    "NAME",
]


def fetch_parks_near(lat: float, lng: float, radius_km: float = 200.0) -> list[dict]:
    """Fetch provincial park polygons within radius_km of lat/lng.

    Returns list of dicts: {park_id, name, park_type, centroid_lat, centroid_lng,
    polygon_rings, fetched_at}.
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
    parks = []
    for feat in features:
        parsed = _parse_feature(feat, now)
        if parsed is not None:
            parks.append(parsed)

    cache_file.write_text(json.dumps(parks))
    return parks


def fetch_and_store(db, lat: float, lng: float, radius_km: float = 200.0) -> int:
    """Fetch parks and upsert into the provincial_parks table. Returns count stored."""
    from src.services.ingest_runs import record_counts

    parks = fetch_parks_near(lat, lng, radius_km)
    record_counts(fetched=1, empty=int(not parks))
    if not parks:
        return 0
    _ensure_table(db)
    db["provincial_parks"].upsert_all(parks, pk="park_id")
    record_counts(stored=len(parks))
    return len(parks)


# ── internal ──────────────────────────────────────────────────────────────────


def _query_all_pages(xmin: float, ymin: float, xmax: float, ymax: float) -> list[dict]:
    """Page through ESRI REST results (max 1000 per request) until exhausted."""
    all_features: list[dict] = []
    offset = 0
    page_size = 1000

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
    """GET the REST service with a short polite delay."""
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
    geom = feature.get("geometry")
    if not geom:
        return None

    # Resolve field names dynamically
    park_type = _resolve_field(props, _TYPE_FIELD_CANDIDATES) or "unknown"
    name = _resolve_field(props, _NAME_FIELD_CANDIDATES) or "Unknown Park"

    # Feature ID — use first available integer/string ID field
    park_id = None
    for id_field in ("OGF_ID", "PROTECTED_SITE_IDENT", "OBJECTID", "FID"):
        if id_field in props and props[id_field] is not None:
            park_id = str(props[id_field])
            break
    if park_id is None:
        park_id = f"{name}_{park_type}".lower().replace(" ", "_")

    # Extract polygon rings from GeoJSON geometry
    rings = _extract_rings(geom)
    if not rings:
        return None

    # Compute centroid from first (outer) ring
    outer = rings[0]
    if not outer:
        return None
    lats = [pt[1] for pt in outer]
    lngs = [pt[0] for pt in outer]
    centroid_lat = sum(lats) / len(lats)
    centroid_lng = sum(lngs) / len(lngs)

    return {
        "park_id": park_id,
        "name": name,
        "park_type": _normalize_park_type(park_type),
        "centroid_lat": centroid_lat,
        "centroid_lng": centroid_lng,
        "polygon_json": json.dumps(rings),
        "fetched_at": fetched_at,
    }


def _extract_rings(geom: dict) -> list[list[list[float]]] | None:
    """Extract coordinate rings from GeoJSON polygon or multipolygon geometry."""
    gtype = geom.get("type", "")
    coords = geom.get("coordinates")
    if not coords:
        return None

    if gtype == "Polygon":
        return coords  # list of rings, each ring is [[lng, lat], ...]
    if gtype == "MultiPolygon":
        # Return rings from the largest polygon (most coordinates)
        biggest = max(coords, key=lambda poly: len(poly[0]) if poly else 0)
        return biggest
    return None


def _resolve_field(props: dict, candidates: list[str]) -> str | None:
    props_upper = {k.upper(): v for k, v in props.items()}
    for candidate in candidates:
        val = props_upper.get(candidate.upper())
        if val is not None:
            return str(val)
    return None


def _normalize_park_type(raw: str) -> str:
    """Normalize park type string to lowercase canonical form."""
    lower = raw.strip().lower()
    if "wilderness" in lower:
        return "Wilderness"
    if "nature reserve" in lower or "naturereserve" in lower:
        return "Nature Reserve"
    if "cultural heritage" in lower or "culturalheritage" in lower:
        return "Cultural Heritage"
    if "natural environment" in lower or "naturalenvironment" in lower:
        return "Natural Environment"
    if "waterway" in lower:
        return "Waterway"
    if "recreational" in lower:
        return "Recreational"
    return raw.strip().title()


def _ensure_table(db) -> None:
    if "provincial_parks" not in db.table_names():
        db["provincial_parks"].create(
            {
                "park_id": str,
                "name": str,
                "park_type": str,
                "centroid_lat": float,
                "centroid_lng": float,
                "polygon_json": str,
                "fetched_at": str,
            },
            pk="park_id",
        )
