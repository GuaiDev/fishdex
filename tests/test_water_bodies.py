"""Water bodies: the model, derivation from stretches and mouth lakes, linking spots, storage, API.

Derivation is pure, so most tests hand it stretches and anchors and read the
water bodies back. Nothing touches the network; the storage and API tests use
throwaway databases, never the real one.
"""

import pytest
from pydantic import ValidationError

from src.models.lake_outline import MATCH_NAME, LakeOutline
from src.models.shoreline_feature import ShorelineFeature
from src.models.spot import SpotBuildReport
from src.models.water_body import WaterBody
from src.services import spot_labels
from src.services.spots import StretchHints, build_spots, classify, get_spot_detail
from src.services.water_bodies import (
    LIST_FIELDS,
    LakeRef,
    derive_water_bodies,
    get_water_body_detail,
    lake_type,
    river_type,
    slugify,
    water_bodies_geojson,
    water_body_features_geojson,
)
from src.storage.water_bodies import (
    count_shoreline_features,
    get_water_body,
    list_shoreline_features,
    list_water_bodies,
)
from tests.test_spots import (  # noqa: F401  (fixtures are used by name)
    BUILT,
    JARGON,
    JURISDICTION,
    anchor,
    confluence,
    mouth,
    run,
    sample_anchors,
    store,
    stretch,
)


def wb_kwargs(**over):
    base = dict(
        water_body_id="river:credit-river",
        jurisdiction="CA-ON",
        name="Credit River",
        name_source="curated",
        type="river",
        geometry={"type": "LineString", "coordinates": [[-79.6, 43.5], [-79.5, 43.6]]},
        geometry_source="stretch_union",
        built_at=BUILT,
    )
    return {**base, **over}


# ── model ─────────────────────────────────────────────────────────────────────


def test_water_body_requires_a_jurisdiction():
    kwargs = wb_kwargs()
    del kwargs["jurisdiction"]
    with pytest.raises(ValidationError):
        WaterBody(**kwargs)


@pytest.mark.parametrize("name", ["", "  ", "Unnamed lake", "unnamed creek"])
def test_a_name_is_real_or_absent_never_unnamed(name):
    with pytest.raises(ValidationError):
        WaterBody(**wb_kwargs(name=name))


def test_an_unknown_name_stays_empty_and_says_so():
    wb = WaterBody(**wb_kwargs(name=None, name_source="unknown"))
    assert wb.name is None
    with pytest.raises(ValidationError):
        WaterBody(**wb_kwargs(name=None, name_source="curated"))
    with pytest.raises(ValidationError):
        WaterBody(**wb_kwargs(name="Credit River", name_source="unknown"))


@pytest.mark.parametrize("water_body_id", ["river:", "Credit River", "bay:credit", "river:A_b"])
def test_water_body_id_must_be_a_type_and_a_slug(water_body_id):
    with pytest.raises(ValidationError):
        WaterBody(**wb_kwargs(water_body_id=water_body_id))


def test_water_body_id_must_start_with_its_type():
    with pytest.raises(ValidationError):
        WaterBody(**wb_kwargs(water_body_id="lake:credit-river"))


def test_geometry_and_its_source_agree():
    with pytest.raises(ValidationError):
        WaterBody(**wb_kwargs(geometry=None))
    with pytest.raises(ValidationError):
        WaterBody(**wb_kwargs(geometry_source="none"))
    with pytest.raises(ValidationError):
        WaterBody(**wb_kwargs(geometry={"type": "Point", "coordinates": [0, 0]}))


def test_water_body_round_trips_through_json():
    wb = WaterBody(**wb_kwargs(bbox=(-79.6, 43.5, -79.5, 43.6), stretch_ids=["a"]))
    assert WaterBody.model_validate_json(wb.model_dump_json()) == wb


# ── typing and ids ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Credit River", "river"),
        ("Bronte Creek", "creek"),
        ("Sixteen Mile Creek", "creek"),
        ("Sydenham River (Owen Sound)", "river"),
        ("Niagara River", "river"),
    ],
)
def test_a_river_or_creek_is_read_from_its_name(name, expected):
    assert river_type(name) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Lake Ontario", "great_lake"),
        ("Georgian Bay", "great_lake"),
        ("Lake St. Clair", "lake"),
        ("Lake Simcoe", "lake"),
        ("Bay of Quinte", "lake"),
        ("Mill Pond", "pond"),
    ],
)
def test_a_lake_type_comes_from_the_great_lakes_list_or_its_name(name, expected):
    assert lake_type(name)[0] == expected


def test_slugs_are_stable_ascii():
    assert slugify("St. Clair River") == "st-clair-river"
    assert slugify("Sydenham River (Owen Sound)") == "sydenham-river-owen-sound"


# ── derivation ────────────────────────────────────────────────────────────────


def line(x0, y0, x1, y1):
    return {"type": "LineString", "coordinates": [[x0, y0], [x1, y1]]}


def stretch_on(sid, river, geom, bbox, order=0, region=None):
    s = stretch(sid, sid.title(), river, region)
    return s.model_copy(update={"geometry": geom, "bbox": bbox, "sort_order": order})


def test_stretches_of_one_river_are_one_water_body_drawn_as_their_union():
    stretches = [
        stretch_on(
            "grand-b", "Grand River", line(-80.4, 43.4, -80.3, 43.5), (-80.4, 43.4, -80.3, 43.5), 1
        ),
        stretch_on(
            "grand-a", "Grand River", line(-80.5, 43.6, -80.4, 43.4), (-80.5, 43.4, -80.4, 43.6), 0
        ),
        stretch_on(
            "bronte", "Bronte Creek", line(-79.8, 43.4, -79.7, 43.5), (-79.8, 43.4, -79.7, 43.5), 2
        ),
    ]
    out = derive_water_bodies(stretches, [], JURISDICTION, BUILT)
    assert [b.water_body_id for b in out.bodies] == ["river:grand-river", "creek:bronte-creek"]
    grand = out.bodies[0]
    assert grand.stretch_ids == ["grand-a", "grand-b"]
    assert grand.geometry["type"] == "MultiLineString"
    assert len(grand.geometry["coordinates"]) == 2
    assert grand.bbox == (-80.5, 43.4, -80.3, 43.6)
    assert grand.name_source == "curated"
    assert grand.attributes["type_basis"] == "name_suffix"
    assert out.river_id("grand-a") == out.river_id("grand-b") == "river:grand-river"
    assert out.river_id("nowhere") is None


def test_a_multilinestring_stretch_keeps_all_its_parts():
    multi = {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]}
    out = derive_water_bodies(
        [stretch_on("a", "Credit River", multi, (0, 0, 3, 3))], [], JURISDICTION, BUILT
    )
    assert len(out.bodies[0].geometry["coordinates"]) == 2


def test_a_lake_has_no_geometry_and_sits_where_its_mouths_are():
    lakes = [
        LakeRef("Lake Ontario", "curated", "a", 43.5, -79.6),
        LakeRef("Lake Ontario", "curated_region", "b", 43.7, -79.2),
    ]
    out = derive_water_bodies([], lakes, JURISDICTION, BUILT)
    (lake,) = out.bodies
    assert lake.water_body_id == "great_lake:lake-ontario"
    assert lake.type == "great_lake"
    assert lake.geometry is None and lake.geometry_source == "none"
    assert lake.bbox == (-79.6, 43.5, -79.2, 43.7)
    assert lake.name_source == "curated"  # the strongest source wins
    assert lake.stretch_ids == ["a", "b"]
    assert lake.attributes["bbox_basis"] == "river_mouths"
    assert out.lake_id("Lake Ontario") == lake.water_body_id
    assert out.lake_id(None) is None


def test_a_bay_records_the_lake_it_belongs_to():
    out = derive_water_bodies(
        [], [LakeRef("Georgian Bay", "curated", "a", 44.5, -80.0)], JURISDICTION, BUILT
    )
    assert out.bodies[0].attributes["part_of"] == "Lake Huron"


def test_lake_spellings_that_share_a_slug_are_one_lake_and_the_others_are_kept():
    lakes = [
        LakeRef("Lake St. Clair", "curated", "a", 42.3, -82.5),
        LakeRef("Lake St Clair", "ohn", "b", 42.4, -82.4),
    ]
    (lake,) = derive_water_bodies([], lakes, JURISDICTION, BUILT).bodies
    assert lake.name == "Lake St. Clair"
    assert lake.attributes["also_spelled"] == ["Lake St Clair"]


# ── lake outlines ─────────────────────────────────────────────────────────────


def outline(**over):
    base = dict(
        curated_name="Rice Lake",
        matched=True,
        geometry={
            "type": "Polygon",
            "coordinates": [
                [[-78.3, 44.1], [-78.3, 44.3], [-78.1, 44.3], [-78.1, 44.1], [-78.3, 44.1]]
            ],
        },
        ogf_ids=[124215626],
        ohn_name="Rice Lake",
        effective_datetime="2020-10-07",
        match_basis=MATCH_NAME,
        distance_m=0.0,
        licence="Open Government Licence – Ontario",
        licence_url="https://www.ontario.ca/page/open-government-licence-ontario",
        note="",
    )
    return LakeOutline(**{**base, **over})


def test_a_matched_outline_becomes_the_lakes_geometry_and_bbox():
    (lake,) = derive_water_bodies(
        [],
        [LakeRef("Rice Lake", "curated", "a", 44.2, -78.24)],
        JURISDICTION,
        BUILT,
        outlines={"Rice Lake": outline()},
    ).bodies
    assert lake.geometry["type"] == "Polygon"
    assert lake.geometry_source == "ohn_waterbody"
    assert lake.bbox == (-78.3, 44.1, -78.1, 44.3)
    assert lake.attributes["bbox_basis"] == "outline"
    assert lake.attributes["outline_source"] == "OHN Waterbody"
    assert lake.attributes["outline_ogf_ids"] == [124215626]
    assert lake.attributes["outline_ohn_name"] == "Rice Lake"
    assert lake.attributes["outline_effective_datetime"] == "2020-10-07"
    assert lake.attributes["outline_match_basis"] == MATCH_NAME
    assert lake.attributes["outline_licence"] == "Open Government Licence – Ontario"
    assert lake.attributes["outline_licence_url"].endswith("open-government-licence-ontario")


def test_an_unmatched_outline_falls_back_to_the_mouths():
    (lake,) = derive_water_bodies(
        [],
        [LakeRef("Lake Simcoe", "curated", "a", 44.4, -79.4)],
        JURISDICTION,
        BUILT,
        outlines={"Lake Simcoe": outline(curated_name="Lake Simcoe", matched=False, geometry=None)},
    ).bodies
    assert lake.geometry is None
    assert lake.geometry_source == "none"
    assert lake.attributes["bbox_basis"] == "river_mouths"


# ── linking spots ─────────────────────────────────────────────────────────────


def test_every_spot_is_a_child_of_its_rivers_water_body():
    result = run(sample_anchors())
    assert {s.water_body_id for s in result.spots} == {"river:credit-river"}
    assert result.report.spots_linked == len(result.spots)
    assert result.report.spots_without_water_body == 0
    assert [b.water_body_id for b in result.water_bodies] == ["river:credit-river"]
    assert result.report.water_bodies_total == 1
    assert result.report.water_bodies_by_type == {"river": 1}


def test_a_river_mouth_is_on_its_river_and_also_on_its_lake_and_the_names_agree():
    result = run([mouth()], hints={"credit-lower": StretchHints(lake="Lake Ontario")})
    (spot,) = result.spots
    lake = next(b for b in result.water_bodies if b.type == "great_lake")
    assert spot.water_body_id == "river:credit-river"
    assert spot.also_on_water_body_ids == [lake.water_body_id]
    assert spot.structure_attrs["lake"] == lake.name == "Lake Ontario"
    assert spot.structure_attrs["lake_name_source"] == lake.name_source == "curated"


def test_the_lake_name_source_follows_where_the_name_came_from():
    curated = mouth().model_copy(update={"name": "Lake Erie", "source": "curated"})
    (spot,) = run([curated]).spots
    assert spot.structure_attrs["lake_name_source"] == "curated"
    assert spot.sources[0].kind == "curation"

    (from_ohn,) = run([mouth(name="Lake Erie")]).spots
    assert from_ohn.structure_attrs["lake_name_source"] == "ohn"

    result = run([mouth()], [stretch(region="Lake Huron")])
    assert result.spots[0].structure_attrs["lake_name_source"] == "curated_region"
    assert [b.name_source for b in result.water_bodies if b.type == "great_lake"] == [
        "curated_region"
    ]


def test_a_mouth_with_no_lake_name_makes_no_lake_and_invents_none():
    result = run([mouth()])
    (spot,) = result.spots
    assert spot.structure_attrs["lake"] is None
    assert spot.also_on_water_body_ids == []
    assert {b.type for b in result.water_bodies} == {"river"}


def test_a_junction_listed_under_two_stretches_is_also_on_the_other_river():
    stretches = [stretch(), stretch("sheldon", "Sheldon", "Sheldon Creek")]
    anchors = [
        confluence("a", 43.5, -79.6, 30.0, name="Sheldon Creek"),
        confluence("b", 43.5, -79.6, 30.0, sid="sheldon", flows_on=False),
    ]
    (spot,) = run(anchors, stretches).spots
    assert spot.water_body_id == "river:credit-river"
    assert spot.also_on_water_body_ids == ["creek:sheldon-creek"]


def test_a_spot_whose_stretch_is_unknown_is_counted_unlinked_not_hidden():
    anchors = [confluence("a", 43.5, -79.6, 30.0, name="X Creek", sid="ghost")]
    result = run(anchors, [stretch()])
    (spot,) = result.spots
    assert spot.water_body_id is None
    assert result.report.spots_without_water_body == 1
    assert result.report.spots_linked == 0


def test_water_body_ids_come_from_the_name_not_from_stretch_ids():
    a = derive_water_bodies([stretch("credit-lower", "Lower Credit")], [], JURISDICTION, BUILT)
    b = derive_water_bodies(
        [stretch("credit-renamed", "Credit, lower end")], [], JURISDICTION, BUILT
    )
    assert [w.water_body_id for w in a.bodies] == [w.water_body_id for w in b.bodies]
    assert a.bodies[0].stretch_ids != b.bodies[0].stretch_ids


def test_the_report_counts_names_by_source_and_outlines():
    report = run([mouth()], hints={"credit-lower": StretchHints(lake="Lake Ontario")}).report
    assert report.water_body_names == {"curated": 2}
    assert report.water_bodies_without_geometry == 1
    assert SpotBuildReport(jurisdiction=JURISDICTION).water_bodies_total == 0


def test_existing_kinds_and_titles_are_unchanged_by_linking():
    result = run([mouth()], hints={"credit-lower": StretchHints(lake="Lake Ontario")})
    (spot,) = result.spots
    assert spot.kind == "river_mouth"
    assert spot.title == "Where the Credit River meets Lake Ontario"
    assert spot.water_id == "credit-lower"


# ── storage and build ─────────────────────────────────────────────────────────


def built(db, curation):
    store(db, sample_anchors(), [stretch(region="Lake Ontario")])
    return build_spots(db, curation)


def test_build_stores_water_bodies_and_links_spots(db, curation):
    report = built(db, curation)
    assert report.spots_without_water_body == 0
    ids = {wb.water_body_id for wb in list_water_bodies(db)}
    assert ids == {"river:credit-river", "great_lake:lake-ontario"}
    linked = {r[0] for r in db.execute("SELECT DISTINCT water_body_id FROM spots").fetchall()}
    assert linked == {"river:credit-river"}


def test_stored_water_bodies_read_back_equal(db, curation):
    built(db, curation)
    expected = {
        wb.water_body_id: wb
        for wb in classify(
            sample_anchors(),
            [stretch(region="Lake Ontario")],
            JURISDICTION,
            built_at=BUILT,
        ).water_bodies
    }
    for wb in list_water_bodies(db):
        assert wb.model_dump(exclude={"built_at"}) == expected[wb.water_body_id].model_dump(
            exclude={"built_at"}
        )
    assert get_water_body(db, "nope:nope") is None


def test_rebuilding_replaces_water_bodies_and_leaves_none_stale(db, curation):
    built(db, curation)
    store(db, sample_anchors()[:1], [stretch()])  # the mouth, and so the lake, are gone
    build_spots(db, curation)
    assert {wb.water_body_id for wb in list_water_bodies(db)} == {"river:credit-river"}


def test_a_failed_write_leaves_spots_and_water_bodies_as_they_were(db, curation):
    from src.storage.spots import replace_spots

    built(db, curation)
    before = db.execute("SELECT COUNT(*) FROM spots").fetchone()[0]
    result = classify(sample_anchors(), [stretch()], JURISDICTION, built_at=BUILT)
    result.spots[0].tier = 9  # violates nothing in the model, so break the table instead
    db.execute("CREATE TRIGGER boom BEFORE INSERT ON spots BEGIN SELECT RAISE(ABORT, 'boom'); END")
    with pytest.raises(Exception, match="boom"):
        replace_spots(db, JURISDICTION, result.spots, result.water_bodies)
    assert db.execute("SELECT COUNT(*) FROM spots").fetchone()[0] == before
    assert {wb.water_body_id for wb in list_water_bodies(db)} == {
        "river:credit-river",
        "great_lake:lake-ontario",
    }


def test_filters_by_bbox_and_type(db, curation):
    built(db, curation)
    far = list_water_bodies(db, bbox=(10, 10, 11, 11))
    assert far == []
    near_mouth = list_water_bodies(db, bbox=(-79.52, 43.39, -79.48, 43.41))
    assert "great_lake:lake-ontario" in {wb.water_body_id for wb in near_mouth}
    assert [wb.type for wb in list_water_bodies(db, types=["river"])] == ["river"]
    assert list_water_bodies(db, jurisdiction="US-MI") == []


def test_a_spots_table_from_before_water_bodies_is_brought_up_to_date(tmp_path):
    import sqlite3

    from sqlite_utils.db import Database

    from src.storage.database import _ensure_spots

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE spots (spot_id TEXT PRIMARY KEY, kind TEXT NOT NULL,"
        " jurisdiction TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL, water_id TEXT,"
        " water_name TEXT, title TEXT NOT NULL,"
        " subtitle TEXT, tier INTEGER NOT NULL, structure_type TEXT NOT NULL,"
        " structure_attrs_json TEXT NOT NULL DEFAULT '{}', signals_json TEXT NOT NULL DEFAULT '{}',"
        " getting_there_json TEXT NOT NULL DEFAULT '[]', bridges_json TEXT NOT NULL DEFAULT '[]',"
        " sources_json TEXT NOT NULL DEFAULT '[]', built_at TEXT NOT NULL)"
    )
    conn.commit()
    db = Database(conn)
    _ensure_spots(db)
    _ensure_spots(db)  # idempotent
    cols = {c.name for c in db["spots"].columns}
    assert {"water_body_id", "also_on_water_body_ids_json"} <= cols
    assert "water_bodies" in db.table_names()


# ── read side ─────────────────────────────────────────────────────────────────


def test_geojson_lists_rivers_with_lines_and_lakes_without(db, curation):
    built(db, curation)
    body = water_bodies_geojson(db)
    assert body["meta"]["count"] == 2
    assert body["meta"]["type_counts"]["river"] == 1
    by_id = {f["id"]: f for f in body["features"]}
    river = by_id["river:credit-river"]
    assert river["geometry"]["type"] == "MultiLineString"
    assert river["properties"]["display_name"] == "Credit River"
    assert river["properties"]["spot_counts"]
    assert river["properties"]["jurisdiction"] == JURISDICTION
    lake = by_id["great_lake:lake-ontario"]
    assert lake["geometry"] is None
    assert lake["properties"]["name_source"] == "curated_region"
    assert [f["properties"]["type"] for f in body["features"]] == ["river", "great_lake"]


def test_a_rivers_line_must_cross_the_view_not_just_its_overall_box(db):
    from src.storage.spots import replace_spots

    # An L-shaped river: its box covers the whole square, its line only two edges.
    elbow = {"type": "LineString", "coordinates": [[0, 0], [0, 10], [10, 10]]}
    river = WaterBody(**wb_kwargs(geometry=elbow, bbox=(0, 0, 10, 10)))
    replace_spots(db, JURISDICTION, [], [river])
    assert water_bodies_geojson(db, bbox=(1, 1, 9, 9))["features"] == []
    assert len(water_bodies_geojson(db, bbox=(-1, 4, 1, 6))["features"]) == 1
    # a long segment with no vertex in the view still counts when it crosses it
    assert len(water_bodies_geojson(db, bbox=(4, 9, 6, 11))["features"]) == 1


def test_geojson_type_filter_leaves_the_counts_alone(db, curation):
    built(db, curation)
    body = water_bodies_geojson(db, types=["great_lake"])
    assert [f["id"] for f in body["features"]] == ["great_lake:lake-ontario"]
    assert body["meta"]["type_counts"]["river"] == 1


def test_an_unnamed_water_body_reads_as_unnamed_in_words_with_a_null_name(db):
    from src.storage.spots import replace_spots

    nameless = WaterBody(
        **wb_kwargs(
            water_body_id="lake:pond-1",
            type="lake",
            name=None,
            name_source="unknown",
            geometry=None,
            geometry_source="none",
            bbox=(0, 0, 1, 1),
        )
    )
    replace_spots(db, JURISDICTION, [], [nameless])
    (feature,) = water_bodies_geojson(db)["features"]
    assert feature["properties"]["name"] is None
    assert feature["properties"]["display_name"] == "A lake with no name on file"
    assert not feature["properties"]["display_name"].lower().startswith("unnamed")


def test_detail_lists_the_spots_on_a_water_body_and_those_also_there(db, curation):
    built(db, curation)
    river = get_water_body_detail(db, "river:credit-river")
    assert river["name"] == "Credit River"
    assert river["section_ids"] == ["credit-lower"]
    assert {s["kind"] for s in river["spots"]} >= {"river_mouth", "below_dam"}
    assert river["also_here_spots"] == []
    lake = get_water_body_detail(db, "great_lake:lake-ontario")
    assert lake["spots"] == []
    assert [s["kind"] for s in lake["also_here_spots"]] == ["river_mouth"]
    assert get_water_body_detail(db, "river:nope") is None


def test_spot_detail_names_its_water_body_and_the_lake_it_is_also_on(db, curation):
    built(db, curation)
    mouth_id = next(
        r[0] for r in db.execute("SELECT spot_id FROM spots WHERE kind='river_mouth'").fetchall()
    )
    detail = get_spot_detail(db, mouth_id)
    assert detail["water_body"]["water_body_id"] == "river:credit-river"
    assert detail["water_body"]["name_source"] == "curated"
    assert [w["water_body_id"] for w in detail["also_on_water_bodies"]] == [
        "great_lake:lake-ontario"
    ]
    assert detail["more_on_this_water"]  # the stretch-level list is still served


# ── API ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def wb_client(tmp_path, monkeypatch, curation, _template_db):
    import shutil

    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app, get_current_user

    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    api_db = database.get_db()
    built(api_db, curation)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_water_bodies_endpoint_returns_a_feature_collection(wb_client):
    body = wb_client.get("/map/water-bodies").json()
    assert body["type"] == "FeatureCollection"
    assert body["meta"]["count"] == 2
    assert {"water_body_id", "name", "name_source", "type", "jurisdiction"} <= set(
        body["features"][0]["properties"]
    )


def test_water_bodies_endpoint_filters(wb_client):
    rivers = wb_client.get("/map/water-bodies?type=river,creek").json()
    assert [f["properties"]["type"] for f in rivers["features"]] == ["river"]
    inside = wb_client.get("/map/water-bodies?bbox=-80,43,-79,44").json()
    assert inside["meta"]["count"] == 2
    assert wb_client.get("/map/water-bodies?bbox=10,10,11,11").json()["features"] == []
    assert wb_client.get("/map/water-bodies?jurisdiction=US-MI").json()["features"] == []


@pytest.mark.parametrize("query", ["bbox=1,2,3", "bbox=5,5,1,1", "type=ocean"])
def test_water_bodies_endpoint_rejects_bad_filters(wb_client, query):
    assert wb_client.get(f"/map/water-bodies?{query}").status_code == 422


def test_water_body_endpoint_returns_the_full_record(wb_client):
    body = wb_client.get("/map/water-bodies/river:credit-river").json()
    assert body["name"] == "Credit River"
    assert body["geometry"]["type"] == "MultiLineString"
    assert body["spots"]
    assert wb_client.get("/map/water-bodies/river:nope").status_code == 404


def test_spots_geojson_carries_the_water_body_id(wb_client):
    props = wb_client.get("/map/spots?tier=3").json()["features"][0]["properties"]
    assert props["water_body_id"] == "river:credit-river"


def test_water_body_endpoints_require_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    client = TestClient(app)
    for path in (
        "/map/water-bodies",
        "/map/water-bodies/river:credit-river",
        "/map/water-bodies/river:credit-river/features",
    ):
        assert client.get(path).status_code in (401, 403)


# ── shoreline features: storage, endpoint, words ──────────────────────────────


def feature(**over):
    base = dict(
        feature_id="great_lake:lake-ontario#inlet:1",
        water_body_id="great_lake:lake-ontario",
        jurisdiction=JURISDICTION,
        kind="inlet",
        name="Credit River",
        geometry={"type": "Point", "coordinates": [-79.5, 43.5]},
        source="OHN Watercourse",
        derivation="watercourse_endpoint",
        provenance="record",
        built_at=BUILT,
    )
    return ShorelineFeature(**{**base, **over})


def lake_body(**over):
    base = wb_kwargs(
        water_body_id="great_lake:lake-ontario",
        name="Lake Ontario",
        name_source="curated",
        type="great_lake",
        geometry=None,
        geometry_source="none",
        bbox=(-79.8, 43.3, -79.0, 43.8),
    )
    return WaterBody(**{**base, **over})


def test_shoreline_features_round_trip_through_storage(db):
    from src.storage.spots import replace_spots

    replace_spots(db, JURISDICTION, [], [lake_body()], [feature()])
    (read,) = list_shoreline_features(db, "great_lake:lake-ontario")
    assert read == feature()
    assert count_shoreline_features(db) == 1
    assert count_shoreline_features(db, jurisdiction=JURISDICTION) == 1
    assert list_shoreline_features(db, "great_lake:lake-ontario", kinds=["island"]) == []


def pier_feature(**over):
    base = dict(
        feature_id="great_lake:lake-ontario:pier:way/33656131",
        water_body_id="great_lake:lake-ontario",
        jurisdiction=JURISDICTION,
        kind="pier",
        name="Bronte Pier",
        geometry={"type": "LineString", "coordinates": [[-79.7, 43.39], [-79.69, 43.39]]},
        source="OpenStreetMap",
        derivation="osm_feature",
        provenance="record",
        length_m=120.0,
        built_at=BUILT,
    )
    return ShorelineFeature(**{**base, **over})


def test_a_pier_round_trips_through_storage(db):
    from src.storage.spots import replace_spots

    replace_spots(db, JURISDICTION, [], [lake_body()], [pier_feature()])
    (read,) = list_shoreline_features(db, "great_lake:lake-ontario", kinds=["pier"])
    assert read == pier_feature()
    assert read.derivation == "osm_feature"
    assert read.source == "OpenStreetMap"
    assert read.length_m == 120.0


def test_the_pier_note_credits_openstreetmap(db):
    from src.storage.spots import replace_spots

    replace_spots(db, JURISDICTION, [], [lake_body()], [pier_feature()])
    (f,) = water_body_features_geojson(db, "great_lake:lake-ontario")["features"]
    assert f["geometry"]["type"] == "LineString"
    assert f["properties"]["derivation"] == "osm_feature"
    assert "OpenStreetMap" in f["properties"]["note"]


def test_classify_attaches_a_mapped_structure_to_the_lake():
    from src.models.water_feature import ShoreStructure

    pier = ShoreStructure(
        osm_id="way/1",
        kind="pier",
        name="Rice Lake Pier",
        geometry={"type": "LineString", "coordinates": [[-78.095, 44.19], [-78.095, 44.21]]},
        lat=44.2,
        lng=-78.095,
        jurisdiction=JURISDICTION,
        tags={},
    )
    result = classify(
        [mouth(43.4, -79.5)],
        [stretch()],
        JURISDICTION,
        {"credit-lower": StretchHints(lake="Rice Lake")},
        BUILT,
        outlines={"Rice Lake": outline()},
        shore_structures=[pier],
    )
    (f,) = [x for x in result.shoreline_features if x.kind == "pier"]
    assert f.water_body_id == "lake:rice-lake"
    assert f.name == "Rice Lake Pier"
    assert result.report.shore_structures_fetched == 1
    assert result.report.shoreline_features_by_kind["pier"] == 1


def test_a_rebuild_with_no_features_clears_the_stored_ones(db):
    from src.storage.spots import replace_spots

    replace_spots(db, JURISDICTION, [], [lake_body()], [feature()])
    replace_spots(db, JURISDICTION, [], [lake_body()], [])
    assert list_shoreline_features(db, "great_lake:lake-ontario") == []


def test_features_geojson_is_none_for_an_unknown_body(db):
    assert water_body_features_geojson(db, "great_lake:nope") is None


def test_a_river_has_no_features_and_an_empty_collection_is_the_answer(db):
    from src.storage.spots import replace_spots

    replace_spots(db, JURISDICTION, [], [WaterBody(**wb_kwargs())], [])
    body = water_body_features_geojson(db, "river:credit-river")
    assert body["type"] == "FeatureCollection"
    assert body["features"] == []
    assert body["meta"]["count"] == 0


@pytest.fixture
def feature_client(tmp_path, monkeypatch, _template_db):
    import shutil

    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app, get_current_user
    from src.storage.spots import replace_spots

    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    api_db = database.get_db()
    replace_spots(api_db, JURISDICTION, [], [lake_body(), WaterBody(**wb_kwargs())], [feature()])
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_features_endpoint_returns_a_feature_collection(feature_client):
    body = feature_client.get("/map/water-bodies/great_lake:lake-ontario/features").json()
    assert body["type"] == "FeatureCollection"
    assert body["meta"]["count"] == 1
    (f,) = body["features"]
    assert f["geometry"]["type"] == "Point"
    assert f["properties"]["kind"] == "inlet"
    assert f["properties"]["derivation"] == "watercourse_endpoint"
    assert f["properties"]["provenance"] == "record"
    assert f["properties"]["note"]


def test_features_endpoint_is_empty_for_a_river_and_404_for_an_unknown(feature_client):
    empty = feature_client.get("/map/water-bodies/river:credit-river/features").json()
    assert empty["features"] == []
    assert feature_client.get("/map/water-bodies/river:nope/features").status_code == 404


def test_labels_serve_the_shoreline_feature_vocabulary():
    payload = spot_labels.labels_payload()
    kinds = [e["kind"] for e in payload["shoreline_features"]]
    assert kinds == list(spot_labels.FEATURE_KIND_ORDER)
    for entry in payload["shoreline_features"]:
        assert entry["label"] and entry["chip"] and entry["one_liner"]
    assert set(payload["shoreline_derivations"]) == {
        "shoreline_outline",
        "watercourse_endpoint",
        "bathymetry_contours",
        "osm_feature",
    }
    assert payload["bathymetry_data_note"]


# ── bathymetry: the survey note, storage and read side ───────────────────────


def test_the_survey_note_names_the_year_and_method_or_says_unknown():
    note = spot_labels.shoreline_feature_note(
        "drop_off",
        "bathymetry_contours",
        attributes={"survey_year": 1970, "survey_method": "Transect"},
    )
    assert "From a 1970 survey, Transect" in note

    unknown = spot_labels.shoreline_feature_note("hump", "bathymetry_contours", attributes={})
    assert spot_labels.SURVEY_DATE_UNKNOWN in unknown


def bathymetry_feature(**over):
    base = dict(
        feature_id="lake:rice-lake:drop_off:-78.145_44.19",
        water_body_id="great_lake:lake-ontario",
        jurisdiction=JURISDICTION,
        kind="drop_off",
        name=None,
        geometry={"type": "Point", "coordinates": [-78.145, 44.19]},
        source="LIO Bathymetry",
        source_date="1970-07-10",
        derivation="bathymetry_contours",
        provenance="inference",
        attributes={
            "contour_ogf_ids": [1, 2],
            "survey_year": 1970,
            "survey_method": "Transect",
            "depth_m": -4.0,
            "depth_change_m": 4.0,
            "gap_m": 35.2,
        },
        length_m=4.0,
        built_at=BUILT,
    )
    return ShorelineFeature(**{**base, **over})


def test_a_contour_feature_round_trips_through_storage(db):
    from src.storage.spots import replace_spots

    replace_spots(db, JURISDICTION, [], [lake_body()], [bathymetry_feature()])
    (read,) = list_shoreline_features(db, "great_lake:lake-ontario")
    assert read == bathymetry_feature()
    assert read.attributes["survey_year"] == 1970


def test_a_bathymetry_contour_round_trips_through_storage(db):
    from src.models.bathymetry import BathymetryContour
    from src.storage.spots import replace_spots
    from src.storage.water_bodies import list_bathymetry_contours

    contour = BathymetryContour(
        ogf_id=118430028,
        depth_m=-4.0,
        survey_method="Transect",
        survey_date="1970-07-09",
        geometry={"type": "LineString", "coordinates": [[-78.14, 44.19], [-78.13, 44.2]]},
        water_body_id="great_lake:lake-ontario",
        jurisdiction=JURISDICTION,
        licence="Open Government Licence – Ontario",
        built_at=BUILT,
    )
    replace_spots(db, JURISDICTION, [], [lake_body()], [bathymetry_feature()], [contour])
    (read,) = list_bathymetry_contours(db, "great_lake:lake-ontario")
    assert read == contour


def test_features_geojson_exposes_the_survey_attributes_and_note(db):
    from src.storage.spots import replace_spots

    replace_spots(db, JURISDICTION, [], [lake_body()], [bathymetry_feature()])
    body = water_body_features_geojson(db, "great_lake:lake-ontario")
    (f,) = body["features"]
    props = f["properties"]
    assert props["derivation"] == "bathymetry_contours"
    assert props["attributes"]["survey_year"] == 1970
    assert props["attributes"]["contour_ogf_ids"] == [1, 2]
    assert "From a 1970 survey, Transect" in props["note"]


def test_a_rebuild_with_no_contours_clears_the_stored_ones(db):
    from src.models.bathymetry import BathymetryContour
    from src.storage.spots import replace_spots
    from src.storage.water_bodies import list_bathymetry_contours

    contour = BathymetryContour(
        ogf_id=1,
        depth_m=-4.0,
        geometry={"type": "LineString", "coordinates": [[-78.14, 44.19], [-78.13, 44.2]]},
        jurisdiction=JURISDICTION,
    )
    replace_spots(db, JURISDICTION, [], [lake_body()], [], [contour])
    replace_spots(db, JURISDICTION, [], [lake_body()], [], [])
    assert list_bathymetry_contours(db) == []


def test_classify_reads_drop_offs_and_humps_from_a_supplied_survey():
    from src.models.bathymetry import LIO_BATHYMETRY_SOURCE, BathymetryContour, BathymetryIndex
    from src.services.bathymetry import LakeBathymetry

    def ring(minx, miny, maxx, maxy):
        return [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]

    contours = [
        BathymetryContour(
            ogf_id=1,
            depth_m=-6.0,
            geometry={"type": "LineString", "coordinates": ring(-78.29, 44.11, -78.11, 44.29)},
            source=LIO_BATHYMETRY_SOURCE,
        ),
        BathymetryContour(
            ogf_id=2,
            depth_m=-3.0,
            geometry={"type": "LineString", "coordinates": ring(-78.24, 44.16, -78.16, 44.24)},
            source=LIO_BATHYMETRY_SOURCE,
        ),
        # A steep pair below the shoal: one drop-off.
        BathymetryContour(
            ogf_id=3,
            depth_m=-3.0,
            geometry={"type": "LineString", "coordinates": [[-78.25, 44.15], [-78.15, 44.15]]},
            source=LIO_BATHYMETRY_SOURCE,
        ),
        BathymetryContour(
            ogf_id=4,
            depth_m=-5.0,
            geometry={"type": "LineString", "coordinates": [[-78.25, 44.1505], [-78.15, 44.1505]]},
            source=LIO_BATHYMETRY_SOURCE,
        ),
    ]
    index = BathymetryIndex(
        ogf_id=500,
        geometry={"type": "Polygon", "coordinates": [ring(-78.3, 44.1, -78.1, 44.3)]},
        survey_year=1970,
        survey_method="Transect",
    )
    result = classify(
        [mouth()],
        [stretch()],
        JURISDICTION,
        {"credit-lower": StretchHints(lake="Rice Lake")},
        BUILT,
        outlines={"Rice Lake": outline()},
        bathymetry={"Rice Lake": LakeBathymetry(index, contours)},
    )

    kinds = {f.kind for f in result.shoreline_features}
    assert "hump" in kinds and "drop_off" in kinds
    humps = [f for f in result.shoreline_features if f.kind == "hump"]
    assert humps[0].derivation == "bathymetry_contours"
    # The contours carry no SURVEY_DATE, and the index year is not borrowed.
    assert humps[0].attributes["survey_year"] is None
    assert result.report.lakes_with_survey_contours == 1
    assert result.report.lakes_without_survey_contours == 0
    assert result.report.bathymetry_contours_stored == 4
    assert len(result.bathymetry_contours) == 4
    assert all(c.water_body_id == "lake:rice-lake" for c in result.bathymetry_contours)


def test_classify_counts_a_lake_with_no_survey_contours():
    result = classify(
        [mouth()],
        [stretch()],
        JURISDICTION,
        {"credit-lower": StretchHints(lake="Rice Lake")},
        BUILT,
        outlines={"Rice Lake": outline()},
    )
    assert result.bathymetry_contours == []
    assert not [f for f in result.shoreline_features if f.kind in ("hump", "drop_off")]
    assert result.report.lakes_without_survey_contours == 1


def test_build_stores_the_survey_contours_and_their_features(db, curation):
    from src.models.bathymetry import LIO_BATHYMETRY_SOURCE, BathymetryContour, BathymetryIndex
    from src.services.bathymetry import LakeBathymetry
    from src.storage.water_bodies import count_bathymetry_contours, list_bathymetry_contours

    def ring(minx, miny, maxx, maxy):
        return [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]

    contours = [
        BathymetryContour(
            ogf_id=1,
            depth_m=-6.0,
            geometry={"type": "LineString", "coordinates": ring(-78.29, 44.11, -78.11, 44.29)},
            source=LIO_BATHYMETRY_SOURCE,
        ),
        BathymetryContour(
            ogf_id=2,
            depth_m=-3.0,
            geometry={"type": "LineString", "coordinates": ring(-78.24, 44.16, -78.16, 44.24)},
            source=LIO_BATHYMETRY_SOURCE,
        ),
    ]
    index = BathymetryIndex(
        ogf_id=500,
        geometry={"type": "Polygon", "coordinates": [ring(-78.3, 44.1, -78.1, 44.3)]},
        survey_year=1970,
        survey_method="Transect",
    )
    store(db, [mouth(name="Rice Lake")], [stretch()])
    report = build_spots(
        db,
        curation,
        outline_resolver=lambda mouths: {"Rice Lake": outline()},
        watercourse_fetcher=lambda *_: [],
        bathymetry_fetcher=lambda _outline: LakeBathymetry(index, contours),
    )
    assert report.bathymetry_contours_stored == 2
    assert report.lakes_with_survey_contours == 1
    assert count_bathymetry_contours(db) == 2
    stored = list_bathymetry_contours(db, "lake:rice-lake")
    assert {c.ogf_id for c in stored} == {1, 2}
    assert all(c.water_body_id == "lake:rice-lake" for c in stored)
    humps = list_shoreline_features(db, "lake:rice-lake", kinds=["hump"])
    assert len(humps) == 1
    assert humps[0].derivation == "bathymetry_contours"


# ── one flaky upstream fetch does not stop the build ─────────────────────────


def test_a_failed_watercourse_fetch_costs_that_lakes_inlets_not_the_build(db, curation):
    store(db, [mouth(name="Rice Lake")], [stretch()])

    def flaky(*_cell):
        raise TimeoutError("read timed out")

    report = build_spots(
        db,
        curation,
        outline_resolver=lambda mouths: {"Rice Lake": outline()},
        watercourse_fetcher=flaky,
        bathymetry_fetcher=lambda _o: _no_bathymetry(),
    )
    assert report.spots_total >= 1  # the spots were still written
    assert report.upstream_failures["shore_watercourses"] >= 1
    assert report.lakes_with_outline == 1


def test_a_failed_bathymetry_fetch_leaves_the_lake_without_contours(db, curation):
    store(db, [mouth(name="Rice Lake")], [stretch()])

    def flaky(_outline):
        raise RuntimeError("OHN returned an error body")

    report = build_spots(
        db,
        curation,
        outline_resolver=lambda mouths: {"Rice Lake": outline()},
        watercourse_fetcher=lambda *_: [],
        bathymetry_fetcher=flaky,
    )
    assert report.upstream_failures == {"bathymetry": 1}
    assert report.lakes_with_survey_contours == 0
    assert report.spots_total >= 1


def test_a_failed_outline_lookup_is_counted_and_the_other_lakes_keep_theirs(db, curation):
    store(db, [mouth(name="Rice Lake")], [stretch()])

    def resolver(mouths):
        return {
            "Rice Lake": outline(),
            "Lake Ontario": LakeOutline(
                curated_name="Lake Ontario",
                matched=False,
                note="outline lookup failed (ReadTimeout)",
                lookup_failed=True,
            ),
        }

    report = build_spots(
        db,
        curation,
        outline_resolver=resolver,
        watercourse_fetcher=lambda *_: [],
        bathymetry_fetcher=lambda _o: _no_bathymetry(),
    )
    assert report.upstream_failures == {"lake_outline": 1}
    assert report.lakes_with_outline == 1


def test_a_resolver_that_raises_does_not_stop_the_build(db, curation):
    store(db, [mouth(name="Rice Lake")], [stretch()])

    def broken(_mouths):
        raise RuntimeError("OHN returned HTTP 200 with a 500 error body")

    report = build_spots(
        db,
        curation,
        outline_resolver=broken,
        watercourse_fetcher=lambda *_: [],
        bathymetry_fetcher=lambda _o: _no_bathymetry(),
    )
    assert report.upstream_failures == {"lake_outline": 1}
    assert report.spots_total >= 1


def test_a_clean_build_reports_no_upstream_failures(db, curation):
    store(db, [mouth(name="Rice Lake")], [stretch()])
    report = build_spots(
        db,
        curation,
        outline_resolver=lambda mouths: {"Rice Lake": outline()},
        watercourse_fetcher=lambda *_: [],
        bathymetry_fetcher=lambda _o: _no_bathymetry(),
    )
    assert report.upstream_failures == {}


def _no_bathymetry():
    from src.services.bathymetry import LakeBathymetry

    return LakeBathymetry(None, [])


# ── the words ─────────────────────────────────────────────────────────────────


def test_labels_serve_the_water_body_vocabulary():
    payload = spot_labels.labels_payload()
    assert [t["type"] for t in payload["water_body_types"]] == [
        "river",
        "creek",
        "lake",
        "pond",
        "great_lake",
    ]
    assert set(payload["name_sources"]) == {"curated", "curated_region", "ohn", "osm", "unknown"}
    assert payload["no_outline_note"]


def test_the_water_body_words_carry_no_jargon_or_unnamed_titles():
    for text in spot_labels.all_strings():
        assert not JARGON.search(text), text
    for entry in spot_labels.WATER_BODY_TYPES.values():
        assert not entry["unnamed"].lower().startswith("unnamed")


# ── trimming the list: fields, geometry=false, simplify ───────────────────────


def big_lake(db, n=2000):
    """A lake with a many-vertex outline, stored as the build would store it."""
    import math

    from src.storage.water_bodies import write_water_bodies

    ring = [
        [-79.4 + 0.2 * math.cos(2 * math.pi * i / n), 43.6 + 0.1 * math.sin(2 * math.pi * i / n)]
        for i in range(n)
    ]
    ring.append(ring[0])
    body = WaterBody(
        water_body_id="great_lake:lake-ontario",
        jurisdiction="CA-ON",
        name="Lake Ontario",
        name_source="curated",
        type="great_lake",
        geometry={"type": "Polygon", "coordinates": [ring]},
        geometry_source="ohn_waterbody",
        bbox=(-79.6, 43.5, -79.2, 43.7),
        built_at=BUILT,
    )
    db.conn.execute("DELETE FROM water_bodies WHERE water_body_id = ?", [body.water_body_id])
    write_water_bodies(db.conn, "CA-ON", [body])
    db.conn.commit()


@pytest.fixture
def lake_client(wb_client):
    from src.storage.database import get_db

    big_lake(get_db())
    return wb_client


def test_default_list_is_unchanged_by_the_new_options(lake_client):
    body = lake_client.get("/map/water-bodies").json()
    assert set(body["meta"]) == {"count", "type_counts"}
    lake = next(f for f in body["features"] if f["id"] == "great_lake:lake-ontario")
    assert len(lake["geometry"]["coordinates"][0]) == 2001
    assert set(lake["properties"]) == set(LIST_FIELDS)


def test_fields_keeps_only_the_named_properties_and_always_the_id(lake_client):
    body = lake_client.get("/map/water-bodies?fields=display_name,type").json()
    for f in body["features"]:
        assert list(f["properties"]) == ["water_body_id", "display_name", "type"]
        assert f["id"] == f["properties"]["water_body_id"]


def test_fields_without_spot_counts_does_not_count_spots(lake_client, monkeypatch):
    import src.services.water_bodies as service

    def boom(*a, **k):
        raise AssertionError("spots were counted")

    monkeypatch.setattr(service, "_spot_counts", boom)
    assert lake_client.get("/map/water-bodies?fields=name").status_code == 200


def test_geometry_false_serves_null_shapes_and_says_so(lake_client):
    body = lake_client.get("/map/water-bodies?geometry=false").json()
    assert body["meta"]["geometry"] == "none"
    assert all(f["geometry"] is None for f in body["features"])
    assert {f["id"] for f in body["features"]} == {
        f["id"] for f in lake_client.get("/map/water-bodies").json()["features"]
    }


@pytest.mark.parametrize(
    "bbox",
    [
        "-80,43,-79,44",  # everything inside
        "-79.45,43.55,-79.35,43.65",  # inside the lake's own box
        "-79.9,43.0,-79.8,43.1",  # nothing
        "-79.45,43.9,-79.35,44.1",  # in the lake's box? no: north of it
    ],
)
def test_geometry_false_returns_the_same_waters_as_the_full_answer(lake_client, bbox):
    full = lake_client.get(f"/map/water-bodies?bbox={bbox}").json()
    lean = lake_client.get(f"/map/water-bodies?bbox={bbox}&geometry=false").json()
    assert [f["id"] for f in lean["features"]] == [f["id"] for f in full["features"]]
    assert lean["meta"]["count"] == full["meta"]["count"]
    assert lean["meta"]["type_counts"] == full["meta"]["type_counts"]


def test_bbox_still_uses_the_real_outline_when_geometry_is_not_sent(tmp_path, _template_db):
    """A box that overlaps the stored box but misses the line is out, with or without shapes."""
    import shutil

    from src.storage.database import get_db
    from src.storage.water_bodies import write_water_bodies

    shutil.copy(_template_db, tmp_path / "l.db")
    db = get_db(tmp_path / "l.db")
    # An L-shaped river: its box covers the corner nobody can fish.
    l_shape = {"type": "LineString", "coordinates": [[-80.0, 44.0], [-80.0, 43.0], [-79.0, 43.0]]}
    write_water_bodies(
        db.conn,
        "CA-ON",
        [
            WaterBody(
                **wb_kwargs(
                    water_body_id="river:l-river",
                    name="L River",
                    geometry=l_shape,
                    bbox=(-80.0, 43.0, -79.0, 44.0),
                )
            )
        ],
    )
    db.conn.commit()
    corner = (-79.6, 43.6, -79.4, 43.8)
    for geometry in (True, False):
        assert water_bodies_geojson(db, corner, geometry=geometry)["features"] == []
    on_line = (-80.1, 43.4, -79.9, 43.6)
    for geometry in (True, False):
        assert len(water_bodies_geojson(db, on_line, geometry=geometry)["features"]) == 1


def test_list_without_geometry_never_reads_the_stored_shapes(lake_client):
    from src.storage.database import get_db

    assert all(wb.geometry is None for wb in list_water_bodies(get_db(), include_geometry=False))
    assert any(wb.geometry for wb in list_water_bodies(get_db()))


def test_simplify_thins_the_outline_and_keeps_the_same_waters(lake_client):
    full = lake_client.get("/map/water-bodies").json()
    thin = lake_client.get("/map/water-bodies?simplify=500").json()
    assert thin["meta"]["geometry"] == "simplified"
    assert thin["meta"]["simplify_m"] == 500
    assert [f["id"] for f in thin["features"]] == [f["id"] for f in full["features"]]
    lake = next(f for f in thin["features"] if f["id"] == "great_lake:lake-ontario")
    assert 4 <= len(lake["geometry"]["coordinates"][0]) < 200
    assert lake["geometry"]["type"] == "Polygon"


def test_zoom_thins_more_when_zoomed_out(lake_client):
    def points(zoom):
        body = lake_client.get(f"/map/water-bodies?zoom={zoom}").json()
        lake = next(f for f in body["features"] if f["id"] == "great_lake:lake-ontario")
        assert body["meta"]["zoom"] == zoom
        return len(lake["geometry"]["coordinates"][0])

    assert points(5) < points(12) <= 2001


@pytest.mark.parametrize(
    "query",
    [
        "fields=nope",
        "simplify=-1",
        "simplify=abc",
        "zoom=40",
        "zoom=-1",
        "simplify=10&zoom=5",
        "geometry=false&simplify=10",
        "geometry=false&zoom=5",
    ],
)
def test_bad_shape_options_are_rejected(lake_client, query):
    assert lake_client.get(f"/map/water-bodies?{query}").status_code == 422


def test_detail_geometry_options(lake_client):
    path = "/map/water-bodies/great_lake:lake-ontario"
    assert len(lake_client.get(path).json()["geometry"]["coordinates"][0]) == 2001
    no_shape = lake_client.get(f"{path}?geometry=false").json()
    assert no_shape["geometry"] is None
    assert no_shape["name"] == "Lake Ontario"
    assert "geometry_source" in no_shape
    thin = lake_client.get(f"{path}?simplify=500").json()
    assert len(thin["geometry"]["coordinates"][0]) < 200
    assert lake_client.get(f"{path}?geometry=false&zoom=4").status_code == 422


def test_skipped_shore_cells_reach_the_build_failures():
    from src.services.spots import _lake_outline_inputs

    failures: dict[str, int] = {}
    mouths = [anchor("lake_mouth", "m", 44.2, -78.2, sid="s", name="Rice Lake", shore_ogf_ids=[1])]
    _outlines, _watercourses, structures = _lake_outline_inputs(
        mouths,
        JURISDICTION,
        outline_resolver=lambda _m: {"Rice Lake": outline()},
        watercourse_fetcher=lambda *_c: [],
        structure_fetcher=lambda _cells: ([], 2),
        failures=failures,
    )
    assert structures == []
    assert failures == {"shore_structures": 2}
