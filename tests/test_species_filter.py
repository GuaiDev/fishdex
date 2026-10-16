"""`species=` on the spots list: records only, and never a way to ask where a listed fish is.

The walkthrough found `species=Clinostomus elongatus` (Redside Dace, Endangered)
returning the six spots whose cards say "Protected fish have been recorded near
here", because GBIF rows filed under the scientific name alone were never checked
against the species file, and a hybrid label carried a listed fish's name past
the screen. Every case here builds real records in a throwaway database.
"""

import shutil

import pytest

from src.services.spots import spots_geojson
from src.storage.species_ranges import upsert_species_ranges
from src.storage.spots import replace_spots
from tests.test_spot_card import (
    ECKARDT,
    eckardt,
    gbif,
    inat,
    junction,
    species_row,
)

CHUB = ("Semotilus atromaculatus", "Creek Chub")
DARTER = ("Etheostoma caeruleum", "Rainbow Darter")
BASS = ("Micropterus dolomieu", "Smallmouth Bass")
DACE = ("Clinostomus elongatus", "Redside Dace")
LONGNOSE = ("Rhinichthys cataractae", "Longnose Dace")
HOG_SUCKER = ("Hypentelium nigricans", "Northern Hog Sucker")  # not in the species file

WORLD = (-180.0, -90.0, 180.0, 90.0)


@pytest.fixture(scope="module")
def _template_db(tmp_path_factory):
    from src.storage.database import get_db

    path = tmp_path_factory.mktemp("filter_template") / "template.db"
    get_db(path).conn.close()
    return path


@pytest.fixture
def db(tmp_path, _template_db):
    from src.storage.database import get_db

    path = tmp_path / "filter.db"
    shutil.copy(_template_db, path)
    db = get_db(path)
    upsert_species_ranges(
        db,
        [
            species_row(CHUB[1], CHUB[0]),
            species_row(DARTER[1], DARTER[0]),
            species_row(BASS[1], BASS[0]),
            species_row(LONGNOSE[1], LONGNOSE[0]),
            species_row(DACE[1], DACE[0], sara_status="Endangered"),
        ],
    )
    replace_spots(db, "CA-ON", [junction(), eckardt()])
    return db


def ids(species, db, **kw):
    body = spots_geojson(db, bbox=WORLD, max_tier=3, species=species, **kw)
    return [f["id"] for f in body["features"]]


JUNCTION = junction().spot_id
ECK = eckardt().spot_id


# ── the ordinary case still works ────────────────────────────────────────────


def test_a_species_returns_the_spots_that_have_it_by_either_name(db):
    inat(db, *DARTER)
    inat(db, *CHUB, near=ECKARDT)
    assert ids("Rainbow Darter", db) == [JUNCTION]
    assert ids("rainbow darter", db) == [JUNCTION]
    assert ids("etheostoma caeruleum", db) == [JUNCTION]
    assert ids("darter", db) == [JUNCTION]


def test_a_gbif_row_filed_under_the_scientific_name_alone_is_found_by_the_common_name(db):
    gbif(db, DARTER[0], None, "2016-06-01")
    assert ids("Rainbow Darter", db) == [JUNCTION]
    assert ids("etheostoma caeruleum", db) == [JUNCTION]


def test_a_species_nobody_recorded_returns_no_spots_but_keeps_the_kind_counts(db):
    inat(db, *CHUB)
    body = spots_geojson(db, bbox=WORLD, max_tier=3, species="smallmouth bass")
    assert body["features"] == []
    assert body["meta"]["kind_counts"]["creek_junction"] == 2


def test_only_the_spots_in_view_are_looked_up(db):
    inat(db, *DARTER)
    inat(db, *DARTER, near=ECKARDT)
    in_view = (-79.7, 43.4, -79.5, 43.6)
    body = spots_geojson(db, bbox=in_view, max_tier=3, species="rainbow darter")
    assert [f["id"] for f in body["features"]] == [JUNCTION]


# ── the listed-species leak ──────────────────────────────────────────────────


def test_a_listed_species_returns_nothing_by_common_name(db):
    inat(db, *DACE, near=ECKARDT)
    assert ids("Redside Dace", db) == []
    assert ids("redside dace", db) == []


def test_a_listed_species_returns_nothing_by_scientific_name_even_when_recorded_that_way(db):
    """The leak: a GBIF row with no common name was matched without ever being checked."""
    gbif(db, DACE[0], None, "2016-06-01", near=ECKARDT)
    assert ids("Clinostomus elongatus", db) == []
    assert ids("clinostomus elongatus", db) == []


def test_a_partial_name_does_not_return_a_spot_that_has_a_listed_match(db):
    """ "dace" matches Longnose Dace everywhere and Redside Dace at Eckardt: that spot is out."""
    inat(db, *LONGNOSE)
    inat(db, *LONGNOSE, near=ECKARDT)
    inat(db, *DACE, near=ECKARDT)
    assert ids("dace", db) == [JUNCTION]


def test_a_hybrid_with_a_listed_parent_is_never_a_way_to_find_the_parent(db):
    inat(db, "Clinostomus elongatus x Semotilus atromaculatus", "Redside Dace X Creek Chub",
         near=ECKARDT)  # fmt: skip
    assert ids("Redside Dace", db) == []
    assert ids("clinostomus elongatus", db) == []
    assert ids("Redside Dace X Creek Chub", db) == []


def test_a_hybrid_is_not_a_record_of_its_unlisted_parent_either(db):
    """A hybrid is not a species: it does not put a spot into "creek chub"."""
    inat(db, "Clinostomus elongatus x Semotilus atromaculatus", "Redside Dace X Creek Chub",
         near=ECKARDT)  # fmt: skip
    assert ids("creek chub", db) == []


def test_a_species_the_file_does_not_know_is_not_filterable_because_it_could_be_listed(db):
    """Fails closed: the file knows 69 species. Its card still shows the fish, flagged unchecked."""
    inat(db, *HOG_SUCKER)
    gbif(db, HOG_SUCKER[0], None, "2016-06-01")
    assert ids("northern hog sucker", db) == []
    assert ids("hypentelium nigricans", db) == []


def test_another_users_logged_catch_never_puts_a_spot_into_the_answer(db):
    session_id = db["sessions"].insert({"date": "2026-06-01", "user_id": 7}).last_pk
    db["stops"].insert(
        {
            "session_id": session_id,
            "location_text": "Somewhere",
            "lat": 43.5,
            "lng": -79.6,
            "species_caught": '["rainbow darter"]',
        }
    )
    assert ids("rainbow darter", db) == []


def test_a_listed_species_query_ignores_what_else_is_recorded(db):
    inat(db, *CHUB)
    inat(db, *DACE)
    assert ids("Redside Dace", db) == []
    assert ids("Creek Chub", db) == [JUNCTION]


# ── speed work must not move the screen ──────────────────────────────────────


def test_each_name_is_checked_against_the_species_file_once_per_request(db, monkeypatch):
    """The speed-up: the same fish at many spots is one lookup, not one per spot."""
    import src.services.context.species as species_mod

    inat(db, *CHUB)
    inat(db, *CHUB, near=ECKARDT)
    real = species_mod.listing_check
    seen = []

    def counting(db_, scientific_name, common_name):
        seen.append((scientific_name, common_name))
        return real(db_, scientific_name=scientific_name, common_name=common_name)

    monkeypatch.setattr(species_mod, "listing_check", counting)
    assert sorted(ids("creek chub", db)) == sorted([JUNCTION, ECK])
    assert seen.count(("Semotilus atromaculatus", "Creek Chub")) == 1


def test_a_listed_record_at_one_spot_still_drops_only_that_spot_when_names_are_cached(db):
    """A cached 'not listed' for a name must not clear a different, listed name nearby."""
    inat(db, *CHUB)
    inat(db, *CHUB, near=ECKARDT)
    inat(db, *DACE, near=ECKARDT)
    # "c" matches Creek Chub at both spots and Redside Dace (the 'c' in "Redside Dace") at one.
    assert ids("c", db) == [JUNCTION]


def test_a_species_not_in_the_file_still_never_passes_when_other_spots_cached_known_names(db):
    inat(db, *CHUB)
    inat(db, *HOG_SUCKER)
    inat(db, *CHUB, near=ECKARDT)
    assert ids("sucker", db) == []
    assert sorted(ids("chub", db)) == sorted([JUNCTION, ECK])


def test_the_records_lookup_can_leave_out_the_bird_signal(db, monkeypatch):
    """`species=` runs the lookup once per spot and reads only `.species`."""
    from src.models.context import Place
    from src.services.context import slices

    def boom(*a, **k):
        raise AssertionError("the bird signal was read")

    monkeypatch.setattr(slices, "_piscivore_field", boom)
    inat(db, *CHUB)
    place = Place(query="x", lat=43.5, lng=-79.6, radius_km=2.0)
    lean = slices.build_records(db, place, with_piscivore=False)
    assert [r.common_name for r in lean.species] == ["Creek Chub"]
    empty = slices.build_records(
        db, Place(query="y", lat=10, lng=10, radius_km=2.0), with_piscivore=False
    )
    assert empty.species == []


def test_observation_positions_are_indexed(db):
    names = {
        r[1]
        for t in ("observations", "gbif_observations")
        for r in db.execute(f"PRAGMA index_list({t})")
    }
    assert {"idx_observations_position", "idx_gbif_observations_position"} <= names
