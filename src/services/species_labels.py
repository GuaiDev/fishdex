"""What a recorded species label is, before it reaches a card, a count or a filter.

GBIF and iNaturalist rows carry whatever the contributor or the taxonomy backbone
put in the species and vernacular fields, and the real rows around Toronto are not
all species: a hybrid ("Lake chub X Central stoneroller ?"), a group ("Carps and
Minnows", "Salmonidae", "Nocomis Girard, 1856"), an aquarium or marine fish
("Giant Gourami", "Haddock"), or the same fish under two names ("Alewife" and
"Alosa pseudoharengus"). Counting those as species put "121 new species" on a card
where the honest number was lower, and let a hybrid carry a listed fish's name
past the listed-species screen.

`clean_species_records` is the one place that decides, called from
`context.slices.build_records` so every consumer of `describe()` (the card, the
`new_to_you` list, the `species=` filter, the agent) sees the same cleaned set:

* a hybrid is dropped, **except** that each parent the species file lists
  (`status_known_listed`) is kept as a record of that parent, so the existing
  listed-species screen withholds it and the card says only that protected fish
  were recorded near here;
* a group, an aquarium fish and a non-Ontario fish are dropped;
* a species recorded under its scientific name in one row and its common name in
  another is merged into one record, shown under the common name.

Names it cannot place (a real species that no list here knows) pass through
unchanged. That is deliberate: this module removes what is demonstrably not a
species, it does not guess.
"""

import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from sqlite_utils import Database

from src.models.context import SpeciesRecord

logger = logging.getLogger(__name__)


class LabelKind(StrEnum):
    SPECIES = "species"
    HYBRID = "hybrid"
    GROUP = "group"
    NON_ONTARIO = "non_ontario"


@dataclass(frozen=True)
class Label:
    kind: LabelKind
    parts: tuple[str, ...] = ()
    """For a hybrid, the parents as written ("Redside Dace", "Creek Chub")."""


# "A X B", "A × B", "... hybrid", or a trailing "?" (an identification the
# contributor was not sure of, which is not a record of that species either).
_HYBRID_MARK = re.compile(r"\s[x×]\s|×|\bhybrid\b|\?\s*$", re.IGNORECASE)
_HYBRID_SPLIT = re.compile(r"\s[x×]\s|\s*×\s*", re.IGNORECASE)
_HYBRID_NOISE = re.compile(r"\bhybrid\b|\?|\bbackcross\b", re.IGNORECASE)

# A binomial ("Etheostoma nigrum") or trinomial ("Esox americanus vermiculatus"),
# lower-case epithets only, so "Nocomis Girard, 1856" and "Salmonidae" are not.
_BINOMIAL = re.compile(r"^[A-Z][a-z]+ [a-z]{3,}(?: [a-z]{3,})?$")
# A genus or higher rank with an author and year ("Nocomis Girard, 1856"), a
# family/order/tribe by its suffix ("Salmonidae", "Cypriniformes", "Lepominae"),
# or a barcode cluster ("BOLD:AAA5988"). A bare capitalised word is only a genus
# when a vernacular is filed beside it: a logged catch of "Pumpkinseed" has none.
_AUTHORED_RANK = re.compile(r"^[A-Z][a-z]+\s+[A-Z][^,]*,\s*\d{4}$")
_RANK_SUFFIX = re.compile(r"^[A-Z][a-z]+(idae|inae|iformes|oidei|oidea|morpha|ini|ptera)$")
_SINGLE_WORD = re.compile(r"^[A-Z][a-z]+$")
_BARCODE = re.compile(r"^BOLD:", re.IGNORECASE)

# Common names that name a kind of fish, not a species.
_GROUP_WORDS = re.compile(
    r"\b(fishes|minnows|carps|trouts|salmons|catfishes|cichlids|suckers|sunfishes|"
    r"basses|daces|chubs|shiners|darters|sculpins|whitefishes|redhorses|pikes|"
    r"family|order|class|and allies|and relatives)\b",
    re.IGNORECASE,
)
_GENERIC_NAMES = frozenset(
    {
        "sucker", "sunfish", "shiner", "minnow", "dace", "chub", "darter", "bass",
        "trout", "salmon", "catfish", "lamprey", "whitefish", "perch", "pike",
        "sturgeon", "carp", "gar", "bullhead", "redhorse", "stickleback", "sculpin",
        "fish", "stoneroller", "crappie",
    }
)  # fmt: skip

# Genera that are aquarium, marine or tropical fish, or otherwise not found in
# Ontario waters. Read off the real rows (iNaturalist photos taken in aquariums,
# GBIF market and museum specimens), so it is a list of what is known to turn up,
# not a taxonomy of what could.
_NON_ONTARIO_GENERA = frozenset(
    {
        "ameca", "amphilophus", "amphiprion", "anarrhichthys", "astyanax",
        "balistoides", "barbonymus", "betta", "catlocarpio", "chalceus", "chromis",
        "cyphotilapia", "dawkinsia", "dicentrarchus", "eques", "forcipiger",
        "gadus", "gambusia", "glossolepis", "haplochromis", "heniochus",
        "hippocampus", "labidochromis", "lates", "lutjanus", "maylandia",
        "megalops", "melanogrammus", "melanotaenia", "oreochromis", "osphronemus",
        "pangasianodon", "paracanthurus", "phractocephalus", "platax", "poecilia",
        "pollachius", "pseudomugil", "pterois", "pterophyllum", "ptychochromis",
        "pygocentrus", "rhomboplites", "sciaenochromis", "scleropages", "scomber",
        "scorpaenichthys", "sebastes", "selene", "siganus", "trigonostigma",
        "xiphophorus", "zebrasoma",
    }
)  # fmt: skip
_NON_ONTARIO_SPECIES = frozenset(
    {
        "lepisosteus platyrhincus",  # Florida gar
        "amia ocellicauda",  # eyetail bowfin
        "chrosomus erythrogaster",  # southern redbelly dace
        "esox niger",  # chain pickerel
    }
)


def _is_binomial(name: str | None) -> bool:
    return bool(name and _BINOMIAL.match(name.strip()))


def _is_higher_rank(sci: str, has_vernacular: bool) -> bool:
    if _BARCODE.match(sci) or _AUTHORED_RANK.match(sci) or _RANK_SUFFIX.match(sci):
        return True
    return has_vernacular and bool(_SINGLE_WORD.match(sci))


def classify(species: str | None, common_name: str | None = None) -> Label:
    """What kind of thing one record's names describe. Pure; no database."""
    names = [n.strip() for n in (species, common_name) if n and n.strip()]
    if not names:
        return Label(LabelKind.GROUP)

    for name in names:
        if _HYBRID_MARK.search(name):
            return Label(LabelKind.HYBRID, parts=_hybrid_parts(names))

    sci = species.strip() if species and species.strip() else None
    if sci and _is_higher_rank(sci, has_vernacular=bool(common_name and common_name.strip())):
        return Label(LabelKind.GROUP)
    for name in names:
        low = name.lower()
        if low in _GENERIC_NAMES or _GROUP_WORDS.search(name):
            return Label(LabelKind.GROUP)

    if sci:
        low = " ".join(sci.lower().split()[:2])
        if sci.split()[0].lower() in _NON_ONTARIO_GENERA or low in _NON_ONTARIO_SPECIES:
            return Label(LabelKind.NON_ONTARIO)
    return Label(LabelKind.SPECIES)


def _hybrid_parts(names: list[str]) -> tuple[str, ...]:
    """The parents named in a hybrid label ("Redside Dace X Creek Chub" -> two).

    Every name that carries a hybrid mark is read, so a parent spelled only in the
    scientific field is still found. "Longnose Dace hybrid" names one parent.
    """
    parts: list[str] = []
    for name in names:
        if not _HYBRID_MARK.search(name):
            continue
        for piece in _HYBRID_SPLIT.split(name):
            piece = _HYBRID_NOISE.sub("", piece).strip(" ,")
            if piece and piece not in parts:
                parts.append(piece)
    return tuple(parts)


# ── cleaning a batch of records ───────────────────────────────────────────────


def _known_pairs(db: Database) -> tuple[dict[str, str], dict[str, str]]:
    """(scientific -> common, common -> scientific) from the species file and the mapping."""
    from src.services.species_mapping import COMMON_TO_SCIENTIFIC

    sci_to_common: dict[str, str] = {}
    common_to_sci: dict[str, str] = {}
    for common, sci in COMMON_TO_SCIENTIFIC.items():
        sci_to_common.setdefault(sci.lower(), common.title())
        common_to_sci.setdefault(common.lower(), sci)
    try:
        if "species_ranges" in db.table_names():
            for row in db["species_ranges"].rows_where(select="species, scientific_name"):
                if row["species"] and row["scientific_name"]:
                    sci_to_common[row["scientific_name"].lower()] = row["species"]
                    common_to_sci[row["species"].lower()] = row["scientific_name"]
    except Exception:  # noqa: BLE001 - the species file is an aid here, never a requirement
        logger.warning("species file unavailable for label cleaning", exc_info=True)
    return sci_to_common, common_to_sci


def _listed_parent_records(db: Database, rec: SpeciesRecord, parts: tuple[str, ...]):
    """A record of each listed parent of a hybrid, so the listed-species screen sees it."""
    from src.services.context.species import status_known_listed
    from src.storage.species_ranges import query_species_range_exact

    for part in parts:
        if not part:
            continue
        try:
            sr = query_species_range_exact(db, common_name=part, scientific_name=part)
        except Exception:  # noqa: BLE001
            logger.warning("hybrid parent lookup failed for %r", part, exc_info=True)
            continue
        if sr is not None and status_known_listed(sr):
            yield rec.model_copy(
                update={"species": sr.scientific_name or sr.species, "common_name": sr.species}
            )


def clean_species_records(db: Database, records: list[SpeciesRecord]) -> list[SpeciesRecord]:
    """The records a person may be told about: hybrids, groups and out-of-range fish removed,
    one entry per fish whichever name it was recorded under.

    A hybrid with a listed parent comes back as a record of that parent (see the
    module docstring). Order is preserved among the survivors.
    """
    sci_to_common, common_to_sci = _known_pairs(db)
    # Names seen together in this very batch teach the pairing too: a GBIF row
    # filed as "Alosa pseudoharengus" and an iNaturalist row "Alewife" at the
    # same place are one fish even if the species file has never heard of it.
    for rec in records:
        if rec.common_name and _is_binomial(rec.species):
            sci_to_common.setdefault(rec.species.lower(), rec.common_name)
            common_to_sci.setdefault(rec.common_name.lower(), rec.species)

    kept: list[SpeciesRecord] = []
    dropped = 0
    for rec in records:
        label = classify(rec.species, rec.common_name)
        if label.kind is LabelKind.HYBRID:
            kept.extend(_listed_parent_records(db, rec, label.parts))
            dropped += 1
            continue
        if label.kind is not LabelKind.SPECIES:
            dropped += 1
            continue
        kept.append(_with_both_names(rec, sci_to_common, common_to_sci))
    if dropped:
        logger.debug("label cleaning dropped %d of %d records", dropped, len(records))
    return kept


def _with_both_names(
    rec: SpeciesRecord, sci_to_common: dict[str, str], common_to_sci: dict[str, str]
) -> SpeciesRecord:
    """Put the scientific name in `species` and the common name in `common_name`, when both
    are known, so two rows for one fish share a key."""
    if _is_binomial(rec.species):
        sci = rec.species.strip()
        common = rec.common_name or sci_to_common.get(sci.lower())
    else:
        # `species` holds a vernacular ("Alewife", "Smallmouth Bass") or a name
        # this module cannot parse; treat it as the common name.
        common = rec.common_name or rec.species.strip()
        sci = common_to_sci.get(common.lower())
        if sci is None and rec.common_name:
            sci = common_to_sci.get(rec.common_name.lower())
        if sci is None:
            return rec
    if common and common.lower() == sci.lower():
        common = None
    # A common name can belong to two scientific names (a taxonomy split or a
    # synonym); the common name is what the angler reads, so it decides the key.
    key_sci = common_to_sci.get(common.lower(), sci) if common else sci
    return rec.model_copy(update={"species": key_sci, "common_name": common})
