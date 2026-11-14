"""HydroSegmentRecord and the hydro_segments table + R*Tree. Throwaway databases only."""

import pytest
from pydantic import ValidationError
from shapely import wkb

from src.models.hydro_segment import HydroSegmentRecord
from src.services.hydro_nodes import encode_node
from src.storage.database import ensure_schema

_INSERT = """
    INSERT INTO hydro_segments (jurisdiction, segment_source, seg_id, start_node, end_node,
        length_m, name, flow_verified, is_waterbody_path, upstream_km, tile_key, geom,
        min_lon, max_lon, min_lat, max_lat)
    VALUES (:jurisdiction, :segment_source, :seg_id, :start_node, :end_node,
        :length_m, :name, :flow_verified, :is_waterbody_path, :upstream_km, :tile_key, :geom,
        :min_lon, :max_lon, :min_lat, :max_lat)
    ON CONFLICT(segment_source, seg_id) DO UPDATE SET
        upstream_km = excluded.upstream_km, geom = excluded.geom,
        min_lon = excluded.min_lon, max_lon = excluded.max_lon,
        min_lat = excluded.min_lat, max_lat = excluded.max_lat
"""


def _rec(seg_id=1, source="OHN", wkt="LINESTRING (-79.5 43.5, -79.4 43.6)", **kw):
    fields = dict(jurisdiction="CA-ON", flow_verified=True, length_m=1000.0)
    fields.update(kw)
    return HydroSegmentRecord.from_wkt(geom_wkt=wkt, segment_source=source, seg_id=seg_id, **fields)


def _put(db, rec):
    row = rec.model_dump()
    row["flow_verified"] = int(row["flow_verified"])
    row["is_waterbody_path"] = int(row["is_waterbody_path"])
    db.execute(_INSERT, row)


def _in_box(db, min_lon, max_lon, min_lat, max_lat):
    return {
        r[0]
        for r in db.execute(
            "SELECT s.seg_id FROM hydro_segments_rtree r JOIN hydro_segments s ON s.rowid = r.id "
            "WHERE r.max_lon >= ? AND r.min_lon <= ? AND r.max_lat >= ? AND r.min_lat <= ?",
            (min_lon, max_lon, min_lat, max_lat),
        )
    }


# --- model -------------------------------------------------------------------


def test_from_wkt_derives_nodes_bbox_and_wkb():
    rec = _rec()
    assert rec.start_node == encode_node(-79.5, 43.5)
    assert rec.end_node == encode_node(-79.4, 43.6)
    assert (rec.min_lon, rec.max_lon, rec.min_lat, rec.max_lat) == (-79.5, -79.4, 43.5, 43.6)
    assert wkb.loads(rec.geom).wkt == "LINESTRING (-79.5 43.5, -79.4 43.6)"
    assert rec.is_waterbody_path is False
    assert rec.upstream_km is None
    assert rec.name is None


def test_from_wkt_accepts_legacy_node_strings():
    rec = _rec(start_node="-79.5,43.5", end_node="-79.4,43.6")
    assert rec.start_node == encode_node(-79.5, 43.5)


def test_multilinestring_needs_explicit_nodes():
    wkt = "MULTILINESTRING ((-79.5 43.5, -79.4 43.6), (-79.4 43.6, -79.3 43.7))"
    with pytest.raises(ValueError):
        _rec(wkt=wkt)
    rec = _rec(wkt=wkt, start_node="-79.5,43.5", end_node="-79.3,43.7")
    assert rec.max_lon == -79.3


def test_non_line_geometry_rejected():
    with pytest.raises(ValueError):
        _rec(wkt="POINT (-79.5 43.5)")


def test_bad_fields_rejected():
    with pytest.raises(ValidationError):
        _rec(jurisdiction="Ontario")
    with pytest.raises(ValidationError):
        _rec(length_m=-1)
    with pytest.raises(ValidationError):
        _rec(upstream_km=-0.1)
    with pytest.raises(ValidationError):
        _rec(source="")


def test_inverted_bbox_rejected():
    rec = _rec()
    data = rec.model_dump() | {"min_lon": -79.0, "max_lon": -80.0}
    with pytest.raises(ValidationError):
        HydroSegmentRecord(**data)


def test_works_for_another_jurisdiction():
    rec = _rec(source="FWA", jurisdiction="CA-BC", wkt="LINESTRING (-123.2 49.2, -123.1 49.3)")
    assert rec.jurisdiction == "CA-BC"


# --- table -------------------------------------------------------------------


def test_schema_is_idempotent(db):
    ensure_schema(db)
    ensure_schema(db)
    assert "hydro_segments" in db.table_names()
    assert "hydro_segments_rtree" in db.table_names()


def test_primary_key_is_source_then_id(db):
    info = db.execute("PRAGMA table_info(hydro_segments)").fetchall()
    pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
    assert pk == ["segment_source", "seg_id"]


def test_same_id_in_two_sources_coexists(db):
    _put(db, _rec(seg_id=5, source="OHN"))
    _put(db, _rec(seg_id=5, source="FWA", jurisdiction="CA-BC"))
    assert db.execute("SELECT count(*) FROM hydro_segments").fetchone()[0] == 2


def test_round_trip_through_table(db):
    rec = _rec(
        seg_id=9, name="Credit River", is_waterbody_path=True, tile_key="t1", upstream_km=3.5
    )
    _put(db, rec)
    row = db.execute(
        "SELECT start_node, end_node, name, flow_verified, is_waterbody_path, upstream_km, "
        "tile_key, geom FROM hydro_segments WHERE seg_id = 9"
    ).fetchone()
    assert row[0] == rec.start_node and row[1] == rec.end_node
    assert row[2:7] == ("Credit River", 1, 1, 3.5, "t1")
    assert bytes(row[7]) == rec.geom


def test_upstream_km_nullable(db):
    _put(db, _rec())
    assert db.execute("SELECT upstream_km FROM hydro_segments").fetchone()[0] is None


def test_rtree_finds_by_bbox(db):
    _put(db, _rec(seg_id=1, wkt="LINESTRING (-79.5 43.5, -79.4 43.6)"))
    _put(db, _rec(seg_id=2, wkt="LINESTRING (-123.2 49.2, -123.1 49.3)", source="FWA"))
    assert _in_box(db, -79.6, -79.3, 43.4, 43.7) == {1}
    assert _in_box(db, -123.3, -123.0, 49.0, 49.5) == {2}
    assert _in_box(db, -100, -90, 40, 41) == set()


def test_rtree_follows_upsert_without_orphans(db):
    _put(db, _rec(seg_id=1, wkt="LINESTRING (-79.5 43.5, -79.4 43.6)"))
    _put(db, _rec(seg_id=1, wkt="LINESTRING (-70.5 44.5, -70.4 44.6)"))
    assert _in_box(db, -79.6, -79.3, 43.4, 43.7) == set()
    assert _in_box(db, -70.6, -70.3, 44.4, 44.7) == {1}
    assert db.execute("SELECT count(*) FROM hydro_segments_rtree").fetchone()[0] == 1


def test_rtree_follows_delete(db):
    _put(db, _rec())
    db.execute("DELETE FROM hydro_segments WHERE seg_id = 1")
    assert db.execute("SELECT count(*) FROM hydro_segments_rtree").fetchone()[0] == 0


def test_upstream_km_update_leaves_rtree_alone(db):
    _put(db, _rec())
    db.execute("UPDATE hydro_segments SET upstream_km = 12.0")
    assert _in_box(db, -79.6, -79.3, 43.4, 43.7) == {1}
    assert db.execute("SELECT count(*) FROM hydro_segments_rtree").fetchone()[0] == 1
