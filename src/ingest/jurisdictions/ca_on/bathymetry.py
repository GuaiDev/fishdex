"""LIO Bathymetry ingestion — a lake's survey index and its depth contours.

Source: LIO Open Data MapServer (LIO_Open01)
  Layer 31: Bathymetry Index (one polygon per surveyed lake)
  Layer 30: Bathymetry Line  (the depth contours; DEPTH in metres, <= 0)

The same service the watercourse and waterbody adapters read (ca_on/
hydro_network.py), so these fetches go through that module's 30-day file cache
and the caller's injected fetchers are the only network work a rebuild does.

What the layers are like, measured 2026-10-14 (see the water-body audit):

- The server's `maxRecordCount` is 5,000 for both layers, under the Open
  Government Licence – Ontario (the layer's own `copyrightText`).
- Layer 30 `DEPTH` is a single precision value in metres, zero or negative (0 is
  the shoreline). `SURVEY_DATE` is an epoch-millisecond date and is often NULL
  on the oldest surveys; `SURVEY_METHOD` and `CREATION_METHODOLOGY` are free
  text ("Transect", "Hand Digitized").
- Layer 31 carries `SURVEY_YEAR` and `SURVEY_METHOD` for the whole survey, and
  `WBY_LID`, the source's lake key. `WBY_LID` is NOT present on the OHN
  Waterbody layer (25), so a lake is matched to its survey by overlap instead
  (services/bathymetry.py); the id is stored for reference.

Attribution travels with every contour that reaches the map:
`LIO_BATHYMETRY_ATTRIBUTION` and `LIO_BATHYMETRY_LICENCE_URL` below.
"""

import logging
from typing import Any

from src.ingest.jurisdictions.ca_on.hydro_network import (
    _SERVICE_BASE,
    _cached_get,
    parse_effective_date,
)
from src.ingest.jurisdictions.ca_on.waterbody import rings_to_geojson
from src.models.bathymetry import BathymetryContour, BathymetryIndex

logger = logging.getLogger(__name__)

BATHY_INDEX_LAYER = 31
BATHY_LINE_LAYER = 30
_PAGE_SIZE = 5000
_MAX_PAGES = 50

# The layer's own `copyrightText`. The Open Government Licence – Ontario lets us
# copy and adapt with attribution, so the attribution is not optional.
LIO_BATHYMETRY_LICENCE = "Open Government Licence – Ontario"
LIO_BATHYMETRY_LICENCE_URL = "https://www.ontario.ca/page/open-government-licence-ontario"
LIO_BATHYMETRY_ATTRIBUTION = (
    "Contains information licensed under the Open Government Licence – Ontario. "
    "Bathymetry, Ontario Ministry of Natural Resources (Land Information Ontario)."
)

_INDEX_OUT_FIELDS = (
    "OGF_ID,PROJECT_NAME,WBY_LID,SURVEY_YEAR,SCALE_TEXT,SURVEY_METHOD,"
    "BATHYMETRY_LINE_IND,BATHYMETRY_POINT_IND,HISTORIC_MAP_IND,EFFECTIVE_DATETIME"
)
_LINE_OUT_FIELDS = "OGF_ID,DEPTH,SURVEY_METHOD,SURVEY_DATE,CREATION_METHODOLOGY,LOCATION_ACCURACY"


def fetch_bathymetry_indices(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> list[BathymetryIndex]:
    """Every Bathymetry Index polygon intersecting a bbox. Cached 30 days."""
    url = f"{_SERVICE_BASE}/{BATHY_INDEX_LAYER}/query"
    params = {
        "geometry": _envelope(min_lon, min_lat, max_lon, max_lat),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": _INDEX_OUT_FIELDS,
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": str(_PAGE_SIZE),
        "f": "json",
    }
    features = _fetch_all_pages(url, params)
    out: list[BathymetryIndex] = []
    skipped = 0
    for feat in features:
        index = _parse_index(feat)
        if index is None:
            skipped += 1
        else:
            out.append(index)
    _log_skipped("bathymetry index", skipped, len(features))
    return out


def fetch_bathymetry_contours(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> list[BathymetryContour]:
    """Every Bathymetry Line contour intersecting a bbox. Cached 30 days."""
    url = f"{_SERVICE_BASE}/{BATHY_LINE_LAYER}/query"
    params = {
        "geometry": _envelope(min_lon, min_lat, max_lon, max_lat),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": _LINE_OUT_FIELDS,
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": str(_PAGE_SIZE),
        "f": "json",
    }
    features = _fetch_all_pages(url, params)
    out: list[BathymetryContour] = []
    skipped = 0
    for feat in features:
        contour = _parse_contour(feat)
        if contour is None:
            skipped += 1
        else:
            out.append(contour)
    _log_skipped("bathymetry contour", skipped, len(features))
    return out


# ── paging ──────────────────────────────────────────────────────────────────


def _fetch_all_pages(url: str, params: dict) -> list[dict]:
    """Every feature of a query, following `exceededTransferLimit` page by page.

    The service silently returns a thinned subset when a query is bigger than one
    page, so a single-page read can look complete while missing contours. A query
    still truncated at the page cap is logged at WARNING: a lake's features are
    then derived from an incomplete survey, and that has to be loud.
    """
    features: list[dict] = []
    for page in range(_MAX_PAGES):
        data = _cached_get(url, {**params, "resultOffset": str(page * _PAGE_SIZE)})
        batch = data.get("features", [])
        features.extend(batch)
        if not data.get("exceededTransferLimit") or not batch:
            return features
    logger.warning(
        "LIO bathymetry: query still truncated after %d pages (%d features); "
        "a lake's contours may be incomplete",
        _MAX_PAGES,
        len(features),
    )
    return features


# ── parsers ─────────────────────────────────────────────────────────────────


def _parse_index(feat: dict) -> BathymetryIndex | None:
    attrs = feat.get("attributes", {})
    ogf_id = attrs.get("OGF_ID")
    if ogf_id is None:
        return None
    rings = (feat.get("geometry") or {}).get("rings")
    geometry = rings_to_geojson(rings) if rings else {}
    if not geometry:
        return None
    try:
        return BathymetryIndex(
            ogf_id=int(ogf_id),
            project_name=attrs.get("PROJECT_NAME") or None,
            wby_lid=attrs.get("WBY_LID") or None,
            survey_year=_as_int(attrs.get("SURVEY_YEAR")),
            scale_text=attrs.get("SCALE_TEXT") or None,
            survey_method=attrs.get("SURVEY_METHOD") or None,
            bathymetry_line_ind=_as_bool(attrs.get("BATHYMETRY_LINE_IND")),
            bathymetry_point_ind=_as_bool(attrs.get("BATHYMETRY_POINT_IND")),
            historic_map_ind=_as_bool(attrs.get("HISTORIC_MAP_IND")),
            effective_datetime=parse_effective_date(attrs.get("EFFECTIVE_DATETIME")),
            geometry=geometry,
            licence=LIO_BATHYMETRY_LICENCE,
        )
    except (ValueError, TypeError) as exc:
        logger.debug("Skipping bathymetry index OGF_ID=%s: %s", ogf_id, exc)
        return None


def _parse_contour(feat: dict) -> BathymetryContour | None:
    attrs = feat.get("attributes", {})
    ogf_id = attrs.get("OGF_ID")
    depth = attrs.get("DEPTH")
    if ogf_id is None or depth is None:
        return None
    paths = (feat.get("geometry") or {}).get("paths")
    geometry = _paths_to_geojson(paths) if paths else {}
    if not geometry:
        return None
    try:
        return BathymetryContour(
            ogf_id=int(ogf_id),
            depth_m=float(depth),
            survey_method=attrs.get("SURVEY_METHOD") or None,
            survey_date=parse_effective_date(attrs.get("SURVEY_DATE")),
            creation_methodology=attrs.get("CREATION_METHODOLOGY") or None,
            location_accuracy=attrs.get("LOCATION_ACCURACY") or None,
            geometry=geometry,
            licence=LIO_BATHYMETRY_LICENCE,
        )
    except (ValueError, TypeError) as exc:
        logger.debug("Skipping bathymetry contour OGF_ID=%s: %s", ogf_id, exc)
        return None


def _paths_to_geojson(paths: list[list[list[float]]]) -> dict:
    """Esri line paths → GeoJSON LineString (one path) or MultiLineString (many).

    A path with fewer than two points cannot draw a line and is dropped; the
    caller's count of what it could not parse is what keeps that visible.
    """
    lines = [p for p in paths if p and len(p) >= 2]
    if not lines:
        return {}
    if len(lines) == 1:
        return {"type": "LineString", "coordinates": lines[0]}
    return {"type": "MultiLineString", "coordinates": lines}


# ── helpers ─────────────────────────────────────────────────────────────────


def _envelope(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> str:
    return f"{min_lon:.6f},{min_lat:.6f},{max_lon:.6f},{max_lat:.6f}"


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return value == "Yes" if isinstance(value, str) else bool(value)


def _log_skipped(label: str, skipped: int, total: int) -> None:
    if not skipped:
        return
    share = skipped / max(total, 1)
    log = logger.warning if share >= 0.01 else logger.info
    log("LIO %s: %d of %d features unparseable", label, skipped, total)
