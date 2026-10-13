"""Weed reports: the model, storage, the on-the-water check, and the API.

A weed edge is the user's own sighting. These tests pin down three things: it is
checked against the water body it names, it says "reported, not verified" on every
read, and it is private: one user can never read, list, change or delete another's,
and no response carries a count. Every database is temporary; every login goes
through a real token, so the isolation is tested the way the app enforces it.
"""

import json
import shutil
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import src.storage.database as database
from src.api.main import app
from src.auth.auth import generate_invite_code, redeem_invite_code
from src.models.water_body import WaterBody
from src.models.weed_report import WeedGeometry, WeedReportCreate, WeedReportUpdate
from src.services.weed_reports import (
    UnknownWaterBody,
    WeedReportError,
    check_on_water,
    labels,
    list_weed_reports,
    weed_reports_geojson,
)
from src.storage import weed_reports as store
from src.storage.spots import replace_spots

BUILT = "2026-10-01T00:00:00+00:00"
TODAY = date.today()
SEEN = "2026-10-04"

# An L-shaped river: west to east along 43.50, then north along -79.60.
RIVER = [[-79.70, 43.50], [-79.60, 43.50], [-79.60, 43.60]]
# A lake outline: a box.
LAKE = [[-79.50, 43.60], [-79.30, 43.60], [-79.30, 43.70], [-79.50, 43.70], [-79.50, 43.60]]


def _bodies():
    return [
        WaterBody(
            water_body_id="river:credit-river",
            jurisdiction="CA-ON",
            name="Credit River",
            name_source="curated",
            type="river",
            geometry={"type": "LineString", "coordinates": RIVER},
            geometry_source="stretch_union",
            bbox=(-79.70, 43.50, -79.60, 43.60),
            built_at=BUILT,
        ),
        WaterBody(
            water_body_id="lake:rice-lake",
            jurisdiction="CA-ON",
            name="Rice Lake",
            name_source="curated",
            type="lake",
            geometry={"type": "Polygon", "coordinates": [LAKE]},
            geometry_source="ohn_waterbody",
            bbox=(-79.50, 43.60, -79.30, 43.70),
            built_at=BUILT,
        ),
        WaterBody(
            water_body_id="great_lake:lake-ontario",
            jurisdiction="CA-ON",
            name="Lake Ontario",
            name_source="curated",
            type="great_lake",
            bbox=(-79.9, 43.2, -78.3, 43.9),
            built_at=BUILT,
        ),
    ]


def point(lng, lat):
    return {"type": "Point", "coordinates": [lng, lat]}


def line(*positions):
    return {"type": "LineString", "coordinates": [list(p) for p in positions]}


def report(**over):
    base = dict(
        water_body_id="river:credit-river",
        geometry=point(-79.65, 43.50),
        observed_on=SEEN,
    )
    return {**base, **over}


@pytest.fixture
def db(tmp_path, _template_db):
    path = tmp_path / "weeds.db"
    shutil.copy(_template_db, path)
    handle = database.get_db(path)
    replace_spots(handle, "CA-ON", [], _bodies(), [])
    return handle


# ── the model ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Polygon", "coordinates": []},
        {"type": "Point", "coordinates": [-79.6]},
        {"type": "Point", "coordinates": [-79.6, 143.5]},  # latitude out of range
        {"type": "Point", "coordinates": [-279.6, 43.5]},  # longitude out of range
        {"type": "Point", "coordinates": ["a", "b"]},
        {"type": "Point", "coordinates": [True, False]},
        {"type": "LineString", "coordinates": [[-79.6, 43.5]]},
        {"type": "LineString", "coordinates": [[-79.6, 43.5], [-79.6, 43.5]]},
        {"type": "LineString", "coordinates": [[-79.6, 43.5]] * 600},
        {"type": "Point", "coordinates": [-79.6, 43.5], "extra": 1},
    ],
)
def test_a_geometry_is_a_point_or_a_line_with_valid_positions(geometry):
    with pytest.raises(ValidationError):
        WeedGeometry.model_validate(geometry)


def test_a_report_cannot_carry_a_reporter_a_provenance_or_a_visibility():
    for extra in ("user_id", "provenance", "visibility", "shared_with", "jurisdiction"):
        with pytest.raises(ValidationError):
            WeedReportCreate.model_validate(report(**{extra: "x"}))


def test_a_sighting_cannot_be_dated_in_the_future():
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        WeedReportCreate.model_validate(report(observed_on=tomorrow))
    with pytest.raises(ValidationError):
        WeedReportUpdate.model_validate({"observed_on": tomorrow})
    assert WeedReportCreate.model_validate(report(observed_on=TODAY.isoformat()))


def test_an_update_may_clear_the_note_but_not_the_position_or_date():
    assert WeedReportUpdate.model_validate({"note": None}).model_fields_set == {"note"}
    for field in ("geometry", "observed_on"):
        with pytest.raises(ValidationError):
            WeedReportUpdate.model_validate({field: None})
    with pytest.raises(ValidationError):
        WeedReportUpdate.model_validate({"water_body_id": "river:other"})


# ── on the water ──────────────────────────────────────────────────────────────


def wb(db, water_body_id):
    from src.storage.water_bodies import get_water_body

    return get_water_body(db, water_body_id)


def geometry(raw):
    return WeedGeometry.model_validate(raw)


def test_a_point_on_or_near_a_rivers_line_is_on_the_water(db):
    river = wb(db, "river:credit-river")
    check_on_water(river, geometry(point(-79.65, 43.50)))
    # ~100 m north of the line: inside the 150 m allowance for a drawn centre line.
    check_on_water(river, geometry(point(-79.65, 43.5009)))


def test_a_point_far_from_the_line_is_refused_with_how_far(db):
    river = wb(db, "river:credit-river")
    with pytest.raises(WeedReportError) as err:
        check_on_water(river, geometry(point(-79.65, 43.503)))
    assert err.value.status == 422
    assert "Credit River" in err.value.detail
    assert "150 m" in err.value.detail


def test_every_part_of_a_line_must_be_on_the_water_not_just_its_ends(db):
    river = wb(db, "river:credit-river")
    # Along the first leg: fine.
    check_on_water(river, geometry(line((-79.69, 43.50), (-79.62, 43.50))))
    # Both ends are on the river but the line cuts the corner, kilometres from the water.
    with pytest.raises(WeedReportError):
        check_on_water(river, geometry(line((-79.69, 43.50), (-79.60, 43.59))))


def test_a_lake_takes_anything_inside_its_outline_and_a_little_beyond(db):
    lake = wb(db, "lake:rice-lake")
    check_on_water(lake, geometry(point(-79.40, 43.65)))
    check_on_water(lake, geometry(line((-79.42, 43.63), (-79.38, 43.66))))
    # ~50 m outside the outline.
    check_on_water(lake, geometry(point(-79.40, 43.60 - 0.00045)))
    with pytest.raises(WeedReportError) as err:
        check_on_water(lake, geometry(point(-79.40, 43.58)))
    assert "outline" in err.value.detail


def test_a_line_longer_than_an_edge_is_refused(db):
    lake = wb(db, "lake:rice-lake")
    with pytest.raises(WeedReportError, match="longer than 10 km"):
        check_on_water(lake, geometry(line((-79.49, 43.61), (-79.31, 43.61))))


def test_a_water_body_with_no_outline_cannot_take_a_report(db):
    with pytest.raises(WeedReportError, match="no outline on file"):
        check_on_water(wb(db, "great_lake:lake-ontario"), geometry(point(-79.4, 43.6)))


def test_nothing_is_decided_from_a_water_bodys_bounding_box(db):
    # Inside the river's box but 5 km from its line.
    with pytest.raises(WeedReportError):
        check_on_water(wb(db, "river:credit-river"), geometry(point(-79.68, 43.58)))


# ── storage ───────────────────────────────────────────────────────────────────


def add(db, user_id, geom=None, water_body_id="river:credit-river", observed_on=SEEN, note=None):
    return store.create_weed_report(
        db,
        user_id,
        water_body_id,
        "CA-ON",
        geometry(geom or point(-79.65, 43.50)),
        date.fromisoformat(observed_on),
        note,
    )


def test_a_report_round_trips_with_its_reporter_and_provenance(db):
    rid = add(db, 7, note="  weeds start at the riffle  ")
    got = store.get_weed_report(db, 7, rid)
    assert (got.user_id, got.water_body_id, got.jurisdiction) == (7, "river:credit-river", "CA-ON")
    assert got.observed_on == date(2026, 10, 4)
    assert got.provenance == "user_reported"
    assert got.geometry.coordinates == [-79.65, 43.50]
    assert got.bbox == (-79.65, 43.50, -79.65, 43.50)
    assert got.created_at and got.updated_at is None


def test_the_table_will_not_store_any_other_provenance(db):
    add(db, 1)
    with pytest.raises(Exception, match="CHECK"):
        db.execute("UPDATE weed_reports SET provenance = 'derived'")


def test_every_storage_function_is_scoped_to_the_user(db):
    mine = add(db, 1, note="mine")
    theirs = add(db, 2, note="theirs")
    assert store.get_weed_report(db, 1, theirs) is None
    assert [r.id for r in store.list_weed_reports(db, 1)] == [mine]
    assert [r.id for r in store.list_weed_reports(db, 2)] == [theirs]
    assert store.update_weed_report(db, 1, theirs, {"note": "taken"}) is False
    assert store.delete_weed_report(db, 1, theirs) is False
    assert store.get_weed_report(db, 2, theirs).note == "theirs"
    assert store.list_weed_reports(db, 3) == []


def test_list_filters_by_water_body_and_box_and_orders_newest_sighting_first(db):
    old = add(db, 1, observed_on="2025-07-01")
    new = add(db, 1, observed_on="2026-09-01", geom=point(-79.65, 43.50))
    lake = add(db, 1, geom=point(-79.40, 43.65), water_body_id="lake:rice-lake")
    assert [r.id for r in store.list_weed_reports(db, 1)] == [lake, new, old]
    only_river = store.list_weed_reports(db, 1, water_body_id="river:credit-river")
    assert [r.id for r in only_river] == [new, old]
    around_lake = store.list_weed_reports(db, 1, bbox=(-79.45, 43.6, -79.35, 43.7))
    assert [r.id for r in around_lake] == [lake]
    assert store.list_weed_reports(db, 1, bbox=(10, 10, 11, 11)) == []


def test_a_line_is_found_by_a_box_it_crosses(db):
    rid = add(db, 1, geom=line((-79.69, 43.50), (-79.62, 43.50)))
    assert [r.id for r in store.list_weed_reports(db, 1, bbox=(-79.66, 43.4, -79.65, 43.6))] == [
        rid
    ]


def test_update_changes_only_what_is_sent_and_stamps_the_time(db):
    rid = add(db, 1, note="before")
    assert store.update_weed_report(db, 1, rid, {"note": None})
    got = store.get_weed_report(db, 1, rid)
    assert got.note is None and got.observed_on == date(2026, 10, 4) and got.updated_at
    assert store.update_weed_report(db, 1, rid, {"geometry": geometry(point(-79.66, 43.50))})
    assert store.get_weed_report(db, 1, rid).bbox == (-79.66, 43.50, -79.66, 43.50)
    assert store.update_weed_report(db, 1, 999, {"note": "x"}) is False


def test_delete_removes_the_report(db):
    rid = add(db, 1)
    assert store.delete_weed_report(db, 1, rid) is True
    assert store.get_weed_report(db, 1, rid) is None
    assert store.delete_weed_report(db, 1, rid) is False


def test_the_table_is_created_on_an_old_database_without_touching_rows(tmp_path):
    handle = database.get_db(tmp_path / "old.db")
    handle.execute("DROP TABLE weed_reports")
    handle.conn.commit()
    database.migrate_weed_reports(handle)
    database.migrate_weed_reports(handle)
    assert handle.execute("SELECT COUNT(*) FROM weed_reports").fetchone()[0] == 0


# ── services: words ───────────────────────────────────────────────────────────


def test_every_read_says_reported_not_verified_with_the_date(db):
    add(db, 1, note="thick lilies")
    (item,) = list_weed_reports(db, 1)["weed_reports"]
    assert item["status_label"] == "reported, not verified"
    assert item["date_label"] == "seen 2026-10-04"
    assert item["caption"] == "Weed edge · reported, not verified · seen 2026-10-04"
    assert item["provenance"] == "user_reported"
    assert item["water_body_name"] == "Credit River"
    (feature,) = weed_reports_geojson(db, 1)["features"]
    assert feature["properties"]["status_label"] == "reported, not verified"
    assert feature["properties"]["caption"] == item["caption"]
    assert feature["properties"]["observed_on"] == SEEN
    assert feature["id"] == feature["properties"]["weed_report_id"]


def test_a_water_body_gone_from_the_build_leaves_the_report_readable(db):
    add(db, 1)
    replace_spots(db, "CA-ON", [], [], [])
    (item,) = list_weed_reports(db, 1)["weed_reports"]
    assert item["water_body_id"] == "river:credit-river"
    assert item["water_body_name"] is None


def test_the_wording_has_no_unverified_claim_of_fish_or_quality():
    words = json.dumps(labels.labels_payload()).lower()
    for banned in ("hot", "best", "good", "popular", "guarantee", "fish are", "verified by"):
        assert banned not in words.replace("not verified", "")


# ── API ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch, _template_db):
    """A client over a throwaway database, and two real logins (tokens) on it."""
    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    handle = database.get_db()
    replace_spots(handle, "CA-ON", [], _bodies(), [])

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

        def post(self, who, **over):
            return self.client.post("/map/weed-reports", json=report(**over), headers=who["h"])

        def get(self, who, path):
            return self.client.get(path, headers=who["h"])

    return Api()


def test_every_route_needs_a_login(api):
    c = api.client
    for method, path in (
        ("get", "/map/weed-reports"),
        ("get", "/map/weed-reports/layer"),
        ("get", "/map/weed-reports/labels"),
        ("get", "/map/weed-reports/1"),
        ("post", "/map/weed-reports"),
        ("patch", "/map/weed-reports/1"),
        ("delete", "/map/weed-reports/1"),
    ):
        assert getattr(c, method)(path).status_code in (401, 403), (method, path)
        bad = {"Authorization": "Bearer nope"}
        assert getattr(c, method)(path, headers=bad).status_code == 401, (method, path)


def test_create_returns_the_labelled_report_with_the_reporters_own_data(api):
    response = api.post(api.ann, note="weeds start at the riffle")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["water_body_id"] == "river:credit-river"
    assert body["jurisdiction"] == "CA-ON"
    assert body["geometry"] == point(-79.65, 43.50)
    assert body["observed_on"] == SEEN
    assert body["note"] == "weeds start at the riffle"
    assert body["status_label"] == "reported, not verified"
    assert body["provenance"] == "user_reported"
    assert "user_id" not in body
    stored = store.get_weed_report(api.db, api.ann["id"], body["weed_report_id"])
    assert stored.user_id == api.ann["id"]


def test_the_reporter_is_the_token_never_the_body(api):
    response = api.client.post(
        "/map/weed-reports", json=report(user_id=api.bob["id"]), headers=api.ann["h"]
    )
    assert response.status_code == 422


def test_create_refuses_what_is_not_on_the_water(api):
    far = api.post(api.ann, geometry=point(-79.65, 43.503))
    assert far.status_code == 422 and "Credit River" in far.json()["detail"]
    no_outline = api.post(
        api.ann, water_body_id="great_lake:lake-ontario", geometry=point(-79.4, 43.6)
    )
    assert no_outline.status_code == 422 and "no outline on file" in no_outline.json()["detail"]
    unknown = api.post(api.ann, water_body_id="river:nope")
    assert unknown.status_code == 404
    assert api.get(api.ann, "/map/weed-reports").json() == {"weed_reports": []}


@pytest.mark.parametrize(
    "bad",
    [
        {"observed_on": "yesterday"},
        {"observed_on": (TODAY + timedelta(days=2)).isoformat()},
        {"geometry": {"type": "Polygon", "coordinates": []}},
        {"geometry": point(-79.65, 143.5)},
        {"note": "x" * 2001},
    ],
)
def test_create_validates_the_body(api, bad):
    assert api.post(api.ann, **bad).status_code == 422


def test_list_and_layer_filter_by_water_body_and_box(api):
    river = api.post(api.ann).json()["weed_report_id"]
    lake = api.post(api.ann, water_body_id="lake:rice-lake", geometry=point(-79.40, 43.65)).json()[
        "weed_report_id"
    ]
    ids = lambda r: [x["weed_report_id"] for x in r.json()["weed_reports"]]  # noqa: E731
    assert set(ids(api.get(api.ann, "/map/weed-reports"))) == {river, lake}
    assert ids(api.get(api.ann, "/map/weed-reports?water_body_id=lake:rice-lake")) == [lake]
    assert ids(api.get(api.ann, "/map/weed-reports?bbox=-79.7,43.4,-79.6,43.55")) == [river]
    layer = api.get(api.ann, "/map/weed-reports/layer?water_body_id=river:credit-river").json()
    assert [f["id"] for f in layer["features"]] == [river]
    assert api.get(api.ann, "/map/weed-reports?bbox=1,2,3").status_code == 422
    assert api.get(api.ann, "/map/weed-reports/layer?bbox=5,5,1,1").status_code == 422


def test_the_layer_is_geojson_and_each_feature_is_labelled_and_dated(api):
    rid = api.post(api.ann, geometry=line((-79.69, 43.50), (-79.62, 43.50))).json()[
        "weed_report_id"
    ]
    layer = api.get(api.ann, "/map/weed-reports/layer").json()
    assert layer["type"] == "FeatureCollection"
    assert layer["meta"]["layer"] == "Your weed edges"
    (feature,) = layer["features"]
    assert feature["id"] == rid
    assert feature["geometry"]["type"] == "LineString"
    props = feature["properties"]
    assert props["status_label"] == "reported, not verified"
    assert props["observed_on"] == SEEN and props["date_label"] == "seen 2026-10-04"
    assert props["kind_label"] == "Weed edge"
    assert props["provenance"] == "user_reported"


def test_patch_changes_note_date_and_position_and_checks_the_position_again(api):
    rid = api.post(api.ann, note="before").json()["weed_report_id"]
    url = f"/map/weed-reports/{rid}"
    ok = api.client.patch(
        url,
        json={"note": None, "observed_on": "2026-10-05", "geometry": point(-79.66, 43.50)},
        headers=api.ann["h"],
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["note"] is None and body["observed_on"] == "2026-10-05"
    assert body["geometry"] == point(-79.66, 43.50) and body["updated_at"]
    assert body["date_label"] == "seen 2026-10-05"
    off = api.client.patch(url, json={"geometry": point(-79.65, 43.503)}, headers=api.ann["h"])
    assert off.status_code == 422
    assert api.get(api.ann, url).json()["geometry"] == point(-79.66, 43.50)
    for bad in ({"geometry": None}, {"water_body_id": "lake:rice-lake"}, {"user_id": 1}):
        assert api.client.patch(url, json=bad, headers=api.ann["h"]).status_code == 422
    empty = api.client.patch(url, json={}, headers=api.ann["h"])
    assert empty.status_code == 200 and empty.json()["observed_on"] == "2026-10-05"


def test_delete_then_get_is_a_404(api):
    rid = api.post(api.ann).json()["weed_report_id"]
    assert api.client.delete(f"/map/weed-reports/{rid}", headers=api.ann["h"]).json() == {
        "deleted": rid
    }
    assert api.get(api.ann, f"/map/weed-reports/{rid}").status_code == 404
    assert api.client.delete(f"/map/weed-reports/{rid}", headers=api.ann["h"]).status_code == 404


def test_labels_are_served_by_the_api(api):
    body = api.get(api.ann, "/map/weed-reports/labels").json()
    assert body["status_label"] == "reported, not verified"
    assert body["near_line_m"] == 150 and body["near_outline_m"] == 100
    assert body["layer"]["label"] == "Your weed edges"


# ── privacy: nobody reaches anybody else's reports ────────────────────────────


def test_another_user_cannot_read_list_edit_or_delete_a_report(api):
    rid = api.post(api.ann, note="private").json()["weed_report_id"]
    url = f"/map/weed-reports/{rid}"

    # Read: a 404 identical to an id nobody has used.
    theirs = api.get(api.bob, url)
    nobody = api.get(api.bob, "/map/weed-reports/987654")
    assert theirs.status_code == nobody.status_code == 404
    assert theirs.json() == nobody.json()

    # List and layer, with and without filters that would match it.
    for path in (
        "/map/weed-reports",
        "/map/weed-reports?water_body_id=river:credit-river",
        "/map/weed-reports?bbox=-80,43,-79,44",
        "/map/weed-reports/layer",
        "/map/weed-reports/layer?water_body_id=river:credit-river&bbox=-80,43,-79,44",
    ):
        body = api.get(api.bob, path).json()
        assert body.get("weed_reports", body.get("features")) == [], path

    # Edit and delete: 404, and the report is exactly as it was.
    before = api.get(api.ann, url).json()
    assert (
        api.client.patch(
            url, json={"note": "hijacked", "observed_on": "2026-01-01"}, headers=api.bob["h"]
        ).status_code
        == 404
    )
    assert api.client.delete(url, headers=api.bob["h"]).status_code == 404
    assert api.get(api.ann, url).json() == before
    assert before["note"] == "private"


def test_users_with_reports_each_see_only_their_own(api):
    a = api.post(api.ann, note="ann's").json()["weed_report_id"]
    b = api.post(api.bob, note="bob's", geometry=point(-79.66, 43.50)).json()["weed_report_id"]
    for who, mine, other in ((api.ann, a, b), (api.bob, b, a)):
        listed = api.get(who, "/map/weed-reports").json()["weed_reports"]
        assert [r["weed_report_id"] for r in listed] == [mine]
        layer = api.get(who, "/map/weed-reports/layer").json()["features"]
        assert [f["id"] for f in layer] == [mine]
        assert api.get(who, f"/map/weed-reports/{other}").status_code == 404
    assert store.get_weed_report(api.db, api.ann["id"], b) is None


def _keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_no_response_carries_a_count_or_changes_with_other_peoples_reports(api):
    paths = (
        "/map/weed-reports",
        "/map/weed-reports/layer",
        "/map/weed-reports/labels",
    )
    empty = {p: api.get(api.bob, p).json() for p in paths}
    water = {
        p: api.get(api.bob, p).json()
        for p in (
            "/map/water-bodies",
            "/map/water-bodies/river:credit-river",
            "/map/water-bodies/lake:rice-lake",
            "/map/spots?tier=3",
        )
    }

    # Ann reports a lot; none of it may show in anything Bob can call.
    for _ in range(3):
        assert api.post(api.ann).status_code == 201
    assert (
        api.post(api.ann, water_body_id="lake:rice-lake", geometry=point(-79.40, 43.65)).status_code
        == 201
    )

    for p in paths:
        assert api.get(api.bob, p).json() == empty[p], p
    for p, before in water.items():
        assert api.get(api.bob, p).json() == before, p

    # And even Ann's own responses hold no count, total or tally of any kind.
    for p in (*paths, "/map/weed-reports/1"):
        body = api.get(api.ann, p).json()
        for key in _keys(body):
            assert not any(w in key.lower() for w in ("count", "total", "tally", "num_")), (p, key)
    layer_meta = api.get(api.ann, "/map/weed-reports/layer").json()["meta"]
    assert set(layer_meta) == {"layer", "note", "visible_to"}


def test_there_is_no_sharing_route_and_no_report_reaches_the_water_body_endpoints(api):
    paths = {getattr(r, "path", "") for r in app.routes}
    weed_paths = {p for p in paths if "weed" in p}
    assert weed_paths == {
        "/map/weed-reports",
        "/map/weed-reports/labels",
        "/map/weed-reports/layer",
        "/map/weed-reports/{report_id}",
    }
    assert not any(w in p for p in weed_paths for w in ("share", "public", "export", "all"))


def test_unknown_water_body_error_type_is_a_404(db):
    body = WeedReportCreate.model_validate(report(water_body_id="river:nope"))
    from src.services.weed_reports import create_weed_report

    with pytest.raises(UnknownWaterBody) as err:
        create_weed_report(db, 1, body)
    assert err.value.status == 404


# ── the fixture other tools build against ─────────────────────────────────────


def test_the_api_fixture_still_matches_the_models_and_the_labels():
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "weed_reports_api_responses.json"
    entries = json.loads(path.read_text())["requests"]
    assert {e["status"] for e in entries} >= {200, 201, 404, 422}
    for e in entries:
        method, route = e["request"].split(" ", 1)
        if e["status"] >= 400:
            assert set(e["response"]) == {"detail"}
        elif route == "/map/weed-reports/labels":
            assert e["response"] == labels.labels_payload()
        elif "/layer" in route:
            assert e["response"]["type"] == "FeatureCollection"
            for f in e["response"]["features"]:
                assert f["properties"]["status_label"] == labels.STATUS_LABEL
                assert f["id"] == f["properties"]["weed_report_id"]
        elif method == "DELETE":
            assert set(e["response"]) == {"deleted"}
        else:
            items = e["response"].get("weed_reports", [e["response"]])
            for item in items:
                WeedGeometry.model_validate(item["geometry"])
                assert item["status_label"] == labels.STATUS_LABEL
                assert item["provenance"] == "user_reported"
                assert item["caption"] == labels.caption(item["observed_on"])
                assert "user_id" not in item
