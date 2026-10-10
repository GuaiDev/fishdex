"""Stretch anchors: confluence and lake-mouth derivation, OSM matching, rebuild, API.

Everything runs on a small hand-built network so the expected answer is known
by construction. No test touches the network: the OSM corridor is a fake the
test hands to build_anchors.

    lon -80.00, flowing south            lon -79.95
    43.10 r1 ─┐
    43.09 r2  │
    43.08 r3  │  (braid b1 leaves at 43.08, rejoins at 43.07)
    43.07 r4  │
    43.06 r5  │
    43.05 r6 ─┼──────────── t1 (Maple Creek, 20 km headwater) joins here
    43.04 r7  │      d1: a 1 km ditch joins at 43.03
    43.03 r8  │
    43.02 r9  │
    43.01 r10 │
    43.00 ────┴─ mouth, on a lake shore line running east-west through it

The main river is two stretches split at the Maple Creek confluence
(upper r1-r5, lower r6-r10), and Maple Creek is a third.
"""

import pytest

from src.models.stretch import FishingStretch, StretchSegment
from src.models.stretch_anchor import AnchorBuildReport, StretchAnchor
from src.models.water_feature import CorridorFeature
from tests.test_stretches import LON, seg

HEADER = """
version = 1
jurisdiction = "CA-ON"
build_bbox = [-80.5, 42.9, -79.5, 43.3]
"""
M_PER_DEG = 111_000


def network():
    segs = [seg(900, [(LON, 43.20), (LON, 43.10)], length_m=50_000)]
    for k in range(10):  # r1..r10 are ogf 101..110
        segs.append(seg(101 + k, [(LON, 43.10 - 0.01 * k), (LON, 43.09 - 0.01 * k)]))
    # A side channel leaving the river at 43.08 and rejoining at 43.07.
    segs.append(seg(150, [(LON, 43.08), (LON + 0.002, 43.075), (LON, 43.07)]))
    # Maple Creek joins at 43.05, fed by a long headwater.
    segs.append(seg(950, [(-79.85, 43.05), (-79.90, 43.05)], length_m=20_000))
    segs.append(seg(201, [(-79.90, 43.05), (-79.95, 43.05)], name="Maple Creek (ruisseau Maple)"))
    segs.append(seg(202, [(-79.95, 43.05), (LON, 43.05)]))
    # A ditch joins at 43.03.
    segs.append(seg(300, [(-79.99, 43.03), (LON, 43.03)], length_m=1_000))
    # Lake shore line through the mouth: one segment in, one out, both lake-sized.
    segs.append(seg(990, [(-80.10, 43.00), (LON, 43.00)], length_m=40_000_000))
    segs.append(seg(991, [(LON, 43.00), (-79.90, 43.00)], length_m=40_000_000))
    return segs


@pytest.fixture(scope="module")
def _template_db(tmp_path_factory):
    """Schema setup costs over a second; build it once and copy the file per test."""
    from src.storage.database import get_db

    path = tmp_path_factory.mktemp("template") / "template.db"
    get_db(path).conn.close()
    return path


@pytest.fixture
def db(tmp_path, _template_db):
    import shutil

    from src.storage.database import get_db

    path = tmp_path / "anchors.db"
    shutil.copy(_template_db, path)
    return get_db(path)


def _stretch(sid, name, ids):
    return FishingStretch(
        stretch_id=sid,
        name=name,
        river="Test River",
        jurisdiction="CA-ON",
        length_km=1.0,
        segment_count=len(ids),
        geometry={"type": "LineString", "coordinates": [[LON, 43.10], [LON, 43.00]]},
        bbox=(-80.1, 43.0, -79.9, 43.1),
        centroid_lat=43.05,
        centroid_lon=LON,
        built_at="2026-10-10T00:00:00+00:00",
    )


STRETCHES = {
    "test-upper": ("Upper", [101, 102, 103, 104, 105]),
    "test-lower": ("Lower", [106, 107, 108, 109, 110]),
    "test-maple": ("Maple Creek", [202]),
}


@pytest.fixture
def stretches(db, tmp_path):
    """Publish the three stretches directly, then point a curation file at them."""
    from src.storage.stretches import replace_stretches

    published = [_stretch(sid, n, ids) for sid, (n, ids) in STRETCHES.items()]
    members = [
        StretchSegment(ogf_id=i, stretch_id=sid, jurisdiction="CA-ON", seq=seq)
        for sid, (_, ids) in STRETCHES.items()
        for seq, i in enumerate(ids)
    ]
    replace_stretches(db, "CA-ON", published, members)
    path = tmp_path / "curation.toml"
    path.write_text(HEADER)
    return path


def feat(kind, lat, lon, osm_id, name=None, line=None, **tags):
    return CorridorFeature(
        osm_id=osm_id,
        kind=kind,
        name=name,
        lat=lat,
        lng=lon,
        jurisdiction="CA-ON",
        tags=tags,
        line=line,
    )


def nothing(line, jurisdiction):
    return []


def build(db, path, fetch=nothing, segments=None):
    from src.services.stretch_anchors import build_anchors

    return build_anchors(
        db, path, segments=network() if segments is None else segments, fetch_corridor=fetch
    )


def by(db, sid, anchor_type):
    from src.storage.stretch_anchors import list_anchors

    return [a for a in list_anchors(db, sid) if a.anchor_type == anchor_type]


# ── models ────────────────────────────────────────────────────────────────────


def test_anchor_model_validates_id_jurisdiction_and_type():
    base = dict(
        anchor_id="test-upper:confluence:n8000000_4305000",
        stretch_id="test-upper",
        jurisdiction="CA-ON",
        anchor_type="confluence",
        lat=43.05,
        lon=-80.0,
        source="ohn_topology",
        source_ref="ogf:1",
        built_at="2026-10-10T00:00:00+00:00",
    )
    assert StretchAnchor(**base).attributes == {}
    for bad in (
        {"jurisdiction": "Ontario"},
        {"anchor_type": "treasure"},
        {"anchor_id": "Has Spaces"},
        {"source": "guess"},
        {"lat": 91},
    ):
        with pytest.raises(ValueError):
            StretchAnchor(**{**base, **bad})


def test_report_counts_drops_and_names_stretches_without_osm():
    from src.models.stretch_anchor import AnchorBuildIssue

    report = AnchorBuildReport(jurisdiction="CA-ON", stretches=2)
    report.drop("x")
    report.drop("x", 2)
    assert report.dropped == {"x": 3}
    report.issues.append(AnchorBuildIssue(stretch_id="b", kind="osm_unavailable", detail="down"))
    report.issues.append(AnchorBuildIssue(stretch_id="a", kind="osm_unavailable", detail="down"))
    assert report.stretches_without_osm == ["a", "b"]


# ── confluences ───────────────────────────────────────────────────────────────


def test_confluence_is_found_named_and_sized(db, stretches):
    build(db, stretches)
    (c,) = by(db, "test-upper", "confluence")
    assert (c.lat, c.lon) == (43.05, LON)
    assert c.name == "Maple Creek"  # bilingual suffix stripped
    assert c.source == "ohn_topology" and c.jurisdiction == "CA-ON"
    assert c.attributes["joining_ogf_ids"] == [202]
    assert c.attributes["joining_upstream_km"] > 20
    assert c.attributes["stretch_flows_on"] is False  # the upper stretch ends here
    assert c.ogf_id == 105


def test_confluence_name_is_found_further_up_an_unnamed_mouth(db, stretches):
    """Segment 202 (the mouth) is unnamed; 201 above it is Maple Creek."""
    build(db, stretches)
    (c,) = by(db, "test-upper", "confluence")
    assert c.attributes["name_from_ogf_id"] == 201


def test_the_river_a_tributary_stretch_ends_in_is_its_confluence(db, stretches):
    build(db, stretches)
    (c,) = by(db, "test-maple", "confluence")
    assert (c.lat, c.lon) == (43.05, LON)
    assert c.name is None  # the main river is unnamed in this network
    assert set(c.attributes["joining_ogf_ids"]) == {105}  # the river arriving from above


def test_the_seam_between_two_stretches_is_not_a_confluence_but_a_tributary_there_is(db, stretches):
    """The lower stretch starts where Maple Creek joins. The river arriving from the
    upper stretch is its own continuation; the creek is the confluence."""
    report = build(db, stretches)
    (c,) = by(db, "test-lower", "confluence")
    assert c.attributes["joining_ogf_ids"] == [202]
    assert report.dropped["confluence_stretch_seam"] >= 1


def test_small_tributaries_and_side_channels_are_counted_not_published(db, stretches):
    report = build(db, stretches)
    # the 1 km ditch at 43.03 and the braid that leaves and rejoins the river
    assert report.dropped["confluence_small_tributary"] == 1
    assert report.dropped["confluence_side_channel"] == 1
    assert [a.name for a in by(db, "test-lower", "confluence")] == ["Maple Creek"]


# ── lake mouths ───────────────────────────────────────────────────────────────


def test_lake_mouth_is_where_the_river_meets_the_shore_line(db, stretches):
    build(db, stretches)
    (m,) = by(db, "test-lower", "lake_mouth")
    assert (m.lat, m.lon) == (43.0, LON)
    assert set(m.attributes["shore_ogf_ids"]) == {990, 991}
    assert not by(db, "test-upper", "lake_mouth")  # ends at a seam, not the lake


def test_the_lake_itself_is_not_a_confluence(db, stretches):
    report = build(db, stretches)
    assert not [a for a in by(db, "test-lower", "confluence") if a.lat == 43.0]
    assert report.dropped["confluence_is_lake_mouth"] == 1


def test_a_river_that_simply_continues_into_big_water_is_not_a_mouth(db, stretches):
    """No shore line arriving from the side: just the next stretch of the river."""
    segs = [s for s in network() if s.ogf_id != 990]
    build(db, stretches, segments=segs)
    assert not by(db, "test-lower", "lake_mouth")


# ── OSM anchors ───────────────────────────────────────────────────────────────


def metres_east(lon, m, lat=43.05):
    import math

    return lon + m / (M_PER_DEG * math.cos(math.radians(lat)))


def corridor():
    return [
        # Dam 40 m off the river line on the lower stretch, mapped three ways.
        feat("dam", 43.045, metres_east(LON, 40), "way/1", name="Test Dam", height="4"),
        feat("weir", 43.045, metres_east(LON, 60), "node/2"),
        # A dam on a pond 1 km away: not on the river.
        feat("dam", 43.045, metres_east(LON, 1000), "way/3", name="Pond Dam"),
        feat("parking", 43.025, metres_east(LON, 120), "way/10", name="River Park"),
        feat("parking", 43.025, metres_east(LON, 900), "way/11"),  # too far from the water
        feat("parking", 43.025, metres_east(LON, 100), "way/12", access="private"),
        feat("boat_launch", 43.015, metres_east(LON, 30), "node/13"),
        feat("trail_head", 43.035, metres_east(LON, 200), "node/14", name="Riverside Trail"),
        # Test Road crosses the river at 43.02, mapped as two ways 60 m apart.
        feat(
            "road",
            43.02,
            LON,
            "way/20",
            name="Test Road",
            line=[(-80.01, 43.02), (-79.99, 43.02)],
            highway="secondary",
            bridge="yes",
        ),
        feat(
            "road",
            43.0206,
            LON,
            "way/21",
            name="Test Road",
            line=[(-80.01, 43.0206), (-79.99, 43.0206)],
            highway="secondary",
        ),
        # A road that never reaches the water.
        feat(
            "road",
            43.04,
            -79.97,
            "way/22",
            name="Far Road",
            line=[(-79.98, 43.04), (-79.96, 43.04)],
        ),
    ]


def fetch_corridor(line, jurisdiction):
    return corridor()


def test_dam_is_snapped_to_the_stretch_and_duplicate_mappings_merge(db, stretches):
    report = build(db, stretches, fetch_corridor)
    (dam,) = by(db, "test-lower", "dam")
    assert (dam.subtype, dam.name, dam.source_ref) == ("dam", "Test Dam", "way/1")
    assert dam.source == "osm" and dam.ogf_id == 106  # r6 runs 43.05 -> 43.04
    assert 30 < dam.snap_distance_m < 60
    assert dam.attributes["also_mapped_as"] == ["weir:node/2"]
    assert report.dropped["dam_duplicate_mapping"] >= 1
    assert report.dropped["dam_off_channel"] >= 1  # the pond dam, once per stretch tried


def test_tailwater_is_placed_downstream_of_the_dam(db, stretches):
    build(db, stretches, fetch_corridor)
    (tail,) = by(db, "test-lower", "tailwater")
    (dam,) = by(db, "test-lower", "dam")
    assert tail.name == "Below Test Dam"
    assert tail.lat < dam.lat  # the river flows south
    metres = (dam.lat - tail.lat) * M_PER_DEG
    assert 60 < metres < 160
    assert tail.attributes["dam_source_ref"] == dam.source_ref
    reach = tail.attributes["reach"]
    assert reach["type"] == "LineString" and len(reach["coordinates"]) >= 2
    assert 300 < tail.attributes["reach_m"] <= 410


def test_tailwater_needs_a_verified_flow_direction(db, stretches):
    segs = [
        s.model_copy(update={"flow_verified": False}) if s.ogf_id in (106, 107) else s
        for s in network()
    ]
    report = build(db, stretches, fetch_corridor, segments=segs)
    assert by(db, "test-lower", "dam")  # still a dam
    assert not by(db, "test-lower", "tailwater")  # but no guess about which side is below
    assert "tailwater_unverified" in {i.kind for i in report.issues}


def test_a_lock_has_no_tailwater(db, stretches):
    def fetch(line, j):
        return [feat("lock", 43.045, metres_east(LON, 20), "node/5", name="Test Lock")]

    build(db, stretches, fetch)
    assert by(db, "test-lower", "dam")[0].subtype == "lock"
    assert not by(db, "test-lower", "tailwater")


def test_access_points_must_be_public_and_near_the_water(db, stretches):
    report = build(db, stretches, fetch_corridor)
    access = {a.subtype: a for a in by(db, "test-lower", "access_point")}
    kinds = sorted(a.subtype for a in by(db, "test-lower", "access_point"))
    assert kinds == ["boat_launch", "parking", "road_crossing", "trail_head"]
    assert access["parking"].name == "River Park"
    assert access["parking"].snap_distance_m == pytest.approx(120, abs=15)
    assert report.dropped["access_not_public"] >= 1
    assert report.dropped["access_too_far_from_water"] >= 1


def test_a_road_crossing_is_one_per_road_and_place(db, stretches):
    report = build(db, stretches, fetch_corridor)
    (x,) = [a for a in by(db, "test-lower", "access_point") if a.subtype == "road_crossing"]
    assert x.name == "Test Road"
    assert x.attributes["bridge"] == "yes"
    assert x.lon == pytest.approx(LON, abs=1e-4)
    assert x.lat == pytest.approx(43.02, abs=0.001)
    assert report.dropped["crossing_duplicate_way"] >= 1
    assert report.dropped["crossing_not_on_channel"] >= 1  # Far Road


def test_osm_anchors_belong_only_to_the_stretches_they_sit_on(db, stretches):
    build(db, stretches, fetch_corridor)
    assert not by(db, "test-upper", "dam") and not by(db, "test-upper", "access_point")


# ── rebuild ───────────────────────────────────────────────────────────────────


def snapshot(db):
    from src.storage.stretch_anchors import list_anchors, list_segment_lines

    anchors = [a.model_dump(exclude={"built_at"}) for a in list_anchors(db)]
    lines = [
        (line.ogf_id, line.geometry) for sid in STRETCHES for line in list_segment_lines(db, sid)
    ]
    return anchors, lines


def test_rebuild_is_idempotent(db, stretches):
    first = build(db, stretches, fetch_corridor)
    before = snapshot(db)
    second = build(db, stretches, fetch_corridor)
    assert snapshot(db) == before
    assert first.anchors_total == second.anchors_total == len(before[0]) > 0
    ids = [a["anchor_id"] for a in before[0]]
    assert len(ids) == len(set(ids))


def test_anchor_ids_survive_a_change_in_what_else_is_found(db, stretches):
    """A saved pin points at an anchor id: it must not depend on list position."""
    build(db, stretches, fetch_corridor)
    ids_full = {a["anchor_id"] for a in snapshot(db)[0]}
    build(db, stretches, lambda line, j: list(reversed(corridor()))[:-3])
    ids_less = {a["anchor_id"] for a in snapshot(db)[0]}
    assert ids_less < ids_full or ids_less == ids_full
    assert {i for i in ids_full if ":confluence:" in i or ":lake_mouth:" in i} <= ids_less


def test_segment_lines_are_stored_for_every_stretch_segment(db, stretches):
    from src.storage.stretch_anchors import list_segment_lines

    build(db, stretches)
    lines = list_segment_lines(db, "test-lower")
    assert [line.ogf_id for line in lines] == [106, 107, 108, 109, 110]  # trace order
    assert all(line.geometry["type"] == "LineString" for line in lines)


def test_overpass_outage_keeps_the_last_osm_anchors_and_says_so(db, stretches):
    build(db, stretches, fetch_corridor)
    before = {a["anchor_id"] for a in snapshot(db)[0]}

    def down(line, jurisdiction):
        raise ConnectionError("overpass is down")

    report = build(db, stretches, down)
    assert {a["anchor_id"] for a in snapshot(db)[0]} == before
    assert report.stretches_without_osm == ["test-lower", "test-maple", "test-upper"]
    assert report.carried_over_stretches == ["test-lower"]  # the only stretch with OSM anchors
    assert "overpass is down" in report.issues[0].detail


def test_first_build_during_an_outage_has_topology_anchors_and_a_loud_report(db, stretches):
    def down(line, jurisdiction):
        raise TimeoutError("slow")

    report = build(db, stretches, down)
    assert by(db, "test-lower", "lake_mouth") and by(db, "test-upper", "confluence")
    assert not by(db, "test-lower", "dam")
    assert len(report.stretches_without_osm) == 3
    assert report.carried_over_stretches == []


def test_dropping_a_stretch_drops_its_anchors_but_rebuilding_one_keeps_them(db, stretches):
    """Anchors outlive a stretch rebuild (so an Overpass outage right after it still
    has last time's dams to carry over), but not the stretch itself."""
    from src.storage.stretch_anchors import count_anchors
    from src.storage.stretches import replace_stretches

    build(db, stretches, fetch_corridor)
    total = count_anchors(db)
    assert total > 0
    kept = [_stretch(sid, n, ids) for sid, (n, ids) in STRETCHES.items() if sid != "test-maple"]
    members = [
        StretchSegment(ogf_id=i, stretch_id=sid, jurisdiction="CA-ON", seq=seq)
        for sid, (_, ids) in STRETCHES.items()
        if sid != "test-maple"
        for seq, i in enumerate(ids)
    ]
    replace_stretches(db, "CA-ON", kept, members)
    assert not by(db, "test-maple", "confluence")
    assert by(db, "test-lower", "lake_mouth") and by(db, "test-upper", "confluence")
    assert 0 < count_anchors(db) < total
    assert db.execute(
        "SELECT COUNT(*) FROM stretch_segment_lines WHERE stretch_id = 'test-maple'"
    ).fetchone() == (0,)

    replace_stretches(db, "CA-ON", [], [])
    assert count_anchors(db) == 0
    assert db.execute("SELECT COUNT(*) FROM stretch_segment_lines").fetchone() == (0,)


def test_an_outage_right_after_a_stretch_rebuild_still_has_the_last_osm_anchors(db, stretches):
    from src.storage.stretches import list_stretches, replace_stretches, stretch_segment_ids

    build(db, stretches, fetch_corridor)
    before = {a["anchor_id"] for a in snapshot(db)[0] if ":dam:" in a["anchor_id"]}
    assert before

    published = list_stretches(db, "CA-ON")
    members = [
        StretchSegment(ogf_id=i, stretch_id=s.stretch_id, jurisdiction="CA-ON", seq=seq)
        for s in published
        for seq, i in enumerate(stretch_segment_ids(db, s.stretch_id))
    ]
    replace_stretches(db, "CA-ON", published, members)  # what build_stretches does

    def down(line, jurisdiction):
        raise ConnectionError("overpass is down")

    build(db, stretches, down)
    assert {a["anchor_id"] for a in snapshot(db)[0] if ":dam:" in a["anchor_id"]} == before


# ── GeoJSON and API ───────────────────────────────────────────────────────────


def test_geojson_has_segment_lines_and_anchor_points(db, stretches):
    from src.services.stretch_anchors import stretch_anchors_geojson

    build(db, stretches, fetch_corridor)
    fc = stretch_anchors_geojson(db, "test-lower")
    assert fc["type"] == "FeatureCollection"
    assert fc["stretch"]["stretch_id"] == "test-lower"
    assert len(fc["stretch"]["bbox"]) == 4

    kinds = {f["geometry"]["type"] for f in fc["features"]}
    assert kinds == {"LineString", "Point"}
    segments = [f for f in fc["features"] if f["properties"]["feature"] == "segment"]
    anchors = [f for f in fc["features"] if f["properties"]["feature"] == "anchor"]
    assert len(segments) == 5
    types = {f["properties"]["anchor_type"] for f in anchors}
    assert types == {"confluence", "lake_mouth", "dam", "tailwater", "access_point"}
    dam = next(f for f in anchors if f["properties"]["anchor_type"] == "dam")
    assert dam["id"] == dam["properties"]["anchor_id"]
    assert dam["geometry"]["coordinates"] == [
        pytest.approx(by(db, "test-lower", "dam")[0].lon),
        pytest.approx(by(db, "test-lower", "dam")[0].lat),
    ]
    assert dam["properties"]["source"] == "osm" and dam["properties"]["jurisdiction"] == "CA-ON"


def test_geojson_for_an_unknown_stretch_is_none(db):
    from src.services.stretch_anchors import stretch_anchors_geojson

    assert stretch_anchors_geojson(db, "nope") is None


@pytest.fixture
def client(tmp_path, monkeypatch, stretches):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app, get_current_user

    # `stretches` built into its own db; point the app at a fresh file and rebuild there
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    api_db = database.get_db()
    from src.storage.stretches import replace_stretches

    published = [_stretch(sid, n, ids) for sid, (n, ids) in STRETCHES.items()]
    members = [
        StretchSegment(ogf_id=i, stretch_id=sid, jurisdiction="CA-ON", seq=seq)
        for sid, (_, ids) in STRETCHES.items()
        for seq, i in enumerate(ids)
    ]
    replace_stretches(api_db, "CA-ON", published, members)
    build(api_db, stretches, fetch_corridor)

    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_anchors_endpoint_returns_the_stretch_as_geojson(client):
    body = client.get("/map/stretches/test-lower/anchors").json()
    assert body["type"] == "FeatureCollection"
    assert body["stretch"]["name"] == "Lower"
    assert {f["properties"]["feature"] for f in body["features"]} == {"segment", "anchor"}


def test_anchors_endpoint_404s_for_an_unknown_stretch(client):
    assert client.get("/map/stretches/nope/anchors").status_code == 404


def test_anchors_endpoint_requires_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    assert TestClient(app).get("/map/stretches/test-lower/anchors").status_code in (401, 403)


def test_build_stops_asking_overpass_after_repeated_failures(db, stretches, monkeypatch):
    import src.services.stretch_anchors as module

    monkeypatch.setattr(module, "_GIVE_UP_AFTER", 1)
    calls = []

    def down(line, jurisdiction):
        calls.append(1)
        raise TimeoutError("slow")

    report = build(db, stretches, down)
    assert len(calls) == 1  # the other two stretches were not even tried
    assert len(report.stretches_without_osm) == 3
    assert sum("Overpass failed for 1 stretches in a row" in i.detail for i in report.issues) == 2


def test_tailwater_is_listed_with_the_stretch_whose_water_it_is_in(db, stretches):
    """A dam just above the seam between two stretches: the dam sits at the end of
    the upper one, but the water below it is the lower stretch's."""

    def fetch(line, j):
        return [feat("dam", 43.0508, metres_east(LON, 40), "way/9", name="Seam Dam")]

    build(db, stretches, fetch)
    (upper_dam,) = by(db, "test-upper", "dam")
    (tail,) = by(db, "test-lower", "tailwater")
    assert not by(db, "test-upper", "tailwater")
    assert tail.ogf_id == 106
    assert tail.attributes["dam_source_ref"] == upper_dam.source_ref == "way/9"
    assert tail.lat < 43.05  # below the seam, in the lower stretch's water


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli(monkeypatch, tmp_path):
    import src.cli.main as cli
    import src.storage.database as database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "cli.db")
    return cli


def test_build_stretches_rebuilds_anchors_from_the_same_network(monkeypatch, tmp_path):
    """Replacing stretches clears their anchors, so the command must rebuild them —
    from the network it already fetched, not a second download."""
    from typer.testing import CliRunner

    from src.models.stretch import StretchBuildReport

    cli = _cli(monkeypatch, tmp_path)
    network_calls, order = [], []
    segments = network()

    def load_network(curation):
        network_calls.append(1)
        return segments

    def build_stretches(db, path, segs):
        order.append(("stretches", segs))
        return StretchBuildReport(jurisdiction="CA-ON", network_segments=len(segs), curated_total=0)

    def build_anchors(db, path, segs=None):
        order.append(("anchors", segs))
        return AnchorBuildReport(jurisdiction="CA-ON", stretches=61, anchors_total=7)

    monkeypatch.setattr("src.services.stretches.load_network", load_network)
    monkeypatch.setattr("src.services.stretches.build_stretches", build_stretches)
    monkeypatch.setattr("src.services.stretch_anchors.build_anchors", build_anchors)

    result = CliRunner().invoke(cli.app, ["build-stretches"])
    assert result.exit_code == 0, result.output
    assert [name for name, _ in order] == ["stretches", "anchors"]
    assert all(segs is segments for _, segs in order)
    assert len(network_calls) == 1
    assert "Stretch anchors" in result.output


def test_build_anchors_command_reports_what_it_did_not_keep(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from src.models.stretch_anchor import AnchorBuildIssue

    cli = _cli(monkeypatch, tmp_path)
    report = AnchorBuildReport(
        jurisdiction="CA-ON",
        stretches=2,
        anchors_total=3,
        by_type={"confluence": 2, "dam": 1},
        dropped={"access_not_public": 4},
        issues=[AnchorBuildIssue(stretch_id="s", kind="osm_unavailable", detail="504")],
    )
    monkeypatch.setattr("src.services.stretch_anchors.build_anchors", lambda *a, **k: report)

    result = CliRunner().invoke(cli.app, ["build-anchors"])
    assert result.exit_code == 0, result.output
    assert "access_not_public" in result.output and "osm_unavailable" in result.output


def test_an_off_river_dam_does_not_swallow_a_weir_on_the_river(db, stretches):
    def fetch(line, j):
        return [
            # 140 m off the river, so not on it; the weir is 120 m from this dam,
            # inside the merge radius, and ranks below it.
            feat("dam", 43.045, metres_east(LON, 140), "way/50", name="Pond Dam"),
            feat("weir", 43.045, metres_east(LON, 20), "node/51"),
        ]

    report = build(db, stretches, fetch)
    (dam,) = by(db, "test-lower", "dam")
    assert dam.source_ref == "node/51" and dam.subtype == "weir"
    assert dam.attributes.get("also_mapped_as") is None
    assert report.dropped["dam_off_channel"] >= 1
