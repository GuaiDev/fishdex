"""Service entry point for BC-specific data ingest.

Orchestrates the CA-BC adapters:
  - FWA stream network  (ca_bc/hydro_network.py)
  - FISS fish observations (ca_bc/fish_observations.py)
  - FISS stocking extraction (ca_bc/fish_observations.py)
  - NuSEDS salmon escapement (ca_bc/nuseds.py — province-wide, not bbox)
  - BC fishing regulations (ca_bc/regulations.py — province-wide, not bbox)
  - BC EnMoDS water quality (ca_bc/water_quality.py — current-tier results file)

Run by `fishbot ingest-source` / `weekly-ingest` through this folder's plugin.py.
Global sources (iNat, GBIF, WSC, OSM, eBird) are handled by the standard
ingest pipeline and do not need to be called here.
"""

import importlib
import logging
from datetime import datetime

from src.jurisdictions.plugin import AreaContext
from src.services.ingest_runs import record_counts
from src.storage.database import get_db

logger = logging.getLogger(__name__)


def ingest_bc_hydro_network(
    lat: float,
    lon: float,
    radius_km: float = 50.0,
) -> tuple[int, int]:
    """Fetch and store FWA stream segments for a BC location. Returns (seg_count, 0).

    Barriers are not separately indexed in FWA (no equivalent to OHN barrier layer),
    so barrier_count is always 0.
    """
    _fwa = importlib.import_module("src.ingest.jurisdictions.ca_bc.hydro_network")
    db = get_db()

    logger.info(
        "FWA: fetching stream segments — lat=%.4f lon=%.4f radius=%.0fkm", lat, lon, radius_km
    )
    segments = _fwa.fetch_watercourses(lat, lon, radius_km)
    logger.info("FWA: %d segments fetched from WFS", len(segments))

    now = datetime.utcnow().isoformat()

    # Delete only CA-BC rows from the previous ingest for this jurisdiction
    if "stream_segments" in db.table_names():
        db["stream_segments"].delete_where("jurisdiction = ?", ["CA-BC"])

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

    logger.info("FWA: %d segments stored to DB", len(segments))
    record_counts(stored=len(segments))
    return len(segments), 0


def ingest_fiss_observations(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Fetch and store FISS fish observations for a BC location. Returns count stored."""
    from src.storage.observations import upsert_observations

    _fiss = importlib.import_module("src.ingest.jurisdictions.ca_bc.fish_observations")
    db = get_db()

    logger.info(
        "FISS: fetching fish observations — lat=%.4f lng=%.4f radius=%.0fkm", lat, lng, radius_km
    )
    observations = _fiss.fetch_observations(lat, lng, radius_km)
    logger.info("FISS: %d observations fetched from WFS", len(observations))
    if observations:
        upsert_observations(db, observations)
    logger.info("FISS: %d observations stored to DB", len(observations))
    record_counts(stored=len(observations))
    return len(observations)


def ingest_bc_water_quality(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Fetch BC EnMoDS water quality readings near a location. Returns count stored.

    Downloads the current-tier results file once (cached 30 days), filters it to
    radius_km around the point and upserts into water_quality_readings, which the
    water slice of the context layer already reads.
    """
    from src.storage.water_quality import upsert_water_quality_readings

    _wq = importlib.import_module("src.ingest.jurisdictions.ca_bc.water_quality")
    db = get_db()
    logger.info(
        "BC EnMoDS: fetching water quality — lat=%.4f lng=%.4f radius=%.0fkm", lat, lng, radius_km
    )
    readings = _wq.fetch_water_quality_readings(lat, lng, radius_km)
    record_counts(fetched=1, empty=int(not readings))
    if readings:
        upsert_water_quality_readings(db, readings)
    logger.info("BC EnMoDS: %d readings stored", len(readings))
    record_counts(stored=len(readings))
    return len(readings)


def ingest_fiss_stocking(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Extract stocking records from FISS and write to stocking_records table.

    Re-uses the cached WFS response from the FISS observations call — no
    additional HTTP request is made if called after ingest_fiss_observations.
    Returns count stored.
    """
    _fiss = importlib.import_module("src.ingest.jurisdictions.ca_bc.fish_observations")
    db = get_db()
    logger.info("FISS stocking: extracting stocking records — lat=%.4f lng=%.4f", lat, lng)
    rows = _fiss.fetch_stocking_from_fiss(lat, lng, radius_km)
    if rows:
        # stocking_records has no ingested_at column (unlike regulation_chunks) —
        # upsert_all would raise sqlite3.OperationalError if we set one.
        db["stocking_records"].upsert_all(rows, pk="record_id")
    logger.info("FISS stocking: %d stocking records stored", len(rows))
    record_counts(stored=len(rows))
    return len(rows)


def ingest_bc_nuseds() -> int:
    """Download NuSEDS salmon escapement XLSX and store to salmon_escapement table.

    Province-wide (not bbox-scoped). Requires openpyxl.
    Returns record count stored.
    """
    _nuseds = importlib.import_module("src.ingest.jurisdictions.ca_bc.nuseds")
    db = get_db()
    logger.info("NuSEDS: downloading and parsing salmon escapement …")
    rows = _nuseds.fetch_salmon_escapement()
    record_counts(fetched=1, empty=int(not rows))
    if rows:
        now = datetime.utcnow().isoformat()
        for r in rows:
            r["ingested_at"] = now
        db["salmon_escapement"].upsert_all(rows, pk="record_id")
    logger.info("NuSEDS: %d escapement records stored", len(rows))
    record_counts(stored=len(rows))
    return len(rows)


def ingest_bc_regulations() -> int:
    """Download and parse BC fishing regulations PDF. Returns chunk count."""
    _regs = importlib.import_module("src.ingest.jurisdictions.ca_bc.regulations")
    db = get_db()
    logger.info("BC regulations: fetching and parsing PDF …")
    chunks = _regs.fetch_regulations()
    record_counts(fetched=1, empty=int(not chunks))
    if chunks:
        _upsert_regulation_chunks(db, chunks)
    logger.info("BC regulations: %d chunks stored", len(chunks))
    record_counts(stored=len(chunks))
    return len(chunks)


def _upsert_regulation_chunks(db, chunks: list[dict]) -> None:
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


def ingest_bc_data(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> dict[str, int]:
    """Run all BC-specific ingest adapters for a location. Returns counts per source."""
    fwa_segs, fwa_barriers = ingest_bc_hydro_network(lat, lng, radius_km)
    fiss_count = ingest_fiss_observations(lat, lng, radius_km)
    fiss_stocking = ingest_fiss_stocking(lat, lng, radius_km)
    wq_count = ingest_bc_water_quality(lat, lng, radius_km)
    return {
        "fwa_segments": fwa_segs,
        "fwa_barriers": fwa_barriers,
        "fiss_observations": fiss_count,
        "fiss_stocking": fiss_stocking,
        "water_quality_readings": wq_count,
    }


# ── source runners ────────────────────────────────────────────────────────────
# Called by the generic ingest (`fishbot ingest-source`, `weekly-ingest`) with the
# area being run; named in this folder's plugin.py.


def run_hydro_network(ctx: AreaContext) -> int:
    return ingest_bc_hydro_network(ctx.lat, ctx.lng, radius_km=ctx.radius_km)[0]


def run_fish_observations(ctx: AreaContext) -> int:
    return ingest_fiss_observations(ctx.lat, ctx.lng, radius_km=ctx.radius_km)


def run_water_quality(ctx: AreaContext) -> int:
    return ingest_bc_water_quality(ctx.lat, ctx.lng, radius_km=ctx.radius_km)


def run_stocking(ctx: AreaContext) -> int:
    return ingest_fiss_stocking(ctx.lat, ctx.lng, radius_km=ctx.radius_km)


def run_salmon_escapement(ctx: AreaContext) -> int:
    return ingest_bc_nuseds()


def run_regulations(ctx: AreaContext) -> int:
    return ingest_bc_regulations()
