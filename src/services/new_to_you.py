"""Which species recorded at a spot this angler has not caught yet.

    new_species = recorded at the spot − the user's confirmed catches

The listed-species screen is applied by the caller before anything reaches
here (`spot_card.screen_species`, on `status_known_listed`), so a protected fish
is never in the "recorded" side. This module only knows who has caught what.

Per-user and computed per request. Nothing here is stored, cached or shared:
`caught_keys` reads the one user's catch rows and the result lives for the
length of the request. Only confirmed catches count (`confirmed_only=True`,
the same rule the FishDex screen uses), so a photo-ID or text-parser guess the
user has not confirmed is never "caught".

Names are matched as a set of keys per species (canonical scientific name and
lowercase common name) because a catch row holds the common name the angler
confirmed, while a record holds whatever its source called the fish.
"""

from sqlite_utils.db import Database

from src.services.species_family import canonical_scientific_name
from src.services.species_mapping import COMMON_TO_SCIENTIFIC
from src.storage.catches import get_all_catches_for_user
from src.storage.species_ranges import query_deduped_species_ranges_for_jurisdiction


def species_keys(scientific: str | None, common: str | None) -> frozenset[str]:
    """Every name one species can be matched by: canonical scientific, then common."""
    keys: set[str] = set()
    if scientific and scientific.strip():
        keys.add(canonical_scientific_name(scientific.strip()).lower())
    if common and common.strip():
        keys.add(common.strip().lower())
    return frozenset(keys)


def caught_keys(db: Database, user_id: int, jurisdiction: str = "CA-ON") -> frozenset[str]:
    """The match keys of every species this user has confirmed catching.

    A catch row stores only the common name. The region's species pool (the
    same deduplicated list the FishDex screen uses) turns it into a scientific
    name, so a catch of "Smallmouth Bass" also matches a record filed under
    Micropterus dolomieu. A common name the pool does not know still matches
    by that name alone.
    """
    catches = get_all_catches_for_user(db, user_id, confirmed_only=True)
    if not catches:
        return frozenset()
    scientific_by_common = {
        sr.species.strip().lower(): sr.scientific_name
        for sr in query_deduped_species_ranges_for_jurisdiction(db, jurisdiction)
        if sr.scientific_name
    }
    keys: set[str] = set()
    for row in catches:
        common = (row.get("species") or "").strip().lower()
        if not common:
            continue
        scientific = scientific_by_common.get(common) or COMMON_TO_SCIENTIFIC.get(common)
        keys |= species_keys(scientific, common)
    return frozenset(keys)


def is_caught(caught: frozenset[str], scientific: str | None, common: str | None) -> bool:
    return bool(caught & species_keys(scientific, common))
