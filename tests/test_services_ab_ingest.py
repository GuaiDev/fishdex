"""Regression test for ingest_ab_stocking against the real DB schema.

Guards against the ingested_at bug: ab_ingest.py used to set r["ingested_at"]
before upserting into stocking_records, which has no such column and raised
sqlite3.OperationalError the moment this path was actually exercised (it
never had been, until this adapter was tested live).
"""

from sqlite_utils import Database

from src.storage.database import ensure_schema


def _make_db(tmp_path) -> Database:
    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    return db


def test_ingest_ab_stocking_does_not_crash_on_real_schema(tmp_path, monkeypatch):
    from src.services import ab_ingest

    db = _make_db(tmp_path)
    monkeypatch.setattr(ab_ingest, "get_db", lambda: db)

    fake_rows = [
        {
            "record_id": "AB_2026_1",
            "waterbody_name": "Test Lake",
            "waterbody_code": None,
            "municipality": "Calgary",
            "county": None,
            "lat": 51.05,
            "lng": -114.07,
            "jurisdiction": "CA-AB",
            "species": "Rainbow Trout",
            "species_code": "RNTR",
            "year": 2026,
            "month": None,
            "quantity": 1000,
            "life_stage": "15cm 3N",
            "stocking_purpose": "before June 15th",
            "stocked_at": None,
        }
    ]
    monkeypatch.setattr(
        "src.ingest.jurisdictions.ca_ab.stocking.fetch_stocking_records",
        lambda: fake_rows,
    )

    n = ab_ingest.ingest_ab_stocking()

    assert n == 1
    stored = list(db["stocking_records"].rows)
    assert len(stored) == 1
    assert stored[0]["jurisdiction"] == "CA-AB"


def _fake_segment(ogf_id: int):
    from src.models.hydrology import StreamSegment

    return StreamSegment(
        ogf_id=ogf_id,
        watercourse_type="Stream",
        name=f"AB creek {ogf_id}",
        flow_verified=False,
        permanency="Permanent",
        flow_classification=None,
        stream_order=3,
        length_m=100.0,
        geom_wkt="LINESTRING (-114 51, -114.1 51.1)",
        start_node="-114.0,51.0",
        end_node="-114.1,51.1",
        jurisdiction="CA-AB",
        segment_source="FWMIS",
    )


def test_ab_hydro_ingest_never_overwrites_other_jurisdiction_row(tmp_path, monkeypatch):
    from src.services import ab_ingest

    db = _make_db(tmp_path)
    monkeypatch.setattr(ab_ingest, "get_db", lambda: db)
    db["stream_segments"].insert(
        {
            "ogf_id": 42,
            "watercourse_type": "Stream",
            "name": "Ontario creek",
            "jurisdiction": "CA-ON",
            "segment_source": "OHN",
        },
        pk="ogf_id",
    )
    monkeypatch.setattr(
        "src.ingest.jurisdictions.ca_ab.hydro_network.fetch_watercourses",
        lambda lat, lng, radius_km: [_fake_segment(42)],
    )

    ab_ingest.ingest_ab_hydro_network(51.05, -114.07)

    on_row = db["stream_segments"].get(42)
    assert on_row["name"] == "Ontario creek"
    assert on_row["jurisdiction"] == "CA-ON"
    assert db["stream_segments"].count_where("jurisdiction = ?", ["CA-AB"]) == 1


def test_ab_hydro_ingest_keeps_earlier_areas_and_empty_fetch_wipes_nothing(tmp_path, monkeypatch):
    from src.services import ab_ingest

    db = _make_db(tmp_path)
    monkeypatch.setattr(ab_ingest, "get_db", lambda: db)
    batches = iter([[_fake_segment(1)], [_fake_segment(2)], []])
    monkeypatch.setattr(
        "src.ingest.jurisdictions.ca_ab.hydro_network.fetch_watercourses",
        lambda lat, lng, radius_km: next(batches),
    )

    for _ in range(3):
        ab_ingest.ingest_ab_hydro_network(51.05, -114.07)

    assert db["stream_segments"].count_where("jurisdiction = ?", ["CA-AB"]) == 2
