"""Service entry point for Manitoba-specific data ingest.

Orchestrates the CA-MB adapters:
  - MB stocking records (ca_mb/stocking.py — ArcGIS FeatureServer)
  - MB WaLLAS stream network (ca_mb/hydro_network.py — ArcGIS MapServer)

Called from the /ingest/data-mb API endpoint or `weekly-ingest`.
Global sources (iNat, GBIF, WSC, OSM, eBird) are handled by the standard
ingest pipeline and are not called here. MB water quality and fish
observations are stubs (no usable machine-readable source — see ca_mb/).
"""

import importlib
import logging
from datetime import datetime

from src.storage.database import get_db

logger = logging.getLogger(__name__)


def ingest_mb_hydro_network(
    lat: float,
    lon: float,
    radius_km: float = 50.0,
) -> tuple[int, int]:
    """Fetch and store WaLLAS stream segments. Returns (seg_count, 0).

    Barriers are not published in the WaLLAS watercourse layer, so
    barrier_count is always 0.
    """
    _wallas = importlib.import_module("src.ingest.jurisdictions.ca_mb.hydro_network")
    db = get_db()

    logger.info(
        "WaLLAS: fetching stream segments — lat=%.4f lon=%.4f radius=%.0fkm",
        lat,
        lon,
        radius_km,
    )
    segments = _wallas.fetch_watercourses(lat, lon, radius_km)
    logger.info("WaLLAS: %d segments fetched", len(segments))

    now = datetime.utcnow().isoformat()
    if "stream_segments" in db.table_names():
        db["stream_segments"].delete_where("jurisdiction = ?", ["CA-MB"])

    seg_rows = [
        {
            "ogf_id": s.ogf_id,
            "watercourse_type": s.watercourse_type,
            "name": s.name,
            "flow_verified": int(s.flow_verified),
            "permanency": s.permanency,
            "flow_classification": s.flow_classification,
            "stream_order": s.stream_order,
            "length_m": s.length_m,
            "geom_wkt": s.geom_wkt,
            "start_node": s.start_node,
            "end_node": s.end_node,
            "jurisdiction": s.jurisdiction,
            "segment_source": s.segment_source,
            "ingested_at": now,
        }
        for s in segments
    ]
    if seg_rows:
        db["stream_segments"].insert_all(seg_rows, pk="ogf_id", replace=True)

    logger.info("WaLLAS: %d segments stored to DB", len(segments))
    return len(segments), 0


def ingest_mb_stocking(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Fetch and store MB stocking records near a location. Returns count stored."""
    _mb = importlib.import_module("src.ingest.jurisdictions.ca_mb.stocking")
    db = get_db()

    logger.info("MB stocking: fetching — lat=%.4f lng=%.4f radius=%.0fkm", lat, lng, radius_km)
    rows = _mb.fetch_stocking_records(lat, lng, radius_km)
    if rows:
        # stocking_records has no ingested_at column — see ca_ab/stocking.py note.
        db["stocking_records"].upsert_all(rows, pk="record_id")
    logger.info("MB stocking: %d records stored", len(rows))
    return len(rows)


def ingest_mb_data(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> dict[str, int]:
    """Run all Manitoba-specific ingest adapters. Returns counts per source."""
    segs, barriers = ingest_mb_hydro_network(lat, lng, radius_km)
    stocking = ingest_mb_stocking(lat, lng, radius_km)
    return {
        "mb_wallas_segments": segs,
        "mb_wallas_barriers": barriers,
        "mb_stocking": stocking,
    }
