"""Observation service — the agent talks to this, not to the ingest module directly."""

import importlib
import json

from src.storage.database import get_db
from src.storage.observations import query_observations, upsert_observations

# "global" is a Python keyword so we can't use a regular import statement
_inat = importlib.import_module("src.ingest.global.inaturalist")
fetch_observations = _inat.fetch_observations


def fetch_and_store(
    lat: float,
    lng: float,
    radius_km: float = 50,
    days_back: int | None = 90,
) -> int:
    from src.services.ingest_runs import record_counts

    observations = fetch_observations(lat, lng, radius_km, days_back)
    record_counts(fetched=1, empty=int(not observations))
    if not observations:
        return 0
    db = get_db()
    upsert_observations(db, observations)
    record_counts(stored=len(observations))
    return len(observations)


def query_for_agent(
    lat: float,
    lng: float,
    radius_km: float = 50,
    days_back: int = 90,
    species_filter: str | None = None,
) -> str:
    db = get_db()
    observations, total_count = query_observations(
        db, lat, lng, radius_km, days_back, species_filter
    )

    if not observations:
        return json.dumps(
            {
                "count": 0,
                "total_count": total_count,
                "observations": [],
                "note": (
                    "No observations found in the local database for this area and time range. "
                    "Try running `make ingest` first."
                ),
            }
        )

    records = [
        {
            "species": o.species,
            "common_name": o.common_name,
            "observed_on": o.observed_on.isoformat(),
            "place": o.place_guess,
            "quality_grade": o.quality_grade,
            "jurisdiction": o.jurisdiction,
            "observer": o.observer,
        }
        for o in observations
    ]
    return json.dumps({"count": len(records), "total_count": total_count, "observations": records})
