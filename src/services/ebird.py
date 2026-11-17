"""eBird piscivore activity service — agent and CLI interface."""

import importlib
import json
import logging
from collections import defaultdict
from datetime import date

from src.services.ingest_runs import record_counts
from src.storage.bird_observations import query_bird_observations, upsert_bird_observations
from src.storage.database import get_db

# src/ingest/global/ can't be imported normally — "global" is a keyword
_ebird_ingest = importlib.import_module("src.ingest.global.ebird")
fetch_piscivore_observations = _ebird_ingest.fetch_piscivore_observations

logger = logging.getLogger(__name__)

# Osprey and merganser: confirmed active pursuit of fish
# Heron and kingfisher: strong presence indicators but wider foraging range
# Cormorant: productive habitat signal but also follows invertebrate prey
_CONFIDENCE_HIGH = {"osprey1", "commer1"}
_CONFIDENCE_MODERATE = {"grbher3", "belkin1"}
# doccor alone → "low"

_INAT_PISCIVORE_NAMES = [
    "Great Blue Heron",
    "Osprey",
    "Belted Kingfisher",
    "Common Merganser",
    "Double-crested Cormorant",
]

_INAT_NAME_TO_CODE = {
    "great blue heron": "grbher3",
    "osprey": "osprey1",
    "belted kingfisher": "belkin1",
    "common merganser": "commer1",
    "double-crested cormorant": "doccor",
}

_KM_PER_DEGREE = 111.0


def fetch_and_store(
    lat: float,
    lng: float,
    radius_km: float = 50,
    days_back: int = 30,
) -> int:
    """Fetch piscivore observations and upsert to DB. Returns count stored."""
    obs = fetch_piscivore_observations(lat, lng, radius_km, days_back)
    if obs:
        upsert_bird_observations(get_db(), obs)
    record_counts(stored=len(obs))
    return len(obs)


def _query_inat_piscivores(db, lat: float, lng: float, radius_km: float) -> list[dict]:
    """Query iNaturalist observations for piscivore bird common names."""
    if "observations" not in db.table_names():
        return []
    deg = radius_km / _KM_PER_DEGREE
    conditions = " OR ".join(["LOWER(common_name) LIKE ?" for _ in _INAT_PISCIVORE_NAMES])
    where = f"lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND ({conditions})"
    params: list = [lat - deg, lat + deg, lng - deg, lng + deg] + [
        f"%{n.lower()}%" for n in _INAT_PISCIVORE_NAMES
    ]
    return list(db["observations"].rows_where(where, params, order_by="observed_on desc"))


def get_piscivore_activity_for_agent(
    lat: float,
    lng: float,
    radius_km: float = 50,
    days_back: int = 30,
) -> str:
    """Return JSON piscivore activity summary for a location.

    Combines eBird (recent, last N days) and iNaturalist (historical) records.
    fish_presence_confidence is derived from which species are active across
    both sources: osprey/merganser = high, heron/kingfisher = moderate,
    cormorant alone = low, no sightings = none.
    """
    db = get_db()
    ebird_records = query_bird_observations(db, lat, lng, radius_km, days_back)
    inat_rows = _query_inat_piscivores(db, lat, lng, radius_km)

    ebird_count = len(ebird_records)
    inat_count = len(inat_rows)
    combined_count = ebird_count + inat_count

    sources = []
    if ebird_count:
        sources.append("eBird")
    if inat_count:
        sources.append("iNaturalist")

    if not ebird_records and not inat_rows:
        return json.dumps(
            {
                "query": {"lat": lat, "lng": lng, "radius_km": radius_km, "days_back": days_back},
                "ebird_count": 0,
                "inat_count": 0,
                "combined_count": 0,
                "sources": [],
                "observations": [],
                "summary": {},
                "fish_presence_confidence": "none",
                "habitat_note": (
                    "No piscivore bird activity recorded within the query area and time window. "
                    "This may reflect low observer effort rather than fish absence — "
                    "eBird coverage varies significantly by location. "
                    "Run `make ingest` to populate."
                ),
                "attribution": "Data from eBird.org (Cornell Lab of Ornithology)",
            }
        )

    # eBird: group by species for summary and habitat note
    by_species: dict[str, list] = defaultdict(list)
    for r in ebird_records:
        by_species[r.species_code].append(r)

    # iNat: derive species codes for confidence calculation
    inat_active_codes: set[str] = set()
    for row in inat_rows:
        code = _INAT_NAME_TO_CODE.get((row.get("common_name") or "").lower())
        if code:
            inat_active_codes.add(code)

    active_codes = set(by_species.keys()) | inat_active_codes

    all_dates: list[date] = [r.observed_on for r in ebird_records]
    all_dates += [date.fromisoformat(row["observed_on"][:10]) for row in inat_rows]
    most_recent = max(all_dates)

    # Build observations output with source labels
    obs_out = []
    for r in ebird_records:
        obs_out.append(
            {
                "source": "eBird (recent, last 30 days)",
                "species_code": r.species_code,
                "common_name": r.common_name,
                "observed_on": r.observed_on.isoformat(),
                "how_many": r.how_many,
                "location_name": r.location_name,
                "lat": r.lat,
                "lng": r.lng,
                "significance": r.piscivore_significance,
            }
        )
    for row in inat_rows:
        common_name = row.get("common_name") or ""
        code = _INAT_NAME_TO_CODE.get(common_name.lower())
        significance = _ebird_ingest._SIGNIFICANCE.get(code, "") if code else ""
        obs_out.append(
            {
                "source": "iNaturalist (historical)",
                "species_code": code,
                "common_name": common_name,
                "observed_on": row["observed_on"][:10],
                "how_many": None,
                "location_name": row.get("place_guess"),
                "lat": row["lat"],
                "lng": row["lng"],
                "significance": significance,
            }
        )

    # Species summary: merge eBird and iNat counts by species code
    combined_by_code: dict[str, dict] = {}
    for code, recs in sorted(by_species.items()):
        counts = [r.how_many for r in recs if r.how_many is not None]
        combined_by_code[code] = {
            "species_code": code,
            "common_name": recs[0].common_name,
            "ebird_sightings": len(recs),
            "inat_sightings": 0,
            "total_sightings": len(recs),
            "max_count": max(counts) if counts else None,
            "most_recent": max(r.observed_on for r in recs).isoformat(),
            "significance": recs[0].piscivore_significance,
        }
    for row in inat_rows:
        common_name = row.get("common_name") or ""
        code = _INAT_NAME_TO_CODE.get(common_name.lower())
        if not code:
            continue
        obs_date = date.fromisoformat(row["observed_on"][:10])
        if code in combined_by_code:
            combined_by_code[code]["inat_sightings"] += 1
            combined_by_code[code]["total_sightings"] += 1
            if obs_date > date.fromisoformat(combined_by_code[code]["most_recent"]):
                combined_by_code[code]["most_recent"] = obs_date.isoformat()
        else:
            combined_by_code[code] = {
                "species_code": code,
                "common_name": common_name,
                "ebird_sightings": 0,
                "inat_sightings": 1,
                "total_sightings": 1,
                "max_count": None,
                "most_recent": obs_date.isoformat(),
                "significance": _ebird_ingest._SIGNIFICANCE.get(code, ""),
            }

    species_summary = sorted(combined_by_code.values(), key=lambda x: x["species_code"])

    # Confidence from combined active codes across both sources
    if active_codes & _CONFIDENCE_HIGH:
        confidence = "high"
    elif active_codes & _CONFIDENCE_MODERATE:
        confidence = "moderate"
    elif active_codes:
        confidence = "low"
    else:
        confidence = "none"

    habitat_note = _build_habitat_note(set(by_species.keys()), most_recent, by_species)

    result: dict = {
        "query": {"lat": lat, "lng": lng, "radius_km": radius_km, "days_back": days_back},
        "ebird_count": ebird_count,
        "inat_count": inat_count,
        "combined_count": combined_count,
        "sources": sources,
        "observations": obs_out,
        "summary": {
            "species_active": species_summary,
            "total_sightings": combined_count,
            "most_recent_observation": most_recent.isoformat(),
            "species_count": len(combined_by_code),
        },
        "fish_presence_confidence": confidence,
        "habitat_note": habitat_note,
        "attribution": "Data from eBird.org (Cornell Lab of Ornithology)",
    }
    if combined_count > 0 and _has_isolated_water(db, lat, lng, radius_km):
        result["dispersal_context"] = (
            "High waterfowl activity near isolated pond — waterfowl-mediated cyprinid "
            "dispersal is a documented mechanism. Common carp, goldfish, and related "
            "species can colonize via this pathway."
        )
    return json.dumps(result)


def _stream_near(db, lat: float, lng: float, radius_m: float = 200) -> bool:
    """Return True if any OHN stream segment has a first vertex within radius_m."""
    if "stream_segments" not in db.table_names():
        return False
    deg = radius_m / 111_000
    row = db.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT
            CAST(SUBSTR(p, 1, INSTR(p,' ')-1) AS REAL) vlng,
            CAST(SUBSTR(p, INSTR(p,' ')+1,
                        CASE WHEN INSTR(p,',')>0
                             THEN INSTR(p,',')-INSTR(p,' ')-1
                             ELSE LENGTH(p) END) AS REAL) vlat
          FROM (SELECT TRIM(SUBSTR(geom_wkt, INSTR(geom_wkt,'(')+1)) p
                FROM stream_segments)
        )
        WHERE vlat BETWEEN ? AND ? AND vlng BETWEEN ? AND ?
        """,
        [lat - deg, lat + deg, lng - deg * 1.4, lng + deg * 1.4],
    ).fetchone()
    return bool(row and row[0] > 0)


def _has_isolated_water(db, lat: float, lng: float, radius_km: float) -> bool:
    """Return True if any pond/reservoir within radius_km has no nearby stream segment."""
    if "water_features" not in db.table_names():
        return False
    deg = radius_km / 111.0
    ponds = list(
        db["water_features"].rows_where(
            "feature_type IN ('pond', 'reservoir') AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
            [lat - deg, lat + deg, lng - deg, lng + deg],
        )
    )
    return any(not _stream_near(db, p["lat"], p["lng"]) for p in ponds)


def _build_habitat_note(
    active_codes: set[str],
    most_recent: date,
    by_species: dict[str, list],
) -> str:
    parts: list[str] = []
    days_ago = (date.today() - most_recent).days

    if "osprey1" in active_codes:
        n = len(by_species["osprey1"])
        parts.append(
            f"Osprey recorded {n} time(s) — strongest fish presence signal. "
            "Osprey only hunt where fish are at the surface and catchable."
        )
    if "commer1" in active_codes:
        n = len(by_species["commer1"])
        parts.append(
            f"Common Merganser recorded {n} time(s) — confirms fish at depth. "
            "Mergansers dive and pursue fish actively."
        )
    if "grbher3" in active_codes:
        n = len(by_species["grbher3"])
        parts.append(
            f"Great Blue Heron recorded {n} time(s) — indicates shallow fish-bearing water. "
            "Herons forage widely; activity near a specific spot is a credible signal."
        )
    if "belkin1" in active_codes:
        n = len(by_species["belkin1"])
        parts.append(
            f"Belted Kingfisher recorded {n} time(s) — confirms small fish in accessible water."
        )
    if "doccor" in active_codes:
        n = len(by_species["doccor"])
        parts.append(
            f"Double-crested Cormorant recorded {n} time(s) — productive fish habitat signal, "
            "though cormorants also target invertebrates."
        )

    recency = "today" if days_ago == 0 else f"{days_ago} day(s) ago"
    parts.append(
        f"Most recent observation: {recency}. "
        "Bird activity reflects conditions at time of sighting."
    )
    parts.append(
        "Absence of piscivore records does not confirm fish absence — observer coverage varies."
    )

    return " ".join(parts)
