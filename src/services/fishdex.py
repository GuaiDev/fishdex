"""Aggregation service behind the "My FishDex" collection screen (GET /fishdex).

Shapes real catches + the regional species pool into the state the design
handoff (design_handoff_fishdex_collection/README.md, "State Management")
specifies: caughtSpecies[] and regionSpeciesPool[], both carrying a `family`
so the frontend can compute the flat/grouped adaptive layout and per-family
completion counts. See species_family.py for the family classification.

Every species in both lists also carries `image`: the one reference illustration
every viewer sees for that species (or the shared placeholder). It is separate
from `representativePhotoUrl`, which is the user's own catch photo. See
species_images.py and docs/api/fishdex-species-image.md.
"""

from typing import Any

from sqlite_utils.db import Database

from src.services.species_family import canonical_scientific_name, get_family
from src.services.species_images import image_payload, images_by_id
from src.services.species_mapping import COMMON_TO_SCIENTIFIC
from src.storage.catches import get_all_catches_for_user, get_personal_best
from src.storage.species_ranges import query_deduped_species_ranges_for_jurisdiction


def get_fishdex_data(db: Database, user_id: int, jurisdiction: str = "CA-ON") -> dict[str, Any]:
    # confirmed_only: a catch's species is a text-parser/photo-vision
    # suggestion until the user confirms or corrects it (see /catches/pending
    # and /catches/{id}/confirm-species) — the collection screen must never
    # present an unconfirmed guess as a caught species.
    catches = get_all_catches_for_user(db, user_id, confirmed_only=True)

    # Canonical display names from the regional pool (e.g. "Smallmouth Bass"),
    # keyed by scientific name, so a caught species that's also in the pool
    # gets the pool's exact capitalization rather than a naive title-case guess.
    # Deduped/canonicalized so a taxonomic-split alias (e.g. "Northern
    # Largemouth Bass") can't double-count the region pool or resolve a real
    # catch to a confusing duplicate label — see query_deduped_species_ranges_for_jurisdiction.
    pool_rows = query_deduped_species_ranges_for_jurisdiction(db, jurisdiction)
    canonical_names = {
        canonical_scientific_name(sr.scientific_name): sr.species
        for sr in pool_rows
        if sr.scientific_name
    }

    images = images_by_id(db)

    by_species: dict[str, list[dict]] = {}
    for c in catches:
        key = (c.get("species") or "").strip().lower()
        if not key:
            continue
        by_species.setdefault(key, []).append(c)

    caught_species = []
    for common_name_key, rows in by_species.items():
        scientific_name = COMMON_TO_SCIENTIFIC.get(common_name_key)
        if scientific_name:
            scientific_name = canonical_scientific_name(scientific_name)
            family, family_common_name = get_family(scientific_name)
        else:
            family, family_common_name = ("Unclassified", "Other")

        display_name = canonical_names.get(scientific_name) or common_name_key.title()

        photo_rows = [r for r in rows if r.get("photo_url")]
        representative_photo_url = photo_rows[-1]["photo_url"] if photo_rows else None

        # Personal best now lives in the personal_bests table, updated at
        # catch-insert time by trip_logger._maybe_update_personal_best
        # whenever the multi-catch logging UI's structured size field is
        # set — keyed the same way this dict groups catches (raw species
        # text, stripped/lowercased), so the lookup lines up by construction.
        # Catches logged before that table existed (or the older free-text
        # biggest_size field, which no earlier input path actually
        # populated) fall back to a best-effort parse — reported honestly
        # rather than fabricated either way.
        pb_cm = get_personal_best(db, user_id, common_name_key)
        if pb_cm is not None:
            max_length_inches = pb_cm / 2.54
            is_personal_best = True
        else:
            max_length_inches = None
            is_personal_best = False
            for r in rows:
                raw = r.get("biggest_size")
                if not raw:
                    continue
                try:
                    val = float(str(raw).strip().split()[0])
                except (ValueError, IndexError):
                    continue
                if max_length_inches is None or val > max_length_inches:
                    max_length_inches = val
                    is_personal_best = True

        caught_species.append({
            "id": scientific_name or common_name_key,
            "commonName": display_name,
            "scientificName": scientific_name,
            "family": family,
            "familyCommonName": family_common_name,
            "count": len(rows),
            "maxLengthInches": max_length_inches,
            "isPersonalBest": is_personal_best,
            "personalBestLength": max_length_inches if is_personal_best else None,
            # No acknowledgment-state table exists yet to track "seen" new
            # finds per the handoff's "until acknowledged" language — a
            # species caught exactly once is used as an honest proxy for
            # "newly added to the dex" instead of inventing dismissal state.
            "isNewFind": len(rows) == 1,
            "representativePhotoUrl": representative_photo_url,
            "image": image_payload(images.get(scientific_name or "")),
        })

    region_species_pool = []
    for sr in pool_rows:
        sci = canonical_scientific_name(sr.scientific_name) if sr.scientific_name else None
        if sci:
            family, family_common_name = get_family(sci)
        else:
            family, family_common_name = ("Unclassified", "Other")
        region_species_pool.append({
            "id": sci or sr.species.lower(),
            "commonName": sr.species,
            "scientificName": sci,
            "family": family,
            "familyCommonName": family_common_name,
            "image": image_payload(images.get(sci or "")),
        })

    return {
        "caughtSpecies": caught_species,
        "regionSpeciesPool": region_species_pool,
    }
