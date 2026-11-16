"""BC Freshwater Atlas (FWA) stream network ingestion via DataBC WFS.

Source: DataBC Open Data — WFS 2.0.0
  Layer: WHSE_BASEMAPPING.FWA_STREAM_NETWORKS_SP
  Endpoint: https://openmaps.gov.bc.ca/geo/pub/WHSE_BASEMAPPING.FWA_STREAM_NETWORKS_SP/ows

Fetch is scoped to a bounding box derived from lat/lon/radius_km. Results are
paginated using WFS 2.0.0 standard startIndex / count parameters. No tiling is
needed: the WFS service returns complete unsampled results for any bbox size
(unlike the ArcGIS MapServer scale-dependent sampling issue in the OHN adapter).

The LINEAR_FEATURE_ID is stored in stream_segments.ogf_id. That column was
named after OHN's OGF_ID but functions as the source-system native integer ID
for all jurisdictions. Use the segment_source column ('OHN' vs 'FWA') to
identify which system assigned the ID.

Geometry: GeoJSON MultiLineString. Segments whose centroid exceeds
_SIMPLIFY_BEYOND_KM from the query centre are simplified to centroid POINT
(same heuristic as the OHN adapter) to reduce storage for large radius fetches.

Cache TTL: 30 days per paginated request.

DataBC WFS BBOX axis order: longitude first (x=lon, y=lat) for EPSG:4326 —
this matches lon/lat order used throughout the rest of the codebase.
"""

import hashlib
import json
import logging
import math
import time
from pathlib import Path

import httpx
from shapely.geometry import MultiLineString, Point, shape

from src.models.hydrology import StreamSegment
from src.services.ingest_runs import record_counts, record_error

_WFS_URL = (
    "https://openmaps.gov.bc.ca/geo/pub/"
    "WHSE_BASEMAPPING.FWA_STREAM_NETWORKS_SP/ows"
)
_TYPE_NAME = "pub:WHSE_BASEMAPPING.FWA_STREAM_NETWORKS_SP"
_PAGE_SIZE = 1000
_CACHE_DIR = Path("data/cache/fwa")
_CACHE_TTL_SECONDS = 2_592_000  # 30 days
_SIMPLIFY_BEYOND_KM = 75.0
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

# FWA EDGE_TYPE codes → watercourse_type string
_EDGE_TYPE_MAP = {
    1000: "Stream",
    1050: "Lake-defined Stream",
    1100: "River",
    1150: "Canal",
    1200: "Reservoir-defined Stream",
    1250: "Constructed",
    1300: "Wetland",
    1350: "Ditch",
    1400: "Tidal",
    1450: "Man-made",
    1475: "Intermittent Stream",
    1800: "Swamp",
}

logger = logging.getLogger(__name__)


def fetch_watercourses(
    lat: float,
    lon: float,
    radius_km: float = 50.0,
) -> list[StreamSegment]:
    """Fetch FWA stream segments within radius_km of lat/lon. Cached 30 days."""
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lon, radius_km)
    # Appending ",CRS:84" tells the WFS to interpret bbox coords as lon/lat and
    # reproject into the layer's native BC Albers CRS before spatial filtering.
    # Without it, the server treats lon/lat values as Albers metres → 0 results.
    bbox_str = f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f},CRS:84"

    base_params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": _TYPE_NAME,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "bbox": bbox_str,
        # GEOMETRY must be listed explicitly; omitting propertyName returns
        # native BC Albers coords, and srsName=EPSG:4326 without GEOMETRY
        # returns null geometry. This combination gives reprojected lon/lat.
        "propertyName": (
            "LINEAR_FEATURE_ID,GNIS_NAME,STREAM_ORDER,STREAM_MAGNITUDE,"
            "EDGE_TYPE,WATERSHED_GROUP_CODE,FEATURE_LENGTH_M,GEOMETRY"
        ),
    }

    features = _wfs_paginate(_WFS_URL, base_params)
    logger.info(
        "FWA watercourse: received %d raw features for %.0fkm radius", len(features), radius_km
    )

    segments: list[StreamSegment] = []
    seen: set[int] = set()
    for feat in features:
        seg = _parse_segment(feat, home_lat=lat, home_lon=lon)
        if seg is not None and seg.ogf_id not in seen:
            seen.add(seg.ogf_id)
            segments.append(seg)

    logger.info("FWA watercourse fetch complete: %d segments", len(segments))
    return segments


# ── WFS pagination ─────────────────────────────────────────────────────────────


def _wfs_paginate(url: str, base_params: dict) -> list[dict]:
    """Paginate a WFS 2.0.0 GetFeature request across all pages."""
    features: list[dict] = []
    start = 0
    while True:
        params = {**base_params, "startIndex": start, "count": _PAGE_SIZE}
        import urllib.parse
        full_url = url + "?" + urllib.parse.urlencode(params)
        logger.info("FWA WFS request: %s", full_url)
        record_counts(fetched=1)
        try:
            data = _cached_get(url, params)
        except Exception as exc:
            record_counts(skipped=1)
            record_error(exc)
            logger.error("FWA WFS request failed at startIndex=%d: %s", start, exc)
            break
        page = data.get("features", [])
        record_counts(empty=int(not page))
        logger.info(
            "FWA WFS startIndex=%d: %d features on page (totalFeatures=%s)",
            start, len(page), data.get("totalFeatures"),
        )
        features.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE
    return features


# ── parser ─────────────────────────────────────────────────────────────────────


def _parse_segment(
    feat: dict,
    home_lat: float | None = None,
    home_lon: float | None = None,
) -> StreamSegment | None:
    props = feat.get("properties") or {}
    geojson_geom = feat.get("geometry")
    if not geojson_geom:
        return None

    try:
        geom_obj = shape(geojson_geom)
    except Exception as exc:
        logger.debug("FWA: could not parse geometry: %s", exc)
        return None

    # MultiLineString → use the first linestring for start/end nodes
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

    start_coord = coords[0]   # (lon, lat)
    end_coord = coords[-1]

    centroid = geom_obj.centroid
    if home_lat is not None and home_lon is not None:
        dist_km = _haversine_km(home_lat, home_lon, centroid.y, centroid.x)
        geom_wkt = (
            Point(centroid.x, centroid.y).wkt
            if dist_km > _SIMPLIFY_BEYOND_KM
            else geom_obj.wkt
        )
    else:
        geom_wkt = geom_obj.wkt

    fid = props.get("LINEAR_FEATURE_ID")
    if fid is None:
        return None

    edge_type = props.get("EDGE_TYPE") or 1000
    watercourse_type = _EDGE_TYPE_MAP.get(int(edge_type), "Stream")

    stream_order_raw = props.get("STREAM_ORDER")
    stream_order = int(stream_order_raw) if stream_order_raw is not None else None

    try:
        return StreamSegment(
            ogf_id=int(fid),
            watercourse_type=watercourse_type,
            name=props.get("GNIS_NAME") or None,
            flow_verified=False,
            permanency="Permanent",
            flow_classification=None,
            stream_order=stream_order,
            length_m=float(props.get("FEATURE_LENGTH_M") or 0.0),
            geom_wkt=geom_wkt,
            start_node=f"{round(start_coord[0], 5)},{round(start_coord[1], 5)}",
            end_node=f"{round(end_coord[0], 5)},{round(end_coord[1], 5)}",
            jurisdiction="CA-BC",
            segment_source="FWA",
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("FWA: skipping segment LINEAR_FEATURE_ID=%s: %s", fid, exc)
        return None


# ── HTTP + file cache ──────────────────────────────────────────────────────────


def _cached_get(url: str, params: dict) -> dict:
    """GET with 30-day file cache keyed by URL + sorted params."""
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


# ── geometry helpers ───────────────────────────────────────────────────────────


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_deg = radius_km / 111.0
    lon_deg = radius_km / (111.320 * math.cos(math.radians(lat)))
    return (lon - lon_deg, lat - lat_deg, lon + lon_deg, lat + lat_deg)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))
