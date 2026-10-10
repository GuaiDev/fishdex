"""Service entry point for Quebec-specific data ingest.

Orchestrates CA-QC adapters:
  - QC species ranges (MELCCFP GeoJSON)
  - QC regulations (stub — see ca_qc/regulations.py)
  - QC water quality (MELCCFP Réseau-rivières, via Données Québec)

Global sources (iNat, GBIF, WSC, OSM) are handled by the standard pipeline.
"""

import importlib
import logging
from datetime import datetime

from src.storage.database import get_db

logger = logging.getLogger(__name__)


def ingest_qc_species_ranges() -> int:
    """Download and store QC MELCCFP freshwater fish ranges. Returns count.

    Uses the merge-safe upsert (not a blind upsert_all) since species_ranges
    is shared across jurisdictions and keyed on common name — many QC species
    (Largemouth Bass, Walleye, ...) already have an existing CA-ON/US row.
    """
    from src.storage.species_ranges import upsert_species_ranges_merged

    _mod = importlib.import_module("src.ingest.jurisdictions.ca_qc.species_ranges")
    db = get_db()
    logger.info("QC species ranges: fetching …")
    rows = _mod.fetch_species_ranges()
    if rows:
        upsert_species_ranges_merged(db, rows)
    logger.info("QC species ranges: %d records stored", len(rows))
    return len(rows)


def ingest_qc_regulations() -> int:
    """Fetch Quebec fishing regulations. Returns chunk count (currently 0 — stub)."""
    _mod = importlib.import_module("src.ingest.jurisdictions.ca_qc.regulations")
    db = get_db()
    logger.info("QC regulations: fetching regulation chunks …")
    chunks = _mod.fetch_regulations()
    if chunks:
        _upsert_regulation_chunks(db, chunks)
    logger.info("QC regulations: %d chunks stored", len(chunks))
    return len(chunks)


def ingest_qc_water_quality(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> int:
    """Fetch and store Quebec river water quality readings. Returns count."""
    _mod = importlib.import_module("src.ingest.jurisdictions.ca_qc.water_quality")
    logger.info(
        "QC water quality: fetching — lat=%.4f lng=%.4f radius=%.0fkm",
        lat,
        lng,
        radius_km,
    )
    readings = _mod.fetch_water_quality_readings(lat, lng, radius_km)
    if readings:
        from src.storage.water_quality import upsert_water_quality_readings

        upsert_water_quality_readings(get_db(), readings)
    logger.info("QC water quality: %d readings stored", len(readings))
    return len(readings)


def ingest_qc_data(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> dict[str, int]:
    """Run all Quebec-specific ingest adapters. Returns counts per source."""
    species_ranges = ingest_qc_species_ranges()
    regulations = ingest_qc_regulations()
    wq = ingest_qc_water_quality(lat, lng, radius_km)
    return {
        "qc_species_ranges": species_ranges,
        "qc_regulations": regulations,
        "qc_water_quality": wq,
    }


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
