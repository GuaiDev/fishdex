"""The coverage check: models, the ArcGIS reference adapter, the ladder, the gates, the CLI.

Everything runs on throwaway databases and a recorded LIO response; nothing reaches the network.
"""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from src.models.coverage import (
    Cause,
    CoverageRegistry,
    DbProbe,
    FocusArea,
    ListingProbe,
    ParityRow,
    ProbeSpot,
    ReferenceFeature,
    ReferenceSpec,
)
from src.services.coverage import (
    geometry,
    load_registry,
    registry_path,
    run_coverage_check,
    to_markdown,
)
from src.services.coverage.ladder import check_point_class
from src.services.coverage.probe import open_readonly, probe_connection
from src.storage.database import get_db

FIXTURE = Path(__file__).parent / "fixtures" / "lio_dam_inventory_response.json"
REGISTRY = Path(__file__).parent.parent / "data" / "coverage" / "references_ca_on.toml"
BOX = (-79.52, 43.62, -79.45, 43.70)


def _spec(**kw) -> ReferenceSpec:
    base = dict(
        feature_class="dams",
        label="Dam inventory",
        service_url="https://example.test/arcgis/rest/services/X/MapServer/0",
        id_field="OGF_ID",
        name_field="DAM_NAME",
        scope_m=150,
        anchor_m=150,
        spot_m=300,
        anchor_types=["dam", "tailwater"],
        spot_kinds=["below_dam"],
        min_recall=0.9,
    )
    base.update(kw)
    return ReferenceSpec(**base)


def _registry(**kw) -> CoverageRegistry:
    base = dict(
        jurisdiction="CA-ON",
        focus={"home": FocusArea(name="home", bbox=BOX)},
        references=[_spec()],
        parity={"tables": ["spots", "water_bodies"]},
    )
    base.update(kw)
    return CoverageRegistry.model_validate(base)


def _feature(ref_id="1", lat=43.65, lon=-79.50) -> ReferenceFeature:
    return ReferenceFeature(ref_id=ref_id, name=f"Dam {ref_id}", lat=lat, lon=lon)


# ── models ──────────────────────────────────────────────────────────────────


def test_focus_bbox_must_be_ordered():
    with pytest.raises(ValidationError):
        FocusArea(name="x", bbox=(-79.0, 43.0, -80.0, 44.0))


def test_registry_names_focus_areas_from_their_keys():
    reg = CoverageRegistry.model_validate(
        {
            "jurisdiction": "CA-ON",
            "focus": {"home": {"bbox": [-80, 43, -79, 44]}},
            "references": [],
            "parity": {"tables": []},
        }
    )
    assert reg.focus["home"].name == "home"


def test_reference_radii_must_be_positive():
    with pytest.raises(ValidationError):
        _spec(scope_m=0)


def test_parity_row_with_nothing_built_cannot_fail():
    assert ParityRow(table="t", build_rows=0, hosted_rows=None).ok
    assert ParityRow(table="t", build_rows=None, hosted_rows=None).ok
    assert not ParityRow(table="t", build_rows=5, hosted_rows=None).ok
    assert not ParityRow(table="t", build_rows=5, hosted_rows=4).ok
    assert ParityRow(table="t", build_rows=5, hosted_rows=5).ok


def test_ontario_registry_loads_and_names_its_layers():
    reg = load_registry(registry_path("CA-ON"))
    assert reg.jurisdiction == "CA-ON"
    assert "home" in reg.focus
    assert [r.feature_class for r in reg.references] == ["dams"]
    assert "water_bodies" in reg.parity.tables


def test_missing_registry_says_what_to_copy(tmp_path):
    with pytest.raises(FileNotFoundError, match="references_ca_on.toml"):
        load_registry(tmp_path / "references_us_mi.toml")


def test_registry_path_slug():
    assert registry_path("US-MI").name == "references_us_mi.toml"


# ── geometry ────────────────────────────────────────────────────────────────


def test_metres_between_one_degree_of_latitude():
    assert geometry.metres_between(43.0, -79.0, 44.0, -79.0) == pytest.approx(111_320, rel=1e-3)


def test_point_to_lines_distance_and_empty():
    line = [[(-79.51, 43.65), (-79.49, 43.65)]]
    assert geometry.point_to_lines_m(43.65, -79.50, line) == pytest.approx(0, abs=0.01)
    # 0.001 degrees of latitude north of the line is about 111 m.
    assert geometry.point_to_lines_m(43.651, -79.50, line) == pytest.approx(111.3, abs=0.5)
    assert geometry.point_to_lines_m(43.65, -79.5, []) == float("inf")


def test_lines_of_reads_both_line_types_and_ignores_polygons():
    ls = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
    mls = {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]}
    assert len(geometry.lines_of(json.dumps(ls))) == 1
    assert len(geometry.lines_of(mls)) == 2
    assert geometry.lines_of({"type": "Polygon", "coordinates": []}) == []
    assert geometry.lines_of(None) == []


# ── the ArcGIS reference adapter ────────────────────────────────────────────


def _arcgis():
    import importlib

    from src.ingest import discovery  # noqa: F401  (package import keeps `global` resolvable)

    return importlib.import_module("src.ingest.global.arcgis_reference")


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _recorded(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=json.loads(FIXTURE.read_text()))


def test_adapter_reads_the_recorded_dam_response(tmp_path):
    adapter = _arcgis()
    got = adapter.fetch_reference(_spec(), BOX, cache_dir=tmp_path, client=_client(_recorded))
    assert got.raw_count == 7 and got.skipped == 0 and not got.from_cache
    assert {f.name for f in got.features} >= {"Humber River 2nd Dam", "Humber River:6877"}
    one = next(f for f in got.features if f.name == "Humber River 2nd Dam")
    assert (one.lat, one.lon) == pytest.approx((43.6534, -79.5029), abs=1e-4)


def test_adapter_serves_the_second_call_from_the_cache(tmp_path):
    adapter = _arcgis()
    calls = []

    def handler(req):
        calls.append(req)
        return _recorded(req)

    adapter.fetch_reference(_spec(), BOX, cache_dir=tmp_path, client=_client(handler))
    again = adapter.fetch_reference(_spec(), BOX, cache_dir=tmp_path, client=_client(handler))
    assert again.from_cache and len(calls) == 1


def test_adapter_cache_key_covers_the_filter_and_the_box(tmp_path):
    adapter = _arcgis()
    calls = []

    def handler(req):
        calls.append(req)
        return _recorded(req)

    c = _client(handler)
    adapter.fetch_reference(_spec(), BOX, cache_dir=tmp_path, client=c)
    adapter.fetch_reference(_spec(where="DAM_ID > 900"), BOX, cache_dir=tmp_path, client=c)
    adapter.fetch_reference(_spec(), (-80, 43, -79, 44), cache_dir=tmp_path, client=c)
    assert len(calls) == 3


def test_adapter_expires_a_stale_cache(tmp_path):
    adapter = _arcgis()
    calls = []

    def handler(req):
        calls.append(req)
        return _recorded(req)

    adapter.fetch_reference(_spec(), BOX, cache_dir=tmp_path, client=_client(handler), now=1000.0)
    adapter.fetch_reference(
        _spec(), BOX, cache_dir=tmp_path, client=_client(handler), ttl_seconds=10, now=10**10
    )
    assert len(calls) == 2


def test_adapter_raises_on_an_arcgis_error_body(tmp_path):
    adapter = _arcgis()

    def handler(req):
        return httpx.Response(200, json={"error": {"code": 400, "message": "bad where"}})

    with pytest.raises(adapter.ReferenceFetchError, match="bad where"):
        adapter.fetch_reference(_spec(), BOX, cache_dir=tmp_path, client=_client(handler))


def test_adapter_raises_when_the_id_field_matches_nothing(tmp_path):
    adapter = _arcgis()
    with pytest.raises(adapter.ReferenceFetchError, match="id_field"):
        adapter.fetch_reference(
            _spec(id_field="NO_SUCH_FIELD"), BOX, cache_dir=tmp_path, client=_client(_recorded)
        )


def test_adapter_counts_what_it_skips(caplog):
    adapter = _arcgis()
    raw = [
        {"attributes": {"OGF_ID": 1}, "geometry": {"x": -79.5, "y": 43.6}},
        {"attributes": {"OGF_ID": 2}, "geometry": None},
        {"attributes": {"OGF_ID": 1}, "geometry": {"x": -79.4, "y": 43.6}},
        {"attributes": {"OGF_ID": None}, "geometry": {"x": -79.4, "y": 43.6}},
    ]
    with caplog.at_level("WARNING"):
        got = adapter.parse_features(raw, _spec())
    assert len(got.features) == 1 and got.skipped == 3
    assert got.skipped_reasons == {
        "no point geometry": 1,
        "duplicate id": 1,
        "no OGF_ID": 1,
    }
    assert "3 of 4" in caplog.text


def test_adapter_pages_until_the_service_stops_saying_more(tmp_path):
    adapter = _arcgis()
    pages = []

    def handler(req):
        offset = int(req.url.params["resultOffset"])
        pages.append(offset)
        n = 1000 if offset < 2000 else 5
        feats = [
            {"attributes": {"OGF_ID": offset + i}, "geometry": {"x": -79.5, "y": 43.6}}
            for i in range(n)
        ]
        return httpx.Response(200, json={"features": feats, "exceededTransferLimit": n == 1000})

    got = adapter.fetch_reference(_spec(), BOX, cache_dir=tmp_path, client=_client(handler))
    assert pages == [0, 1000, 2000] and len(got.features) == 2005


# ── the ladder ──────────────────────────────────────────────────────────────

LINE = json.dumps({"type": "LineString", "coordinates": [[-79.51, 43.65], [-79.49, 43.65]]})


def _build_db(tmp_path, *, anchors=(), spots=(), species=0, coverage="fetched"):
    path = tmp_path / "build.db"
    db = get_db(path)
    db.execute(
        "INSERT INTO stretch_segment_lines (ogf_id, stretch_id, jurisdiction, name, "
        "geometry_geojson) VALUES (1, 'humber', 'CA-ON', 'Humber', ?)",
        [LINE],
    )
    db.execute(
        "INSERT INTO stretch_osm_coverage (stretch_id, jurisdiction, status, checked_at) "
        "VALUES ('humber', 'CA-ON', ?, 'now')",
        [coverage],
    )
    for i, (lat, lon, typ) in enumerate(anchors):
        db.execute(
            "INSERT INTO stretch_anchors (anchor_id, stretch_id, jurisdiction, anchor_type, lat, "
            "lng, source, source_ref, built_at) VALUES (?, 'humber', 'CA-ON', ?, ?, ?, 'osm', "
            "'way/1', 'now')",
            [f"a{i}", typ, lat, lon],
        )
    for i, (lat, lon, kind) in enumerate(spots):
        db.execute(
            "INSERT INTO spots (spot_id, kind, jurisdiction, lat, lng, title, tier, "
            "structure_type, built_at) VALUES (?, ?, 'CA-ON', ?, ?, 't', 1, 'x', 'now')",
            [f"s{i}", kind, lat, lon],
        )
    if species:
        for i in range(species):
            db.execute(
                "INSERT INTO species_ranges (species, jurisdictions_present) VALUES (?, 'CA-ON')",
                [f"fish{i}"],
            )
    db.conn.commit()
    db.conn.close()
    return path


def _walk(path, features, **kw):
    conn = open_readonly(path)
    try:
        return check_point_class(_spec(), features, conn, "CA-ON", **kw)
    finally:
        conn.close()


def test_ladder_each_rung_has_its_own_cause(tmp_path):
    path = _build_db(
        tmp_path,
        anchors=[(43.65, -79.50, "dam"), (43.6502, -79.495, "tailwater")],
        spots=[(43.65, -79.50, "below_dam")],
    )
    feats = [
        _feature("far", lat=43.70, lon=-79.50),  # nowhere near a stretch
        _feature("noanchor", lat=43.6504, lon=-79.4915),  # on the line, no anchor within 150 m
        _feature("nospot", lat=43.6502, lon=-79.495),  # anchor, spot is 400 m away
        _feature("ok", lat=43.65, lon=-79.50),
    ]
    rep = _walk(path, feats)
    causes = {r.ref_id: r.cause for r in rep.results}
    assert causes == {
        "far": Cause.MISSING_CURATION,
        "noanchor": Cause.MISSING_DATA,
        "nospot": Cause.DETECTION,
        "ok": Cause.MATCHED,
    }
    assert (rep.reference_total, rep.in_scope, rep.out_of_scope, rep.matched) == (4, 3, 1, 1)
    assert rep.recall == pytest.approx(1 / 3)
    assert rep.passes is False


def test_missing_data_names_the_osm_coverage_of_the_stretch(tmp_path):
    path = _build_db(tmp_path, coverage="fetched")
    rep = _walk(path, [_feature()])
    assert rep.results[0].cause is Cause.MISSING_DATA
    assert "coverage for the stretch: fetched" in rep.results[0].detail


def test_ladder_deploy_rung_fires_only_with_a_hosted_side(tmp_path):
    path = _build_db(
        tmp_path, anchors=[(43.65, -79.50, "dam")], spots=[(43.65, -79.50, "below_dam")]
    )
    feat = [_feature()]
    assert _walk(path, feat).results[0].cause is Cause.MATCHED
    empty_host = DbProbe(
        source="host",
        table_rows={},
        listing=ListingProbe(table="species_ranges", table_present=False),
    )
    assert _walk(path, feat, hosted=empty_host).results[0].cause is Cause.DEPLOY
    full_host = empty_host.model_copy(
        update={"spots": [ProbeSpot(spot_id="s0", kind="below_dam", lat=43.65, lon=-79.5)]}
    )
    assert _walk(path, feat, hosted=full_host).results[0].cause is Cause.MATCHED


def test_ladder_without_stretches_is_unmeasured_not_a_pass(tmp_path):
    path = tmp_path / "empty.db"
    get_db(path).conn.close()
    rep = _walk(path, [_feature()])
    assert rep.unmeasured_reason and "build-stretches" in rep.unmeasured_reason
    assert rep.recall is None and rep.passes is None


def test_ladder_ignores_other_jurisdictions(tmp_path):
    path = _build_db(tmp_path)
    conn = open_readonly(path)
    try:
        rep = check_point_class(_spec(), [_feature()], conn, "CA-BC")
    finally:
        conn.close()
    assert rep.unmeasured_reason


# ── probes ──────────────────────────────────────────────────────────────────


def test_probe_is_read_only_and_counts_missing_tables_as_none(tmp_path):
    path = _build_db(tmp_path, species=3)
    before = path.read_bytes()
    conn = open_readonly(path)
    try:
        probe = probe_connection(
            conn,
            source="x",
            tables=["spots", "no_such_table", 'bad"name'],
            listing_table="species_ranges",
            jurisdiction="CA-ON",
        )
        with pytest.raises(Exception):
            conn.execute("DELETE FROM spots")
    finally:
        conn.close()
    assert probe.table_rows == {"spots": 0, "no_such_table": None, 'bad"name': None}
    assert probe.listing.rows_for_jurisdiction == 3 and probe.listing.table_present
    assert path.read_bytes() == before


def test_listing_table_without_a_jurisdiction_column_claims_no_rows(tmp_path):
    path = tmp_path / "x.db"
    db = get_db(path)
    db.execute("CREATE TABLE plain_listing (species TEXT)")
    db.execute("INSERT INTO plain_listing VALUES ('a')")
    db.conn.commit()
    db.conn.close()
    conn = open_readonly(path)
    try:
        probe = probe_connection(
            conn, source="x", tables=[], listing_table="plain_listing", jurisdiction="CA-ON"
        )
    finally:
        conn.close()
    assert probe.listing.rows_total == 1 and probe.listing.rows_for_jurisdiction == 0


# ── the runner and its gates ────────────────────────────────────────────────


def _fetch_one(features):
    adapter = _arcgis()

    def fetch(spec, bbox):
        return adapter.ReferenceFetch(features=features, raw_count=len(features))

    return fetch


def _gate(report, name):
    return next(g for g in report.gates if g.name == name)


def test_build_only_run_skips_host_gates_and_fails_an_empty_listing_table(tmp_path):
    path = _build_db(
        tmp_path, anchors=[(43.65, -79.50, "dam")], spots=[(43.65, -79.50, "below_dam")]
    )
    rep = run_coverage_check(_registry(), "home", path, fetch=_fetch_one([_feature()]))
    assert _gate(rep, "api_reachable").skipped and _gate(rep, "deploy_parity").skipped
    assert not _gate(rep, "listing_table").passed
    assert _gate(rep, "dams_in_scope_recall").passed
    assert rep.exit_code == 1  # the empty listing table is a hard failure


def test_a_clean_build_exits_zero(tmp_path):
    path = _build_db(
        tmp_path,
        anchors=[(43.65, -79.50, "dam")],
        spots=[(43.65, -79.50, "below_dam")],
        species=2,
    )
    rep = run_coverage_check(_registry(), "home", path, fetch=_fetch_one([_feature()]))
    assert rep.exit_code == 0 and not rep.hard_failures


def test_focus_gate_failure_informs_but_does_not_block(tmp_path):
    path = _build_db(tmp_path, species=2)
    rep = run_coverage_check(_registry(), "home", path, fetch=_fetch_one([_feature()]))
    assert [g.name for g in rep.focus_failures] == ["dams_in_scope_recall"]
    assert rep.exit_code == 0


def test_an_unmeasurable_class_fails_its_gate_instead_of_passing_it(tmp_path):
    path = tmp_path / "empty.db"
    get_db(path).conn.close()
    rep = run_coverage_check(_registry(), "home", path, fetch=_fetch_one([_feature()]))
    gate = _gate(rep, "dams_in_scope_recall")
    assert not gate.passed and "could not be measured" in gate.measured


def test_hosted_db_adds_parity_and_judges_the_hosts_listing_table(tmp_path):
    build = _build_db(tmp_path, species=2)
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    host = _build_db(host_dir, species=0)
    rep = run_coverage_check(
        _registry(),
        "home",
        build,
        hosted_db=host,
        fetch=_fetch_one([]),
    )
    assert _gate(rep, "deploy_parity").passed is True  # both hold 0 rows of spots/water_bodies
    listing = _gate(rep, "listing_table")
    assert not listing.passed and "on the host" in listing.measured


def test_parity_fails_when_the_host_lacks_rows(tmp_path):
    build = _build_db(tmp_path, spots=[(43.65, -79.5, "below_dam")], species=1)
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    host = _build_db(host_dir, species=1)
    rep = run_coverage_check(_registry(), "home", build, hosted_db=host, fetch=_fetch_one([]))
    gate = _gate(rep, "deploy_parity")
    assert not gate.passed and "spots 0 of 1" in gate.measured
    assert rep.exit_code == 1


def test_api_mode_runs_liveness_and_asks_the_host_for_its_tables(tmp_path):
    from src.models.coverage import LivenessReport, VantageResult

    build = _build_db(tmp_path, spots=[(43.65, -79.5, "below_dam")], species=1)
    seen = {}

    def host_probe(url, key, **kw):
        seen.update(url=url, key=key, **kw)
        return DbProbe(
            source=url,
            table_rows={"spots": 1, "water_bodies": 0},
            listing=ListingProbe(
                table="species_ranges", table_present=True, rows_for_jurisdiction=9
            ),
        )

    def outside(url, path, required):
        return LivenessReport(
            url=url + path,
            required=required,
            prober="test",
            vantage_points=[VantageResult(node=n, ok=True, status=200) for n in "abc"],
        )

    rep = run_coverage_check(
        _registry(),
        "home",
        build,
        api_url="https://api.example",
        api_key="k",
        fetch=_fetch_one([]),
        probe_outside=outside,
        host_probe=host_probe,
    )
    assert seen["key"] == "k" and seen["tables"] == ["spots", "water_bodies"]
    assert _gate(rep, "api_reachable").passed
    assert _gate(rep, "deploy_parity").passed and _gate(rep, "listing_table").passed
    assert rep.exit_code == 0


def test_liveness_outage_fails_the_gate_instead_of_crashing(tmp_path):
    build = _build_db(tmp_path, spots=[(43.65, -79.5, "below_dam")], species=1)

    def outside(url, path, required):
        raise httpx.ConnectError("check-host.net unreachable")

    rep = run_coverage_check(
        _registry(),
        "home",
        build,
        api_url="https://api.example",
        fetch=_fetch_one([]),
        probe_outside=outside,
    )
    gate = _gate(rep, "api_reachable")
    assert not gate.passed and not gate.skipped
    assert "check-host.net unreachable" in gate.measured
    assert rep.exit_code != 0


def test_empty_reference_fails_the_recall_gate(tmp_path):
    path = _build_db(tmp_path, spots=[(43.65, -79.5, "below_dam")], species=1)
    rep = run_coverage_check(_registry(), "home", path, fetch=_fetch_one([]))
    gates = [g for g in rep.gates if g.name.endswith("_in_scope_recall")]
    assert gates and all(not g.passed for g in gates)


def test_adapter_does_not_cache_an_empty_answer(tmp_path):
    adapter = _arcgis()
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"features": []})

    for _ in range(2):
        adapter.fetch_reference(_spec(), BOX, cache_dir=tmp_path, client=_client(handler))
    assert len(calls) == 2


def test_api_mode_without_a_key_says_parity_was_skipped(tmp_path):
    from src.models.coverage import LivenessReport, VantageResult

    build = _build_db(tmp_path, species=1)
    rep = run_coverage_check(
        _registry(),
        "home",
        build,
        api_url="https://api.example",
        api_key=None,
        fetch=_fetch_one([]),
        probe_outside=lambda url, path, required: LivenessReport(
            url=url,
            required=required,
            prober="t",
            vantage_points=[VantageResult(node="a", ok=False, detail="down")],
        ),
    )
    assert _gate(rep, "deploy_parity").skipped
    assert any("admin key" in n for n in rep.notes)
    assert not _gate(rep, "api_reachable").passed and rep.exit_code == 1


def test_unknown_focus_names_the_ones_that_exist(tmp_path):
    path = _build_db(tmp_path)
    with pytest.raises(ValueError, match="home"):
        run_coverage_check(_registry(), "mars", path, fetch=_fetch_one([]))


def test_non_independent_reference_is_flagged_in_notes(tmp_path):
    path = _build_db(tmp_path)
    reg = _registry(references=[_spec(independent=False)])
    rep = run_coverage_check(reg, "home", path, fetch=_fetch_one([]))
    assert any("self-agreement" in n for n in rep.notes)


def test_markdown_leads_with_gates_and_lists_misses_by_cause(tmp_path):
    path = _build_db(tmp_path, species=1)
    rep = run_coverage_check(_registry(), "home", path, fetch=_fetch_one([_feature("a")]))
    md = to_markdown(rep)
    assert md.index("## Gates") < md.index("## dams")
    assert "### missing_data (1)" in md and "Dam a" in md


# ── liveness ────────────────────────────────────────────────────────────────


def test_liveness_node_parsing():
    from src.services.coverage.liveness import parse_node

    assert parse_node("de1", [[1, 0.2, "OK", "200", "1.2.3.4"]]).ok
    broken = parse_node("sg1", [[0, 0.1, "Broken pipe", None, None]])
    assert not broken.ok and broken.detail == "Broken pipe"
    assert not parse_node("uk1", [[1, 0.2, "Moved", "301", "1.2.3.4"]]).ok  # up, but not 200
    assert not parse_node("x", None).ok
    assert not parse_node("x", "garbage").ok


def test_liveness_polls_check_host_until_every_node_answers():
    from src.services.coverage.liveness import probe_outside

    polls = []

    def handler(req):
        if req.url.path == "/check-http":
            assert req.url.params["host"] == "https://api.example/health"
            return httpx.Response(200, json={"request_id": "r1", "nodes": {"de1": [], "sg1": []}})
        polls.append(1)
        if len(polls) == 1:
            return httpx.Response(200, json={"de1": [[1, 0.1, "OK", "200", "ip"]], "sg1": None})
        return httpx.Response(
            200,
            json={
                "de1": [[1, 0.1, "OK", "200", "ip"]],
                "sg1": [[0, 0.1, "Broken pipe", None, None]],
            },
        )

    rep = probe_outside(
        "https://api.example/", "/health", 2, client=_client(handler), sleep=lambda s: None
    )
    assert len(polls) == 2 and rep.ok_count == 1 and not rep.ok
    assert rep.prober == "check-host.net"


def test_host_probe_client_sends_the_key_and_reads_the_probe_shape():
    from src.services.coverage.host import fetch_host_probe

    payload = DbProbe(
        source="host",
        table_rows={"spots": 4},
        listing=ListingProbe(table="species_ranges", table_present=True, rows_for_jurisdiction=2),
    )

    def handler(req):
        assert req.headers["x-api-key"] == "secret"
        assert req.url.params["tables"] == "spots,water_bodies"
        return httpx.Response(200, json=payload.model_dump())

    got = fetch_host_probe(
        "https://api.example",
        "secret",
        tables=["spots", "water_bodies"],
        listing_table="species_ranges",
        jurisdiction="CA-ON",
        spot_kinds=["below_dam"],
        bbox=None,
        client=_client(handler),
    )
    assert got.table_rows == {"spots": 4} and got.source == "https://api.example"


# ── the admin probe endpoint ────────────────────────────────────────────────


def test_admin_probe_endpoint_needs_the_key_and_reports_the_served_db(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.api import main as api_main
    from src.storage import database

    path = _build_db(tmp_path, spots=[(43.65, -79.5, "below_dam")], species=2)
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setenv("FISHBOT_API_KEY", "secret")
    client = TestClient(api_main.app)
    q = {"tables": "spots,water_bodies", "jurisdiction": "CA-ON", "kinds": "below_dam"}

    assert client.get("/admin/coverage-probe", params=q).status_code == 401
    ok = client.get("/admin/coverage-probe", params=q, headers={"X-Api-Key": "secret"})
    assert ok.status_code == 200
    body = DbProbe.model_validate(ok.json())
    assert body.table_rows["spots"] == 1 and body.listing.rows_for_jurisdiction == 2
    assert [s.kind for s in body.spots] == ["below_dam"]


# ── the command ─────────────────────────────────────────────────────────────


def _invoke(monkeypatch, tmp_path, args, features):
    from src.cli import main as cli

    adapter = _arcgis()
    monkeypatch.setattr(
        adapter,
        "fetch_reference",
        lambda spec, bbox, **k: adapter.ReferenceFetch(features=features, raw_count=len(features)),
    )
    return CliRunner().invoke(
        cli.app, ["coverage-check", "--out-dir", str(tmp_path / "out"), *args]
    )


def test_command_exits_non_zero_on_a_hard_gate_and_writes_both_reports(monkeypatch, tmp_path):
    path = _build_db(tmp_path)  # no species_ranges rows: the listing gate fails
    result = _invoke(monkeypatch, tmp_path, ["--db", str(path)], [_feature()])
    assert result.exit_code == 1, result.output
    assert "listing_table" in result.output and "Hard gate failed" in result.output
    out = tmp_path / "out"
    assert (out / "coverage_ca_on_home.md").exists()
    assert json.loads((out / "coverage_ca_on_home.json").read_text())["jurisdiction"] == "CA-ON"


def test_command_exits_zero_when_the_hard_gates_hold(monkeypatch, tmp_path):
    path = _build_db(
        tmp_path,
        anchors=[(43.65, -79.50, "dam")],
        spots=[(43.65, -79.50, "below_dam")],
        species=2,
    )
    result = _invoke(monkeypatch, tmp_path, ["--db", str(path)], [_feature()])
    assert result.exit_code == 0, result.output


def test_command_exits_two_when_the_check_cannot_run(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, ["--db", str(tmp_path / "missing.db")], [])
    assert result.exit_code == 2 and "could not run" in result.output


def test_command_exits_two_for_a_jurisdiction_with_no_registry(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, ["--jurisdiction", "US-MI"], [])
    assert result.exit_code == 2 and "references_us_mi.toml" in result.output
