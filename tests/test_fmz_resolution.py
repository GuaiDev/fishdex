"""Zone resolution must be correct or refuse — never a confident guess.

The predecessor was a table of 20 overlapping lat/lng rectangles resolved by
first match. Oakville (Lake Ontario) resolved to FMZ 5 — Rainy River, ~1,500km
away — and returned that zone's limits with no uncertainty marker. The old test
asserted only `1 <= zone <= 20`, which is why it never caught it.
"""

import json

import pytest

from src.storage.fmz_boundaries import boundary_count, resolve_zone

OAKVILLE = (43.4675, -79.6877)


@pytest.fixture
def db(fast_db):
    return fast_db
def _add_zone(db, zone, name, minx, miny, maxx, maxy):
    db["fmz_boundaries"].upsert({
        "zone": zone, "zone_name": name, "jurisdiction": "CA-ON",
        "geom_wkt": (
            f"POLYGON(({minx} {miny}, {maxx} {miny}, {maxx} {maxy}, "
            f"{minx} {maxy}, {minx} {miny}))"
        ),
        "bbox_minx": minx, "bbox_miny": miny, "bbox_maxx": maxx, "bbox_maxy": maxy,
    }, pk="zone", alter=True)


def test_no_boundaries_loaded_refuses_rather_than_guessing(db):
    assert boundary_count(db) == 0
    r = resolve_zone(db, *OAKVILLE)
    assert not r.resolved
    assert r.empty_reason == "zone_boundaries_not_loaded"


def test_point_inside_a_polygon_resolves(db):
    _add_zone(db, 20, "Lake Ontario", -80.0, 43.2, -76.5, 44.0)
    r = resolve_zone(db, *OAKVILLE)
    assert r.resolved
    assert r.zone == 20


def test_point_outside_every_polygon_refuses(db):
    _add_zone(db, 20, "Lake Ontario", -80.0, 43.2, -76.5, 44.0)
    r = resolve_zone(db, 40.7, -74.0)  # New York City
    assert not r.resolved
    assert r.empty_reason == "outside_known_zones"


def test_bbox_hit_without_polygon_containment_still_refuses(db):
    """The bbox only narrows candidates — it must never decide the answer.

    That conflation is exactly what the old rectangle table did.
    """
    # An L-shaped zone whose bounding box covers Oakville but whose polygon does not.
    db["fmz_boundaries"].upsert({
        "zone": 5, "zone_name": "Elsewhere", "jurisdiction": "CA-ON",
        "geom_wkt": (
            "POLYGON((-80.0 43.2, -79.0 43.2, -79.0 43.3, -79.9 43.3, "
            "-79.9 44.0, -80.0 44.0, -80.0 43.2))"
        ),
        "bbox_minx": -80.0, "bbox_miny": 43.2, "bbox_maxx": -79.0, "bbox_maxy": 44.0,
    }, pk="zone", alter=True)
    r = resolve_zone(db, *OAKVILLE)
    assert not r.resolved, "a bounding-box hit is not containment"


def test_overlapping_polygons_refuse_rather_than_pick_the_first(db):
    """First-match-wins over overlapping shapes is the original bug."""
    _add_zone(db, 20, "Lake Ontario", -80.0, 43.2, -76.5, 44.0)
    _add_zone(db, 5, "Overlapping", -80.0, 43.2, -79.0, 44.0)
    r = resolve_zone(db, *OAKVILLE)
    assert not r.resolved
    assert r.empty_reason == "ambiguous_zone"
    assert "FMZ 5" in r.detail and "FMZ 20" in r.detail


def test_regulations_are_withheld_when_the_zone_is_unresolved(db, monkeypatch):
    """Retrieved-or-refused has to cover locating the reader, not just the text."""
    import src.services.regulations as regs

    monkeypatch.setattr(regs, "get_db", lambda: db)
    r = json.loads(regs.get_regulations_for_agent(lat=OAKVILLE[0], lng=OAKVILLE[1]))
    assert r.get("regulations_withheld") is True
    assert "text" not in r
