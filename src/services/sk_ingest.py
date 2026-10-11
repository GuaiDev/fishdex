"""Service entry point for Saskatchewan-specific data ingest.

Orchestrates the CA-SK adapters:
  - SK stream network (ca_sk/hydro_network.py — SaskGIS ArcGIS MapServer 121)
  - SK fish observations (ca_sk/fish_observations.py — SK Fish Atlas)
  - SK stocking (ca_sk/stocking.py — SK Fish Stocking History)
  - SK water quality (ca_sk/water_quality.py — Long Term Water Chemistry)

Called from the /ingest/data-sk API endpoint or `weekly-ingest`.
Global sources (iNat, GBIF, WSC, OSM, eBird) are handled by the standard
ingest pipeline and are not called here.
"""

import importlib
import logging
from datetime import datetime

from src.storage.database import get_db

logger = logging.getLogger(__name__)

SK_OGF_ID_OFFSET = 30000000000


def ingest_sk_hydro_network(
    lat: float,
    lon: float,
    radius_km: float = 50.0,
) -> tuple[int, int]:
    """Fetch and store SaskGIS stream segments. Returns (seg_count, 0).

    Barriers are not published in this layer, so barrier_count is always 0.
    """
    _sk = importlib.import_module("src.ingest.jurisdictions.ca_sk.hydro_network")
    db = get_db()

    logger.info(
        "SK hydro: fetching stream segments — lat=%.4f lon=%.4f radius=%.0fkm",
        lat,
        lon,
        radius_km,
    )
    segments = _sk.fetch_watercourses(lat, lon, radius_km)
    logger.info("SK hydro: %d segments fetched", len(segments))

    now = datetime.utcnow().isoformat()
    seg_rows = [
        {
            "ogf_id": SK_OGF_ID_OFFSET + s.ogf_id,
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

    logger.info("SK hydro: %d segments stored to DB", len(segments))
    return len(segments), 0


def ingest_sk_fish_observations(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Fetch and store SK Fish Atlas observations. Returns count stored."""
    from src.storage.observations import upsert_observations

    _sk = importlib.import_module("src.ingest.jurisdictions.ca_sk.fish_observations")
    db = get_db()

    logger.info(
        "SK Fish Atlas: fetching observations — lat=%.4f lng=%.4f radius=%.0fkm",
        lat,
        lng,
        radius_km,
    )
    observations = _sk.fetch_observations(lat, lng, radius_km)
    if observations:
        upsert_observations(db, observations)
    logger.info("SK Fish Atlas: %d observations stored", len(observations))
    return len(observations)


def ingest_sk_stocking(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Fetch and store SK stocking records. Returns count stored."""
    _sk = importlib.import_module("src.ingest.jurisdictions.ca_sk.stocking")
    db = get_db()

    logger.info("SK stocking: fetching — lat=%.4f lng=%.4f radius=%.0fkm", lat, lng, radius_km)
    rows = _sk.fetch_stocking_records(lat, lng, radius_km)
    if rows:
        db["stocking_records"].upsert_all(rows, pk="record_id")
    logger.info("SK stocking: %d records stored", len(rows))
    return len(rows)


def ingest_sk_water_quality(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Fetch and store SK long-term water chemistry readings. Returns count stored."""
    from src.storage.water_quality import upsert_water_quality_readings

    _sk = importlib.import_module("src.ingest.jurisdictions.ca_sk.water_quality")
    db = get_db()

    logger.info("SK water quality: fetching — lat=%.4f lng=%.4f radius=%.0fkm", lat, lng, radius_km)
    readings = _sk.fetch_water_quality_readings(lat, lng, radius_km)
    if readings:
        upsert_water_quality_readings(db, readings)
    logger.info("SK water quality: %d readings stored", len(readings))
    return len(readings)


def ingest_sk_data(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> dict[str, int]:
    """Run all Saskatchewan-specific ingest adapters. Returns counts per source."""
    segs, barriers = ingest_sk_hydro_network(lat, lng, radius_km)
    observations = ingest_sk_fish_observations(lat, lng, radius_km)
    stocking = ingest_sk_stocking(lat, lng, radius_km)
    wq = ingest_sk_water_quality(lat, lng, radius_km)
    return {
        "sk_hydro_segments": segs,
        "sk_hydro_barriers": barriers,
        "sk_fish_observations": observations,
        "sk_stocking": stocking,
        "sk_water_quality": wq,
    }
