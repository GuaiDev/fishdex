"""MNRF regulations service — agent and CLI interface for Ontario fishing regulations."""

import json
import logging
import re

from src.services.ingest_runs import record_counts
from src.storage.database import get_db
from src.storage.regulations import (
    count_regulation_chunks,
    get_regulation_chunk,
    upsert_regulation_chunks,
)

# Rough FMZ bounding boxes for lat/lng → zone estimation.
# Format: (zone, min_lat, max_lat, min_lng, max_lng)
# Ordered most-specific first. Overlap is intentional — first match wins.
# These are approximations; zone boundaries are irregular polygons.
# _FMZ_BOXES removed. It was 20 overlapping lat/lng rectangles resolved by
# first match, so the answer depended on list order — and several were
# mislabelled: the box commented "GTA lake shore" returned zone 5 (Rainy River)
# and the box numbered 20 covered James Bay when FMZ 20 is Lake Ontario.
# Zones now come from real MNRF polygons via point-in-polygon lookup.
_MAX_SPECIES_CONTEXT = 3000  # chars returned when species filter applied
_MAX_OVERVIEW = 2000  # chars returned for zone overview (no species filter)

logger = logging.getLogger(__name__)


def ingest_regulations() -> int:
    """Download and parse MNRF regulations PDF. Returns number of zone chunks stored."""
    from src.ingest.jurisdictions.ca_on.regulations import (
        download_regulations_pdf,
        extract_zone_chunks,
    )

    pdf_path = download_regulations_pdf()
    chunks = extract_zone_chunks(pdf_path)
    record_counts(fetched=1, empty=int(not chunks))
    if not chunks:
        return 0
    db = get_db()
    upsert_regulation_chunks(db, chunks)
    record_counts(stored=len(chunks))
    return len(chunks)


def ingest_fmz_boundaries() -> int:
    """Download and store the MNRF FMZ polygon layer. Returns rows stored."""
    from src.ingest.jurisdictions.ca_on.fmz_boundaries import fetch_fmz_boundaries
    from src.storage.fmz_boundaries import upsert_fmz_boundaries

    rows = fetch_fmz_boundaries()
    record_counts(fetched=1, empty=int(not rows))
    if not rows:
        logger.error("No FMZ boundaries fetched — zone resolution will fail closed")
        return 0
    written = upsert_fmz_boundaries(get_db(), rows)
    record_counts(stored=written, skipped=len(rows) - written)
    return written


def get_regulations_for_agent(
    zone: int | None = None,
    species: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> str:
    """Return regulations text for the specified FMZ zone as a JSON string."""
    db = get_db()
    zone_name = None
    zone_source = "caller-supplied"

    if zone is None:
        if lat is None or lng is None:
            return json.dumps(
                {
                    "empty_reason": "no_location_given",
                    "error": (
                        "Ontario is divided into 20 Fisheries Management Zones. "
                        "Provide 'zone' (1-20) or lat/lng coordinates."
                    ),
                }
            )
        resolution = _resolve_zone(db, lat, lng)
        if not resolution.resolved:
            # Fail closed. A confident answer from the wrong zone is the
            # dangerous outcome here, not the absence of an answer.
            return json.dumps(
                {
                    "empty_reason": resolution.empty_reason,
                    "error": resolution.detail,
                    "regulations_withheld": True,
                    "note": (
                        "No regulations are returned for an unresolved location. "
                        "Supply the FMZ number directly if you know it."
                    ),
                }
            )
        zone = resolution.zone
        zone_name = resolution.zone_name
        zone_source = "point-in-polygon against the MNRF FMZ layer"

    if "regulation_chunks" not in db.table_names() or count_regulation_chunks(db) == 0:
        return json.dumps(
            {
                "zone": zone,
                "error": (
                    "Regulations database is empty. Run `make ingest` to download and "
                    "parse the MNRF Recreational Fishing Regulations Summary PDF."
                ),
            }
        )

    chunk = get_regulation_chunk(db, zone)
    if chunk is None:
        return json.dumps(
            {
                "zone": zone,
                "error": (
                    f"No regulations found for FMZ {zone}. "
                    "Run `make ingest` to populate the database."
                ),
            }
        )

    if species:
        text, truncated = _extract_species_context(chunk.raw_text, species)
    else:
        text = chunk.raw_text[:_MAX_OVERVIEW]
        truncated = len(chunk.raw_text) > _MAX_OVERVIEW

    result: dict = {
        "zone": chunk.zone,
        "regulation_year": chunk.regulation_year,
        "jurisdiction": chunk.jurisdiction,
        "species_query": species,
        "text": text,
        "source_url": chunk.source_url,
        "disclaimer": (
            "Always verify limits, seasons, and slot sizes against the current MNRF "
            "Recreational Fishing Regulations Summary before fishing. "
            "Specific waterbodies may have special orders that override zone defaults."
        ),
    }
    if truncated:
        result["truncated"] = True
        result["truncation_note"] = (
            "Text was truncated. Full regulations available in the MNRF PDF."
        )
    result["zone_name"] = zone_name
    result["zone_source"] = zone_source

    # Province-wide rules travel with every zone answer. They apply everywhere,
    # so retrieval must not depend on the reader guessing which FMZ chunk they
    # were filed under — the bait rules were previously reachable only via
    # FMZ 12, by accident.
    result["province_wide"] = _province_wide_sections(db, species)

    return json.dumps(result)


def _province_wide_sections(db, species: str | None) -> list[dict]:
    """Rules that apply across Ontario regardless of zone."""
    from src.storage.regulations import get_province_wide_chunks

    out = []
    for row in get_province_wide_chunks(db):
        text = row.get("raw_text") or ""
        if species:
            extracted, truncated = _extract_species_context(text, species)
            if not extracted:
                # Keep the section listed even with no species hit, so the
                # reader knows province-wide rules exist and were consulted.
                extracted, truncated = text[:_MAX_OVERVIEW], len(text) > _MAX_OVERVIEW
        else:
            extracted, truncated = text[:_MAX_OVERVIEW], len(text) > _MAX_OVERVIEW
        out.append(
            {
                "section": row.get("section"),
                "applies": "all of Ontario",
                "text": extracted,
                "truncated": truncated,
                "char_count": row.get("char_count"),
            }
        )
    return out


def _resolve_zone(db, lat: float, lng: float):
    """Locate a point in a real FMZ polygon. Never guesses."""
    from src.storage.fmz_boundaries import resolve_zone

    return resolve_zone(db, lat, lng)


def _extract_species_context(text: str, species: str) -> tuple[str, bool]:
    """Find all mentions of the species in the text and return surrounding context.

    Returns (extracted_text, truncated_flag).
    """
    pattern = re.compile(re.escape(species), re.IGNORECASE)
    matches = list(pattern.finditer(text))

    if not matches:
        # Species not found by name — return zone overview so LLM can reason
        overview = text[:_MAX_OVERVIEW]
        note = (
            f"\n\n[Note: '{species}' not found by exact name in this zone's text. "
            "The species may appear under a different name or may not have special "
            "rules listed (general limits apply). Full zone overview shown above.]"
        )
        return overview + note, len(text) > _MAX_OVERVIEW

    window = 600  # chars of context around each match
    snippets: list[str] = []
    seen_ranges: list[tuple[int, int]] = []

    for m in matches[:5]:  # cap at 5 occurrences
        start = max(0, m.start() - window)
        end = min(len(text), m.end() + window)
        # Skip if this range substantially overlaps one already added
        if any(abs(start - s) < window for s, _ in seen_ranges):
            continue
        seen_ranges.append((start, end))
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet = snippet + "…"
        snippets.append(snippet)

    combined = "\n\n---\n\n".join(snippets)
    truncated = len(combined) > _MAX_SPECIES_CONTEXT
    return combined[:_MAX_SPECIES_CONTEXT], truncated
