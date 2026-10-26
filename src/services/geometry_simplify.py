"""Thinning a drawn shape for a map that does not need every vertex.

A province's lake outlines come from government data digitised at a scale far finer
than a phone screen. A client that only draws them at zoom 8 can ask for the shape
thinned to what it will show, and get a fraction of the bytes.

The tolerance is a distance in metres: no vertex of the answer is further than that
from the stored line. It is the same arithmetic anywhere, so a province or state added
later needs nothing here. Only lines and polygons are thinned; a point is returned
as it is.
"""

import math

from shapely.affinity import scale
from shapely.geometry import mapping, shape

# Metres in one degree of latitude, and of longitude on the equator.
_M_PER_DEG = 111_320.0
# Web-map tiles are 256 px wide and cover this many metres at zoom 0 on the equator.
_ZOOM0_M_PER_PX = 156_543.03
MAX_ZOOM = 22

_THINNABLE = {"LineString", "MultiLineString", "Polygon", "MultiPolygon"}


def tolerance_for_zoom(zoom: float, lat: float) -> float:
    """Metres on the ground that one screen pixel covers at this zoom and latitude."""
    return _ZOOM0_M_PER_PX * math.cos(math.radians(lat)) / 2**zoom


def simplify_geometry(
    geometry: dict | None, tolerance_m: float | None = None, zoom: float | None = None
) -> dict | None:
    """The geometry thinned to `tolerance_m` metres, or to one pixel at `zoom`.

    Returns the input untouched when neither is given or the shape cannot be thinned.
    Topology is preserved, so a polygon never turns into a line or crosses itself.
    A shape thinned to nothing keeps its original, rather than becoming empty.
    """
    if not geometry or geometry.get("type") not in _THINNABLE or not geometry.get("coordinates"):
        return geometry
    if tolerance_m is None and zoom is None:
        return geometry
    geom = shape(geometry)
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    mid_lat = (min_lat + max_lat) / 2
    if tolerance_m is None:
        tolerance_m = tolerance_for_zoom(zoom, mid_lat)
    if tolerance_m <= 0:
        return geometry
    # A degree of longitude is shorter than one of latitude away from the equator, so
    # squeeze longitude into the same units, thin, and put it back.
    squeeze = max(math.cos(math.radians(mid_lat)), 0.01)
    flat = scale(geom, xfact=squeeze, yfact=1.0, origin=(0, 0))
    thin = flat.simplify(tolerance_m / _M_PER_DEG, preserve_topology=True)
    if thin.is_empty:
        return geometry
    return mapping(scale(thin, xfact=1 / squeeze, yfact=1.0, origin=(0, 0)))
