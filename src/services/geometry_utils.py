"""Small geometry helpers shared by the water-body outline and feature services.

Kept in one place so the convexity and island code does not each grow its own
copy of "flatten this shapely geometry to polygons" and "roughly how many square
metres is this". Areas are approximations in degrees converted at the shape's own
latitude — good enough to rank islands and cap a list, never a survey figure.
"""

import math

M_PER_DEG = 111_320.0


def polygons_of(geom) -> list:
    """Every polygon part of a shapely geometry, dropping stray lines and points."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out: list = []
        for part in geom.geoms:
            out.extend(polygons_of(part))
        return out
    return []


def approx_area_m2(polygon) -> float:
    """A polygon's area in square metres, from its degrees at its own latitude."""
    lat = polygon.representative_point().y
    return polygon.area * (M_PER_DEG**2) * math.cos(math.radians(lat))


def approx_length_m(geom) -> float:
    """A geometry's length in metres, from its degrees at its own latitude."""
    lat = geom.representative_point().y
    return geom.length * M_PER_DEG * math.cos(math.radians(lat))
