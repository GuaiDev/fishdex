"""Repeated structures: near-coincident spots merge, the rest get titles of their own."""

import pytest

from src.services import spot_labels
from src.services.spot_repeats import NEARBY_JUNCTION_M, REPEAT_MERGE_M
from tests.test_spots import access, confluence, dam, run, tail

LON = -79.5
# Metres to degrees of latitude, close enough for a test.
M = 1 / 111_320.0


def weir(n: int, north_m: float, sid: str = "credit-lower"):
    """A verified, unnamed weir `north_m` metres north of the base point."""
    lat = 43.5 + north_m * M
    return [
        dam(f"way/{n}", lat=lat, subtype="weir", sid=sid),
        tail(f"way/{n}", lat=lat - 0.0003, kind="weir", sid=sid),
    ]


def weirs(result):
    return [s for s in result.spots if s.kind == "below_dam"]


def test_weirs_within_the_radius_collapse_into_one_spot():
    anchors = weir(1, 0) + weir(2, 300) + weir(3, 600)
    result = run(anchors)
    # 0 and 300 m merge; 600 m is beyond the radius of the lead.
    assert len(weirs(result)) == 2
    assert result.report.dropped["repeat_structure_merged"] == 1


def test_a_line_of_weirs_does_not_chain_into_one_spot():
    step = REPEAT_MERGE_M["below_dam"] * 0.8
    anchors = [a for i in range(5) for a in weir(i, i * step)]
    assert len(weirs(run(anchors))) > 1


def test_the_merged_spot_keeps_every_source_and_the_ids_it_absorbed():
    result = run(weir(1, 0) + weir(2, 200))
    (spot,) = weirs(result)
    assert len(spot.structure_attrs["merged_spot_ids"]) == 1
    assert len(spot.sources) == 4  # two dams and two tailwaters
    assert spot.structure_attrs["merged_spot_ids"][0] != spot.spot_id


def test_merging_does_not_depend_on_input_order():
    anchors = weir(1, 0) + weir(2, 200) + weir(3, 900)
    forward = run(anchors)
    backward = run(list(reversed(anchors)))
    assert [s.spot_id for s in weirs(forward)] == [s.spot_id for s in weirs(backward)]
    assert [s.title for s in weirs(forward)] == [s.title for s in weirs(backward)]


def test_spots_with_different_titles_are_never_merged():
    anchors = [
        dam("way/1", name="Mill Dam"),
        tail("way/1"),
        *weir(2, 300),
    ]
    assert len(weirs(run(anchors))) == 2


def test_same_title_on_different_rivers_is_not_merged():
    from tests.test_spots import stretch

    stretches = [stretch("a", "Upper A", "Alpha River"), stretch("b", "Upper B", "Beta River")]
    anchors = weir(1, 0, sid="a") + weir(2, 300, sid="b")
    assert len(weirs(run(anchors, stretches))) == 2


def test_junctions_use_their_own_tighter_radius():
    near = REPEAT_MERGE_M["creek_junction"] * 0.5
    far = REPEAT_MERGE_M["creek_junction"] * 2
    merged = run(
        [
            confluence("a", 43.5, LON, 5.0, name="Maple Creek"),
            confluence("b", 43.5 + near * M, LON, 5.0, name="Maple Creek"),
        ]
    )
    apart = run(
        [
            confluence("a", 43.5, LON, 5.0, name="Maple Creek"),
            confluence("b", 43.5 + far * M, LON, 5.0, name="Maple Creek"),
        ]
    )
    assert len(merged.spots) == 1
    assert len(apart.spots) == 2


def test_river_mouths_are_not_merged():
    from tests.test_spots import mouth

    result = run([mouth(43.5, LON), mouth(43.5 + 50 * M, LON, sid="credit-lower")])
    assert len([s for s in result.spots if s.kind == "river_mouth"]) == 2


# ── distinguishing the rest ───────────────────────────────────────────────────


def far_weirs(count: int):
    gap = 3000.0  # beyond every landmark radius
    return [a for i in range(count) for a in weir(i, i * gap)]


def test_a_repeated_title_takes_the_nearest_named_road():
    anchors = far_weirs(2) + [
        access("road_crossing", "way/90", 43.5 - 0.0003, LON, name="Steeles Avenue East"),
    ]
    result = run(anchors)
    titles = sorted(s.title for s in weirs(result))
    assert len(set(titles)) == 2
    assert any(t.endswith(", near Steeles Avenue East") for t in titles)
    near = next(s for s in weirs(result) if "Steeles" in s.title)
    assert near.structure_attrs["title_distinguished_by"] == "road"
    assert result.report.titles_distinguished["road"] == 1


def test_parking_with_a_name_is_used_when_there_is_no_named_road():
    anchors = far_weirs(2) + [
        access("parking", "way/91", 43.5 - 0.0003, LON, name="Old Mill Parking"),
    ]
    result = run(anchors)
    assert any(s.title.endswith(", near Old Mill Parking") for s in weirs(result))


def test_a_named_creek_joining_close_by_is_a_landmark():
    anchors = far_weirs(2) + [confluence("c", 43.5 + 0.0005, LON, 40.0, name="Black Creek")]
    result = run(anchors)
    assert any(s.title.endswith(", near where Black Creek joins") for s in weirs(result))
    assert NEARBY_JUNCTION_M > 0


def test_with_no_landmark_spots_are_counted_along_the_group():
    result = run(far_weirs(3))
    titles = sorted(s.title for s in weirs(result))
    assert len(set(titles)) == 3
    base = "Below the weir on the Credit River"
    assert titles == [
        f"{base} (1 of 3, north to south)",
        f"{base} (2 of 3, north to south)",
        f"{base} (3 of 3, north to south)",
    ]
    assert all(s.structure_attrs["title_distinguished_by"] == "order" for s in weirs(result))


def test_two_spots_with_the_same_landmark_still_get_different_titles():
    anchors = far_weirs(2) + [access("road_crossing", "way/92", 43.5, LON, name="Main Street")]
    titles = [s.title for s in weirs(run(anchors))]
    assert len(set(titles)) == 2


def test_a_unique_title_is_left_alone():
    result = run(weir(1, 0))
    (spot,) = weirs(result)
    assert spot.title == "Below the weir on the Credit River"
    assert "title_distinguished_by" not in spot.structure_attrs
    assert result.report.titles_distinguished == {}


def test_the_words_come_from_the_labels_and_carry_no_jargon():
    import re

    strings = [*spot_labels.DISTINGUISHED_TITLES.values()]
    assert set(spot_labels.DISTINGUISHED_TITLES) == {"road", "access", "junction", "order"}
    assert all(s in spot_labels.all_strings() for s in strings)
    jargon = re.compile(r"\b(confluence|tailwater|anchor|stretch|segment|OHN)\b", re.I)
    assert not any(jargon.search(s) for s in strings)
    payload = spot_labels.labels_payload()["repeated_titles"]
    assert payload["templates"] == spot_labels.DISTINGUISHED_TITLES


@pytest.mark.parametrize("kind", sorted(REPEAT_MERGE_M))
def test_every_merge_radius_is_positive(kind):
    assert REPEAT_MERGE_M[kind] > 0
