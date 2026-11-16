"""MNRF stocking service — agent and CLI interface for stocking records."""

import json
from collections import defaultdict
from datetime import datetime

from src.services.ingest_runs import record_counts
from src.storage.database import get_db
from src.storage.stocking import query_stocking, upsert_stocking_records

# Life stages that indicate put-and-take stocking (catchable-size fish)
_PUT_AND_TAKE_STAGES = frozenset({"yearling", "yearlings", "adult", "adults", "catchable"})
# Life stages that indicate early-stage stocking (may self-sustain)
_EARLY_STAGES = frozenset({"fry", "fingerling", "fingerlings", "fry or fingerling"})
_CURRENT_YEAR = datetime.now().year


def ingest_stocking_data() -> int:
    """Download and upsert MNRF stocking CSV. Returns number of records stored."""
    from src.ingest.jurisdictions.ca_on.stocking import (
        download_stocking_data,
        parse_stocking_records,
    )

    csv_path = download_stocking_data()
    records = parse_stocking_records(csv_path)
    record_counts(fetched=1, empty=int(not records))
    if not records:
        return 0
    db = get_db()
    upsert_stocking_records(db, records)
    record_counts(stored=len(records))
    return len(records)


def get_stocking_for_agent(
    waterbody_name: str | None = None,
    species: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 50,
    year_from: int | None = None,
    max_waterbodies: int = 10,
) -> str:
    """Return JSON stocking history for matching water bodies."""
    db = get_db()
    records = query_stocking(
        db,
        waterbody_name=waterbody_name,
        species=species,
        lat=lat,
        lng=lng,
        radius_km=radius_km if (lat is not None and lng is not None) else None,
        year_from=year_from,
    )

    if not records:
        note = "No MNRF stocking records found"
        if waterbody_name:
            note += f" for '{waterbody_name}'"
        if species:
            note += f" (species: {species})"
        note += (
            ". This may be a wild fishery, an unstocked water body, "
            "or a gap in MNRF records. Run `make ingest` if the database is empty."
        )
        return json.dumps(
            {
                "query": {
                    "waterbody_name": waterbody_name,
                    "species": species,
                    "year_from": year_from,
                },
                "total_events": 0,
                "waterbodies": [],
                "note": note,
            }
        )

    # Group by waterbody name
    by_waterbody: dict[str, list] = defaultdict(list)
    for r in records:
        by_waterbody[r.waterbody_name].append(r)

    waterbodies = []
    for wb_name, wb_records in by_waterbody.items():
        years = [r.year for r in wb_records]
        most_recent_year = max(years)
        quantities = [r.quantity for r in wb_records if r.quantity is not None]
        total_quantity = sum(quantities) if quantities else None
        species_stocked = sorted({r.species for r in wb_records})
        life_stages = sorted({r.life_stage for r in wb_records if r.life_stage})

        is_put_and_take = _compute_put_and_take(wb_records)
        wild_population_likely = _compute_wild_likely(wb_records, most_recent_year, life_stages)
        stocking_note = _build_stocking_note(
            wb_name,
            wb_records,
            is_put_and_take,
            wild_population_likely,
            most_recent_year,
            species_stocked,
            life_stages,
            total_quantity,
        )

        sorted_records = sorted(wb_records, key=lambda x: x.year, reverse=True)
        events = [
            {
                "year": r.year,
                "species": r.species,
                "life_stage": r.life_stage,
                "quantity": r.quantity,
                "waterbody_code": r.waterbody_code,
            }
            for r in sorted_records[:5]
        ]

        waterbodies.append(
            {
                "waterbody_name": wb_name,
                "event_count": len(wb_records),
                "is_put_and_take": is_put_and_take,
                "wild_population_likely": wild_population_likely,
                "most_recent_year": most_recent_year,
                "species_stocked": species_stocked,
                "life_stages": life_stages,
                "total_quantity": total_quantity,
                "stocking_note": stocking_note,
                "events": events,
            }
        )

    total_waterbodies_found = len(waterbodies)
    waterbodies.sort(key=lambda w: w["most_recent_year"], reverse=True)
    waterbodies = waterbodies[:max_waterbodies]

    result: dict = {
        "query": {
            "waterbody_name": waterbody_name,
            "species": species,
            "year_from": year_from,
        },
        "total_events": len(records),
        "total_waterbodies_found": total_waterbodies_found,
        "showing": len(waterbodies),
        "waterbodies": waterbodies,
    }
    if total_waterbodies_found > max_waterbodies:
        result["note"] = (
            f"Showing {max_waterbodies} most recently stocked. "
            "Use waterbody_name filter to narrow results."
        )
    return json.dumps(result)


def _compute_put_and_take(records: list) -> bool:
    """True if any record in the last 3 years has a catchable-size life stage."""
    cutoff = _CURRENT_YEAR - 3
    for r in records:
        if r.year >= cutoff and r.life_stage and r.life_stage.lower() in _PUT_AND_TAKE_STAGES:
            return True
    return False


def _compute_wild_likely(records: list, most_recent_year: int, life_stages: list[str]) -> bool:
    """True if all stockings were early-stage AND last stocking was >5 years ago."""
    if not records:
        return False
    # "more than 5 years ago" means year < current_year - 5 (2021 is exactly 5 yrs, not >5)
    if most_recent_year >= _CURRENT_YEAR - 5:
        return False
    # All life stages must be early-stage (case-insensitive)
    return all(
        (ls.lower() in _EARLY_STAGES)
        for ls in life_stages
        if ls  # skip None/empty
    )


def _build_stocking_note(
    wb_name: str,
    records: list,
    is_put_and_take: bool,
    wild_population_likely: bool,
    most_recent_year: int,
    species_stocked: list[str],
    life_stages: list[str],
    total_quantity: int | None,
) -> str:
    species_str = " and ".join(species_stocked) if species_stocked else "unknown species"
    stage_str = ", ".join(life_stages) if life_stages else "unknown stage"
    qty_str = f" ({total_quantity:,} fish total)" if total_quantity else ""

    if is_put_and_take:
        return (
            f"{wb_name} was stocked with {stage_str} {species_str} as recently as "
            f"{most_recent_year}{qty_str}. This is a put-and-take fishery — fish "
            "availability typically peaks in spring shortly after stocking events. "
            "These are hatchery fish, not a wild self-sustaining population."
        )

    if wild_population_likely:
        return (
            f"{wb_name} was last stocked with {stage_str} {species_str} in "
            f"{most_recent_year}{qty_str}. Stocking was early-stage only and "
            f"more than 5 years ago — if fish are present today, they likely "
            "represent a self-sustaining wild population, though this is inferred "
            "from stocking history and not confirmed by a recent survey."
        )

    if most_recent_year > _CURRENT_YEAR - 5:
        return (
            f"{wb_name} was stocked with {stage_str} {species_str} as recently as "
            f"{most_recent_year}{qty_str}. Stocking was early-stage only — "
            "too recent to determine whether a self-sustaining population has established."
        )

    return (
        f"{wb_name} has stocking records for {species_str} ({stage_str}), "
        f"most recently in {most_recent_year}{qty_str}."
    )
