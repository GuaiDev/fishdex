"""What counts as a species before it reaches a card, a count or a filter.

The labels below are the real ones the Toronto walkthrough found on cards:
hybrids ("Lake chub X Central stoneroller ?"), groups ("Salmons and Trouts"),
aquarium and marine fish ("Giant Gourami"), and one fish under two names
("Alewife" next to "Alosa pseudoharengus").
"""

import shutil

import pytest

from src.models.context import Provenance, ProvenanceKind, SpeciesRecord
from src.services.species_labels import LabelKind, classify, clean_species_records
from src.services.spot_card import build_spot_card
from src.storage.species_ranges import upsert_species_ranges
from tests.test_spot_card import gbif, inat, junction, species_row

# ── classify: one record's names ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("species", "common"),
    [
        ("Etheostoma nigrum", "Johnny Darter"),
        ("Micropterus dolomieu", None),
        ("Smallmouth Bass", None),
        ("Pumpkinseed", None),  # a logged catch: one capitalised word, no vernacular beside it
        ("Esox americanus vermiculatus", "Grass Pickerel"),  # a subspecies is a species
        ("Alosa pseudoharengus", "Alewife"),
    ],
)
def test_real_species_stay(species, common):
    assert classify(species, common).kind is LabelKind.SPECIES


@pytest.mark.parametrize(
    ("species", "common"),
    [
        ("Lake chub X Central stoneroller ?", None),
        ("Pimephales promelas x Notropis", "Fathead Minnow Hybrid"),
        ("Luxilus cornutus", "Common Shiner X Longnose Dace"),
        ("Rhinichthys cataractae", "Longnose Dace hybrid"),
        ("Green Sunfish X Pumpkinseed ?", None),
    ],
)
def test_hybrids_and_unsure_identifications_are_not_species(species, common):
    assert classify(species, common).kind is LabelKind.HYBRID


def test_a_hybrid_names_its_parents():
    label = classify("Redside Dace X Creek Chub", None)
    assert label.kind is LabelKind.HYBRID
    assert label.parts == ("Redside Dace", "Creek Chub")
    assert classify("Rhinichthys cataractae", "Longnose Dace hybrid").parts == ("Longnose Dace",)


@pytest.mark.parametrize(
    ("species", "common"),
    [
        ("Salmonidae", "Salmons and Trouts"),
        ("Cypriniformes", "Carps and Minnows"),
        ("Nocomis Girard, 1856", "Chubs"),
        ("Leuciscidae", "North American Minnows"),
        ("BOLD:AAA5988", None),
        ("Catostomidae", "Sucker"),
        ("Actinopterygii", "Fish"),
        ("Shiner", None),
        (None, None),
    ],
)
def test_groups_are_not_species(species, common):
    assert classify(species, common).kind is LabelKind.GROUP


@pytest.mark.parametrize(
    ("species", "common"),
    [
        ("Osphronemus goramy", "Giant Gourami"),
        ("Balistoides conspicillum", "Clown Triggerfish"),
        ("Hippocampus kuda", "Bigbelly Seahorse"),
        ("Melanogrammus aeglefinus", "Haddock"),
        ("Astyanax mexicanus", "Mexican Tetra"),
    ],
)
def test_aquarium_and_marine_fish_are_not_ontario_species(species, common):
    assert classify(species, common).kind is LabelKind.NON_ONTARIO


def test_goldfish_stay_because_they_are_an_established_introduction():
    """The cleaner removes what is demonstrably not an Ontario fish; it does not guess."""
    assert classify("Carassius auratus", "Goldfish").kind is LabelKind.SPECIES


# ── clean_species_records: a batch ───────────────────────────────────────────


@pytest.fixture(scope="module")
def _template_db(tmp_path_factory):
    from src.storage.database import get_db

    path = tmp_path_factory.mktemp("labels_template") / "template.db"
    get_db(path).conn.close()
    return path


@pytest.fixture
def db(tmp_path, _template_db):
    from src.storage.database import get_db

    path = tmp_path / "labels.db"
    shutil.copy(_template_db, path)
    db = get_db(path)
    upsert_species_ranges(
        db,
        [
            species_row("Creek Chub", "Semotilus atromaculatus"),
            species_row("Alewife", "Alosa pseudoharengus"),
            species_row("Redside Dace", "Clinostomus elongatus", sara_status="Endangered"),
        ],
    )
    return db


def rec(species, common=None, count=1):
    return SpeciesRecord(
        species=species,
        common_name=common,
        count=count,
        provenance=Provenance(kind=ProvenanceKind.RECORD, source="iNaturalist"),
    )


def test_junk_is_dropped_and_real_species_survive_in_order(db):
    out = clean_species_records(
        db,
        [
            rec("Semotilus atromaculatus", "Creek Chub"),
            rec("Lake chub X Central stoneroller ?"),
            rec("Salmonidae", "Salmons and Trouts"),
            rec("Osphronemus goramy", "Giant Gourami"),
            rec("Etheostoma nigrum", "Johnny Darter"),
        ],
    )
    assert [r.common_name for r in out] == ["Creek Chub", "Johnny Darter"]


def test_one_fish_under_two_names_becomes_one_key(db):
    out = clean_species_records(
        db,
        [
            rec("Alosa pseudoharengus"),  # GBIF: scientific name only
            rec("Alewife"),  # iNaturalist: vernacular only
        ],
    )
    assert {(r.species, r.common_name) for r in out} == {("Alosa pseudoharengus", "Alewife")}


def test_a_pairing_seen_in_the_batch_teaches_the_rest_of_it(db):
    """The species file has never heard of this fish; two rows in one batch pair it up."""
    out = clean_species_records(
        db,
        [
            rec("Hypentelium nigricans", "Northern Hog Sucker"),
            rec("Hypentelium nigricans"),
            rec("Northern Hog Sucker"),
        ],
    )
    assert {(r.species, r.common_name) for r in out} == {
        ("Hypentelium nigricans", "Northern Hog Sucker")
    }


def test_a_hybrid_with_a_listed_parent_becomes_a_record_of_that_parent(db):
    out = clean_species_records(db, [rec("Redside Dace X Creek Chub", None)])
    assert [(r.species, r.common_name) for r in out] == [("Clinostomus elongatus", "Redside Dace")]


def test_a_hybrid_with_no_listed_parent_leaves_nothing(db):
    assert clean_species_records(db, [rec("Common Shiner X Longnose Dace")]) == []


def test_an_unknown_real_species_passes_through_unchanged(db):
    r = rec("Notropis photogenis")
    assert clean_species_records(db, [r]) == [r]


# ── through the card ─────────────────────────────────────────────────────────


def test_the_card_names_no_hybrid_group_or_aquarium_fish_and_counts_each_fish_once(db):
    inat(db, "Semotilus atromaculatus", "Creek Chub")
    inat(db, "Lake chub X Central stoneroller ?", None)
    inat(db, "Salmonidae", "Salmons and Trouts")
    inat(db, "Osphronemus goramy", "Giant Gourami")
    gbif(db, "Alosa pseudoharengus", None, "2016-06-01")
    inat(db, "Alewife", "Alewife")
    card = build_spot_card(db, junction())
    shown = {s.name for s in card.might_find.species}
    assert shown == {"Creek Chub", "Alewife"}
    assert card.meta.species_shown == 2


def test_a_listed_fish_hiding_in_a_hybrid_label_gets_the_care_message_and_no_name(db):
    inat(db, "Semotilus atromaculatus", "Creek Chub")
    inat(db, "Redside Dace X Creek Chub", None)
    card = build_spot_card(db, junction())
    assert card.hook.kind == "protected_species"
    assert card.meta.species_withheld_protected == 1
    text = str(card.model_dump())
    assert "Redside" not in text and "Clinostomus" not in text


def test_the_angler_s_own_logged_catch_is_left_exactly_as_typed(db):
    session_id = db["sessions"].insert({"date": "2026-06-01", "user_id": 5}).last_pk
    stop_id = (
        db["stops"]
        .insert(
            {
                "session_id": session_id,
                "user_id": 5,
                "location_text": "Somewhere",
                "lat": 43.5,
                "lng": -79.6,
                "species_caught": '["Shiner"]',
            }
        )
        .last_pk
    )
    assert stop_id
    from src.models.context import Place
    from src.services.context import slices

    place = Place(query="x", lat=43.5, lng=-79.6, radius_km=2.0)
    mine = slices.build_records(db, place, user_id=5).species
    assert [r.species.lower() for r in mine] == ["shiner"]
