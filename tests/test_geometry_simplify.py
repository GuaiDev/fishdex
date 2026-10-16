"""Thinning a drawn shape: the tolerance is metres, topology holds, points pass through."""

import math

import pytest
from shapely.geometry import shape

from src.services.geometry_simplify import simplify_geometry, tolerance_for_zoom


def wiggly_line(n=400, lat=43.6):
    """A line east along a latitude with a 20 m wobble: thinning to 100 m must straighten it."""
    return {
        "type": "LineString",
        "coordinates": [[-80.0 + i * 0.001, lat + (0.0001 if i % 2 else 0.0)] for i in range(n)],
    }


def lake(n=360, r=0.05, lat=43.6, lon=-79.4):
    ring = [
        [lon + r * math.cos(2 * math.pi * i / n), lat + r * math.sin(2 * math.pi * i / n)]
        for i in range(n)
    ]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def n_points(g):
    return len(shape(g).wkt.split(","))


def test_a_line_is_thinned_to_the_tolerance():
    line = wiggly_line()
    thin = simplify_geometry(line, tolerance_m=100)
    assert thin["type"] == "LineString"
    assert n_points(thin) < n_points(line) / 10
    assert shape(thin).coords[0] == tuple(line["coordinates"][0])
    assert shape(thin).coords[-1] == tuple(line["coordinates"][-1])


def test_a_polygon_stays_a_closed_valid_polygon():
    thin = simplify_geometry(lake(), tolerance_m=100)
    poly = shape(thin)
    assert thin["type"] == "Polygon"
    assert poly.is_valid
    assert n_points(thin) < 360
    assert poly.area == pytest.approx(shape(lake()).area, rel=0.05)


def test_a_zero_or_missing_tolerance_changes_nothing():
    line = wiggly_line()
    assert simplify_geometry(line) is line
    assert simplify_geometry(line, tolerance_m=0) is line


def test_a_point_and_a_missing_shape_pass_through():
    point = {"type": "Point", "coordinates": [-79.4, 43.6]}
    assert simplify_geometry(point, tolerance_m=1000) is point
    assert simplify_geometry(None, tolerance_m=1000) is None


def test_zoom_is_one_pixel_of_tolerance_and_a_higher_zoom_keeps_more():
    assert tolerance_for_zoom(0, 0) == pytest.approx(156543.03)
    assert tolerance_for_zoom(10, 60) == pytest.approx(156543.03 * 0.5 / 1024, rel=1e-3)
    line = wiggly_line()
    coarse = simplify_geometry(line, zoom=6)
    fine = simplify_geometry(line, zoom=16)
    assert n_points(coarse) <= n_points(fine) <= n_points(line)


def test_a_multipart_shape_keeps_every_part():
    multi = {
        "type": "MultiLineString",
        "coordinates": [
            wiggly_line(100, lat=43.6)["coordinates"],
            wiggly_line(100, lat=44.6)["coordinates"],
        ],
    }
    thin = simplify_geometry(multi, tolerance_m=100)
    assert thin["type"] == "MultiLineString"
    assert len(thin["coordinates"]) == 2
