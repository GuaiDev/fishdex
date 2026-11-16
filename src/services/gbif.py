"""GBIF observation service — the agent talks to this, not to the ingest module directly."""

import importlib
import json
from collections import defaultdict

from src.storage.database import get_db
from src.storage.gbif_observations import query_gbif_observations, upsert_gbif_observations
from src.storage.observations import query_observations

# "global" is a Python keyword so we can't use a regular import statement
_gbif = importlib.import_module("src.ingest.global.gbif")
fetch_gbif_observations = _gbif.fetch_gbif_observations


def fetch_and_store(
    lat: float,
    lng: float,
    radius_km: float = 50,
) -> int:
    from src.services.ingest_runs import record_counts

    observations = fetch_gbif_observations(lat, lng, radius_km)
    record_counts(fetched=1, empty=int(not observations))
    if not observations:
        return 0
    db = get_db()
    upsert_gbif_observations(db, observations)
    record_counts(stored=len(observations))
    return len(observations)


def query_for_agent(
    lat: float,
    lng: float,
    radius_km: float = 50,
    days_back: int | None = None,
    species_filter: str | None = None,
) -> str:
    db = get_db()

    gbif_obs = query_gbif_observations(db, lat, lng, radius_km, days_back, species_filter)

    # Cross-reference local iNaturalist records only — no live API calls
    inat_days = days_back if days_back is not None else 36500
    inat_obs, _ = query_observations(db, lat, lng, radius_km, inat_days, species_filter)

    if not gbif_obs and not inat_obs:
        return json.dumps(
            {
                "count": 0,
                "observations": [],
                "note": (
                    "No observations found in the local database for this area. "
                    "Try running `make ingest` first."
                ),
            }
        )

    # Group iNat observations by species
    inat_by_species: dict[str, int] = defaultdict(int)
    inat_common: dict[str, str | None] = {}
    for o in inat_obs:
        inat_by_species[o.species] += 1
        if o.common_name and o.species not in inat_common:
            inat_common[o.species] = o.common_name

    # Group GBIF observations by species
    gbif_by_species: dict[str, list] = defaultdict(list)
    for o in gbif_obs:
        gbif_by_species[o.species].append(o)

    all_species = sorted(set(gbif_by_species) | set(inat_by_species))

    records = []
    for sp in all_species:
        gbif_list = gbif_by_species.get(sp, [])
        inat_count = inat_by_species.get(sp, 0)
        common = (
            gbif_list[0].common_name
            if gbif_list and gbif_list[0].common_name
            else inat_common.get(sp)
        )
        basis_set = sorted({o.basis_of_record for o in gbif_list})
        records.append(
            {
                "species": sp,
                "common_name": common,
                "inat_count": inat_count,
                "gbif_count": len(gbif_list),
                "gbif_basis": basis_set,
            }
        )

    # Build top-level summary with basis breakdown
    total_gbif = len(gbif_obs)
    total_inat = len(inat_obs)

    basis_counts: dict[str, int] = defaultdict(int)
    for o in gbif_obs:
        basis_counts[o.basis_of_record] += 1
    basis_parts = [
        f"{v} {k.lower().replace('_', ' ')}"
        for k, v in sorted(basis_counts.items(), key=lambda x: -x[1])
    ]
    gbif_breakdown = f" ({', '.join(basis_parts)})" if basis_parts else ""
    summary = f"{total_inat} from iNaturalist, {total_gbif} from GBIF{gbif_breakdown}"

    return json.dumps(
        {
            "total_count": total_gbif + total_inat,
            "inat_count": total_inat,
            "gbif_count": total_gbif,
            "summary": summary,
            "species": records,
        }
    )
