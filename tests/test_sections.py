"""Sections: the overview map's one-shape-per-water layer and the pins inside each.

Every database is temporary. The cast is small and drawn by hand, so the right
answer is known by construction: the Humber in two sections (three weirs and a
river mouth on the lower one), Mimico Creek (a junction listed under both rivers),
Rice Lake with an outline and shoreline features, and Lake Ontario with none.
"""

import json
import shutil
from datetime import date

import pytest
from fastapi.testclient import TestClient

import src.storage.database as database
from src.api.main import app
from src.auth.auth import generate_invite_code, redeem_invite_code
from src.models.shoreline_feature import ShorelineFeature
from src.models.spot import AccessRef, Spot
from src.models.stretch import FishingStretch
from src.models.water_body import WaterBody
from src.models.weed_report import WeedGeometry
from src.services import spot_labels as words
from src.services.sections import (
    LAKE_MIN_PART_DEG2,
    section_detail,
    sections_geojson,
    simplify_outline,
)
from src.storage import weed_reports as weed_store
from src.storage.spots import replace_spots
from src.storage.stretches import replace_stretches
from tests.test_api_vocab import assert_clean

BUILT = "2026-10-01T00:00:00+00:00"
SEEN = date(2026, 10, 4)

# The Humber runs south: upper section 43.80 down to 43.70, lower 43.70 down to 43.60.
HUMBER_UPPER = [[-79.60, 43.80], [-79.60, 43.70]]
HUMBER_LOWER = [[-79.60, 43.70], [-79.60, 43.60]]
MIMICO = [[-79.50, 43.65], [-79.55, 43.62]]
# Rice Lake: a box with an island cut out of it.
RICE = [[-78.30, 44.00], [-78.00, 44.00], [-78.00, 44.20], [-78.30, 44.20], [-78.30, 44.00]]
ISLAND = [[-78.20, 44.08], [-78.15, 44.08], [-78.15, 44.12], [-78.20, 44.12], [-78.20, 44.08]]
JURISDICTION = "CA-ON"


def stretch(sid, name, river, coords, jurisdiction=JURISDICTION, order=0):
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return FishingStretch(
        stretch_id=sid,
        name=name,
        river=river,
        region="Toronto",
        jurisdiction=jurisdiction,
        length_km=10.0,
        segment_count=3,
        geometry={"type": "LineString", "coordinates": coords},
        bbox=(min(lons), min(lats), max(lons), max(lats)),
        centroid_lat=round(sum(lats) / len(lats), 5),
        centroid_lon=round(sum(lons) / len(lons), 5),
        sort_order=order,
        built_at=BUILT,
    )


def water_body(wb_id, name, wb_type, geometry=None, bbox=None, stretch_ids=(), source=None):
    return WaterBody(
        water_body_id=wb_id,
        jurisdiction=JURISDICTION,
        name=name,
        name_source="curated",
        type=wb_type,
        geometry=geometry,
        geometry_source=source or ("none" if geometry is None else "stretch_union"),
        bbox=bbox,
        stretch_ids=list(stretch_ids),
        built_at=BUILT,
    )


def spot(
    kind,
    lng,
    lat,
    water_id="humber-lower",
    body="river:humber-river",
    also_bodies=(),
    also_ids=(),
    tier=1,
    getting_there=(),
):
    key = f"n{abs(round(lng * 1e5))}_{round(lat * 1e5)}"
    attrs = {"also_on_water_ids": list(also_ids)} if also_ids else {}
    return Spot(
        spot_id=f"{kind}:{key}",
        kind=kind,
        jurisdiction=JURISDICTION,
        lat=lat,
        lng=lng,
        water_id=water_id,
        water_name="Lower Humber" if water_id else None,
        water_body_id=body if water_id else None,
        also_on_water_body_ids=list(also_bodies),
        title="Below the weir on the Humber River" if kind == "below_dam" else f"A {kind}",
        subtitle="Humber River",
        tier=tier,
        structure_type={
            "below_dam": "dam",
            "creek_junction": "confluence",
            "river_mouth": "lake_mouth",
            "public_fishing": "fishing_spot",
            "quiet_water": "explore_ranking",
        }[kind],
        structure_attrs=attrs,
        getting_there=list(getting_there),
        built_at=BUILT,
    )


def access(kind, lng, lat, ref):
    return AccessRef(
        kind=kind,
        name=f"{kind} {ref}",
        lat=lat,
        lng=lng,
        distance_m=300,
        source_ref=ref,
        attributes={"access": "yes"},
    )


def feature(kind, n, geometry, derivation="shoreline_outline", attrs=None):
    return ShorelineFeature(
        feature_id=f"lake:rice-lake:{kind}:{n}",
        water_body_id="lake:rice-lake",
        jurisdiction=JURISDICTION,
        kind=kind,
        geometry=geometry,
        source="OHN Waterbody" if derivation == "shoreline_outline" else "LIO Bathymetry",
        derivation=derivation,
        attributes=attrs or {},
        built_at=BUILT,
    )


def pt(lng, lat):
    return {"type": "Point", "coordinates": [lng, lat]}


MOUTH = spot(
    "river_mouth",
    -79.60,
    43.60,
    also_bodies=["great_lake:lake-ontario"],
)
WEIRS = [
    spot("below_dam", -79.601, 43.69, getting_there=[access("parking", -79.602, 43.69, "way/1")]),
    spot("below_dam", -79.602, 43.68, getting_there=[access("parking", -79.602, 43.69, "way/1")]),
    spot(
        "below_dam",
        -79.603,
        43.67,
        getting_there=[access("boat_launch", -79.61, 43.67, "way/2")],
    ),
]
HIDDEN_WEIR = spot("below_dam", -79.604, 43.66, tier=3)
JUNCTION = spot(
    "creek_junction",
    -79.55,
    43.62,
    water_id="mimico-lower",
    body="creek:mimico-creek",
    also_ids=["humber-lower"],
    also_bodies=["river:humber-river"],
)
FISHING = spot("public_fishing", -79.60, 43.75, water_id="humber-upper")
QUIET = spot("quiet_water", -79.30, 43.90, water_id=None, tier=2)
LAKE_MOUTH = spot(
    "river_mouth",
    -78.10,
    44.05,
    water_id="mimico-lower",
    body="creek:mimico-creek",
    also_bodies=["lake:rice-lake"],
)


def all_spots():
    return [MOUTH, *WEIRS, HIDDEN_WEIR, JUNCTION, FISHING, QUIET, LAKE_MOUTH]


def features():
    return [
        feature("inlet", 1, pt(-78.30, 44.10), "watercourse_endpoint"),
        feature("inlet", 2, pt(-78.30, 44.15), "watercourse_endpoint"),
        feature("island", 1, {"type": "Polygon", "coordinates": [ISLAND]}),
        feature("drop_off", 1, pt(-78.10, 44.15), "bathymetry_contours", {"survey_year": 1970}),
        feature(
            "hump",
            1,
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-78.25, 44.02],
                        [-78.22, 44.02],
                        [-78.22, 44.04],
                        [-78.25, 44.04],
                        [-78.25, 44.02],
                    ]
                ],
            },
            "bathymetry_contours",
            {"survey_year": 1970},
        ),
    ]


def bodies():
    return [
        water_body(
            "river:humber-river",
            "Humber River",
            "river",
            {"type": "MultiLineString", "coordinates": [HUMBER_UPPER, HUMBER_LOWER]},
            (-79.60, 43.60, -79.60, 43.80),
            ["humber-upper", "humber-lower"],
        ),
        water_body(
            "creek:mimico-creek",
            "Mimico Creek",
            "creek",
            {"type": "LineString", "coordinates": MIMICO},
            (-79.55, 43.62, -79.50, 43.65),
            ["mimico-lower"],
        ),
        water_body(
            "lake:rice-lake",
            "Rice Lake",
            "lake",
            {"type": "Polygon", "coordinates": [RICE, ISLAND]},
            (-78.30, 44.00, -78.00, 44.20),
            ["mimico-lower"],
            source="ohn_waterbody",
        ),
        water_body(
            "great_lake:lake-ontario",
            "Lake Ontario",
            "great_lake",
            None,
            (-79.70, 43.50, -79.50, 43.62),
            ["humber-lower"],
        ),
    ]


def stretches():
    return [
        stretch(
            "humber-upper", "Upper Humber - Kleinburg to Woodbridge", "Humber River", HUMBER_UPPER
        ),
        stretch(
            "humber-lower",
            "Lower Humber - Woodbridge to Humber Bay",
            "Humber River",
            HUMBER_LOWER,
            order=1,
        ),
        stretch("mimico-lower", "Lower Mimico Creek", "Mimico Creek", MIMICO, order=2),
    ]


def load(db, spots=None):
    replace_stretches(db, JURISDICTION, stretches(), [])
    replace_spots(db, JURISDICTION, all_spots() if spots is None else spots, bodies(), features())


def weed(db, user_id, wb_id, geometry, note=None):
    return weed_store.create_weed_report(
        db, user_id, wb_id, JURISDICTION, WeedGeometry.model_validate(geometry), SEEN, note
    )


@pytest.fixture
def built(db):
    load(db)
    return db


def by_id(collection):
    return {f["id"]: f for f in collection["features"]}


# ── the layer ─────────────────────────────────────────────────────────────────


def test_every_section_is_one_shape_and_a_lake_without_an_outline_is_a_label(built):
    out = sections_geojson(built)
    sections = by_id(out)
    assert set(sections) == {"humber-upper", "humber-lower", "mimico-lower", "lake:rice-lake"}
    assert out["meta"]["count"] == 4
    assert out["meta"]["label_count"] == 1
    for f in out["features"]:
        assert f["geometry"]["type"] in ("LineString", "Polygon")
    assert [f["id"] for f in out["labels"]["features"]] == ["great_lake:lake-ontario"]
    label = out["labels"]["features"][0]
    assert label["geometry"]["type"] == "Point"
    assert label["properties"]["display_name"] == "Lake Ontario"
    assert label["properties"]["note"] == words.SECTION_LABEL_ONLY_NOTE
    assert "geometry" not in label["properties"]


def test_a_section_carries_its_name_kind_and_type(built):
    sections = by_id(sections_geojson(built))
    lower = sections["humber-lower"]["properties"]
    assert lower["name"] == "Lower Humber - Woodbridge to Humber Bay"
    assert lower["kind"] == "river_section"
    assert lower["type"] == "river"
    assert lower["type_label"] == "River"
    assert lower["water_body_id"] == "river:humber-river"
    assert lower["water_body_name"] == "Humber River"
    assert lower["jurisdiction"] == "CA-ON"
    assert lower["label_point"] == [-79.6, 43.65]
    assert sections["mimico-lower"]["properties"]["type"] == "creek"
    lake = sections["lake:rice-lake"]["properties"]
    assert (lake["kind"], lake["type"], lake["name"]) == ("lake", "lake", "Rice Lake")
    assert lake["length_km"] is None
    # The label point is inside the lake, not on the island's hole or the shore.
    lon, lat = lake["label_point"]
    assert -78.30 < lon < -78.00 and 44.00 < lat < 44.20


def test_counts_say_what_is_inside_each_section(built):
    sections = by_id(sections_geojson(built))
    lower = sections["humber-lower"]["properties"]["counts"]
    assert lower["spots"] == {"river_mouth": 1, "creek_junction": 1, "below_dam": 3}
    assert lower["features"] == {}
    assert lower["weed_reports"] == 0
    assert lower["total"] == 5
    assert sections["humber-upper"]["properties"]["counts"]["spots"] == {"public_fishing": 1}
    lake = sections["lake:rice-lake"]["properties"]["counts"]
    assert lake["spots"] == {"river_mouth": 1}
    assert lake["features"] == {"inlet": 2, "island": 1, "drop_off": 1, "hump": 1}
    assert lake["total"] == 6


def test_a_junction_is_in_both_sections_it_is_listed_under(built):
    sections = by_id(sections_geojson(built))
    assert sections["mimico-lower"]["properties"]["counts"]["spots"]["creek_junction"] == 1
    assert sections["humber-lower"]["properties"]["counts"]["spots"]["creek_junction"] == 1
    assert "creek_junction" not in sections["humber-upper"]["properties"]["counts"]["spots"]


def test_the_count_is_the_default_tier_and_tier_three_adds_the_held_back_spots(built):
    default = by_id(sections_geojson(built))["humber-lower"]["properties"]["counts"]
    everything = by_id(sections_geojson(built, max_tier=3))["humber-lower"]["properties"]["counts"]
    assert default["spots"]["below_dam"] == 3
    assert everything["spots"]["below_dam"] == 4
    assert sections_geojson(built, max_tier=1)["meta"]["max_tier"] == 1


def test_quiet_water_belongs_to_no_section(built):
    out = sections_geojson(built, max_tier=3)
    for f in out["features"]:
        assert "quiet_water" not in f["properties"]["counts"]["spots"]
        detail = section_detail(built, f["id"], max_tier=3)
        assert all(p["properties"].get("kind") != "quiet_water" for p in detail["features"])


def test_the_summary_is_the_counts_in_words(built):
    sections = by_id(sections_geojson(built))
    assert (
        sections["humber-lower"]["properties"]["summary"]
        == "1 river mouth, 1 place where creeks join, 3 spots below dams"
    )
    assert sections["lake:rice-lake"]["properties"]["summary"] == (
        "1 river mouth, 2 inlets, 1 island, 1 drop-off, 1 hump"
    )
    assert words.section_summary({}, {}, 0) == words.SECTION_EMPTY_SUMMARY
    assert words.section_summary({}, {}, 1) == "1 weed edge you reported"
    assert words.section_summary({"below_dam": 1}, {"hump": 2}, 3) == (
        "1 spot below a dam, 2 humps, 3 weed edges you reported"
    )


def test_a_river_section_drawn_across_the_view_is_in_it_and_one_that_only_shares_its_box_is_not(
    built,
):
    # Inside the lower Humber's box but nowhere near its line.
    miss = sections_geojson(built, bbox=(-79.55, 43.60, -79.40, 43.70))
    assert "humber-lower" not in by_id(miss)
    assert "mimico-lower" in by_id(miss)
    hit = sections_geojson(built, bbox=(-79.65, 43.64, -79.55, 43.66))
    assert set(by_id(hit)) == {"humber-lower"}
    assert hit["meta"]["label_count"] == 0  # Lake Ontario's label is outside this view


def test_a_label_is_in_view_when_its_place_is(built):
    out = sections_geojson(built, bbox=(-79.70, 43.50, -79.50, 43.62))
    assert [f["id"] for f in out["labels"]["features"]] == ["great_lake:lake-ontario"]


def test_the_type_filter_leaves_the_type_counts_alone(built):
    out = sections_geojson(built, types=["creek"])
    assert set(by_id(out)) == {"mimico-lower"}
    assert out["meta"]["type_counts"] == {
        "river": 2,
        "creek": 1,
        "lake": 1,
        "pond": 0,
        "great_lake": 1,
    }
    assert out["labels"]["features"] == []


def test_the_jurisdiction_filter_keeps_one_jurisdictions_sections(db):
    replace_stretches(
        db,
        "CA-BC",
        [
            stretch(
                "fraser-lower",
                "Lower Fraser",
                "Fraser River",
                [[-122.9, 49.1], [-122.8, 49.2]],
                "CA-BC",
            )
        ],
        [],
    )
    load(db)
    assert set(by_id(sections_geojson(db, jurisdiction="CA-BC"))) == {"fraser-lower"}
    assert "fraser-lower" not in by_id(sections_geojson(db, jurisdiction="CA-ON"))


def test_an_empty_view_is_empty_and_an_unbuilt_database_says_so(db):
    load(db)
    out = sections_geojson(db, bbox=(10, 10, 11, 11))
    assert out["features"] == [] and "notes" not in out["meta"]


def test_a_database_with_no_river_sections_built_says_so(db):
    out = sections_geojson(db)
    assert out["features"] == []
    assert out["meta"]["notes"] == [words.SECTIONS_NOT_BUILT]


# ── the shapes are light ──────────────────────────────────────────────────────


def jagged(n, width=0.4):
    """A wobbling square with n points per side: what a surveyed outline looks like."""
    import math

    def wobble(i):
        return 0.0004 * math.sin(i * 7)

    steps = [i / n for i in range(n)]
    ring = (
        [(t * width, wobble(i)) for i, t in enumerate(steps)]
        + [(width + wobble(i), t * width) for i, t in enumerate(steps)]
        + [(width - t * width, width + wobble(i)) for i, t in enumerate(steps)]
        + [(wobble(i), width - t * width) for i, t in enumerate(steps)]
    )
    ring = [[round(-80 + x, 6), round(44 + y, 6)] for x, y in ring]
    return [*ring, ring[0]]


def test_a_lake_outline_is_simplified_and_drawn_whole():
    ring = jagged(2000)
    island = [[-79.9, 44.1], [-79.8, 44.1], [-79.8, 44.2], [-79.9, 44.2], [-79.9, 44.1]]
    out = simplify_outline({"type": "Polygon", "coordinates": [ring, island]})
    assert out["type"] == "Polygon"
    assert len(out["coordinates"]) == 1  # the island is a pin of its own, not a hole
    assert len(out["coordinates"][0]) < len(ring) / 20
    assert all(len(str(c[0]).split(".")[-1]) <= 5 for c in out["coordinates"][0])


def test_a_small_part_of_an_outline_is_dropped_unless_it_is_all_there_is():
    big = [[-80, 44], [-79, 44], [-79, 45], [-80, 45], [-80, 44]]
    speck = [[-78, 44], [-77.999, 44], [-77.999, 44.001], [-78, 44.001], [-78, 44]]
    multi = {"type": "MultiPolygon", "coordinates": [[big], [speck]]}
    kept = simplify_outline(multi)
    assert kept["type"] == "Polygon"
    only = simplify_outline({"type": "MultiPolygon", "coordinates": [[speck]]})
    assert only["type"] == "Polygon"
    assert LAKE_MIN_PART_DEG2 > 0.001 * 0.001


def test_nine_weirs_on_one_river_are_one_shape_with_a_count_of_nine(db):
    weirs = [spot("below_dam", -79.6 + i * 0.0005, 43.69 - i * 0.0005) for i in range(9)]
    load(db, [*weirs, MOUTH])
    from src.services.spots import spots_geojson

    assert len(spots_geojson(db)["features"]) == 10
    out = sections_geojson(db, bbox=(-79.65, 43.55, -79.58, 43.75))
    assert set(by_id(out)) == {"humber-upper", "humber-lower"}
    lower = by_id(out)["humber-lower"]["properties"]
    assert lower["counts"]["spots"]["below_dam"] == 9
    assert lower["summary"] == "1 river mouth, 9 spots below dams"


# ── a section's pins ──────────────────────────────────────────────────────────


def test_an_unknown_section_and_a_label_only_lake_have_no_detail(built):
    assert section_detail(built, "no-such-section") is None
    assert section_detail(built, "lake:no-such-lake") is None
    assert section_detail(built, "great_lake:lake-ontario") is None  # no outline: not a section
    assert section_detail(built, "river:humber-river") is None  # a river is its sections


def test_a_river_section_lists_every_point_of_interest_as_a_pin(built):
    detail = section_detail(built, "humber-lower")
    assert detail["type"] == "FeatureCollection"
    assert detail["section"]["id"] == "humber-lower"
    assert detail["section"]["geometry"]["type"] == "LineString"
    pins = detail["features"]
    spot_pins = [p for p in pins if p["properties"]["pin_type"] == "spot"]
    assert {p["id"] for p in spot_pins} == {s.spot_id for s in (MOUTH, *WEIRS, JUNCTION)}
    for p in pins:
        assert p["geometry"]["type"] == "Point"
    weir = next(p for p in spot_pins if p["id"] == WEIRS[0].spot_id)
    assert weir["properties"]["spot_id"] == WEIRS[0].spot_id
    assert weir["properties"]["kind"] == "below_dam"
    assert weir["properties"]["title"] == "Below the weir on the Humber River"
    assert weir["properties"]["water_id"] == "humber-lower"
    assert weir["geometry"]["coordinates"] == [WEIRS[0].lng, WEIRS[0].lat]


def test_a_spot_pin_has_the_properties_the_spots_endpoint_gives(built):
    from src.services.spots import spots_geojson

    listed = by_id(spots_geojson(built, kinds=["below_dam"]))
    pins = by_id(section_detail(built, "humber-lower"))
    for spot_id, feat in listed.items():
        assert pins[spot_id]["properties"] == {"pin_type": "spot", **feat["properties"]}
        assert pins[spot_id]["geometry"] == feat["geometry"]


def test_getting_there_pins_are_each_place_once_and_name_the_spots_they_serve(built):
    pins = [
        p
        for p in section_detail(built, "humber-lower")["features"]
        if p["properties"]["pin_type"] == "getting_there"
    ]
    assert [p["properties"]["kind"] for p in pins] == ["parking", "boat_launch"]
    parking, launch = pins
    assert parking["properties"]["serves"] == [WEIRS[0].spot_id, WEIRS[1].spot_id]
    assert launch["properties"]["serves"] == [WEIRS[2].spot_id]
    assert parking["properties"]["label"] == "Parking"
    assert parking["properties"]["source_ref"] == "way/1"
    assert parking["id"] == "parking:way/1"


def test_a_lake_lists_its_shoreline_and_depth_features_and_the_mouths_onto_it(built):
    detail = section_detail(built, "lake:rice-lake")
    assert detail["section"]["geometry"]["type"] == "Polygon"
    pins = by_id(detail)
    assert set(pins) == {
        LAKE_MOUTH.spot_id,
        "lake:rice-lake:inlet:1",
        "lake:rice-lake:inlet:2",
        "lake:rice-lake:island:1",
        "lake:rice-lake:drop_off:1",
        "lake:rice-lake:hump:1",
    }
    drop = pins["lake:rice-lake:drop_off:1"]["properties"]
    assert drop["pin_type"] == "feature"
    assert drop["feature_id"] == "lake:rice-lake:drop_off:1"
    assert drop["label"] == "Drop-off"
    assert drop["derivation"] == "bathymetry_contours"
    assert drop["note"].endswith("From a 1970 survey.")
    assert "shape" not in drop
    # An area is a pin inside it, with the area itself along.
    island = pins["lake:rice-lake:island:1"]
    assert island["geometry"]["type"] == "Point"
    assert island["properties"]["shape"]["type"] == "Polygon"
    assert pins["lake:rice-lake:hump:1"]["properties"]["shape"]["type"] == "Polygon"
    lon, lat = island["geometry"]["coordinates"]
    assert -78.20 < lon < -78.15 and 44.08 < lat < 44.12


def test_a_features_pin_properties_are_the_features_endpoints(built):
    from src.services.water_bodies import water_body_features_geojson

    listed = by_id(water_body_features_geojson(built, "lake:rice-lake"))
    pins = by_id(section_detail(built, "lake:rice-lake"))
    for fid, feat in listed.items():
        props = {
            k: v
            for k, v in pins[fid]["properties"].items()
            if k not in ("pin_type", "label", "shape")
        }
        assert props == feat["properties"]


def test_the_counts_are_exactly_the_pins_at_every_tier(built):
    for tier in (1, 2, 3):
        listed = by_id(sections_geojson(built, max_tier=tier))
        for section_id, feat in listed.items():
            detail = section_detail(built, section_id, max_tier=tier)
            counts = feat["properties"]["counts"]
            assert detail["section"]["properties"]["counts"] == counts
            pins = detail["features"]
            n_spots = sum(1 for p in pins if p["properties"]["pin_type"] == "spot")
            n_features = sum(1 for p in pins if p["properties"]["pin_type"] == "feature")
            n_weed = sum(1 for p in pins if p["properties"]["pin_type"] == "weed_report")
            assert n_spots == sum(counts["spots"].values())
            assert n_features == sum(counts["features"].values())
            assert n_weed == counts["weed_reports"]
            assert detail["meta"]["by_pin_type"]["spot"] == n_spots


def test_a_river_has_no_features_and_a_lake_with_none_has_an_empty_list(db):
    load(db)
    replace_spots(db, JURISDICTION, all_spots(), bodies(), [])
    assert section_detail(db, "humber-lower")["meta"]["by_pin_type"]["feature"] == 0
    lake = section_detail(db, "lake:rice-lake")
    assert lake["meta"]["by_pin_type"]["feature"] == 0
    assert lake["section"]["properties"]["counts"]["features"] == {}


# ── weed reports: the caller's own, nobody else's ─────────────────────────────


def test_weed_reports_are_the_callers_own_and_placed_in_the_nearest_section(built):
    ann_upper = weed(built, 1, "river:humber-river", pt(-79.601, 43.78), "above the bridge")
    ann_line = weed(
        built,
        1,
        "river:humber-river",
        {"type": "LineString", "coordinates": [[-79.60, 43.66], [-79.60, 43.64]]},
    )
    ann_lake = weed(built, 1, "lake:rice-lake", pt(-78.25, 44.10))
    bob_lower = weed(built, 2, "river:humber-river", pt(-79.60, 43.65))

    ann = by_id(sections_geojson(built, user_id=1))
    assert ann["humber-upper"]["properties"]["counts"]["weed_reports"] == 1
    assert ann["humber-lower"]["properties"]["counts"]["weed_reports"] == 1
    assert ann["lake:rice-lake"]["properties"]["counts"]["weed_reports"] == 1
    assert ann["mimico-lower"]["properties"]["counts"]["weed_reports"] == 0
    assert "1 weed edge you reported" in ann["humber-lower"]["properties"]["summary"]

    bob = by_id(sections_geojson(built, user_id=2))
    assert bob["humber-lower"]["properties"]["counts"]["weed_reports"] == 1
    assert bob["humber-upper"]["properties"]["counts"]["weed_reports"] == 0

    stranger = sections_geojson(built, user_id=3)
    assert all(f["properties"]["counts"]["weed_reports"] == 0 for f in stranger["features"])
    anonymous = sections_geojson(built)
    assert all(f["properties"]["counts"]["weed_reports"] == 0 for f in anonymous["features"])

    pins = section_detail(built, "humber-upper", user_id=1)["features"]
    weeds = [p for p in pins if p["properties"]["pin_type"] == "weed_report"]
    assert [p["id"] for p in weeds] == [ann_upper]
    props = weeds[0]["properties"]
    assert props["weed_report_id"] == ann_upper
    assert props["status_label"] == "reported, not verified"
    assert props["provenance"] == "user_reported"
    assert props["note"] == "above the bridge"
    assert props["caption"] == "Weed edge · reported, not verified · seen 2026-10-04"
    assert "shape" not in props
    assert "user_id" not in props

    line = [
        p
        for p in section_detail(built, "humber-lower", user_id=1)["features"]
        if p["properties"]["pin_type"] == "weed_report"
    ]
    assert [p["id"] for p in line] == [ann_line]
    assert line[0]["geometry"] == pt(-79.6, 43.65)  # on the line, in the middle
    assert line[0]["properties"]["shape"]["type"] == "LineString"
    assert bob_lower not in {p["id"] for p in line}
    lake = [
        p
        for p in section_detail(built, "lake:rice-lake", user_id=1)["features"]
        if p["properties"]["pin_type"] == "weed_report"
    ]
    assert [p["id"] for p in lake] == [ann_lake]
    assert (
        section_detail(built, "humber-lower", user_id=3)["meta"]["by_pin_type"]["weed_report"] == 0
    )
    assert section_detail(built, "humber-lower")["meta"]["by_pin_type"]["weed_report"] == 0


def test_a_weed_report_on_a_water_that_is_gone_is_left_out_not_misplaced(built):
    weed(built, 1, "river:gone-river", pt(-79.6, 43.65))
    out = sections_geojson(built, user_id=1)
    assert all(f["properties"]["counts"]["weed_reports"] == 0 for f in out["features"])


# ── the words ─────────────────────────────────────────────────────────────────


def test_no_retired_word_in_a_section_response(built):
    weed(built, 1, "river:humber-river", pt(-79.60, 43.65))
    assert_clean(sections_geojson(built, user_id=1, max_tier=3), "sections")
    for section_id in by_id(sections_geojson(built)):
        assert_clean(section_detail(built, section_id, user_id=1, max_tier=3), section_id)


def test_the_section_words_are_in_the_labels_payload_and_free_of_jargon():
    payload = words.labels_payload()["sections"]
    assert [k["kind"] for k in payload["kinds"]] == ["river_section", "lake"]
    assert set(payload["pin_types"]) == {"spot", "feature", "getting_there", "weed_report"}
    assert set(payload["count_phrases"]) >= set(words.KIND_ORDER) - {"quiet_water"}
    assert payload["not_built"] == words.SECTIONS_NOT_BUILT
    assert_clean(payload, "labels.sections")
    assert_clean(words.labels_payload(), "labels")


# ── the API ───────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch, _template_db):
    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    # Other test files leave a login override on the shared app; these tests use real logins.
    monkeypatch.setattr(app, "dependency_overrides", {})
    handle = database.get_db()
    load(handle)

    def login(name):
        code = generate_invite_code(handle)
        got = redeem_invite_code(handle, code, name)
        assert got["success"], got
        return {"id": got["user_id"], "h": {"Authorization": f"Bearer {got['token']}"}}

    class Api:
        client = TestClient(app)
        db = handle
        ann = login("ann")
        bob = login("bob")

        def get(self, who, path):
            return self.client.get(path, headers=who["h"])

    return Api()


def test_both_routes_need_a_login(api):
    for path in ("/map/sections", "/map/sections/humber-lower"):
        assert api.client.get(path).status_code in (401, 403)
        assert api.client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_the_list_route_serves_the_layer_and_is_never_cached(api):
    response = api.get(api.ann, "/map/sections?bbox=-79.7,43.5,-78.0,44.3&type=river,creek,lake")
    assert response.status_code == 200
    body = response.json()
    assert {f["id"] for f in body["features"]} == {
        "humber-upper",
        "humber-lower",
        "mimico-lower",
        "lake:rice-lake",
    }
    assert response.headers["cache-control"] == "private, no-store"
    one_tier = api.get(api.ann, "/map/sections?tier=1").json()
    assert one_tier["meta"]["max_tier"] == 1


@pytest.mark.parametrize(
    "query",
    ["bbox=1,2,3", "bbox=3,2,1,4", "type=ocean", "tier=0", "tier=4"],
)
def test_bad_parameters_are_a_422(api, query):
    assert api.get(api.ann, f"/map/sections?{query}").status_code == 422


def test_the_detail_route_serves_pins_and_a_lake_id_works_in_the_path(api):
    river = api.get(api.ann, "/map/sections/humber-lower")
    assert river.status_code == 200
    assert river.json()["section"]["id"] == "humber-lower"
    assert river.headers["cache-control"] == "private, no-store"
    lake = api.get(api.ann, "/map/sections/lake:rice-lake")
    assert lake.status_code == 200
    assert lake.json()["meta"]["by_pin_type"]["feature"] == 5
    assert api.get(api.ann, "/map/sections/humber-lower?tier=3").json()["meta"]["max_tier"] == 3
    assert api.get(api.ann, "/map/sections/humber-lower?tier=9").status_code == 422


@pytest.mark.parametrize(
    "section_id", ["no-such-section", "lake:no-such-lake", "great_lake:lake-ontario"]
)
def test_an_unknown_section_is_a_404(api, section_id):
    response = api.get(api.ann, f"/map/sections/{section_id}")
    assert response.status_code == 404
    assert section_id in response.json()["detail"]


def test_one_users_weed_reports_never_reach_another_through_sections(api):
    mine = weed(api.db, api.ann["id"], "river:humber-river", pt(-79.60, 43.65), "mine")
    ann_list = api.get(api.ann, "/map/sections").json()
    bob_list = api.get(api.bob, "/map/sections").json()
    assert by_id(ann_list)["humber-lower"]["properties"]["counts"]["weed_reports"] == 1
    assert by_id(bob_list)["humber-lower"]["properties"]["counts"]["weed_reports"] == 0
    ann_pins = api.get(api.ann, "/map/sections/humber-lower").json()
    bob_pins = api.get(api.bob, "/map/sections/humber-lower").json()
    assert mine in {p["id"] for p in ann_pins["features"]}
    assert mine not in {p["id"] for p in bob_pins["features"]}
    assert "mine" not in json.dumps(bob_pins) and "mine" not in json.dumps(bob_list)


def test_get_map_spots_is_unchanged_and_still_lists_every_pin(api):
    spots = api.get(api.ann, "/map/spots?tier=3").json()
    assert {f["id"] for f in spots["features"]} == {s.spot_id for s in all_spots()}
    assert set(spots) == {"type", "meta", "features"}


# ── the recorded fixture ──────────────────────────────────────────────────────


def test_the_api_fixture_still_matches_the_labels_and_the_pins_it_opens():
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "sections_api_responses.json"
    entries = json.loads(path.read_text())["requests"]
    assert {e["status"] for e in entries} == {200, 404, 422}
    pin_types = set(words.PIN_TYPES)
    seen_pins = set()
    for e in entries:
        assert_clean(e["response"], e["request"])
        if e["status"] != 200:
            assert set(e["response"]) == {"detail"}
            continue
        body = e["response"]
        assert body["type"] == "FeatureCollection"
        sections = body["features"] if "labels" in body else [body["section"]]
        for f in sections:
            props = f["properties"]
            assert f["id"] == props["section_id"]
            assert props["kind"] in words.SECTION_KINDS
            assert props["summary"] == words.section_summary(
                props["counts"]["spots"],
                props["counts"]["features"],
                props["counts"]["weed_reports"],
            )
            assert props["counts"]["total"] == (
                sum(props["counts"]["spots"].values())
                + sum(props["counts"]["features"].values())
                + props["counts"]["weed_reports"]
            )
        if "labels" in body:
            for label in body["labels"]["features"]:
                assert label["geometry"]["type"] == "Point"
                assert label["properties"]["note"] == words.SECTION_LABEL_ONLY_NOTE
        else:
            counted = body["meta"]["by_pin_type"]
            for pin in body["features"]:
                assert pin["geometry"]["type"] == "Point"
                assert pin["properties"]["pin_type"] in pin_types
                seen_pins.add(pin["properties"]["pin_type"])
            assert body["meta"]["count"] == len(body["features"])
            assert (
                counted["spot"] + counted["feature"] + counted["weed_report"]
                == (body["section"]["properties"]["counts"]["total"])
            )
    # The examples the docs show: a spot, a shoreline feature and the caller's weed edge.
    assert seen_pins == {"spot", "feature", "weed_report"}
