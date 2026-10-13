"""Matching a curated lake to its OHN Waterbody outline, by position.

The fetchers are injected, so nothing here touches the network: the tests hand
`resolve_lake_outlines` the polygons a search would have returned.
"""

from src.ingest.jurisdictions.ca_on.waterbody import OhnWaterbody
from src.models.lake_outline import MATCH_LARGEST, MATCH_NAME, MATCH_NONE
from src.services.lake_outlines import (
    MATCH_TOLERANCE_M,
    match_lake_outline,
    resolve_lake_outlines,
)


def wb(ogf_id, name=None, type_="Lake", area=1.0, box=(0.0, 0.0, 0.01, 0.01)):
    x0, y0, x1, y1 = box
    ring = [[x0, y0], [x0, y1], [x1, y1], [x1, y0], [x0, y0]]
    return OhnWaterbody(
        ogf_id=ogf_id,
        waterbody_type=type_,
        raw_name=name,
        name=name,
        area_m2=area,
        geometry={"type": "Polygon", "coordinates": [ring]},
    )


# ── match_lake_outline ────────────────────────────────────────────────────────


def test_a_named_polygon_wins_over_a_larger_unnamed_one():
    small = wb(1, "Rice Lake", area=1000.0)
    big = wb(2, None, area=999999.0)
    out = match_lake_outline("Rice Lake", [(0.005, 0.005)], [big, small])
    assert out.matched is True
    assert out.ogf_ids == [1]
    assert out.match_basis == MATCH_NAME


def test_without_a_name_match_the_largest_lake_type_polygon_is_the_parent():
    pond = wb(2, "Duck Pond", area=5.0)
    lake = wb(3, "Somewhere Bay", area=500.0)
    out = match_lake_outline("Humber Bay", [(0.005, 0.005)], [pond, lake])
    assert out.ogf_ids == [3]
    assert out.match_basis == MATCH_LARGEST


def test_non_lake_types_are_never_matched():
    river = wb(9, "Credit River", type_="River")
    out = match_lake_outline("Credit River", [(0.005, 0.005)], [river])
    assert out.matched is False
    assert out.match_basis == MATCH_NONE


def test_no_candidate_leaves_the_outline_empty_with_a_reason():
    out = match_lake_outline("Rice Lake", [(0.005, 0.005)], [])
    assert out.matched is False
    assert out.geometry is None
    assert "search radius" in out.note


def test_a_mouth_inside_the_polygon_measures_zero():
    out = match_lake_outline("Rice Lake", [(0.005, 0.005)], [wb(1, "Rice Lake")])
    assert out.distance_m == 0.0


# ── resolve_lake_outlines ─────────────────────────────────────────────────────


def test_candidates_from_several_mouths_are_deduped_by_ogf_id():
    seen = []

    def fetch_near(lon, lat, radius_m):
        assert radius_m == MATCH_TOLERANCE_M
        seen.append((lon, lat))
        return [wb(1, "Rice Lake", area=100.0)]

    outlines = resolve_lake_outlines(
        {"Rice Lake": [(44.2, -78.24), (44.19, -78.23)]},
        fetch_near=fetch_near,
        fetch_by_name=lambda _n: [],
    )
    assert len(seen) == 2  # both mouths searched
    assert outlines["Rice Lake"].ogf_ids == [1]


def test_an_unmatched_lake_is_kept_with_a_note_not_dropped():
    outlines = resolve_lake_outlines(
        {"Ghost Lake": [(44.0, -78.0)]},
        fetch_near=lambda *a: [],
        fetch_by_name=lambda _n: [],
    )
    assert "Ghost Lake" in outlines
    assert outlines["Ghost Lake"].matched is False


def test_the_licence_is_attached_to_a_matched_outline_only():
    outlines = resolve_lake_outlines(
        {"Rice Lake": [(44.2, -78.24)]},
        fetch_near=lambda *a: [wb(1, "Rice Lake")],
        fetch_by_name=lambda _n: [],
    )
    assert outlines["Rice Lake"].licence == "Open Government Licence – Ontario"
    assert outlines["Rice Lake"].licence_url.endswith("open-government-licence-ontario")


def test_a_great_lake_is_widened_by_unioning_every_same_named_polygon():
    a = wb(1, "Lake Ontario", area=100.0, box=(0.0, 0.0, 1.0, 1.0))
    b = wb(2, "Lake Ontario", area=100.0, box=(1.0, 0.0, 2.0, 1.0))

    def fetch_by_name(name):
        assert name == "Lake Ontario"
        return [a, b]

    outlines = resolve_lake_outlines(
        {"Lake Ontario": [(0.5, 0.5)]},
        fetch_near=lambda *_a: [wb(1, "Lake Ontario", area=100.0, box=(0.0, 0.0, 1.0, 1.0))],
        fetch_by_name=fetch_by_name,
    )
    out = outlines["Lake Ontario"]
    assert out.ogf_ids == [1, 2]
    assert out.geometry["type"] == "Polygon"  # the two touch on an edge, so they union to one


def test_an_inland_lake_is_never_widened_even_if_the_name_repeats():
    def fetch_by_name(name):  # would union if it were called
        raise AssertionError("inland lakes must not be widened by name")

    outlines = resolve_lake_outlines(
        {"Rice Lake": [(44.2, -78.24)]},
        fetch_near=lambda *a: [wb(1, "Rice Lake")],
        fetch_by_name=fetch_by_name,
    )
    assert outlines["Rice Lake"].ogf_ids == [1]
