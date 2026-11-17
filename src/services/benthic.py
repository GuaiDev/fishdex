"""CABIN benthic macroinvertebrate service — agent and CLI interface."""

import json
import logging
from statistics import mean

from src.services.ingest_runs import record_counts
from src.storage.benthic import query_benthic, upsert_benthic_samples
from src.storage.database import get_db

logger = logging.getLogger(__name__)


def ingest_benthic_data() -> int:
    """Download and upsert CABIN benthic data for Ontario only. Returns record count."""
    return ingest_benthic_samples(get_db(), province_filter="ON")


def ingest_benthic_samples(db=None, province_filter: str | None = None) -> int:
    """Download and upsert CABIN benthic data for all (or filtered) provinces.

    province_filter: 2-letter province code ("ON", "BC", …) or None for all.
    Returns total record count stored.
    """
    from src.ingest.jurisdictions.ca_on.benthic import (
        _PROVINCE_TO_JURISDICTION,
        build_samples,
        download_benthic,
        download_study,
        load_study,
        parse_benthic,
    )

    if db is None:
        db = get_db()

    study_path = download_study()
    benthic_path = download_benthic()
    study_meta, visit_jurisdictions = load_study(study_path)

    if province_filter:
        jur_filter = _PROVINCE_TO_JURISDICTION.get(province_filter.upper())
        visit_jurisdictions = {
            vid: jur for vid, jur in visit_jurisdictions.items() if jur == jur_filter
        }

    if not visit_jurisdictions:
        record_counts(fetched=1, empty=1)
        logger.warning("No Canadian visits found in CABIN study file")
        return 0

    benthic_agg = parse_benthic(benthic_path, visit_jurisdictions)
    records = build_samples(study_meta, benthic_agg, visit_jurisdictions)
    record_counts(
        fetched=len(visit_jurisdictions), empty=len(visit_jurisdictions.keys() - benthic_agg.keys())
    )
    if records:
        upsert_benthic_samples(db, records)
    record_counts(stored=len(records))
    return len(records)


def get_benthic_habitat_for_agent(
    lat: float,
    lng: float,
    radius_km: float = 50,
) -> str:
    """Return JSON benthic habitat summary for a location.

    EPT proportion and richness are stream health indicators derived from
    benthic macroinvertebrate community composition. These constrain which
    species are plausible — they do not confirm presence.
    """
    db = get_db()
    records = query_benthic(db, lat=lat, lng=lng, radius_km=radius_km)

    if not records:
        return json.dumps(
            {
                "query": {"lat": lat, "lng": lng, "radius_km": radius_km},
                "site_count": 0,
                "sites": [],
                "summary": {},
                "habitat_assessment": {
                    "note": (
                        "No CABIN benthic data found within the query area. "
                        "Run `make ingest` to populate the database. "
                        "CABIN coverage is strongest in southern Ontario watersheds."
                    ),
                },
            }
        )

    by_site: dict[str, list] = {}
    for r in records:
        by_site.setdefault(r.site_code, []).append(r)

    sites_out = []
    for code, recs in by_site.items():
        recs_sorted = sorted(recs, key=lambda r: r.sampled_year, reverse=True)
        latest = recs_sorted[0]
        sites_out.append(
            {
                "site_code": code,
                "site_name": latest.site_name,
                "lat": latest.lat,
                "lng": latest.lng,
                "visit_count": len(recs),
                "year_range": [
                    min(r.sampled_year for r in recs),
                    max(r.sampled_year for r in recs),
                ],
                "latest_year": latest.sampled_year,
                "latest_ept_proportion": latest.ept_proportion,
                "latest_ept_richness": latest.ept_richness,
                "latest_total_taxa_richness": latest.total_taxa_richness,
                "latest_habitat_quality": latest.habitat_quality,
                "stream_order": latest.stream_order,
                "local_basin": latest.local_basin,
            }
        )

    ept_props = [r.ept_proportion for r in records]
    ept_richnesses = [r.ept_richness for r in records]
    habitat_counts: dict[str, int] = {"high": 0, "moderate": 0, "impaired": 0}
    for r in records:
        habitat_counts[r.habitat_quality] = habitat_counts.get(r.habitat_quality, 0) + 1

    summary = {
        "visit_count": len(records),
        "site_count": len(by_site),
        "mean_ept_proportion": round(mean(ept_props), 3),
        "mean_ept_richness": round(mean(ept_richnesses), 1),
        "habitat_quality_distribution": habitat_counts,
    }

    assessment = _assess_benthic(records)

    return json.dumps(
        {
            "query": {"lat": lat, "lng": lng, "radius_km": radius_km},
            "site_count": len(by_site),
            "sites": sites_out,
            "summary": summary,
            "habitat_assessment": assessment,
        }
    )


def _assess_benthic(records: list) -> dict:
    ept_props = [r.ept_proportion for r in records]
    mean_prop = mean(ept_props)
    high_pct = sum(1 for r in records if r.habitat_quality == "high") / len(records)
    impaired_pct = sum(1 for r in records if r.habitat_quality == "impaired") / len(records)

    notes: list[str] = []
    implications: list[str] = []

    if mean_prop >= 0.5:
        notes.append(
            f"Mean EPT proportion {mean_prop:.0%} — predominantly high-quality benthic habitat. "
            "Clean gravel substrate with strong EPT community."
        )
        implications.append(
            "Coldwater and clean-water species (brook trout, darters, redhorse) are plausible. "
            "Substrate and thermal data would further constrain predictions."
        )
    elif mean_prop >= 0.25:
        notes.append(
            f"Mean EPT proportion {mean_prop:.0%} — moderate benthic habitat quality. "
            "EPT community present but pollution-tolerant taxa are significant."
        )
        implications.append(
            "Coolwater generalist species plausible. Sensitive taxa (brook trout) are marginal."
        )
    else:
        notes.append(
            f"Mean EPT proportion {mean_prop:.0%} — impaired benthic community. "
            "Pollution-tolerant taxa (Chironomidae, Oligochaeta) dominant."
        )
        implications.append(
            "Sensitive species (darters, redhorse, trout) are unlikely. "
            "Tolerant warmwater species (carp, bullhead, suckers) most plausible."
        )

    if high_pct >= 0.5:
        notes.append(f"{high_pct:.0%} of sampled sites rated 'high' quality.")
    if impaired_pct >= 0.5:
        notes.append(f"{impaired_pct:.0%} of sampled sites rated 'impaired'.")

    return {
        "notes": notes,
        "species_implications": implications,
        "data_caveat": (
            "Benthic EPT data indicates habitat suitability, not species presence. "
            "A high-EPT site is a clean-water environment — it does not confirm that "
            "sensitive species are there. Combine with iNaturalist/GBIF observations "
            "and water quality data for stronger predictions."
        ),
    }
