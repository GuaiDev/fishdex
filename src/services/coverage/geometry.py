"""Distances in metres, in pure Python.

The coverage check measures things a few hundred metres apart inside one region,
so a local equirectangular projection is accurate to well under a metre per
kilometre and needs no GIS library. (numpy-backed libraries also fail to import
on some machines this check must run on.)
"""

import json
import math

_M_PER_DEG_LAT = 111_320.0


def _m_per_deg_lon(lat: float) -> float:
    return _M_PER_DEG_LAT * math.cos(math.radians(lat))


def metres_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points, projected locally around their mean latitude."""
    mid = (lat1 + lat2) / 2
    dx = (lon2 - lon1) * _m_per_deg_lon(mid)
    dy = (lat2 - lat1) * _M_PER_DEG_LAT
    return math.hypot(dx, dy)


def point_to_segment_m(
    lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Metres from a point to the segment a-b, both given as (lon, lat)."""
    kx = _m_per_deg_lon(lat)
    ax, ay = (a[0] - lon) * kx, (a[1] - lat) * _M_PER_DEG_LAT
    bx, by = (b[0] - lon) * kx, (b[1] - lat) * _M_PER_DEG_LAT
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
    return math.hypot(ax + t * dx, ay + t * dy)


def lines_of(geometry_geojson: str | dict | None) -> list[list[tuple[float, float]]]:
    """Every line of a LineString or MultiLineString as lists of (lon, lat)."""
    if not geometry_geojson:
        return []
    geom = json.loads(geometry_geojson) if isinstance(geometry_geojson, str) else geometry_geojson
    kind = geom.get("type")
    coords = geom.get("coordinates") or []
    if kind == "LineString":
        raw = [coords]
    elif kind == "MultiLineString":
        raw = coords
    else:
        return []
    return [[(float(p[0]), float(p[1])) for p in line] for line in raw if len(line) >= 2]


def point_to_lines_m(lat: float, lon: float, lines: list[list[tuple[float, float]]]) -> float:
    """Metres from a point to the nearest part of any line; inf when there are none."""
    best = math.inf
    for line in lines:
        for a, b in zip(line, line[1:]):
            d = point_to_segment_m(lat, lon, a, b)
            if d < best:
                best = d
    return best


def bbox_of(lines: list[list[tuple[float, float]]]) -> tuple[float, float, float, float] | None:
    """(min_lon, min_lat, max_lon, max_lat) of the lines, or None when empty."""
    xs = [p[0] for line in lines for p in line]
    ys = [p[1] for line in lines for p in line]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def pad_bbox(
    box: tuple[float, float, float, float], lat: float, metres: float
) -> tuple[float, float, float, float]:
    """Grow a box by `metres` on every side, at latitude `lat`."""
    dy = metres / _M_PER_DEG_LAT
    dx = metres / _m_per_deg_lon(lat)
    return box[0] - dx, box[1] - dy, box[2] + dx, box[3] + dy
