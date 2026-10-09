"""Alberta stream network ingestion — FWMIS Simplified Hydro Arcs.

Source: FWMIS Fisheries & Wildlife Management Information System via
Geospatial Alberta ArcGIS FeatureServer.
  Service:  fwmis_hydrography
  Layer 0:  fwmis_simplified_hydroarcs (stream network as polylines)

Endpoint:
  https://geospatial.alberta.ca/titan/rest/services/fisheries/fwmis_hydrography/FeatureServer/0/query

The layer exposes stream arcs derived from Alberta's hydrography network.
Each arc carries a water-body id (WB_ID), official/common names, a Strahler
stream order and its length in metres. Geometry is returned in WGS84 directly
when outSR=4326 is requested — no reprojection needed (unlike the BC FWA
adapter). The service advertises a maxRecordCount of 2000 and honours
resultOffset pagination via the exceededTransferLimit flag.

Like the OHN adapter, the outer bbox is pre-tiled into ~0.5° sub-tiles so each
query stays small enough to return unsampled, complete results; pages inside a
tile are walked with resultOffset. Features are deduplicated by OBJECTID
across tiles and pages.

FWMIS has no field for flow direction verification, permanency or a
watercourse-type taxonomy like OHN/FWA — for those fields the same defaults
the FWA adapter uses are kept (flow_verified=False, permanency="Permanent",
watercourse_type="Stream").

Storage: start_node / end_node are the (lon lat) endpoints of the first arc
part rounded to 5 decimals; segments farther than _SIMPLIFY_BEYOND_KM from the
query centre are stored as centroid POINT to halve storage (same heuristic as
the OHN and FWA adapters). Topology is preserved either way.

Cache: 30 days per paginated request under data/cache/fwmis.
"""

import hashlib
import json
import logging
import math
import time
from pathlib import Path

import httpx
from shapely.geometry import LineString, MultiLineString, Point

from src.models.hydrology import StreamSegment

_SERVICE_BASE = (
    "https://geospatial.alberta.ca/titan/rest/services/"
    "fisheries/fwmis_hydrography/FeatureServer/0"
)
_PAGE_SIZE = 2000
_CACHE_DIR = Path("data/cache/fwmis")
_CACHE_TTL_SECONDS = 2_592_000  # 30 days
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"
# Segments beyond this distance from the query centre get simplified to POINT WKT
_SIMPLIFY_BEYOND_KM = 75.0
# Maximum recursion depth for the bbox-tiling guard against server record caps
_MAX_TILE_DEPTH = 5
# Pre-tile the outer bbox into ~0.5° sub-tiles (~55km lat × ~38km lon at 50°N)
_TILE_DEG = 0.5

logger = logging.getLogger(__name__)


def fetch_watercourses(lat: float, lon: float, radius_km: float = 50.0) -> list[StreamSegment]:
    """Fetch FWMIS simplified hydro arcs within radius_km of lat/lon. Cached 30 days."""
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lon, radius_km)
    base_params = {
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": (
            "OBJECTID,WB_ID,OFFICIAL_NM,COMMON_NM,STR_ORDER,HUC_8,Shape__Length"
        ),
        "returnGeometry": "true",
        "resultRecordCount": _PAGE_SIZE,
        "f": "json",
    }
    url = f"{_SERVICE_BASE}/query"

    seen: dict[int, dict] = {}
    tiles = list(_grid_tiles(min_lon, min_lat, max_lon, max_lat))
    logger.info("FWMIS hydroarcs: fetching %d sub-tiles for %.0fkm radius", len(tiles), radius_km)
    for i, (t_min_lon, t_min_lat, t_max_lon, t_max_lat) in enumerate(tiles, 1):
        for feat in _fetch_tile(url, base_params, t_min_lon, t_min_lat, t_max_lon, t_max_lat):
            obj_id = feat.get("attributes", {}).get("OBJECTID")
            if obj_id is not None and obj_id not in seen:
                seen[obj_id] = feat
        if i % 50 == 0:
            logger.info(
                "FWMIS hydroarcs: processed %d/%d tiles (%d features so far)",
                i,
                len(tiles),
                len(seen),
            )

    segments: list[StreamSegment] = []
    for feat in seen.values():
        seg = _parse_segment(feat, home_lat=lat, home_lon=lon)
        if seg is not None:
            segments.append(seg)

    skipped = len(seen) - len(segments)
    if skipped:
        # A feature that fails to parse one field is a row that silently stops
        # existing in the network — surface the share at WARNING when material.
        share = skipped / len(seen)
        log = logger.warning if share >= 0.01 else logger.info
        log("FWMIS hydroarcs: skipped %d of %d features (%.2f%%)", skipped, len(seen), share * 100)

    logger.info("FWMIS hydroarcs fetch complete: %d segments", len(segments))
    return segments


# ── grid tiling ───────────────────────────────────────────────────────────────


def _grid_tiles(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> list[tuple[float, float, float, float]]:
    """Split a bbox into sub-tiles of _TILE_DEG × _TILE_DEG."""
    tiles: list[tuple[float, float, float, float]] = []
    lat = min_lat
    while lat < max_lat:
        t_max_lat = min(lat + _TILE_DEG, max_lat)
        lon = min_lon
        while lon < max_lon:
            t_max_lon = min(lon + _TILE_DEG, max_lon)
            tiles.append((lon, lat, t_max_lon, t_max_lat))
            lon = t_max_lon
        lat = t_max_lat
    return tiles


# ── tiled pagination ──────────────────────────────────────────────────────────


def _fetch_tile(
    url: str,
    base_params: dict,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    depth: int = 0,
) -> list[dict]:
    """Paginate one bbox tile with resultOffset; recurse into quadrants when a
    tile returns an exact multiple of _PAGE_SIZE (server record cap suspected).
    """
    if depth > _MAX_TILE_DEPTH:
        logger.warning(
            "FWMIS: max tiling depth %d reached for bbox %.3f,%.3f,%.3f,%.3f — may be incomplete",
            _MAX_TILE_DEPTH,
            min_lon,
            min_lat,
            max_lon,
            max_lat,
        )
        return []

    bbox_str = f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}"
    features: list[dict] = []
    offset = 0

    while True:
        params = {**base_params, "geometry": bbox_str, "resultOffset": offset}
        data = _cached_get(url, params)
        page = data.get("features", [])
        features.extend(page)
        logger.debug("FWMIS tile depth=%d offset=%d: %d features", depth, offset, len(page))

        # FWMIS signals "more rows exist" via exceededTransferLimit; a short
        # page without that flag is a genuinely complete tile.
        if not page or not data.get("exceededTransferLimit"):
            break
        offset += _PAGE_SIZE

    # Exact multiple of _PAGE_SIZE → server may have capped results; tile to confirm
    if features and len(features) % _PAGE_SIZE == 0:
        logger.info(
            "FWMIS: possible record cap at %d features (depth=%d) — splitting into quadrants",
            len(features),
            depth,
        )
        mid_lon = (min_lon + max_lon) / 2
        mid_lat = (min_lat + max_lat) / 2
        quadrants = [
            (min_lon, min_lat, mid_lon, mid_lat),
            (mid_lon, min_lat, max_lon, mid_lat),
            (min_lon, mid_lat, mid_lon, max_lat),
            (mid_lon, mid_lat, max_lon, max_lat),
        ]
        seen: set = set()
        tiled: list[dict] = []
        for q in quadrants:
            for feat in _fetch_tile(url, base_params, *q, depth=depth + 1):
                obj_id = feat.get("attributes", {}).get("OBJECTID")
                if obj_id not in seen:
                    seen.add(obj_id)
                    tiled.append(feat)
        return tiled

    return features


# ── internal parser ───────────────────────────────────────────────────────────


def _parse_segment(
    feat: dict,
    home_lat: float | None = None,
    home_lon: float | None = None,
) -> StreamSegment | None:
    attrs = feat.get("attributes", {})
    geom = feat.get("geometry", {})
    paths = geom.get("paths", [])

    if not paths or not paths[0] or len(paths[0]) < 2:
        return None

    try:
        if len(paths) == 1:
            line = LineString(paths[0])
        else:
            line = MultiLineString(paths)
    except Exception:
        return None

    first_coords = paths[0]
    start = first_coords[0]
    end = first_coords[-1]

    # Simplify distant segments to centroid POINT to halve storage while
    # preserving topology (start_node / end_node come from the original line)
    if home_lat is not None and home_lon is not None:
        c = line.centroid
        dist_km = _haversine_km(home_lat, home_lon, c.y, c.x)
        geom_wkt = Point(c.x, c.y).wkt if dist_km > _SIMPLIFY_BEYOND_KM else line.wkt
    else:
        geom_wkt = line.wkt

    name = attrs.get("COMMON_NM") or attrs.get("OFFICIAL_NM") or None
    if name == "UNNAMED":
        name = None

    order_raw = attrs.get("STR_ORDER")
    stream_order = int(order_raw) if order_raw is not None else None

    try:
        return StreamSegment(
            ogf_id=int(attrs["OBJECTID"]),
            watercourse_type="Stream",
            name=name,
            flow_verified=False,  # FWMIS has no flow-direction-verification field
            permanency="Permanent",  # FWMIS does not distinguish seasonal/permanent
            flow_classification=None,
            stream_order=stream_order,
            length_m=float(attrs.get("Shape__Length") or 0.0),
            geom_wkt=geom_wkt,
            start_node=f"{round(start[0], 5)},{round(start[1], 5)}",
            end_node=f"{round(end[0], 5)},{round(end[1], 5)}",
            jurisdiction="CA-AB",
            segment_source="FWMIS",
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Skipping segment OBJECTID=%s: %s", attrs.get("OBJECTID"), exc)
        return None


# ── HTTP + cache ──────────────────────────────────────────────────────────────


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
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data))
    return data


# ── geometry helpers ──────────────────────────────────────────────────────────


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat) bounding box for a circle."""
    lat_deg = radius_km / 111.0
    lon_deg = radius_km / (111.320 * math.cos(math.radians(lat)))
    return (lon - lon_deg, lat - lat_deg, lon + lon_deg, lat + lat_deg)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))