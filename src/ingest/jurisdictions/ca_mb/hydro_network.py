"""Manitoba stream network — WaLLAS Reference "Watercourses (NTS 1:20,000)".

Source: Manitoba Government ArcGIS Server — MapServer layer 18
  "Watercourses (NTS 1:20,000)"
  https://web43.gov.mb.ca/arcgis/rest/services/WALLAS/WaLLAS_REFERENCE/MapServer/18

WaLLAS (Water Licensing and Land Approval System) publishes the province's
1:20,000 watercourse network as a queryable polyline layer. It is fetched here
scoped to a bounding box derived from lat/lon/radius_km, using the standard
ArcGIS resultOffset / resultRecordCount pagination and outSR=4326 for lon/lat
coordinates.

Caveats (why the mapping is not one-to-one with OHN):
  - The layer is a mixed feature class. `F_TYPE` includes beaver dams and other
    non-watercourse features, so we filter to `F_TYPE LIKE 'RIVER/STREAM%'`.
  - There is no separate permanency field; the permanency is encoded inside
    `F_TYPE` ("RIVER/STREAM, PEREN (TIME OF PHOTO)" vs "RIVER/STREAM, INDEFINITE").
    The raw string is preserved in `flow_classification` and a normalized value
    written to `permanency`.
  - `FEAT_NAME` is blank for most segments and `ORDER_NUM` is frequently 0
    (unknown), so `name` is usually NULL and `stream_order` is NULL rather than
    a fabricated value.

The layer's OBJECTID is stored in `stream_segments.ogf_id` (the source-system
native integer ID for all jurisdictions; see JURISDICTION_TEMPLATE.md).

Cache TTL: 90 days (the 1:20k base is effectively static).

Note: `web43.gov.mb.ca` is an older (10.51) ArcGIS Server and is occasionally
slow; a single large request can time out transiently. Cached results make a
retry cheap.
"""

import hashlib
import json
import logging
import math
import time
from pathlib import Path

import httpx
from shapely.geometry import MultiLineString, shape

from src.models.hydrology import StreamSegment

_QUERY_URL = (
    "https://web43.gov.mb.ca/arcgis/rest/services/WALLAS/WaLLAS_REFERENCE/MapServer/18/query"
)
_PAGE_SIZE = 1000
_CACHE_DIR = Path("data/cache/mb_wallas")
_CACHE_TTL_SECONDS = 90 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

# Only river/stream features; the layer also holds beaver dams and similar.
_WHERE = "F_TYPE LIKE 'RIVER/STREAM%'"

logger = logging.getLogger(__name__)


def fetch_watercourses(
    lat: float,
    lon: float,
    radius_km: float = 50.0,
) -> list[StreamSegment]:
    """Fetch WaLLAS river/stream segments within radius_km of lat/lon. Cached 90 days."""
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lon, radius_km)
    base_params = {
        "where": _WHERE,
        "geometry": f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OBJECTID,FEAT_NAME,ORDER_NUM,F_TYPE,LENGTH",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    features = _query_paginate(_QUERY_URL, base_params)
    logger.info(
        "WaLLAS watercourse: received %d raw features for %.0fkm radius",
        len(features),
        radius_km,
    )

    segments: list[StreamSegment] = []
    seen: set[int] = set()
    for feat in features:
        seg = _parse_segment(feat)
        if seg is not None and seg.ogf_id not in seen:
            seen.add(seg.ogf_id)
            segments.append(seg)

    logger.info("WaLLAS watercourse fetch complete: %d segments", len(segments))
    return segments


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
        page = data.get("features", [])
        features.extend(page)
        logger.debug("WaLLAS: offset=%d → %d features", offset, len(page))
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return features


# ── parser ─────────────────────────────────────────────────────────────────────


def _parse_segment(feat: dict) -> StreamSegment | None:
    props = feat.get("properties") or {}
    geojson_geom = feat.get("geometry")
    if not geojson_geom:
        return None

    try:
        geom_obj = shape(geojson_geom)
    except Exception as exc:
        logger.debug("WaLLAS: could not parse geometry: %s", exc)
        return None

    if isinstance(geom_obj, MultiLineString):
        lines = list(geom_obj.geoms)
    else:
        lines = [geom_obj]
    if not lines:
        return None

    first_line = lines[0]
    coords = list(first_line.coords)
    if len(coords) < 2:
        return None

    start_coord = coords[0]
    end_coord = coords[-1]

    fid = props.get("OBJECTID")
    if fid is None:
        return None

    f_type = (props.get("F_TYPE") or "").strip()
    permanency = "Permanent" if "PEREN" in f_type.upper() else "Seasonal"

    order_raw = _as_int(props.get("ORDER_NUM"))
    stream_order = order_raw if order_raw and order_raw > 0 else None

    name = (props.get("FEAT_NAME") or "").strip() or None

    try:
        return StreamSegment(
            ogf_id=int(fid),
            watercourse_type="Stream",
            name=name,
            flow_verified=False,
            permanency=permanency,
            flow_classification=f_type or None,
            stream_order=stream_order,
            length_m=_line_length_m(coords),
            geom_wkt=geom_obj.wkt,
            start_node=f"{round(start_coord[0], 5)},{round(start_coord[1], 5)}",
            end_node=f"{round(end_coord[0], 5)},{round(end_coord[1], 5)}",
            jurisdiction="CA-MB",
            segment_source="WaLLAS",
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("WaLLAS: skipping segment OBJECTID=%s: %s", fid, exc)
        return None


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
    if "error" in data:
        raise RuntimeError(f"WaLLAS: ArcGIS error: {data['error']}")
    cache_file.write_text(json.dumps(data))
    return data


# ── geometry helpers ───────────────────────────────────────────────────────────


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _line_length_m(coords: list[tuple[float, float]]) -> float:
    """Sum haversine distance between consecutive lon/lat vertices, in metres."""
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        total += _haversine_m(lat1, lon1, lat2, lon2)
    return round(total, 1)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_deg = radius_km / 111.0
    lon_deg = radius_km / (111.320 * math.cos(math.radians(lat)))
    return (lon - lon_deg, lat - lat_deg, lon + lon_deg, lat + lat_deg)
