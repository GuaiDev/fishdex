"""Spots: the model, classify() over hand-built anchors, the build, and the API.

classify() is pure, so most tests hand it anchors and read the spots back; the
expected answer is known by construction. Nothing touches the network. The
build and API tests use throwaway databases, never the real one.
"""

import re

import pytest
from pydantic import ValidationError

from src.models.spot import AccessRef, Spot, SpotBuildReport
from src.models.spot_card import SpotCard
from src.models.stretch import FishingStretch, StretchSegment
from src.models.stretch_anchor import StretchAnchor
from src.services import spot_labels
from src.services.spots import (
    StretchHints,
    build_spots,
    classify,
    get_spot_detail,
    load_stretch_hints,
    spots_geojson,
)

BUILT = "2026-10-11T00:00:00+00:00"
JURISDICTION = "CA-ON"

# Words a beginner would not know. None may appear in anything the map says.
JARGON = re.compile(
    r"\b(confluence|tailwater|anchor|stretch|ingest|topology|segment|OHN|ogf)\b", re.IGNORECASE
)
# Claims the first version does not make: seasons and gear.
SEASON_OR_GEAR = re.compile(
    r"\b(spring|summer|autumn|fall run|winter|lure|bait|rod|reel|fly|hook|jig|best|hot)\b",
    re.IGNORECASE,
)


def stretch(sid="credit-lower", name="Lower Credit", river="Credit River", region=None):
    return FishingStretch(
        stretch_id=sid,
        name=name,
        river=river,
        region=region,
        jurisdiction=JURISDICTION,
        length_km=10.0,
        segment_count=3,
        geometry={"type": "LineString", "coordinates": [[-79.6, 43.5], [-79.5, 43.6]]},
        bbox=(-79.6, 43.5, -79.5, 43.6),
        centroid_lat=43.55,
        centroid_lon=-79.55,
        built_at=BUILT,
    )


def anchor(kind, aid, lat, lon, sid="credit-lower", subtype=None, name=None, ref=None, **attrs):
    return StretchAnchor(
        anchor_id=f"{sid}:{kind}:{aid}",
        stretch_id=sid,
        jurisdiction=JURISDICTION,
        anchor_type=kind,
        subtype=subtype,
        name=name,
        lat=lat,
        lon=lon,
        source="osm" if kind in ("dam", "tailwater", "access_point") else "ohn_topology",
        source_ref=ref or f"ogf:{aid}",
        attributes=attrs,
        built_at=BUILT,
    )


def confluence(aid, lat, lon, km, name=None, sid="credit-lower", flows_on=True, own_km=500.0):
    return anchor(
        "confluence",
        aid,
        lat,
        lon,
        sid=sid,
        name=name,
        joining_upstream_km=km,
        stretch_upstream_km=own_km,
        stretch_flows_on=flows_on,
    )


def run(anchors, stretches=None, hints=None):
    return classify(anchors, stretches or [stretch()], JURISDICTION, hints, BUILT)


def only(result, kind):
    return [s for s in result.spots if s.kind == kind]


# ── model ─────────────────────────────────────────────────────────────────────


def _spot_kwargs(**over):
    base = dict(
        spot_id="river_mouth:n7957731_4354567",
        kind="river_mouth",
        jurisdiction="CA-ON",
        lat=43.5,
        lng=-79.5,
        title="Where the Credit River meets Lake Ontario",
        tier=1,
        structure_type="lake_mouth",
        built_at=BUILT,
    )
    return {**base, **over}


def test_spot_requires_a_jurisdiction():
    kwargs = _spot_kwargs()
    del kwargs["jurisdiction"]
    with pytest.raises(ValidationError):
        Spot(**kwargs)


def test_spot_rejects_a_malformed_jurisdiction():
    with pytest.raises(ValidationError):
        Spot(**_spot_kwargs(jurisdiction="Ontario"))


@pytest.mark.parametrize("title", ["", "  ", "Unnamed creek", "unnamed Creek junction"])
def test_spot_title_is_never_empty_or_unnamed(title):
    with pytest.raises(ValidationError):
        Spot(**_spot_kwargs(title=title))


@pytest.mark.parametrize(
    "spot_id",
    ["credit-lower:river_mouth:n1_2", "river_mouth:credit-lower", "river_mouth:", "x:1_2"],
)
def test_spot_id_must_be_a_kind_and_coordinate_key(spot_id):
    with pytest.raises(ValidationError):
        Spot(**_spot_kwargs(spot_id=spot_id))


def test_spot_id_must_start_with_its_kind():
    with pytest.raises(ValidationError):
        Spot(**_spot_kwargs(spot_id="below_dam:n7957731_4354567"))


def test_spot_rejects_a_tier_outside_one_to_three():
    with pytest.raises(ValidationError):
        Spot(**_spot_kwargs(tier=4))


def test_spot_round_trips_through_json():
    spot = Spot(
        **_spot_kwargs(
            getting_there=[
                AccessRef(kind="parking", lat=1, lng=2, distance_m=300, source_ref="way/1")
            ]
        )
    )
    assert Spot.model_validate_json(spot.model_dump_json()) == spot


def test_report_ignores_zero_counts():
    report = SpotBuildReport(jurisdiction=JURISDICTION)
    report.drop("a", 0)
    report.hold_back("b", 0)
    report.fallback("c", 0)
    assert report.dropped == report.held_back == report.title_fallbacks == {}


# ── ids ───────────────────────────────────────────────────────────────────────


def test_spot_ids_come_from_coordinates_not_stretches():
    result = run([confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek")])
    (spot,) = result.spots
    assert spot.spot_id == "creek_junction:n7960000_4350000"
    assert "credit" not in spot.spot_id


def test_spot_ids_survive_a_stretch_rename_and_anchor_order():
    anchors = [
        confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek"),
        confluence("b", 43.6, -79.5, 12.0),
    ]
    first = run(anchors)
    renamed = run(list(reversed(anchors)), [stretch(sid="other-id", name="New", river="New River")])
    assert [s.spot_id for s in first.spots] == [s.spot_id for s in renamed.spots]


def test_classify_is_deterministic():
    anchors = [confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek")]
    assert run(anchors).spots == run(anchors).spots


# ── confluences ───────────────────────────────────────────────────────────────


def test_a_confluence_listed_under_two_stretches_is_one_spot():
    anchors = [
        confluence("a", 43.5, -79.6, 30.0, name="Maple Creek", sid="credit-upper"),
        confluence("b", 43.5, -79.6, 30.0, name="Maple Creek", sid="credit-lower"),
    ]
    result = run(anchors, [stretch("credit-upper"), stretch("credit-lower")])
    (spot,) = result.spots
    assert spot.water_id == "credit-lower"  # ties break on stretch id, deterministically
    assert result.report.dropped == {"confluence_duplicate_location": 1}
    assert spot.structure_attrs["also_on_water_ids"] == ["credit-upper"]


def test_the_river_side_of_a_shared_junction_wins_and_the_creek_stretch_names_it():
    river = confluence("a", 43.5, -79.6, 80.0, sid="grand-mid", flows_on=True)
    creek = confluence("b", 43.5, -79.6, 4000.0, name="Grand River", sid="nith", flows_on=False)
    result = run(
        [creek, river],
        [
            stretch("grand-mid", "Middle Grand", "Grand River"),
            stretch("nith", "Nith", "Nith River"),
        ],
    )
    (spot,) = result.spots
    assert spot.title == "Where the Nith River joins the Grand River"
    assert spot.water_id == "grand-mid"
    assert spot.structure_attrs["tributary_name_from_water_id"] == "nith"


def test_a_stretch_that_ends_in_a_bigger_river_reads_from_the_creeks_side():
    result = run(
        [confluence("a", 43.5, -79.6, 4000.0, name="Grand River", flows_on=False, own_km=60.0)],
        [stretch(river="Nith River")],
    )
    assert only(result, "creek_junction")[0].title == "Where the Nith River joins the Grand River"


@pytest.mark.parametrize(
    ("km", "words"),
    [(12.0, "a small creek"), (60.0, "a creek"), (300.0, "a river")],
)
def test_an_unnamed_tributary_is_sized_in_plain_words(km, words):
    result = run([confluence("a", 43.5, -79.6, km)])
    assert result.spots[0].title == f"Where {words} joins the Credit River"
    assert result.report.title_fallbacks == {"creek_junction_unnamed_tributary": 1}


def test_a_creek_name_takes_no_article_and_a_river_name_does():
    result = run(
        [
            confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek"),
            confluence("b", 43.6, -79.6, 30.0, name="Black River"),
        ],
        [stretch(river="Bronte Creek")],
    )
    assert {s.title for s in result.spots} == {
        "Where Sheldon Creek joins Bronte Creek",
        "Where the Black River joins Bronte Creek",
    }


@pytest.mark.parametrize(
    ("name", "km", "tier"),
    [
        ("Sheldon Creek", 30.0, 1),  # named and big: headline
        ("Little Creek", 3.0, 2),  # named, small
        (None, 40.0, 2),  # unnamed, big
        (None, 12.0, 3),  # unnamed and small: held back
    ],
)
def test_confluence_tiers_follow_the_gate(name, km, tier):
    (spot,) = run([confluence("a", 43.5, -79.6, km, name=name)]).spots
    assert spot.tier == tier


def test_held_back_junctions_are_stored_and_counted():
    result = run([confluence("a", 43.5, -79.6, 12.0)])
    assert result.report.held_back == {"confluence_unnamed_and_small": 1}
    assert result.report.by_tier == {"3": 1}
    assert result.report.spots_total == 1


def test_junction_signals_stay_separate_numbers_not_a_score():
    (spot,) = run([confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek")]).spots
    assert spot.signals["named"] is True
    assert spot.signals["size_class"] == "medium"
    assert not any("score" in key for key in spot.signals)


# ── river mouths ──────────────────────────────────────────────────────────────


def mouth(lat=43.5, lon=-79.5, name=None, sid="credit-lower"):
    return anchor("lake_mouth", "m", lat, lon, sid=sid, name=name, shore_ogf_ids=[1, 2])


def test_a_mouth_takes_its_lake_from_the_curation_hints():
    result = run([mouth()], hints={"credit-lower": StretchHints(lake="Lake Ontario")})
    (spot,) = result.spots
    assert spot.title == "Where the Credit River meets Lake Ontario"
    assert spot.tier == 1
    assert spot.structure_attrs["lake"] == "Lake Ontario"


def test_a_mouth_falls_back_to_a_region_that_is_a_lake():
    (spot,) = run([mouth()], [stretch(region="Lake Erie")]).spots
    assert spot.title.endswith("meets Lake Erie")


def test_a_region_that_is_not_a_lake_is_not_used_as_one():
    result = run([mouth()], [stretch(region="Grand River")])
    assert result.spots[0].title == "Where the Credit River flows into the lake"
    assert result.report.title_fallbacks == {"river_mouth_no_lake_name": 1}


def test_bay_of_quinte_takes_its_article():
    (spot,) = run([mouth()], hints={"credit-lower": StretchHints(lake="Bay of Quinte")}).spots
    assert spot.title.endswith("meets the Bay of Quinte")


def test_a_curated_mouth_fills_in_where_the_topology_found_none():
    hints = {"credit-lower": StretchHints(lake="Lake Ontario", mouth=(43.55, -79.58))}
    (spot,) = run([], hints=hints).spots
    assert spot.kind == "river_mouth"
    assert (spot.lat, spot.lng) == (43.55, -79.58)
    assert spot.sources[0].kind == "curation"


def test_a_curated_mouth_never_moves_one_the_topology_found():
    hints = {"credit-lower": StretchHints(lake="Lake Ontario", mouth=(43.9, -79.9))}
    (spot,) = run([mouth()], hints=hints).spots
    assert (spot.lat, spot.lng) == (43.5, -79.5)


def test_the_same_mouth_under_two_stretches_is_one_spot():
    result = run(
        [mouth(sid="a"), mouth(sid="b")],
        [stretch("a"), stretch("b")],
        {"a": StretchHints("Lake Erie")},
    )
    assert len(result.spots) == 1
    assert result.report.dropped == {"river_mouth_duplicate_location": 1}


# ── below dams ────────────────────────────────────────────────────────────────


def dam(ref="way/1", lat=43.5, lon=-79.5, subtype="dam", name=None, sid="credit-lower"):
    return anchor(
        "dam", ref.replace("/", "-"), lat, lon, sid=sid, subtype=subtype, name=name, ref=ref
    )


def tail(ref="way/1", lat=43.499, lon=-79.5, kind="dam", name=None, sid="credit-lower"):
    return anchor(
        "tailwater",
        ref.replace("/", "-"),
        lat,
        lon,
        sid=sid,
        name=name,
        ref=ref,
        dam_source_ref=ref,
        dam_kind=kind,
        reach_m=400,
    )


def test_a_dam_and_its_tailwater_are_one_spot_at_the_tailwater():
    result = run([dam(name="Dunnville Dam"), tail(name="Below Dunnville Dam")])
    (spot,) = result.spots
    assert spot.kind == "below_dam"
    assert spot.title == "Below the Dunnville Dam"
    assert (spot.lat, spot.lng) == (43.499, -79.5)
    assert spot.tier == 1
    assert spot.structure_attrs["tailwater_verified"] is True
    assert len(spot.sources) == 2


def test_a_weir_counts_and_a_lock_does_not():
    result = run(
        [
            dam("way/1", subtype="weir", name="Mill Weir"),
            tail("way/1", kind="weir"),
            dam("way/2", lat=43.6, subtype="lock", name="Lock 3"),
        ]
    )
    (spot,) = result.spots
    assert spot.structure_type == "weir"
    assert result.report.dropped == {"dam_is_lock": 1}


def test_a_dam_name_that_lacks_the_word_gets_it():
    (spot,) = run([dam(name="Elora Mill"), tail()]).spots
    assert spot.title == "Below the Elora Mill dam"


def test_an_unnamed_dam_is_titled_from_the_river_and_ranks_tier_two():
    result = run([dam(), tail()])
    (spot,) = result.spots
    assert spot.title == "Below the dam on the Credit River"
    assert spot.tier == 2
    assert result.report.title_fallbacks == {"below_dam_unnamed": 1}


def test_a_named_dam_with_no_verified_tailwater_is_at_the_dam_not_below_it():
    (spot,) = run([dam(name="Shand Dam")]).spots
    assert spot.title == "At the Shand Dam"
    assert spot.structure_attrs["tailwater_verified"] is False
    assert (spot.lat, spot.lng) == (43.5, -79.5)


def test_an_unnamed_dam_with_no_verified_tailwater_is_not_a_spot():
    result = run([dam()])
    assert result.spots == []
    assert result.report.dropped == {"dam_unverified_and_unnamed": 1}


def test_one_dam_under_two_stretches_is_one_spot_listed_with_the_water_below_it():
    result = run(
        [
            dam("way/1", name="Paris Dam", sid="upper"),
            dam("way/1", name="Paris Dam", sid="lower"),
            tail("way/1", sid="lower"),
        ],
        [stretch("upper"), stretch("lower")],
    )
    (spot,) = result.spots
    assert spot.water_id == "lower"


def test_dam_ways_mapped_close_together_merge():
    result = run(
        [
            dam("way/1", name="Mill Dam"),
            dam("way/2", lat=43.5004, subtype="weir"),  # about 45 m away
            tail("way/1"),
        ]
    )
    assert len(result.spots) == 1


def test_a_tailwater_whose_dam_row_is_gone_still_makes_a_spot():
    (spot,) = run([tail("way/9", name="Below Mill Dam")]).spots
    assert spot.title == "Below the Mill Dam"


# ── public fishing spots ──────────────────────────────────────────────────────


def access(subtype, ref, lat, lon, sid="credit-lower", name=None, **attrs):
    return anchor(
        "access_point",
        ref.replace("/", "-"),
        lat,
        lon,
        sid=sid,
        subtype=subtype,
        name=name,
        ref=ref,
        **attrs,
    )


def test_an_osm_fishing_spot_is_a_spot_and_nothing_else_in_access_is():
    result = run(
        [
            access("fishing_spot", "node/1", 43.5, -79.5, name="Lakeside Pier"),
            access("parking", "way/2", 43.5, -79.5),
            access("trail_head", "node/3", 43.5, -79.5),
            access("boat_launch", "node/4", 43.5, -79.5),
            access("road_crossing", "way/5", 43.5, -79.5),
        ]
    )
    (spot,) = result.spots
    assert spot.kind == "public_fishing"
    assert spot.title == "Lakeside Pier"


def test_an_unnamed_fishing_spot_is_titled_from_the_river():
    (spot,) = run([access("fishing_spot", "node/1", 43.5, -79.5)]).spots
    assert spot.title == "Public fishing spot on the Credit River"


# ── getting there and bridges ────────────────────────────────────────────────


M = 1 / 111_320  # one metre of latitude, in degrees


def test_nearby_access_is_attached_to_a_spot_not_made_into_one():
    result = run(
        [
            confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek"),
            access("parking", "way/2", 43.5 + 300 * M, -79.6, fee="no"),
            access("trail_head", "node/3", 43.5 + 800 * M, -79.6),
            access("boat_launch", "node/4", 43.5 - 500 * M, -79.6),
            access("road_crossing", "way/5", 43.5 + 100 * M, -79.6, name="Main St"),
        ]
    )
    (spot,) = result.spots
    assert [r.kind for r in spot.getting_there] == ["parking", "boat_launch", "trail_head"]
    assert spot.getting_there[0].distance_m == 300
    assert spot.getting_there[0].attributes == {"fee": "no"}
    assert [r.name for r in spot.bridges] == ["Main St"]
    assert spot.signals["nearest_parking_m"] == 300
    assert spot.signals["nearest_launch_m"] == 500
    assert spot.signals["nearest_bridge_m"] == 100


def test_access_beyond_the_radius_is_not_attached_and_is_counted():
    result = run(
        [
            confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek"),
            access("parking", "way/2", 43.5 + 5000 * M, -79.6),
        ]
    )
    assert result.spots[0].getting_there == []
    assert result.report.spots_without_access == 1
    assert result.report.access_anchors == 1


def test_the_same_osm_element_under_two_stretches_is_attached_once():
    result = run(
        [
            confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek"),
            access("parking", "way/2", 43.5 + 300 * M, -79.6, sid="credit-lower"),
            access("parking", "way/2", 43.5 + 300 * M, -79.6, sid="credit-upper"),
        ]
    )
    assert len(result.spots[0].getting_there) == 1
    assert result.report.access_anchors == 1


def test_parking_is_capped_per_kind_nearest_first():
    parking = [access("parking", f"way/{i}", 43.5 + (100 * i) * M, -79.6) for i in range(1, 6)]
    (spot,) = run([confluence("a", 43.5, -79.6, 30.0, name="X Creek"), *parking]).spots
    assert [r.distance_m for r in spot.getting_there] == [100, 200]


# ── report ────────────────────────────────────────────────────────────────────


def test_report_accounts_for_every_anchor_kind():
    result = run(
        [
            confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek"),
            mouth(43.4, -79.5),
            dam(name="Mill Dam"),
            tail(),
            access("fishing_spot", "node/1", 43.7, -79.5),
        ]
    )
    report = result.report
    assert report.anchors_in == 5
    assert report.spots_total == 4
    assert report.by_kind == {
        "river_mouth": 1,
        "creek_junction": 1,
        "below_dam": 1,
        "public_fishing": 1,
    }
    assert sum(report.by_tier.values()) == 4


# ── words ─────────────────────────────────────────────────────────────────────


def test_no_jargon_in_anything_the_map_says():
    for text in spot_labels.all_strings():
        assert not JARGON.search(text), text


def test_titles_and_subtitles_carry_no_jargon_or_unnamed():
    result = run(
        [
            confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek"),
            confluence("b", 43.6, -79.6, 12.0),
            confluence("c", 43.7, -79.6, 300.0),
            mouth(43.4, -79.5),
            dam(),
            tail(),
            access("fishing_spot", "node/1", 43.8, -79.5),
        ],
        [stretch(name="Streetsville to Port Credit")],
    )
    assert len(result.spots) == 6
    for spot in result.spots:
        assert not JARGON.search(spot.title), spot.title
        assert "unnamed" not in spot.title.lower()


def test_each_kind_gets_exactly_one_two_sentence_labelled_paragraph():
    payload = spot_labels.labels_payload()
    assert [k["kind"] for k in payload["kinds"]] == list(spot_labels.KIND_ORDER)
    for entry in payload["kinds"]:
        why = entry["why_fish_gather"]
        assert len(re.findall(r"[.!?](?:\s|$)", why["text"])) == 2, why["text"]
        assert why["label"] == "General reasoning, not a record about this spot"
        assert why["provenance"] == "inference"
        assert not SEASON_OR_GEAR.search(why["text"]), why["text"]


def test_quiet_water_is_distinct_and_last():
    kinds = spot_labels.labels_payload()["kinds"]
    assert kinds[-1]["kind"] == "quiet_water"
    assert [k["distinct"] for k in kinds] == [False, False, False, False, True]


def test_bridges_are_off_by_default_and_getting_there_is_on():
    layers = spot_labels.labels_payload()["layers"]
    assert layers["bridges"]["default_on"] is False
    assert layers["getting_there"]["default_on"] is True


# ── curation hints ────────────────────────────────────────────────────────────


_CURATION_TOML = """
version = 1
jurisdiction = "CA-ON"
build_bbox = [-80.0, 43.0, -79.0, 44.0]

[[stretch]]
id = "credit-lower"
name = "Lower Credit"
river = "Credit River"
lake = "Lake Ontario"
[stretch.mouth]
lat = 43.55
lon = -79.58
[[stretch.reach]]
from = { lat = 43.6, lon = -79.7 }
to = { lat = 43.55, lon = -79.58 }

[[stretch]]
id = "etobicoke-lower"
name = "Lower Etobicoke"
river = "Etobicoke Creek"
[stretch.mouth]
lat = 43.59
lon = -79.54
lake = "Lake Ontario"
[[stretch.reach]]
from = { lat = 43.65, lon = -79.6 }
to = { lat = 43.59, lon = -79.54 }

[[stretch]]
id = "plain"
name = "Plain"
river = "Plain Creek"
[[stretch.reach]]
from = { lat = 43.7, lon = -79.7 }
to = { lat = 43.6, lon = -79.6 }
"""


def test_curated_lake_and_mouth_load_and_reach_the_classifier(tmp_path):
    path = tmp_path / "curation.toml"
    path.write_text(_CURATION_TOML)
    hints = load_stretch_hints(path)
    assert hints == {
        "credit-lower": StretchHints(lake="Lake Ontario", mouth=(43.55, -79.58)),
        "etobicoke-lower": StretchHints(lake="Lake Ontario", mouth=(43.59, -79.54)),
    }

    (spot,) = run([], [stretch("credit-lower")], hints).spots
    assert spot.kind == "river_mouth"
    assert (spot.lat, spot.lng) == (43.55, -79.58)
    assert spot.sources[0].kind == "curation"

    # the curated lake names the water even when the topology found the mouth
    (found,) = run([mouth()], [stretch("credit-lower")], hints).spots
    assert "Lake Ontario" in found.title


def test_the_real_curation_file_loads_without_hints_or_errors():
    assert isinstance(load_stretch_hints(), dict)


# ── storage, build, API ──────────────────────────────────────────────────────


def store(db, anchors, stretches):
    from src.storage.stretch_anchors import replace_anchors
    from src.storage.stretches import replace_stretches

    members = [
        StretchSegment(ogf_id=1000 + i, stretch_id=s.stretch_id, jurisdiction=JURISDICTION, seq=0)
        for i, s in enumerate(stretches)
    ]
    replace_stretches(db, JURISDICTION, stretches, members)
    replace_anchors(db, JURISDICTION, anchors, [])


def sample_anchors():
    return [
        confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek"),
        confluence("b", 43.6, -79.6, 12.0),
        mouth(43.4, -79.5),
        dam(lat=43.7, name="Mill Dam"),
        tail(lat=43.699),
        access("parking", "way/2", 43.5 + 300 * M, -79.6),
    ]


def test_build_spots_stores_what_classify_returns(db, curation):
    store(db, sample_anchors(), [stretch()])
    report = build_spots(db, curation)
    assert report.spots_total == 4
    rows = db.execute("SELECT kind, jurisdiction FROM spots ORDER BY spot_id").fetchall()
    assert len(rows) == 4
    assert {r[1] for r in rows} == {JURISDICTION}


def test_rebuilding_gives_the_same_spots_and_removes_stale_ones(db, curation):
    store(db, sample_anchors(), [stretch()])
    build_spots(db, curation)
    first = db.execute("SELECT spot_id FROM spots ORDER BY spot_id").fetchall()
    build_spots(db, curation)
    assert db.execute("SELECT spot_id FROM spots ORDER BY spot_id").fetchall() == first

    store(db, sample_anchors()[:1], [stretch()])
    build_spots(db, curation)
    assert db.execute("SELECT COUNT(*) FROM spots").fetchone()[0] == 1


def test_build_with_no_anchors_warns_and_empties(db, curation, caplog):
    store(db, [], [stretch()])
    with caplog.at_level("WARNING"):
        report = build_spots(db, curation)
    assert report.spots_total == 0
    assert "no stretch anchors stored" in caplog.text


def test_stored_spots_read_back_equal(db, curation):
    from src.storage.spots import list_spots

    store(db, sample_anchors(), [stretch()])
    build_spots(db, curation)
    classified = run(sample_anchors()).spots
    stored = list_spots(db)
    assert {s.spot_id for s in stored} == {s.spot_id for s in classified}
    assert all(isinstance(s, Spot) for s in stored)


def test_geojson_filters_by_bbox_kind_and_tier(db, curation):
    store(db, sample_anchors(), [stretch()])
    build_spots(db, curation)

    everything = spots_geojson(db, max_tier=3)
    assert everything["meta"]["count"] == 4
    default = spots_geojson(db)  # tier 2: the unnamed small creek is held back
    assert default["meta"]["count"] == 3
    headline = spots_geojson(db, max_tier=1)
    assert {f["properties"]["tier"] for f in headline["features"]} == {1}

    only_dams = spots_geojson(db, kinds=["below_dam"], max_tier=3)
    assert [f["properties"]["kind"] for f in only_dams["features"]] == ["below_dam"]
    # Chip counts ignore the kind filter, so the chips can say what each would add.
    assert only_dams["meta"]["kind_counts"]["creek_junction"] == 2

    south = spots_geojson(db, bbox=(-80.0, 43.0, -79.0, 43.45), max_tier=3)
    assert [f["properties"]["kind"] for f in south["features"]] == ["river_mouth"]


def test_geojson_orders_headline_spots_first_and_never_by_a_quality_score(db, curation):
    store(db, sample_anchors(), [stretch()])
    build_spots(db, curation)
    tiers = [f["properties"]["tier"] for f in spots_geojson(db, max_tier=3)["features"]]
    assert tiers == sorted(tiers)


def test_spot_detail_lists_other_spots_on_the_same_river(db, curation):
    store(db, sample_anchors(), [stretch()])
    build_spots(db, curation)
    spot_id = spots_geojson(db)["features"][0]["id"]
    detail = get_spot_detail(db, spot_id)
    assert detail["spot_id"] == spot_id
    assert detail["jurisdiction"] == JURISDICTION
    assert len(detail["more_on_this_water"]) == 3
    assert spot_id not in {o["spot_id"] for o in detail["more_on_this_water"]}
    assert get_spot_detail(db, "creek_junction:n1_1") is None


@pytest.fixture
def client(tmp_path, monkeypatch, curation, _template_db):
    import shutil

    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app, get_current_user

    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    api_db = database.get_db()
    store(api_db, sample_anchors(), [stretch()])
    build_spots(api_db, curation)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_spots_endpoint_returns_a_feature_collection(client):
    body = client.get("/map/spots").json()
    assert body["type"] == "FeatureCollection"
    assert body["meta"]["count"] == 3
    props = body["features"][0]["properties"]
    assert {"spot_id", "kind", "title", "tier", "jurisdiction"} <= set(props)


def test_spots_endpoint_filters(client):
    box = client.get("/map/spots?bbox=-80,43,-79,43.45&tier=3").json()
    assert [f["properties"]["kind"] for f in box["features"]] == ["river_mouth"]
    kinds = client.get("/map/spots?kind=below_dam,river_mouth&tier=3").json()
    assert {f["properties"]["kind"] for f in kinds["features"]} == {"below_dam", "river_mouth"}
    assert client.get("/map/spots?tier=1").json()["meta"]["max_tier"] == 1
    assert client.get("/map/spots?jurisdiction=US-MI").json()["features"] == []


@pytest.mark.parametrize(
    "query", ["bbox=1,2,3", "bbox=a,b,c,d", "bbox=5,5,1,1", "kind=lake", "tier=0", "tier=4"]
)
def test_spots_endpoint_rejects_bad_filters(client, query):
    assert client.get(f"/map/spots?{query}").status_code == 422


def test_spot_endpoint_returns_the_full_spot(client):
    listed = client.get("/map/spots?kind=creek_junction&tier=1").json()
    spot_id = listed["features"][0]["id"]
    body = client.get(f"/map/spots/{spot_id}").json()
    assert body["title"] == "Where Sheldon Creek joins the Credit River"
    assert body["getting_there"][0]["kind"] == "parking"
    assert body["sources"]


def test_spot_endpoint_404s_for_an_unknown_id(client):
    assert client.get("/map/spots/creek_junction:n1_1").status_code == 404


def test_labels_endpoint_serves_the_vocabulary_and_wins_the_route_over_spot_ids(client):
    body = client.get("/map/spots/labels").json()
    assert [k["chip"] for k in body["kinds"]][:3] == [
        "River mouths",
        "Where creeks join",
        "Below dams",
    ]


def test_spots_endpoints_require_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    client = TestClient(app)
    for path in ("/map/spots", "/map/spots/labels", "/map/spots/creek_junction:n1_1"):
        assert client.get(path).status_code in (401, 403)


# ── the fixture other tools build against ────────────────────────────────────


def test_the_api_fixture_still_matches_the_models_and_the_labels():
    import json
    from pathlib import Path

    from src.models.water_body import WaterBody

    path = Path(__file__).parent / "fixtures" / "spots_api_responses.json"
    entries = json.loads(path.read_text())["requests"]
    assert {e["status"] for e in entries} == {200, 404, 422}
    for e in entries:
        route = e["request"].removeprefix("GET ")
        if e["status"] != 200:
            assert set(e["response"]) == {"detail"}
        elif route == "/map/spots/labels":
            assert e["response"] == spot_labels.labels_payload()
        elif route.endswith("/features"):
            assert e["response"]["type"] == "FeatureCollection"
        elif route.startswith("/map/water-bodies/"):
            detail = {
                k: v
                for k, v in e["response"].items()
                if k not in ("display_name", "spots", "also_here_spots")
            }
            # The API says section_ids / section_union; the model keeps the stored names.
            detail["stretch_ids"] = detail.pop("section_ids")
            detail["geometry_source"] = {"section_union": "stretch_union"}.get(
                detail["geometry_source"], detail["geometry_source"]
            )
            assert WaterBody.model_validate(detail).water_body_id == route.removeprefix(
                "/map/water-bodies/"
            )
        elif route.startswith("/map/water-bodies"):
            assert e["response"]["type"] == "FeatureCollection"
            for f in e["response"]["features"]:
                assert f["id"] == f["properties"]["water_body_id"]
                assert f["properties"]["jurisdiction"]
                assert f["properties"]["name_source"] in spot_labels.NAME_SOURCE_LABELS
                assert f["properties"]["type"] in spot_labels.WATER_BODY_TYPES
        elif route.startswith("/map/spots/"):
            detail = {
                k: v
                for k, v in e["response"].items()
                if k not in ("more_on_this_water", "water_body", "also_on_water_bodies", "card")
            }
            assert Spot.model_validate(detail).spot_id == route.removeprefix("/map/spots/")
            if detail["kind"] == "quiet_water":
                # A reach found by the explore ranking belongs to no curated river.
                assert e["response"]["water_body"] is None and detail["water_body_id"] is None
            else:
                assert e["response"]["water_body"]["water_body_id"] == detail["water_body_id"]
            assert SpotCard.model_validate(e["response"]["card"]).spot_id == detail["spot_id"]
        elif route.startswith("/map/layers/"):
            assert e["response"]["type"] == "FeatureCollection"
            for f in e["response"]["features"]:
                assert f["properties"]["kind"] in spot_labels.ACCESS_LABELS
        else:
            assert e["response"]["type"] == "FeatureCollection"
            for f in e["response"]["features"]:
                assert f["id"] == f["properties"]["spot_id"]
                assert f["properties"]["jurisdiction"]
                if f["properties"]["kind"] != "quiet_water":
                    assert f["properties"]["water_body_id"]
                assert not JARGON.search(f["properties"]["title"])
            # One title per structure: a repeat is merged or told apart. Quiet water
            # is a ranking of reaches, titled by the nearest water, and is left out.
            titles = [
                f["properties"]["title"]
                for f in e["response"]["features"]
                if f["properties"]["kind"] != "quiet_water"
            ]
            assert len(titles) == len(set(titles)), route


# ── the commands ─────────────────────────────────────────────────────────────


def test_build_anchors_runs_the_spot_build_at_its_end(tmp_path, monkeypatch, curation, db):
    from typer.testing import CliRunner

    import src.cli.main as cli
    import src.services.stretch_anchors as anchors_module
    from src.models.stretch_anchor import AnchorBuildReport

    store(db, sample_anchors(), [stretch()])
    monkeypatch.setattr(cli, "get_db", lambda: db)
    monkeypatch.setattr(
        anchors_module,
        "build_anchors",
        lambda *a, **k: AnchorBuildReport(jurisdiction=JURISDICTION, stretches=1),
    )
    result = CliRunner().invoke(cli.app, ["build-anchors", "--curation", str(curation)])
    assert result.exit_code == 0, result.output
    assert db.execute("SELECT COUNT(*) FROM spots").fetchone()[0] == 4
    assert "Map spots" in result.output


def test_build_spots_command_prints_what_it_dropped(tmp_path, monkeypatch, curation, db):
    from typer.testing import CliRunner

    import src.cli.main as cli

    store(db, [dam(), confluence("a", 43.5, -79.6, 12.0)], [stretch()])
    monkeypatch.setattr(cli, "get_db", lambda: db)
    result = CliRunner().invoke(cli.app, ["build-spots", "--curation", str(curation)])
    assert result.exit_code == 0, result.output
    assert "dam_unverified_and_unnamed" in result.output
    assert "confluence_unnamed_and_small" in result.output


def test_spots_endpoint_accepts_species_and_near_filters():
    from fastapi.testclient import TestClient

    from src.api.main import app, get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    client = TestClient(app)
    # near with 2 params
    r = client.get("/map/spots?near=43.2,-80.1&bbox=-80.2,43.1,-80.0,43.3")
    assert r.status_code == 200
    # near with 3 params
    r = client.get("/map/spots?near=43.2,-80.1,10&bbox=-80.2,43.1,-80.0,43.3")
    assert r.status_code == 200


def test_spots_endpoint_rejects_bad_near():
    from fastapi.testclient import TestClient

    from src.api.main import app, get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    client = TestClient(app)
    r = client.get("/map/spots?near=bad")
    assert r.status_code == 422
    for bad in ("nan,1,2", "inf,1,2", "999,1", "43,-79,0", "43,-79,-5", "43,200"):
        assert client.get("/map/spots", params={"near": bad}).status_code == 422, bad


def test_layers_getting_there_and_bridges():
    from fastapi.testclient import TestClient

    from src.api.main import app, get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    client = TestClient(app)
    r = client.get("/map/layers/getting-there")
    assert r.status_code == 200
    r = client.get("/map/layers/bridges")
    assert r.status_code == 200
