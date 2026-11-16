"""Agent-facing species range and SAR services."""

import json

from src.services.ingest_runs import record_counts
from src.storage.database import get_db
from src.storage.species_ranges import query_sar_species, query_species_range, upsert_species_ranges

# Approximate bounding box for Ontario
_ON_LAT_MIN, _ON_LAT_MAX = 41.7, 56.9
_ON_LNG_MIN, _ON_LNG_MAX = -95.2, -74.3

_STATUS_SEVERITY = {
    "Endangered": 0,
    "Threatened": 1,
    "Special Concern": 2,
    "Extirpated": 3,
    "Not at Risk": 4,
    "No Status": 5,
}


# Any SARA/COSEWIC schedule status warrants a conservation note. Threatened and
# Endangered additionally carry legal prohibitions; Special Concern and
# Extirpated do not, but still should never be handed targeting advice.
_LISTED_STATUSES = frozenset({"Special Concern", "Threatened", "Endangered", "Extirpated"})


def _sar_assessment(sr) -> tuple[bool, str]:
    """Decide whether to raise a conservation flag. Fails CLOSED.

    An unverified status is not evidence of safety. Every status in the local
    species file was generated rather than sourced, so trusting a bare
    "Not at Risk" would suppress the conservation flag on the strength of text
    nobody checked — the failure mode with the worst consequences in this
    system, since it ends with someone targeting a protected fish.

    Only a status carrying a real registry citation can clear the flag.
    """
    if not sr.status_is_verified:
        return True, (
            "Conservation status for this species has not been verified against "
            "COSEWIC or the SARA registry. Treating it as potentially listed and "
            "withholding targeting guidance until it is checked."
        )

    listed = {sr.sara_status, sr.ontario_status, sr.cosewic_status} & _LISTED_STATUSES
    if listed:
        return True, f"Listed: {', '.join(sorted(listed))} (source: {sr.status_source})."

    return False, f"Verified not at risk (source: {sr.status_source})."


def _in_ontario(lat: float, lng: float) -> bool:
    return _ON_LAT_MIN <= lat <= _ON_LAT_MAX and _ON_LNG_MIN <= lng <= _ON_LNG_MAX


def get_species_range_for_agent(
    species: str,
    lat: float | None = None,
    lng: float | None = None,
) -> str:
    db = get_db()
    sr = query_species_range(db, species)
    if sr is None:
        return json.dumps(
            {
                "found": False,
                "note": (
                    "Species not in local database. Range data for this species is not yet "
                    "loaded. Consider consulting MNRF or NatureServe for Ontario range information."
                ),
            }
        )

    sar_alert, sar_reason = _sar_assessment(sr)

    is_plausible: bool | None = None
    if lat is not None and lng is not None:
        in_on = _in_ontario(lat, lng)
        is_plausible = in_on and "CA-ON" in sr.jurisdictions_present

    result: dict = {
        "found": True,
        "species": sr.species,
        "scientific_name": sr.scientific_name,
        "native_to_ontario": sr.native_to_ontario,
        "native_to_great_lakes": sr.native_to_great_lakes,
        "introduced": sr.introduced,
        "extirpated_from_ontario": sr.extirpated_from_ontario,
        "general_range": sr.general_range,
        "habitat_notes": sr.habitat_notes,
        "jurisdictions_present": sr.jurisdictions_present,
        "sara_status": sr.sara_status,
        "ontario_status": sr.ontario_status,
        "cosewic_status": sr.cosewic_status,
        "sar_alert": sar_alert,
        "sar_reason": sar_reason,
        "conservation_status_verified": sr.status_is_verified,
        "conservation_status_source": sr.status_source,
        "targeting_guidance_suppressed": sar_alert,
        "is_plausible_at_location": is_plausible,
        # Renamed from "handling_guidance": this text is unsourced and was
        # generated, not gathered. The old name implied official guidance.
        "unverified_habitat_note": sr.habitat_notes,
        "unverified_note_caveat": (
            "Generated during development, not drawn from a cited source. Treat as a "
            "starting hypothesis, not as a record."
        ),
    }
    return json.dumps(result)


def get_sar_species_for_agent(jurisdiction: str = "CA-ON") -> str:
    db = get_db()
    sar_list = query_sar_species(db, jurisdiction)
    if not sar_list:
        return json.dumps(
            {
                "count": 0,
                "note": f"No SAR data loaded for jurisdiction {jurisdiction}.",
            }
        )

    def _severity(s):
        return _STATUS_SEVERITY.get(s.sara_status, 99)

    sar_list.sort(key=_severity)

    entries = [
        {
            "species": s.species,
            "scientific_name": s.scientific_name,
            "sara_status": s.sara_status,
            "ontario_status": s.ontario_status,
            "is_protected": s.is_protected,
            "handling_guidance": s.handling_guidance,
        }
        for s in sar_list
    ]
    return json.dumps({"count": len(entries), "species_at_risk": entries})


def load_and_store() -> int:
    """Load the curated JSON database and upsert into SQLite. Returns species count."""
    from src.ingest.jurisdictions.ca_on.species_ranges import load_species_database

    db = get_db()
    ranges = load_species_database()
    record_counts(fetched=1, empty=int(not ranges))
    if ranges:
        upsert_species_ranges(db, ranges)
    record_counts(stored=len(ranges))
    return len(ranges)
