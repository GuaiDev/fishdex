"""Fishing stretches: clustering, tracing, curation overrides, storage, API.

Every test runs on a hand-built network so the expected answer is known by
construction:

    lon -80.00 (main river, flows south)          lon -79.95
    43.10 ─┐
           │ m1..m5  (upstream half)
    43.05 ─┼──────────── t1..t5 (tributary, flows west)
           │ m6..m10 (downstream half, with a braid b1 beside m8)
    43.00 ─┘ mouth
    lake line offshore along lat 42.997, flowing east

A short ditch runs 40 m east of the main river near lat 43.075, and a long
headwater feeds the top of the main river so it is "major" by accumulation.
"""

import math

import pytest

from src.models.hydrology import StreamSegment
from src.models.stretch import (
    BoundaryPoint,
    CuratedReach,
    FishingStretch,
    StretchCuration,
)
from src.services.stretch_clustering import (
    StreamNetwork,
    cluster_candidates,
    display_name,
    stretch_shape,
    trace_reach,
)

LON = -80.0


def _len_m(coords):
    total = 0.0
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        dx = (x1 - x0) * 111_000 * math.cos(math.radians((y0 + y1) / 2))
        dy = (y1 - y0) * 111_000
        total += math.hypot(dx, dy)
    return total


def seg(ogf_id, coords, name=None, wtype="Stream", length_m=None):
    coords = [(round(x, 5), round(y, 5)) for x, y in coords]
    return StreamSegment(
        ogf_id=ogf_id,
        watercourse_type=wtype,
        name=name,
        flow_verified=True,
        permanency="Permanent",
        length_m=length_m if length_m is not None else _len_m(coords),
        geom_wkt="LINESTRING (" + ", ".join(f"{x} {y}" for x, y in coords) + ")",
        start_node=f"{coords[0][0]},{coords[0][1]}",
        end_node=f"{coords[-1][0]},{coords[-1][1]}",
    )


def network_segments():
    segs = []
    # Headwater: one long segment feeding the top of the main river, so the
    # main river carries far more upstream channel than the ditch.
    segs.append(seg(900, [(LON, 43.20), (LON, 43.10)], length_m=50_000))
    # Main river m1..m10, 0.01° (~1.1 km) each, flowing south.
    for k in range(10):
        lat0 = 43.10 - 0.01 * k
        name = "Test River (rivière Test)" if k in (2, 7) else None
        segs.append(seg(101 + k, [(LON, lat0), (LON, lat0 - 0.01)], name=name))
    # Braid beside m8 (43.03 -> 43.02): same end nodes, bowed east.
    segs.append(seg(150, [(LON, 43.03), (LON + 0.002, 43.025), (LON, 43.02)]))
    # Tributary t1..t5 flowing west into the main river at 43.05, fed by its
    # own long headwater.
    segs.append(seg(950, [(-79.90, 43.05), (-79.95, 43.05)], length_m=20_000))
    for k in range(5):
        lon0 = -79.95 - 0.01 * k
        segs.append(seg(201 + k, [(lon0, 43.05), (lon0 - 0.01, 43.05)], name="Side Creek"))
    # Ditch: 40 m east of m3, not connected to anything big.
    segs.append(seg(300, [(LON + 0.0005, 43.078), (LON + 0.0005, 43.072)], name="Ditch"))
    # Lake line offshore, flowing east past the mouth. Fed by a huge virtual
    # headwater so it dwarfs the river, like a Great Lakes shore flow line.
    segs.append(seg(990, [(-80.30, 42.997), (-80.05, 42.997)], length_m=5_000_000))
    segs.append(seg(400, [(-80.05, 42.997), (-79.95, 42.997)], wtype="Virtual Flow"))
    # The river mouth joins the lake line.
    segs.append(seg(111, [(LON, 43.00), (LON, 42.997)], wtype="Virtual Flow"))
    return segs


@pytest.fixture(scope="module")
def net():
    return StreamNetwork(network_segments())


def pt(lat, lon=LON, **kw):
    return BoundaryPoint(lat=lat, lon=lon, **kw)


def reach(a, b, via=()):
    return CuratedReach.model_validate(
        {"from": a.model_dump(), "to": b.model_dump(), "via": [v.model_dump() for v in via]}
    )


# ── accumulation, snapping, tracing ───────────────────────────────────────────


def test_upstream_km_accumulates_down_the_river(net):
    acc = net.upstream_km
    assert acc[101] > 50  # the 50 km headwater drains through it
    assert acc[106] > acc[105] + 20  # the 20 km tributary has joined
    assert acc[110] > acc[106]


def test_split_flow_is_divided_not_copied():
    """Mass-conserving: where flow splits, each branch gets a share, not the total.

    Copying the upstream total into every branch counts each path separately;
    on a lake's mesh of flow lines that reached 163 billion km.
    """
    split = StreamNetwork(
        [
            seg(1, [(0.0, 0.3), (0.0, 0.2)], length_m=10_000),
            seg(2, [(0.0, 0.2), (-0.01, 0.1)], length_m=1_000),  # left branch
            seg(3, [(0.0, 0.2), (0.01, 0.1)], length_m=1_000),  # right branch
            seg(4, [(-0.01, 0.1), (0.0, 0.0)], length_m=1_000),
            seg(5, [(0.01, 0.1), (0.0, 0.0)], length_m=1_000),
            seg(6, [(0.0, 0.0), (0.0, -0.1)], length_m=1_000),
        ]
    )
    acc = split.upstream_km
    assert acc[2] == pytest.approx(10 / 2 + 1)
    assert acc[6] == pytest.approx(10 + 4 + 1)  # every metre counted exactly once


def test_snap_prefers_the_river_to_a_nearer_ditch(net):
    # 20 m from the ditch, 60 m from the river: both within the slack.
    snap = net.snap(pt(43.075, LON + 0.00075), max_m=400)
    assert snap.ogf_id == 103


def test_snap_does_not_reach_past_the_river_to_the_lake(net):
    # On the river 250 m above the lake line: the lake is bigger but beyond
    # the slack from the nearest channel, so the river wins.
    snap = net.snap(pt(42.99925), max_m=400)
    assert snap.ogf_id == 111


def test_snap_returns_none_when_nothing_is_in_range(net):
    assert net.snap(pt(43.05, -79.80), max_m=400) is None


def test_trace_follows_the_river_and_includes_the_braid(net):
    trace = trace_reach(net, reach(pt(43.095), pt(43.005)), max_snap_m=400)
    assert trace.failure is None
    assert set(trace.ogf_ids) == set(range(101, 111)) | {150}
    assert 201 not in trace.ogf_ids  # did not wander up the tributary
    assert trace.length_m > 9_000


def test_trace_up_a_tributary_needs_no_via(net):
    trace = trace_reach(net, reach(pt(43.05, -79.955), pt(43.095)), max_snap_m=400)
    assert trace.failure is None
    assert {201, 205, 105, 101} <= set(trace.ogf_ids)


def test_trace_reports_a_point_off_the_network(net):
    trace = trace_reach(net, reach(pt(43.05, -79.80, label="Nowhere"), pt(43.005)), 400)
    assert trace.failure == "snap_failed"
    assert "Nowhere" in trace.detail


def test_pinned_point_skips_snapping(net):
    # Unpinned, (43.075, LON + 0.0005) snaps to the river beside the ditch.
    trace = trace_reach(net, reach(pt(43.075, ogf_id=300), pt(43.072, ogf_id=300)), 400)
    assert trace.ogf_ids == [300]


def test_pin_to_unknown_segment_is_a_snap_failure(net):
    trace = trace_reach(net, reach(pt(43.075, ogf_id=123456), pt(43.005)), 400)
    assert trace.failure == "snap_failed"


def test_disconnected_points_report_no_path(net):
    trace = trace_reach(net, reach(pt(43.075, ogf_id=300), pt(43.005)), 400)
    assert trace.failure == "no_path"


def test_stretch_shape_is_a_single_drawable_line(net):
    shape = stretch_shape(net, list(range(101, 111)))
    assert shape.geometry["type"] == "LineString"
    min_lon, min_lat, max_lon, max_lat = shape.bbox
    assert min_lat == pytest.approx(43.0) and max_lat == pytest.approx(43.1)
    assert min_lat < shape.centroid_lat < max_lat


def test_display_name_strips_the_french_duplicate():
    assert display_name("Credit River (rivière Credit)") == "Credit River"
    assert display_name("Credit River (Erin Branch)") == "Credit River (Erin Branch)"
    assert display_name(None) is None


# ── clustering ────────────────────────────────────────────────────────────────


def test_clustering_cuts_major_channel_at_the_confluence(net):
    cands = cluster_candidates(
        net, "CA-ON", min_upstream_km=10, max_upstream_km=1_000, max_km=40, min_km=1
    )
    by_ids = [set(c.ogf_ids) for c in cands]
    # Main river above the confluence, below it, and the tributary: three runs.
    assert any(s >= set(range(101, 106)) for s in by_ids)
    assert any(s >= set(range(201, 206)) for s in by_ids)
    # The braid around m8 is part of the run below, not a cut in it.
    below = next(s for s in by_ids if 106 in s)
    assert below >= {106, 107, 108, 150, 109, 110}
    assert not any(105 in s and 106 in s for s in by_ids)
    assert not any(205 in s and (105 in s or 106 in s) for s in by_ids)


def test_clustering_leaves_out_lake_scale_channel(net):
    cands = cluster_candidates(net, "CA-ON", min_upstream_km=10, max_upstream_km=1_000, min_km=0)
    assert not any(400 in c.ogf_ids or 990 in c.ogf_ids for c in cands)


def test_clustering_splits_long_runs_and_drops_stubs(net):
    cands = cluster_candidates(
        net, "CA-ON", min_upstream_km=10, max_upstream_km=1_000, max_km=2.5, min_km=2
    )
    below = [c for c in cands if set(c.ogf_ids) & {106, 107, 108, 109, 110}]
    assert len(below) >= 2  # ~5.6 km below the confluence, cut at 2.5 km
    assert all(c.length_km <= 2.5 + 1.2 for c in below)  # max_km plus one segment
    assert all(c.length_km >= 2 for c in cands)  # leftovers under min_km are stubs


def test_candidate_names_come_from_ohn_labels(net):
    cands = cluster_candidates(net, "CA-ON", min_upstream_km=10, max_upstream_km=1_000, min_km=1)
    upper = next(c for c in cands if 101 in c.ogf_ids)
    assert upper.suggested_name == "Test River"
    assert upper.downstream.lat < upper.upstream.lat  # it flows south


# ── models ────────────────────────────────────────────────────────────────────


def _curation_dict(**extra):
    return {
        "version": 1,
        "jurisdiction": "CA-ON",
        "build_bbox": [-80.5, 42.9, -79.5, 43.3],
        "stretch": [],
        **extra,
    }


def test_curation_rejects_duplicate_ids():
    entry = {
        "id": "a",
        "name": "A",
        "river": "R",
        "reach": [{"from": {"lat": 43.0, "lon": -80.0}, "to": {"lat": 43.1, "lon": -80.0}}],
    }
    with pytest.raises(ValueError, match="duplicate stretch ids: a"):
        StretchCuration.model_validate(_curation_dict(stretch=[entry, entry]))


def test_curation_rejects_bad_ids_bbox_and_unknown_keys():
    with pytest.raises(ValueError):
        StretchCuration.model_validate(_curation_dict(build_bbox=[-79.0, 43.0, -80.0, 44.0]))
    bad = {
        "id": "Not A Slug",
        "name": "A",
        "river": "R",
        "reach": [{"from": {"lat": 43.0, "lon": -80.0}, "to": {"lat": 43.1, "lon": -80.0}}],
    }
    with pytest.raises(ValueError):
        StretchCuration.model_validate(_curation_dict(stretch=[bad]))
    typo = {**bad, "id": "ok", "nmae": "typo"}
    with pytest.raises(ValueError):
        StretchCuration.model_validate(_curation_dict(stretch=[typo]))


def test_curation_requires_at_least_one_reach():
    entry = {"id": "a", "name": "A", "river": "R", "reach": []}
    with pytest.raises(ValueError):
        StretchCuration.model_validate(_curation_dict(stretch=[entry]))


def test_fishing_stretch_requires_line_geometry():
    base = dict(
        stretch_id="a",
        name="A",
        river="R",
        length_km=1.0,
        segment_count=1,
        bbox=(0, 0, 1, 1),
        centroid_lat=0.5,
        centroid_lon=0.5,
        built_at="2026-10-09T00:00:00+00:00",
    )
    line = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
    FishingStretch(**base, jurisdiction="CA-ON", geometry=line)
    with pytest.raises(ValueError):
        FishingStretch(
            **base, jurisdiction="CA-ON", geometry={"type": "Point", "coordinates": [0, 0]}
        )
    with pytest.raises(ValueError):
        FishingStretch(**base, jurisdiction="Ontario", geometry=line)


def test_shipped_curation_file_is_valid_and_covers_the_named_rivers():
    from src.services.stretches import DEFAULT_CURATION_PATH, load_curation

    cur = load_curation(DEFAULT_CURATION_PATH)
    assert 50 <= len(cur.stretches) <= 80
    rivers = {s.river for s in cur.stretches}
    for r in (
        "Grand River",
        "Thames River",
        "Credit River",
        "Saugeen River",
        "Nottawasaga River",
    ):
        assert r in rivers
    assert "grand-dunnville-port-maitland" in {s.id for s in cur.stretches}
    assert {s.region for s in cur.stretches} >= {"Lake Erie"}
    assert all(cur.jurisdiction_of(s) == "CA-ON" for s in cur.stretches)


# ── build: curation overrides end to end ──────────────────────────────────────


TOML_HEADER = """
version = 1
jurisdiction = "CA-ON"
build_bbox = [-80.5, 42.9, -79.5, 43.3]
"""


def _stretch_toml(sid, name, frm, to, extra=""):
    return f"""
[[stretch]]
id = "{sid}"
name = "{name}"
river = "Test River"
region = "Test"
{extra}
[[stretch.reach]]
from = {{ lat = {frm[0]}, lon = {frm[1]} }}
to = {{ lat = {to[0]}, lon = {to[1]} }}
"""


UPPER = ((43.095, LON), (43.055, LON))
LOWER = ((43.045, LON), (43.005, LON))
WHOLE = ((43.095, LON), (43.005, LON))


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """One database for the module: schema setup costs seconds, and every
    build replaces the CA-ON set wholesale, so tests cannot see each other."""
    from src.storage.database import get_db

    return get_db(tmp_path_factory.mktemp("stretches") / "test.db")


def _build(db, tmp_path, body):
    from src.services.stretches import build_stretches

    path = tmp_path / "stretches.toml"
    path.write_text(TOML_HEADER + body)
    return build_stretches(db, path, segments=network_segments())


def _members(db, sid):
    from src.storage.stretches import stretch_segment_ids

    return stretch_segment_ids(db, sid)


def test_build_publishes_a_stretch_with_segments_and_geojson(db, tmp_path):
    from src.services.stretches import stretches_geojson

    report = _build(db, tmp_path, _stretch_toml("test-whole", "Whole River", *WHOLE))
    assert report.built == ["test-whole"]
    assert report.failed == []
    assert set(_members(db, "test-whole")) == set(range(101, 111)) | {150}

    fc = stretches_geojson(db)
    assert fc["type"] == "FeatureCollection"
    (feat,) = fc["features"]
    assert feat["id"] == "test-whole"
    assert feat["geometry"]["type"] in ("LineString", "MultiLineString")
    props = feat["properties"]
    assert props["name"] == "Whole River"
    assert props["jurisdiction"] == "CA-ON"
    assert props["segment_count"] == 11
    assert len(props["bbox"]) == 4 and len(props["label_point"]) == 2


def test_rename_keeps_the_id_and_changes_the_name(db, tmp_path):
    from src.storage.stretches import list_stretches

    _build(db, tmp_path, _stretch_toml("test-whole", "Old Name", *WHOLE))
    _build(db, tmp_path, _stretch_toml("test-whole", "New Name", *WHOLE))
    (s,) = list_stretches(db)
    assert (s.stretch_id, s.name) == ("test-whole", "New Name")


def test_split_into_two_entries_sharing_a_boundary_is_not_a_conflict(db, tmp_path):
    shared = (43.0455, LON)  # mid-segment on m6, as a curator would place it
    body = _stretch_toml("test-upper", "Upper", UPPER[0], shared) + _stretch_toml(
        "test-lower", "Lower", shared, LOWER[1]
    )
    report = _build(db, tmp_path, body)
    assert report.built == ["test-upper", "test-lower"]
    assert not [i for i in report.issues if i.kind == "segment_conflict"]
    upper, lower = set(_members(db, "test-upper")), set(_members(db, "test-lower"))
    assert not upper & lower  # many-to-one: a segment is in one stretch only
    assert {101, 105, 106} <= upper and {107, 110} <= lower


def test_merge_several_reaches_into_one_stretch(db, tmp_path):
    body = (
        _stretch_toml("test-merged", "River and Creek", *UPPER)
        + """
[[stretch.reach]]
from = { lat = 43.05, lon = -79.955 }
to = { lat = 43.05, lon = -79.995 }
"""
    )
    report = _build(db, tmp_path, body)
    assert report.built == ["test-merged"]
    members = set(_members(db, "test-merged"))
    assert {101, 104} <= members and {201, 204} <= members


def test_exclude_and_include_override_the_trace(db, tmp_path):
    extra = "exclude_ogf_ids = [150]\ninclude_ogf_ids = [300, 424242]"
    report = _build(db, tmp_path, _stretch_toml("test-whole", "W", *WHOLE, extra=extra))
    members = set(_members(db, "test-whole"))
    assert 150 not in members and 300 in members
    unknown = [i for i in report.issues if i.kind == "unknown_ogf_id"]
    assert unknown and "424242" in unknown[0].detail
    assert report.failed == []  # a warning, not a refusal


def test_disabled_stretch_is_kept_on_file_but_not_published(db, tmp_path):
    report = _build(db, tmp_path, _stretch_toml("test-off", "Off", *WHOLE, extra="enabled = false"))
    assert report.disabled == ["test-off"]
    assert report.built == []
    assert _members(db, "test-off") == []


def test_overlapping_stretches_keep_segments_with_the_first(db, tmp_path):
    body = _stretch_toml("test-first", "First", *WHOLE) + _stretch_toml(
        "test-second", "Second", *LOWER
    )
    report = _build(db, tmp_path, body)
    conflicts = [i for i in report.issues if i.kind == "segment_conflict"]
    assert conflicts and conflicts[0].stretch_id == "test-second"
    assert "test-first" in conflicts[0].detail
    assert "test-second" not in report.built  # nothing of its own was left
    assert "test-second" in report.failed


def test_a_failed_stretch_is_reported_and_the_rest_still_build(db, tmp_path):
    body = _stretch_toml("test-good", "Good", *UPPER) + _stretch_toml(
        "test-lost", "Lost", (43.05, -79.80), (43.06, -79.80)
    )
    report = _build(db, tmp_path, body)
    assert report.built == ["test-good"]
    assert report.failed == ["test-lost"]
    assert report.issues[0].kind == "snap_failed"


def test_point_outside_the_build_area_is_refused(db, tmp_path):
    report = _build(db, tmp_path, _stretch_toml("test-far", "Far", (44.5, -80.0), (43.005, LON)))
    assert report.failed == ["test-far"]
    assert report.issues[0].kind == "outside_build_area"


def test_rebuild_drops_a_stretch_removed_from_the_file(db, tmp_path):
    from src.storage.stretches import count_stretches

    body = _stretch_toml("test-upper", "Upper", *UPPER) + _stretch_toml(
        "test-lower", "Lower", *LOWER
    )
    _build(db, tmp_path, body)
    assert count_stretches(db, "CA-ON") == 2
    _build(db, tmp_path, _stretch_toml("test-upper", "Upper", *UPPER))
    assert count_stretches(db, "CA-ON") == 1
    assert _members(db, "test-lower") == []


def test_wandering_trace_is_flagged_and_a_via_point_clears_it(db, tmp_path):
    # Up the main river onto the headwater, which carries 50 km of channel in a
    # 10 km line: far more than 4x the straight distance between the points.
    frm, to = (43.065, LON), (43.15, LON)
    report = _build(db, tmp_path, _stretch_toml("test-detour", "Detour", frm, to))
    assert [i for i in report.issues if i.kind == "suspicious_path"]
    assert report.failed == []  # a warning, not a refusal

    # Naming the course with a via point sets the expected distance.
    body = _stretch_toml("test-detour", "Detour", frm, to).replace(
        "[[stretch.reach]]\n", "[[stretch.reach]]\nvia = [{ lat = 43.199, lon = -80.0 }]\n"
    )
    report = _build(db, tmp_path, body)
    assert not [i for i in report.issues if i.kind == "suspicious_path"]


def test_uncovered_candidates_are_reported_as_pasteable_entries(db, tmp_path):
    import tomllib

    from src.services.stretch_clustering import cluster_candidates
    from src.services.stretches import _uncovered, candidate_toml

    net = StreamNetwork(network_segments())
    cands = cluster_candidates(net, "CA-ON", min_upstream_km=10, max_upstream_km=1_000, min_km=1)
    main = next(c for c in cands if 106 in c.ogf_ids)
    claimed = {i: "test-lower" for i in main.ogf_ids}
    uncovered = _uncovered(net, cands, claimed)
    assert main.candidate_id not in {c.candidate_id for c in uncovered}
    assert main.covered_fraction == 1.0
    assert any(201 in c.ogf_ids for c in uncovered)

    entry = tomllib.loads(candidate_toml(uncovered[0]))
    cur = StretchCuration.model_validate(_curation_dict(stretch=entry["stretch"]))
    assert cur.stretches[0].id == uncovered[0].candidate_id


# ── API ───────────────────────────────────────────────────────────────────────


def test_map_stretches_endpoint_returns_geojson(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app, get_current_user

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    db = database.get_db()
    _build(db, tmp_path, _stretch_toml("test-whole", "Whole River", *WHOLE))

    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    try:
        client = TestClient(app)
        body = client.get("/map/stretches").json()
        assert [f["id"] for f in body["features"]] == ["test-whole"]
        assert client.get("/map/stretches?jurisdiction=US-MI").json()["features"] == []
    finally:
        app.dependency_overrides.clear()


def test_map_stretches_requires_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    assert TestClient(app).get("/map/stretches").status_code in (401, 403)
