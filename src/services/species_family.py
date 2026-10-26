"""Taxonomic family classification for species reference data.

Backs the "My FishDex" collection's flat/grouped adaptive layout. Keyed by
scientific (binomial) name since common names vary across sources (species_mapping.py's COMMON_TO_SCIENTIFIC,
the species_ranges table, and free-text NL-parsed catch descriptions).

Family display names for Centrarchidae, Percidae, Ictaluridae, Catostomidae,
Esocidae, Salmonidae, and Amiidae are exactly as specified in the handoff.
The rest extend the same plain-language convention to cover every family
present in species_mapping.py and the CA-ON species_ranges pool, per the
handoff's instruction to "extend to cover every family in the multi-province
backend pool, not just this starter set."
"""

FAMILY_DISPLAY_NAMES = {
    "Centrarchidae": "Bass & Sunfish",
    "Percidae": "Perch, Walleye & Darters",
    "Ictaluridae": "Catfish & Bullheads",
    "Catostomidae": "Redhorse & Suckers",
    "Esocidae": "Pike & Musky",
    "Salmonidae": "Trout & Salmon",
    "Amiidae": "Bowfin",
    "Cyprinidae": "Minnows & Chubs",
    "Lepisosteidae": "Gar",
    "Acipenseridae": "Sturgeon",
    "Umbridae": "Mudminnows",
    "Gasterosteidae": "Sticklebacks",
    "Moronidae": "White Perch & Bass",
    "Clupeidae": "Herring & Shad",
    "Anguillidae": "Eels",
    "Osmeridae": "Smelt",
    "Petromyzontidae": "Lampreys",
    "Sciaenidae": "Drum",
    "Gobiidae": "Gobies",
    "Polyodontidae": "Paddlefish",
}

# scientific (binomial) name -> family (scientific)
SPECIES_TO_FAMILY: dict[str, str] = {
    # Centrarchidae — Bass & Sunfish
    "Micropterus dolomieu": "Centrarchidae",
    "Micropterus salmoides": "Centrarchidae",
    "Micropterus nigricans": "Centrarchidae",
    "Lepomis gibbosus": "Centrarchidae",
    "Lepomis macrochirus": "Centrarchidae",
    "Lepomis cyanellus": "Centrarchidae",
    "Ambloplites rupestris": "Centrarchidae",
    "Pomoxis nigromaculatus": "Centrarchidae",
    "Pomoxis annularis": "Centrarchidae",
    # Percidae — Perch, Walleye & Darters
    "Perca flavescens": "Percidae",
    "Sander vitreus": "Percidae",
    "Sander canadensis": "Percidae",
    "Etheostoma caeruleum": "Percidae",
    "Etheostoma flabellare": "Percidae",
    "Etheostoma exile": "Percidae",
    "Etheostoma nigrum": "Percidae",
    "Etheostoma microperca": "Percidae",
    "Etheostoma olmstedi": "Percidae",
    "Ammocrypta pellucida": "Percidae",
    # Ictaluridae — Catfish & Bullheads
    "Ictalurus punctatus": "Ictaluridae",
    "Ameiurus melas": "Ictaluridae",
    "Ameiurus nebulosus": "Ictaluridae",
    "Noturus flavus": "Ictaluridae",
    "Noturus gyrinus": "Ictaluridae",
    "Noturus miurus": "Ictaluridae",
    "Noturus insignis": "Ictaluridae",
    "Noturus stigmosus": "Ictaluridae",
    # Catostomidae — Redhorse & Suckers
    "Catostomus commersonii": "Catostomidae",
    "Catostomus catostomus": "Catostomidae",
    "Moxostoma erythrurum": "Catostomidae",
    "Moxostoma valenciennesi": "Catostomidae",
    "Moxostoma anisurum": "Catostomidae",
    "Moxostoma macrolepidotum": "Catostomidae",
    "Hypentelium nigricans": "Catostomidae",
    "Ictiobus bubalus": "Catostomidae",
    "Ictiobus cyprinellus": "Catostomidae",
    # Esocidae — Pike & Musky
    "Esox lucius": "Esocidae",
    "Esox masquinongy": "Esocidae",
    "Esox masquinongy × Esox lucius": "Esocidae",
    # Salmonidae — Trout & Salmon
    "Salvelinus fontinalis": "Salmonidae",
    "Salvelinus namaycush": "Salmonidae",
    "Salmo trutta": "Salmonidae",
    "Salmo salar": "Salmonidae",
    "Oncorhynchus mykiss": "Salmonidae",
    "Oncorhynchus tshawytscha": "Salmonidae",
    "Oncorhynchus kisutch": "Salmonidae",
    "Salvelinus fontinalis × Salvelinus namaycush": "Salmonidae",
    # Amiidae — Bowfin
    "Amia calva": "Amiidae",
    # Cyprinidae — Minnows & Chubs (Cyprinus carpio is special-cased, see get_family)
    "Cyprinus carpio": "Cyprinidae",
    "Carassius auratus": "Cyprinidae",
    "Semotilus atromaculatus": "Cyprinidae",
    "Luxilus cornutus": "Cyprinidae",
    "Luxilus chrysocephalus": "Cyprinidae",
    "Pimephales notatus": "Cyprinidae",
    "Pimephales promelas": "Cyprinidae",
    "Rhinichthys atratulus": "Cyprinidae",
    "Rhinichthys cataractae": "Cyprinidae",
    "Rhinichthys obtusus": "Cyprinidae",
    "Chrosomus neogaeus": "Cyprinidae",
    "Chrosomus eos": "Cyprinidae",
    "Notropis atherinoides": "Cyprinidae",
    "Notropis anogenus": "Cyprinidae",
    "Notropis rubellus": "Cyprinidae",
    "Notropis hudsonius": "Cyprinidae",
    "Nocomis biguttatus": "Cyprinidae",
    "Nocomis micropogon": "Cyprinidae",
    "Cyprinella spiloptera": "Cyprinidae",
    "Clinostomus elongatus": "Cyprinidae",
    "Campostoma anomalum": "Cyprinidae",
    "Hybognathus hankinsoni": "Cyprinidae",
    # Lepisosteidae — Gar
    "Lepisosteus osseus": "Lepisosteidae",
    "Lepisosteus oculatus": "Lepisosteidae",
    # Acipenseridae — Sturgeon
    "Acipenser fulvescens": "Acipenseridae",
    # Umbridae — Mudminnows
    "Umbra limi": "Umbridae",
    # Gasterosteidae — Sticklebacks
    "Culaea inconstans": "Gasterosteidae",
    "Pungitius pungitius": "Gasterosteidae",
    # Moronidae — White Perch & Bass
    "Morone americana": "Moronidae",
    # Clupeidae — Herring & Shad
    "Alosa pseudoharengus": "Clupeidae",
    "Dorosoma cepedianum": "Clupeidae",
    # Anguillidae — Eels
    "Anguilla rostrata": "Anguillidae",
    # Osmeridae — Smelt
    "Osmerus mordax": "Osmeridae",
    # Petromyzontidae — Lampreys
    "Ichthyomyzon fossor": "Petromyzontidae",
    "Lethenteron appendix": "Petromyzontidae",
    # Sciaenidae — Drum
    "Aplodinotus grunniens": "Sciaenidae",
    # Gobiidae — Gobies (invasive)
    "Neogobius melanostomus": "Gobiidae",
    # Polyodontidae — Paddlefish (extirpated from Ontario)
    "Polyodon spathula": "Polyodontidae",
}

# Documented exception (handoff README): Common Carp gets its own folder
# labeled "Carp", subtitled with its binomial name rather than folded into
# the broader Cyprinidae umbrella most anglers don't associate it with —
# the rest of Cyprinidae (shiners, chubs, dace) reads as unrelated bait-fish.
_CARP_SCIENTIFIC_NAME = "Cyprinus carpio"

# A 2022 taxonomic split divided the historic Micropterus salmoides into two
# species: M. salmoides (Florida Bass) and M. nigricans (the widespread
# Northern Largemouth Bass — what Ontario has). Both names describe the same
# fish anglers know as "Largemouth Bass"; M. nigricans has nothing to do with
# Smallmouth Bass (M. dolomieu, unaffected by this split). GBIF/iNaturalist
# records and this project's own species_ranges table use both names
# inconsistently, so anything angler-facing (FishDex, the NL trip parser)
# must collapse them to one canonical identity or a real catch can resolve
# to a confusing "duplicate species" label.
LARGEMOUTH_BASS_POOL = frozenset(["Micropterus nigricans", "Micropterus salmoides"])
_CANONICAL_SCIENTIFIC_ALIASES = {"Micropterus nigricans": "Micropterus salmoides"}


def canonical_scientific_name(scientific_name: str) -> str:
    """Collapse taxonomic aliases that are the same fish for angler-facing
    purposes (currently: the Largemouth Bass Northern/Florida split) to one
    canonical scientific name.
    """
    return _CANONICAL_SCIENTIFIC_ALIASES.get(scientific_name, scientific_name)


def get_family(scientific_name: str) -> tuple[str, str]:
    """Return (family_scientific_name, family_display_name) for a species.

    Falls back to ("Unclassified", "Other") for anything outside the known
    mapping — e.g. a free-text catch the NL parser matched to a species
    name not yet in COMMON_TO_SCIENTIFIC — rather than raising.
    """
    if scientific_name == _CARP_SCIENTIFIC_NAME:
        return (_CARP_SCIENTIFIC_NAME, "Carp")
    family = SPECIES_TO_FAMILY.get(scientific_name)
    if not family:
        return ("Unclassified", "Other")
    return (family, FAMILY_DISPLAY_NAMES.get(family, family))
