"""Ontario Conservation Authority Administrative Areas — ESRI REST ingest.

Queries the LIO Open Data MapServer/11 layer by bounding box.
Returns CA boundary polygons. Used to flag segments inside managed watersheds.
Cache TTL: 30 days.

REST service:
  https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_OPEN_DATA/LIO_Open03/MapServer/11
"""

import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx

_REST_URL = (
    "https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest"
    "/services/LIO_OPEN_DATA/LIO_Open03/MapServer/11/query"
)
_CACHE_DIR = Path("data/cache/ca_boundaries")
_CACHE_TTL_SECONDS = 2_592_000  # 30 days
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

logger = logging.getLogger(__name__)

_UNKNOWN_NAME = "Unknown CA"

# The service publishes COMMON_NAME and LEGAL_NAME. Neither was in the original
# candidate list, so every one of the 24 ingested rows silently became
# "Unknown CA" — the brittle-label-matching failure class (see Known issues:
# matching an exact published label rather than structure). COMMON_NAME first:
# "Upper Thames River" is what a person calls it.
_NAME_FIELD_CANDIDATES = [
    "COMMON_NAME",
    "LEGAL_NAME",
    "OFFICIAL_CONSERVATION_AUTHORITY_NAME",
    "CA_NAME",
    "AUTHORITY_NAME",
    "NAME",
]

# Fields that contain "NAME" but are not the authority's name.
_NAME_FIELD_EXCLUSIONS = ("FILE_NAME", "LAYER_NAME", "DATASET_NAME", "USER_NAME")


def fetch_ca_boundaries_near(lat: float, lng: float, radius_km: float = 200.0) -> list[dict]:
    """Fetch CA boundary polygons within radius_km of lat/lng.

    Returns list of dicts: {ca_id, name, centroid_lat, centroid_lng,
    polygon_json, fetched_at}.
    Cached 30 days.
    """
    deg = radius_km / 111.0
    xmin, ymin = lng - deg, lat - deg
    xmax, ymax = lng + deg, lat + deg

    cache_key = f"ca_{xmin:.3f},{ymin:.3f},{xmax:.3f},{ymax:.3f}"
    key_hash = hashlib.sha256(cache_key.encode()).hexdigest()[:16]
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{key_hash}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            return json.loads(cache_file.read_text())

    features = _query_all_pages(xmin, ymin, xmax, ymax)
    now = datetime.now().isoformat()
    boundaries = []
    n_unnamed = 0
    for feat in features:
        parsed = _parse_feature(feat, now)
        if parsed is not None and parsed.get("name") == _UNKNOWN_NAME:
            n_unnamed += 1
        if parsed is not None:
            boundaries.append(parsed)

    # A placeholder name is indistinguishable from a real one once stored, and
    # all 24 previously-ingested rows were "Unknown CA" with nothing saying so.
    # A boundary whose authority cannot be named cannot route a point to the
    # authority that surveys it, which is the only reason this layer exists.
    if n_unnamed:
        share = n_unnamed / len(boundaries) if boundaries else 1.0
        emit = logger.warning if share >= 0.1 else logger.info
        emit(
            "CA boundaries: %d of %d polygons (%.0f%%) have no resolvable authority "
            "name and were stored as %r. The service's name field has probably been "
            "renamed — check the layer's field list against _NAME_FIELD_CANDIDATES.",
            n_unnamed,
            len(boundaries),
            share * 100,
            _UNKNOWN_NAME,
        )

    cache_file.write_text(json.dumps(boundaries))
    return boundaries


def fetch_and_store(db, lat: float, lng: float, radius_km: float = 200.0) -> int:
    """Fetch CA boundaries and upsert into the ca_boundaries table. Returns count stored."""
    boundaries = fetch_ca_boundaries_near(lat, lng, radius_km)
    if not boundaries:
        return 0
    _ensure_table(db)
    db["ca_boundaries"].upsert_all(boundaries, pk="ca_id")
    return len(boundaries)


# ── internal ──────────────────────────────────────────────────────────────────


def _query_all_pages(xmin: float, ymin: float, xmax: float, ymax: float) -> list[dict]:
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

    name = _resolve_field(props, _NAME_FIELD_CANDIDATES) or _UNKNOWN_NAME

    ca_id = None
    for id_field in ("OBJECTID", "FID", "OBJECTID_1", "CA_ID"):
        if id_field in props and props[id_field] is not None:
            ca_id = str(props[id_field])
            break
    if ca_id is None:
        ca_id = name.lower().replace(" ", "_")

    rings = _extract_rings(geom)
    if not rings:
        return None

    outer = rings[0]
    if not outer:
        return None
    lats = [pt[1] for pt in outer]
    lngs = [pt[0] for pt in outer]
    centroid_lat = sum(lats) / len(lats)
    centroid_lng = sum(lngs) / len(lngs)

    return {
        "ca_id": ca_id,
        "name": name,
        "centroid_lat": centroid_lat,
        "centroid_lng": centroid_lng,
        "polygon_json": json.dumps(rings),
        "fetched_at": fetched_at,
    }


def _extract_rings(geom: dict) -> list[list[list[float]]] | None:
    gtype = geom.get("type", "")
    coords = geom.get("coordinates")
    if not coords:
        return None
    if gtype == "Polygon":
        return coords
    if gtype == "MultiPolygon":
        biggest = max(coords, key=lambda poly: len(poly[0]) if poly else 0)
        return biggest
    return None


def _resolve_field(props: dict, candidates: list[str]) -> str | None:
    """Resolve a named field, by candidate list then by structure.

    The structural pass exists because a published label can be renamed
    upstream without notice, which is exactly how this adapter came to write
    "Unknown CA" for every row. Any string field whose name contains NAME and
    is not an obvious non-name field will do: a wrong-but-plausible name is
    recoverable, a silent placeholder is not.
    """
    props_upper = {k.upper(): v for k, v in props.items()}
    for candidate in candidates:
        val = props_upper.get(candidate.upper())
        if val is not None and str(val).strip():
            return str(val).strip()

    for key, val in props_upper.items():
        if "NAME" not in key or key in _NAME_FIELD_EXCLUSIONS:
            continue
        if isinstance(val, str) and val.strip():
            logger.warning(
                "CA boundaries: no candidate name field matched; fell back to "
                "%r by structure. Add it to _NAME_FIELD_CANDIDATES.",
                key,
            )
            return val.strip()
    return None


def _ensure_table(db) -> None:
    if "ca_boundaries" not in db.table_names():
        db["ca_boundaries"].create(
            {
                "ca_id": str,
                "name": str,
                "centroid_lat": float,
                "centroid_lng": float,
                "polygon_json": str,
                "fetched_at": str,
            },
            pk="ca_id",
        )
