"""Saskatchewan stream network — SaskGIS Hydrography watercourse layer.

Source: Saskatchewan Geospatial Network (SaskGIS) — ArcGIS MapServer layer 121
  (watercourse tier; the 1:20,000 / 1:50,000 NHN-derived network)
  https://gis.saskatchewan.ca/arcgis/rest/services/Hydrography/MapServer/121

Fetched scoped to a bounding box derived from lat/lon/radius_km, paginated with
the standard ArcGIS resultOffset / resultRecordCount parameters and outSR=4326
for lon/lat coordinates.

Field domains (verified against the layer metadata, 2026):
  DEFINITION : 0=None 1=Canal 2=Conduit 3=Ditch 6=Watercourse 7=Tidal river
  PERMANENCY : -1=Unknown 1=Permanent 2=Intermittent
  THEME      : HD=Hydrography (and other National Topographic themes)

`NAMEEN` is blank for most segments. Stream order is not published, so
`stream_order` is NULL rather than fabricated.

The layer's OBJECTID is stored in `stream_segments.ogf_id` (the source-system
native integer ID for all jurisdictions; see JURISDICTION_TEMPLATE.md).

Cache TTL: 90 days (the base network is effectively static).

Note: tiers 107 / 115 / 121 / 123 on this service return identical watercourse
data (different scales of the same NHN network); layer 121 is used here.
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

_QUERY_URL = "https://gis.saskatchewan.ca/arcgis/rest/services/Hydrography/MapServer/121/query"
_PAGE_SIZE = 1000
_CACHE_DIR = Path("data/cache/sk_hydro")
_CACHE_TTL_SECONDS = 90 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

_DEFINITION_MAP = {
    0: "Unknown",
    1: "Canal",
    2: "Conduit",
    3: "Ditch",
    6: "Stream",
    7: "Tidal river",
}
_PERMANENCY_MAP = {-1: "Unknown", 1: "Permanent", 2: "Intermittent"}

logger = logging.getLogger(__name__)


def fetch_watercourses(
    lat: float,
    lon: float,
    radius_km: float = 50.0,
) -> list[StreamSegment]:
    """Fetch SK hydrography segments within radius_km of lat/lon. Cached 90 days."""
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lon, radius_km)
    base_params = {
        "where": "1=1",
        "geometry": f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OBJECTID,NAMEEN,PERMANENCY,DEFINITION,THEME,NHNAREA",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    features = _query_paginate(_QUERY_URL, base_params)
    logger.info("SK hydro: received %d raw features for %.0fkm radius", len(features), radius_km)

    segments: list[StreamSegment] = []
    seen: set[int] = set()
    for feat in features:
        seg = _parse_segment(feat)
        if seg is not None and seg.ogf_id not in seen:
            seen.add(seg.ogf_id)
            segments.append(seg)

    logger.info("SK hydro fetch complete: %d segments", len(segments))
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
        if "error" in data:
            raise RuntimeError(f"SK hydro: ArcGIS error at offset={offset}: {data['error']}")
        page = data.get("features", [])
        features.extend(page)
        logger.debug("SK hydro: offset=%d → %d features", offset, len(page))
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
        logger.debug("SK hydro: could not parse geometry: %s", exc)
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

    definition = _as_int(props.get("DEFINITION"))
    watercourse_type = (
        _DEFINITION_MAP.get(definition, "Stream") if definition is not None else "Stream"
    )

    permanency_code = _as_int(props.get("PERMANENCY"))
    permanency = (
        _PERMANENCY_MAP.get(permanency_code, "Unknown")
        if permanency_code is not None
        else "Unknown"
    )

    name = (props.get("NAMEEN") or "").strip() or None

    try:
        return StreamSegment(
            ogf_id=int(fid),
            watercourse_type=watercourse_type,
            name=name,
            flow_verified=False,
            permanency=permanency,
            flow_classification=(props.get("THEME") or "").strip() or None,
            stream_order=None,
            length_m=_line_length_m(coords),
            geom_wkt=geom_obj.wkt,
            start_node=f"{round(start_coord[0], 5)},{round(start_coord[1], 5)}",
            end_node=f"{round(end_coord[0], 5)},{round(end_coord[1], 5)}",
            jurisdiction="CA-SK",
            segment_source="SKHydro",
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("SK hydro: skipping segment OBJECTID=%s: %s", fid, exc)
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
