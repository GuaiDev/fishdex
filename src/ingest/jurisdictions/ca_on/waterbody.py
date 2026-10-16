"""Ontario Hydro Network (OHN) Waterbody ingestion — lake and pond polygons.

Source: LIO Open Data MapServer (LIO_Open01)
  Layer 25: OHN Waterbody (lake, pond, reservoir and wide-river polygons)

This is the layer the map needs to give the curated lakes a real outline. The
same service the watercourse adapter reads (ca_on/hydro_network.py), so the
fetch goes through that module's 30-day file cache.

What the layer is like, measured 2026-10-12 (see the water-body audit):

- 1.4 M polygons under the Open Government Licence – Ontario (the layer's own
  `copyrightText`). Names are bilingual — "Lake Ontario (lac Ontario)" — and
  NULL (not empty) when unnamed. Only 2.5 % of "Lake" polygons carry a name.
- The Great Lakes are sliced: Lake Ontario is several large polygons, Erie two,
  Huron and Georgian Bay several each. A single lake is therefore a *set* of
  polygons, unioned only when the caller knows they are one water.
- Names are not unique ("Rice Lake" matches unrelated ponds). A polygon is
  matched to our curated lake by position, never by name alone.

Attribution travels with every polygon that reaches the map:
`OHN_WATERBODY_ATTRIBUTION` and `OHN_WATERBODY_LICENCE_URL` below.
"""

import logging
import re
from typing import Any

from pydantic import BaseModel, Field
from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.geometry.polygon import orient

from src.ingest.jurisdictions.ca_on.hydro_network import (
    _SERVICE_BASE,
    _cached_get,
    parse_effective_date,
)

logger = logging.getLogger(__name__)

_WATERBODY_LAYER = 25
_PAGE_SIZE = 1000
# A by-name page is asked for in small pieces on purpose: the polygons of a
# sliced lake (Georgian Bay) are too heavy for one 1000-record page — the
# service answers 500 — and at some sizes in between it answers an EMPTY page
# as if it were the last one. A small page plus the count yardstick in
# _fetch_all_pages turns both into either a complete pull or a loud failure.
_BY_NAME_PAGE_SIZE = 10

# The layer's own `copyrightText`. The Open Government Licence – Ontario lets us
# copy and adapt with attribution, so the attribution is not optional.
OHN_WATERBODY_LICENCE = "Open Government Licence – Ontario"
OHN_WATERBODY_LICENCE_URL = "https://www.ontario.ca/page/open-government-licence-ontario"
OHN_WATERBODY_ATTRIBUTION = (
    "Contains information licensed under the Open Government Licence – Ontario. "
    "Ontario Hydro Network (OHN) Waterbody, Ontario Ministry of Natural Resources."
)

# Out fields shared by every query; geometry is the expensive part, so it is
# asked for explicitly only where the caller needs a shape.
_OUT_FIELDS = (
    "OGF_ID,WATERBODY_TYPE,OFFICIAL_NAME_LABEL,GEL_NAME_IDENT,"
    "SYSTEM_CALCULATED_AREA,EFFECTIVE_DATETIME"
)

_TRAILING_BRACKET = re.compile(r"\s*\([^)]*\)\s*$")


class OhnWaterbody(BaseModel):
    """One OHN Waterbody polygon, parsed and ready to match by position."""

    ogf_id: int
    waterbody_type: str
    raw_name: str | None = None
    """The layer's OFFICIAL_NAME_LABEL, still bilingual, e.g. 'Lake Ontario (lac Ontario)'."""
    name: str | None = None
    """The English name with the trailing bilingual bracket stripped; None if unnamed."""
    area_m2: float | None = None
    effective_datetime: str | None = None
    """ISO date the record took effect, from EFFECTIVE_DATETIME (epoch ms)."""
    geometry: dict = Field(default_factory=dict)
    """GeoJSON Polygon or MultiPolygon (islands are interior rings)."""


def strip_bilingual(name: str | None) -> str | None:
    """'Lake Ontario (lac Ontario)' -> 'Lake Ontario'; None stays None."""
    if not name:
        return None
    stripped = _TRAILING_BRACKET.sub("", name).strip()
    return stripped or None


def rings_to_geojson(rings: list[list[list[float]]]) -> dict:
    """Convert Esri polygon rings to a GeoJSON (Multi)Polygon.

    ArcGIS returns every ring of a feature in one flat `rings` list — outer rings
    and the holes (islands) carved out of them together, with no grouping. Rings
    are grouped by containment: a ring whose representative point falls inside a
    larger ring is a hole of it, everything else is an outer ring. Outer rings
    are oriented counter-clockwise and holes clockwise, per the GeoJSON
    right-hand rule.
    """
    parsed: list[Polygon] = []
    for ring in rings:
        if not ring or len(ring) < 4:
            continue
        try:
            poly = Polygon(ring)
        except (ValueError, TypeError):
            continue
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area == 0:
            continue
        if poly.geom_type == "MultiPolygon":  # buffer(0) can split a bow-tie
            parsed.extend(poly.geoms)
        else:
            parsed.append(poly)

    parsed.sort(key=lambda p: p.area, reverse=True)
    shells: list[list] = []  # [[shell_polygon, [hole_polygons]], ...]
    for poly in parsed:
        for entry in shells:
            if entry[0].contains(poly.representative_point()):
                entry[1].append(poly)
                break
        else:
            shells.append([poly, []])

    out: list[Polygon] = []
    for shell, holes in shells:
        poly = Polygon(shell.exterior, [h.exterior for h in holes])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        out.append(orient(poly, sign=1.0))

    if not out:
        return {}
    if len(out) == 1:
        return mapping(out[0])
    return mapping(MultiPolygon(out))


def fetch_waterbodies_near(
    lon: float,
    lat: float,
    radius_m: float,
    *,
    with_geometry: bool = True,
) -> list[OhnWaterbody]:
    """Every OHN Waterbody whose polygon intersects a circle around (lon, lat).

    Used to match a curated lake to its polygon by position. `radius_m` is a
    metre buffer around the point, resolved server-side in the Web Mercator
    sense by ArcGIS; it is a search radius, not a matching tolerance.
    """
    url = f"{_SERVICE_BASE}/{_WATERBODY_LAYER}/query"
    params = {
        "geometry": _point_geometry(lon, lat),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "distance": str(radius_m),
        "units": "esriSRUnit_Meter",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": _OUT_FIELDS,
        "returnGeometry": "true" if with_geometry else "false",
        "outSR": "4326",
        "resultRecordCount": str(_PAGE_SIZE),
        "f": "json",
    }
    return _parse_features(_fetch_all_pages(url, params))


def fetch_waterbodies_by_name(name: str) -> list[OhnWaterbody]:
    """Every OHN Waterbody whose OFFICIAL_NAME_LABEL contains `name`.

    A name search is never a match: names repeat across unrelated water. It is
    used only to gather the polygons of a lake already identified by position
    (the Great Lakes are sliced into several), which are then unioned.
    """
    url = f"{_SERVICE_BASE}/{_WATERBODY_LAYER}/query"
    params = {
        "where": f"UPPER(OFFICIAL_NAME_LABEL) LIKE UPPER('%{_escape_like(name)}%')",
        "outFields": _OUT_FIELDS,
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": str(_BY_NAME_PAGE_SIZE),
        "f": "json",
    }
    return _parse_features(_fetch_all_pages(url, params))


def _fetch_all_pages(url: str, params: dict) -> list[dict]:
    """Every feature of a query, paged until the service's own count is met.

    The service silently returns a thinned subset when a query is bigger than
    one page, and an empty page as if it were the last when a heavy-geometry
    page is too big to serialize — so a short or empty page is never trusted as
    the end. Completeness is judged against the service's own count for the
    same query (the same yardstick the watercourse tiles use). There is no page
    ceiling: paging runs until the count is met, and a pull the service ends
    early raises rather than return a partial lake. A count the service does
    not give falls back to the `exceededTransferLimit` flag alone.
    """
    expected = _count_features(url, params)
    page_size = int(params.get("resultRecordCount", _PAGE_SIZE))
    features: list[dict] = []
    page = 0
    while True:
        data = _cached_get(url, {**params, "resultOffset": str(page * page_size)})
        page += 1
        batch = data.get("features", [])
        features.extend(batch)
        if expected is not None:
            if len(features) >= expected:
                return features
            if not batch:
                raise RuntimeError(
                    f"OHN waterbody: service ended paging at {len(features)} of "
                    f"{expected} features it counts for this query"
                )
        elif not data.get("exceededTransferLimit") or not batch:
            return features


def _count_features(url: str, params: dict) -> int | None:
    """The service's own count for the same query, or None when it gives none."""
    count_params = {
        k: v
        for k, v in params.items()
        if k not in ("resultRecordCount", "resultOffset", "outFields", "returnGeometry", "outSR")
    }
    count_params["returnCountOnly"] = "true"
    data = _cached_get(url, count_params)
    count = data.get("count")
    return count if isinstance(count, int) else None


def _escape_like(value: str) -> str:
    """Escape the LIKE wildcards in a user-supplied name. `str.replace` only."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _point_geometry(lon: float, lat: float) -> str:
    return f'{{"x":{lon},"y":{lat},"spatialReference":{{"wkid":4326}}}}'


def _parse_features(features: list[dict]) -> list[OhnWaterbody]:
    out: list[OhnWaterbody] = []
    skipped = 0
    for feat in features:
        attrs = feat.get("attributes", {})
        ogf_id = attrs.get("OGF_ID")
        if ogf_id is None:
            skipped += 1
            continue
        geometry: dict = {}
        rings = (feat.get("geometry") or {}).get("rings")
        if rings:
            geometry = rings_to_geojson(rings)
        raw_name = attrs.get("OFFICIAL_NAME_LABEL")
        out.append(
            OhnWaterbody(
                ogf_id=int(ogf_id),
                waterbody_type=attrs.get("WATERBODY_TYPE") or "Unknown",
                raw_name=raw_name,
                name=strip_bilingual(raw_name),
                area_m2=_as_float(attrs.get("SYSTEM_CALCULATED_AREA")),
                effective_datetime=parse_effective_date(attrs.get("EFFECTIVE_DATETIME")),
                geometry=geometry,
            )
        )
    if skipped:
        share = skipped / max(len(features), 1)
        log = logger.warning if share >= 0.01 else logger.info
        log("OHN waterbody: %d of %d features had no OGF_ID", skipped, len(features))
    return out


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
