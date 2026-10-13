"""Species range and SAR CRUD via sqlite-utils."""

import json
from typing import Any

from sqlite_utils.db import Database, NotFoundError

from src.models.species_range import SpeciesAtRisk, SpeciesRange

_PROTECTED_STATUSES = {"Threatened", "Endangered"}
_AT_RISK_STATUSES = {"Threatened", "Endangered", "Special Concern", "Extirpated"}

# Fields where an existing (previously ingested) value should win over a new
# jurisdiction's null/missing value — see upsert_species_ranges_merged.
_FILL_IF_MISSING_FIELDS = (
    "scientific_name",
    "habitat_notes",
    "sara_status",
    "ontario_status",
    "cosewic_status",
    "fishing_notes",
)


def upsert_species_ranges(db: Database, ranges: list[SpeciesRange]) -> None:
    rows = [_to_row(r) for r in ranges]
    db["species_ranges"].upsert_all(rows, pk="species")


def upsert_species_ranges_merged(db: Database, rows: list[dict[str, Any]]) -> None:
    """Upsert raw dict rows into species_ranges, merging with any existing row
    for the same species (common name — the table's PK) instead of overwriting.

    species_ranges is shared across every jurisdiction adapter (ON, QC, ...),
    keyed on common name since many species (Largemouth Bass, Walleye, Brook
    Trout, ...) are shared across provinces. A blind upsert_all(pk="species")
    lets a later jurisdiction's ingest silently wipe out an earlier
    jurisdiction's jurisdictions_present list, status fields, and notes —
    confirmed this actually happens: ingesting QC's MELCCFP data over an
    existing multi-jurisdiction "Largemouth Bass" row (CA-ON + 6 US states)
    would have collapsed jurisdictions_present down to just ["CA-QC"] and
    nulled out ontario_status/habitat_notes.

    Use this (not upsert_species_ranges, which is ON-specific and takes
    SpeciesRange model instances) for any adapter returning raw row dicts.
    """
    table = db["species_ranges"]
    for row in rows:
        existing = None
        if table.exists():
            try:
                existing = table.get(row["species"])
            except NotFoundError:
                existing = None
        merged = _merge_row(dict(existing), row) if existing else row
        table.upsert(merged, pk="species")


def _merge_row(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)

    existing_j = set(json.loads(existing.get("jurisdictions_present") or "[]"))
    incoming_j = set(json.loads(incoming.get("jurisdictions_present") or "[]"))
    merged["jurisdictions_present"] = json.dumps(sorted(existing_j | incoming_j))

    existing_range = existing.get("general_range") or ""
    incoming_range = incoming.get("general_range") or ""
    if incoming_range and incoming_range not in existing_range:
        merged["general_range"] = "; ".join(p for p in (existing_range, incoming_range) if p)

    for field in _FILL_IF_MISSING_FIELDS:
        if not merged.get(field) and incoming.get(field):
            merged[field] = incoming[field]

    merged["last_updated"] = incoming.get("last_updated") or merged.get("last_updated")
    return merged


def query_species_range(db: Database, species: str) -> SpeciesRange | None:
    term = species.strip().lower()
    rows = list(
        db["species_ranges"].rows_where(
            "LOWER(species) LIKE ? OR LOWER(scientific_name) LIKE ?",
            [f"%{term}%", f"%{term}%"],
            limit=1,
        )
    )
    if not rows:
        return None
    return _row_to_range(rows[0])


def query_species_range_exact(
    db: Database, common_name: str | None = None, scientific_name: str | None = None
) -> SpeciesRange | None:
    """One species by its exact common name, else by its scientific name.

    `query_species_range` is a substring search meant for typed questions, so
    "dace" finds whichever dace row comes first. A caller deciding whether a
    recorded species is protected needs the row for *that* species or none:
    a near-miss would clear or flag the wrong fish. A subspecies name falls
    back to its genus and species.
    """
    if "species_ranges" not in db.table_names():
        return None
    if common_name and common_name.strip():
        rows = list(
            db["species_ranges"].rows_where(
                "LOWER(species) = ?", [common_name.strip().lower()], limit=1
            )
        )
        if rows:
            return _row_to_range(rows[0])
    if scientific_name and scientific_name.strip():
        words = scientific_name.strip().lower().split()
        for candidate in dict.fromkeys([" ".join(words), " ".join(words[:2])]):
            rows = list(
                db["species_ranges"].rows_where("LOWER(scientific_name) = ?", [candidate], limit=1)
            )
            if rows:
                return _row_to_range(rows[0])
    return None


def query_species_ranges_for_jurisdiction(db: Database, jurisdiction: str) -> list[SpeciesRange]:
    """Return every species present in the given jurisdiction (e.g. "CA-ON")."""
    rows = list(
        db["species_ranges"].rows_where(
            "jurisdictions_present LIKE ?", [f"%{jurisdiction}%"], order_by="species"
        )
    )
    return [_row_to_range(r) for r in rows]


def query_deduped_species_ranges_for_jurisdiction(
    db: Database, jurisdiction: str
) -> list[SpeciesRange]:
    """Same as query_species_ranges_for_jurisdiction, but collapses taxonomic
    alias rows (e.g. "Northern Largemouth Bass" / M. nigricans alongside
    "Largemouth Bass" / M. salmoides — see species_family.canonical_scientific_name)
    to one row each. Ordered by species name, so the non-alias row is kept.

    This is the single source of truth for "the real, non-duplicated list of
    species in this region" — used by the FishDex collection screen, the NL
    trip parser's species grounding, and photo-based species suggestion, so
    none of them can drift into showing a confusing duplicate species.
    """
    from src.services.species_family import canonical_scientific_name

    seen: set[str] = set()
    deduped = []
    for sr in query_species_ranges_for_jurisdiction(db, jurisdiction):
        if not sr.scientific_name:
            deduped.append(sr)
            continue
        sci = canonical_scientific_name(sr.scientific_name)
        if sci in seen:
            continue
        seen.add(sci)
        deduped.append(sr)
    return deduped


def query_sar_species(db: Database, jurisdiction: str | None = None) -> list[SpeciesAtRisk]:
    status_placeholders = ",".join("?" * len(_AT_RISK_STATUSES))
    params: list[Any] = list(_AT_RISK_STATUSES)

    where = f"(sara_status IN ({status_placeholders}) OR ontario_status IN ({status_placeholders}))"
    params = list(_AT_RISK_STATUSES) + list(_AT_RISK_STATUSES)

    if jurisdiction:
        where += " AND jurisdictions_present LIKE ?"
        params.append(f"%{jurisdiction}%")

    rows = list(db["species_ranges"].rows_where(where, params))
    return [_row_to_sar(r) for r in rows]


def is_species_at_risk(db: Database, species: str) -> bool:
    sr = query_species_range(db, species)
    if sr is None:
        return False
    return (sr.sara_status in _PROTECTED_STATUSES) or (sr.ontario_status in _PROTECTED_STATUSES)


def _to_row(r: SpeciesRange) -> dict[str, Any]:
    return {
        "species": r.species,
        "scientific_name": r.scientific_name,
        "native_to_ontario": int(r.native_to_ontario),
        "native_to_great_lakes": int(r.native_to_great_lakes),
        "introduced": int(r.introduced),
        "extirpated_from_ontario": int(r.extirpated_from_ontario),
        "general_range": r.general_range,
        "habitat_notes": r.habitat_notes,
        "jurisdictions_present": json.dumps(r.jurisdictions_present),
        "sara_status": r.sara_status,
        "ontario_status": r.ontario_status,
        "cosewic_status": r.cosewic_status,
        "fishing_notes": r.fishing_notes,
        "last_updated": r.last_updated.isoformat(),
        "status_source": r.status_source,
        "status_source_url": r.status_source_url,
        "status_last_checked_at": (
            r.status_last_checked_at.isoformat() if r.status_last_checked_at else None
        ),
        "status_assessed_on": r.status_assessed_on,
    }


def _row_to_range(row: dict[str, Any]) -> SpeciesRange:
    d = dict(row)
    d["native_to_ontario"] = bool(d["native_to_ontario"])
    d["native_to_great_lakes"] = bool(d["native_to_great_lakes"])
    d["introduced"] = bool(d["introduced"])
    d["extirpated_from_ontario"] = bool(d["extirpated_from_ontario"])
    d["jurisdictions_present"] = json.loads(d["jurisdictions_present"] or "[]")
    return SpeciesRange.model_validate(d)


def _row_to_sar(row: dict[str, Any]) -> SpeciesAtRisk:
    sara = row.get("sara_status") or ""
    ontario = row.get("ontario_status")
    is_protected = sara in _PROTECTED_STATUSES or (
        ontario is not None and ontario in _PROTECTED_STATUSES
    )

    # Use the more severe of the two statuses as the canonical sara_status for the SAR model
    effective_sara = sara if sara else (ontario or "No Status")
    if effective_sara not in {
        "Not at Risk",
        "Special Concern",
        "Threatened",
        "Endangered",
        "Extirpated",
        "No Status",
    }:
        effective_sara = "No Status"

    guidance = row.get("fishing_notes") or (
        "Release immediately. Do not target. Report sightings to MNRF at 1-877-TIPS-MNR."
    )

    return SpeciesAtRisk(
        species=row["species"],
        scientific_name=row.get("scientific_name"),
        sara_status=effective_sara,  # type: ignore[arg-type]
        ontario_status=ontario,  # type: ignore[arg-type]
        is_protected=is_protected,
        handling_guidance=guidance,
        report_url=None,
    )
