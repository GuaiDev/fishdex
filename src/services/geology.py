"""Ontario surficial geology service — agent and CLI interface."""

import json
import logging

from src.services.ingest_runs import record_counts
from src.storage.database import get_db
from src.storage.geology import query_substrate_area, query_substrate_at_point, upsert_geology_units

logger = logging.getLogger(__name__)

_HABITAT_NOTES: dict[str, str] = {
    "coarse": (
        "Coarse substrate (glaciofluvial outwash / coarse glaciolacustrine sand and gravel) "
        "strongly predicts gravel and cobble stream beds — prime habitat for river redhorse, "
        "blackside darter, johnny darter, madtoms, and other riffle-dwelling species that "
        "require clean, well-sorted substrate for spawning and foraging."
    ),
    "fine": (
        "Fine substrate (glaciolacustrine silt and clay) predicts soft, silty stream beds. "
        "Suitable for tolerant species (bullhead, carp, white sucker) but unfavourable for "
        "riffle-dwelling darters, redhorse, and salmonids that require clean gravel substrate."
    ),
    "bedrock": (
        "Bedrock outcrop (Precambrian / Paleozoic) — stream beds likely have exposed bedrock "
        "shelves interspersed with coarse gravel pockets. Suitable for species tolerant of "
        "fast, rocky water (rainbow darter, creek chub, longnose dace, some shiners)."
    ),
    "organic": (
        "Organic substrate (peat, muck, marl) — typically low-gradient, poorly-drained terrain. "
        "Stream beds likely have fine organic material and episodically low dissolved oxygen. "
        "Most tolerant species only (bullhead, mudminnow, some minnows)."
    ),
    "mixed": (
        "Mixed substrate (till, alluvial, or lacustrine deposits of variable texture). "
        "No strong constraint in either direction without field verification."
    ),
    "no_data": (
        "No surficial geology data for this location. "
        "MRD 128 covers southern Ontario only (roughly south of 46°N). "
        "Run `make ingest` to populate geology data."
    ),
}


def ingest_geology_data(lat: float, lng: float, radius_km: float = 50.0) -> int:
    """Download MRD 128 tiles for the given location and upsert units. Returns count."""
    from src.ingest.jurisdictions.ca_on.geology import load_geology

    units = load_geology(lat, lng, radius_km)
    if units:
        upsert_geology_units(get_db(), units)
    record_counts(stored=len(units))
    return len(units)


def get_substrate_for_agent(lat: float, lng: float, radius_km: float = 10.0) -> str:
    """Return JSON substrate assessment for a point.

    Uses nearest-centroid lookup for the point query and a radius scan for
    nearby context.  Substrate class is surface geology type — not confirmed
    channel substrate.  Combine with CABIN benthic data for stronger inference.
    """
    db = get_db()
    nearest = query_substrate_at_point(db, lat, lng)
    nearby = query_substrate_area(db, lat, lng, radius_km)

    if nearest is None:
        return json.dumps(
            {
                "query": {"lat": lat, "lng": lng, "radius_km": radius_km},
                "substrate_at_point": None,
                "nearby_units": [],
                "substrate_summary": {"dominant_class": "no_data", "classes_present": []},
                "habitat_note": _HABITAT_NOTES["no_data"],
                "data_caveat": (
                    "No geology data found. Run `make ingest` to populate. "
                    "MRD 128 covers southern Ontario only."
                ),
            }
        )

    class_counts: dict[str, int] = {}
    nearby_out = []
    for u in nearby:
        class_counts[u.substrate_class] = class_counts.get(u.substrate_class, 0) + 1
        nearby_out.append(
            {
                "unit_code": u.unit_code,
                "unit_name": u.unit_name,
                "substrate_class": u.substrate_class,
                "primary_material": u.primary_material,
                "centroid_lat": u.centroid_lat,
                "centroid_lng": u.centroid_lng,
            }
        )

    dominant = (
        max(class_counts, key=lambda k: class_counts[k])
        if class_counts
        else nearest.substrate_class
    )

    return json.dumps(
        {
            "query": {"lat": lat, "lng": lng, "radius_km": radius_km},
            "substrate_at_point": {
                "unit_code": nearest.unit_code,
                "unit_name": nearest.unit_name,
                "substrate_class": nearest.substrate_class,
                "primary_material": nearest.primary_material,
            },
            "nearby_units": nearby_out,
            "substrate_summary": {
                "dominant_class": dominant,
                "classes_present": list(class_counts.keys()),
                "class_counts": class_counts,
            },
            "habitat_note": _HABITAT_NOTES.get(nearest.substrate_class, ""),
            "data_caveat": (
                "Southern Ontario coverage only (1:50,000 scale, OGS MRD 128, 2010). "
                "Substrate class reflects surface geology type — not confirmed stream channel "
                "substrate. Glaciofluvial (coarse) units are the strongest predictor of "
                "gravel/cobble stream beds. Combine with CABIN benthic EPT data for "
                "stronger habitat inference."
            ),
        }
    )
