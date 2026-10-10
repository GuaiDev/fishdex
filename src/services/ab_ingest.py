"""Service entry point for Alberta-specific data ingest.

Orchestrates CA-AB adapters:
  - AB stocking records (planned stocking XLSX from Open Alberta)
  - AB regulations (stub — see ca_ab/regulations.py)
  - AB water quality (stub — no public API as of 2026)
  - AB hydro network (FWMIS Simplified Hydro Arcs via Geospatial Alberta)
  - AB fish observations (FWMIS waterbody species presence SPECIES_PRES)

Global sources (iNat, GBIF, WSC, OSM) are handled by the standard pipeline.
NuSEDS salmon escapement is BC-only (not applicable to AB).
"""

import importlib
import logging
from datetime import datetime

from src.storage.database import get_db

logger = logging.getLogger(__name__)

# stream_segments has one primary key (ogf_id) shared by every jurisdiction, and
# FWMIS OBJECTIDs are small integers in the same range as OHN/BC ids. Offsetting
# AB ids into a range no other source reaches guarantees replace=True can only
# ever overwrite a previous AB row, with no change to shared schema or readers.
AB_SEGMENT_ID_OFFSET = 10**12


def ingest_ab_stocking() -> int:
    """Download and store Alberta planned stocking XLSX. Returns count."""
    _mod = importlib.import_module("src.ingest.jurisdictions.ca_ab.stocking")
    db = get_db()
    logger.info("AB stocking: fetching records …")
    rows = _mod.fetch_stocking_records()
    if rows:
        # stocking_records has no ingested_at column (unlike regulation_chunks) —
        # upsert_all would raise sqlite3.OperationalError if we set one.
        db["stocking_records"].upsert_all(rows, pk="record_id")
    logger.info("AB stocking: %d records stored", len(rows))
    return len(rows)


def ingest_ab_hydro_network(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> tuple[int, int]:
    """Fetch and store FWMIS stream segments for an AB location. Returns (seg_count, 0).

    Barriers are not separately indexed in FWMIS (no equivalent to the OHN
    barrier layer), so barrier_count is always 0.
    """
    _mod = importlib.import_module("src.ingest.jurisdictions.ca_ab.hydro_network")
    db = get_db()

    logger.info(
        "FWMIS: fetching stream segments — lat=%.4f lng=%.4f radius=%.0fkm", lat, lng, radius_km
    )
    segments = _mod.fetch_watercourses(lat, lng, radius_km)
    logger.info("FWMIS: %d segments fetched from FeatureServer", len(segments))

    now = datetime.utcnow().isoformat()

    seg_rows = [
        {
            "ogf_id": AB_SEGMENT_ID_OFFSET + s.ogf_id,
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

    logger.info("FWMIS: %d segments stored to DB", len(segments))
    return len(segments), 0


def ingest_ab_fish_observations(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Fetch and store FWMIS waterbody species presence for an AB location.

    Species come from SPECIES_PRES on fwmis_hydro_polygons (layer 1) — authoritative
    survey presence per waterbody, stored as observations (source='FWMIS'). A
    'NO FISH SAMPLED TO DATE' value is treated as unsampled, not absence.
    Returns count stored.
    """
    from src.storage.observations import upsert_observations

    _mod = importlib.import_module("src.ingest.jurisdictions.ca_ab.fish_observations")
    db = get_db()

    logger.info(
        "FWMIS: fetching fish presence — lat=%.4f lng=%.4f radius=%.0fkm", lat, lng, radius_km
    )
    observations = _mod.fetch_waterbody_presence(lat, lng, radius_km)
    logger.info("FWMIS: %d observations fetched from FeatureServer", len(observations))
    if observations:
        upsert_observations(db, observations)
    logger.info("FWMIS: %d observations stored to DB", len(observations))
    return len(observations)


def ingest_ab_regulations() -> int:
    """Fetch Alberta fishing regulations. Returns chunk count (currently 0 — stub)."""
    _mod = importlib.import_module("src.ingest.jurisdictions.ca_ab.regulations")
    db = get_db()
    logger.info("AB regulations: fetching regulation chunks …")
    chunks = _mod.fetch_regulations()
    if chunks:
        _upsert_regulation_chunks(db, chunks)
    logger.info("AB regulations: %d chunks stored", len(chunks))
    return len(chunks)


def ingest_ab_water_quality(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Fetch Alberta water quality readings. Returns count (currently 0 — stub)."""
    _mod = importlib.import_module("src.ingest.jurisdictions.ca_ab.water_quality")
    logger.info(
        "AB water quality: fetching — lat=%.4f lng=%.4f radius=%.0fkm",
        lat, lng, radius_km,
    )
    readings = _mod.fetch_water_quality_readings(lat, lng, radius_km)
    if readings:
        db = get_db()
        db["water_quality_readings"].upsert_all(readings, pk="record_id")
    logger.info("AB water quality: %d readings stored", len(readings))
    return len(readings)


def ingest_ab_data(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> dict[str, int]:
    """Run all Alberta-specific ingest adapters. Returns counts per source."""
    hydro_segs, hydro_barriers = ingest_ab_hydro_network(lat, lng, radius_km)
    fish_obs = ingest_ab_fish_observations(lat, lng, radius_km)
    stocking = ingest_ab_stocking()
    regulations = ingest_ab_regulations()
    wq = ingest_ab_water_quality(lat, lng, radius_km)
    return {
        "ab_hydro_segments": hydro_segs,
        "ab_hydro_barriers": hydro_barriers,
        "ab_fish_observations": fish_obs,
        "ab_stocking": stocking,
        "ab_regulations": regulations,
        "ab_water_quality": wq,
    }


def _upsert_regulation_chunks(db, chunks: list[dict]) -> None:
    """Write regulation chunks; add zone_name if column exists."""
    for chunk in chunks:
        row = {
            "zone": chunk["zone"],
            "jurisdiction": chunk["jurisdiction"],
            "regulation_year": chunk["regulation_year"],
            "raw_text": chunk["raw_text"],
            "char_count": chunk["char_count"],
            "source_url": chunk.get("source_url", ""),
            "ingested_at": chunk.get("ingested_at", datetime.utcnow().isoformat()),
        }
        if chunk.get("zone_name"):
            row["zone_name"] = chunk["zone_name"]
        db["regulation_chunks"].upsert(row, pk=["zone", "jurisdiction", "regulation_year"])
