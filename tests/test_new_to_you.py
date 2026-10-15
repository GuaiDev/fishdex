"""Phase 5: the per-user "new to you" hook and the `new_to_me` filter.

new_species = recorded at the spot − the user's confirmed catches, never a listed
species. Everything is computed per request for the one user, so the cross-user
tests are the point: one angler's catches must never change another's card.
All databases are throwaway copies; nothing touches the real one.
"""

import json
import shutil

import pytest

from src.models.spot_card import SpotCard
from src.services import spot_labels
from src.services.new_to_you import caught_keys
from src.services.spot_card import build_spot_card
from src.services.spots import spots_geojson
from src.storage.catches import insert_catch
from src.storage.species_ranges import upsert_species_ranges
from src.storage.spots import replace_spots
from tests.test_spot_card import (
    ECKARDT,
    eckardt,
    gbif,
    inat,
    junction,
    species_row,
    spot,
)

ALICE, BOB = 1, 2
CHUB = ("Semotilus atromaculatus", "Creek Chub")
DARTER = ("Etheostoma caeruleum", "Rainbow Darter")
BASS = ("Micropterus dolomieu", "Smallmouth Bass")
DACE = ("Clinostomus elongatus", "Redside Dace")


@pytest.fixture(scope="module")
def _template_db(tmp_path_factory):
    from src.storage.database import get_db

    path = tmp_path_factory.mktemp("n2y_template") / "template.db"
    get_db(path).conn.close()
    return path


@pytest.fixture
def db(tmp_path, _template_db):
    from src.storage.database import get_db

    path = tmp_path / "n2y.db"
    shutil.copy(_template_db, path)
    db = get_db(path)
    upsert_species_ranges(
        db,
        [
            species_row(CHUB[1], CHUB[0]),
            species_row(DARTER[1], DARTER[0]),
            species_row(BASS[1], BASS[0]),
            species_row(DACE[1], DACE[0], sara_status="Endangered"),
        ],
    )
    return db


def record_three(db):
    inat(db, *CHUB)
    inat(db, *BASS, when="2023-05-02")
    gbif(db, *DARTER, when="2022-07-09")


def catch(db, user_id, common, confirmed=True):
    session_id = db["sessions"].insert({"date": "2026-06-01", "user_id": user_id}).last_pk
    stop_id = (
        db["stops"]
        .insert({"session_id": session_id, "location_text": "Somewhere", "species_caught": "[]"})
        .last_pk
    )
    insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=user_id,
        species=common,
        species_confirmed=confirmed,
    )


def names(card):
    return [s.name for s in card.new_to_you.species]


# ── the hook ────────────────────────────────────────────────────────────────


def test_hook_chain_builds_every_slot():
    from src.services.context import render

    assert {r.kind for r in render.HOOK_CHAIN if r.build is None} == set()


def test_a_caller_with_no_user_gets_the_shared_card_unchanged(db):
    record_three(db)
    card = build_spot_card(db, junction())
    assert card.new_to_you is None
    assert card.hook.kind == "structure"
    assert "new to you" not in json.dumps(card.model_dump()).lower()


def test_a_user_with_no_catches_has_every_recorded_species_new(db):
    record_three(db)
    card = build_spot_card(db, junction(), ALICE)
    assert card.hook.kind == "new_to_you"
    assert card.hook.title == "Why it's interesting"
    assert card.hook.text == "3 species recorded here you haven't caught yet"
    assert card.new_to_you.count == 3
    assert card.new_to_you.title == spot_labels.NEW_TO_YOU_TITLE
    assert card.new_to_you.tap_prompt == "Tap to see which"
    assert set(names(card)) == {"Creek Chub", "Smallmouth Bass", "Rainbow Darter"}
    # Each name still carries its record's source, so the tap is not a bare list.
    assert all(s.cite for s in card.new_to_you.species)


def test_a_confirmed_catch_removes_that_species_and_the_count_follows(db):
    record_three(db)
    catch(db, ALICE, "creek chub")
    card = build_spot_card(db, junction(), ALICE)
    assert card.hook.text == "2 species recorded here you haven't caught yet"
    assert "Creek Chub" not in names(card)


def test_a_catch_matches_a_record_filed_under_the_scientific_name(db):
    """The catch row holds only the common name; the species pool bridges it."""
    inat(db, *BASS)
    catch(db, ALICE, "smallmouth bass")
    card = build_spot_card(db, junction(), ALICE)
    assert card.new_to_you is None
    assert card.hook.kind == "structure"  # nothing new: the structure hook is the fallback


def test_an_unconfirmed_guess_never_counts_as_caught(db):
    inat(db, *CHUB)
    catch(db, ALICE, "creek chub", confirmed=False)
    assert caught_keys(db, ALICE) == frozenset()
    card = build_spot_card(db, junction(), ALICE)
    assert names(card) == ["Creek Chub"]


def test_one_new_species_uses_the_singular_wording(db):
    inat(db, *CHUB)
    card = build_spot_card(db, junction(), ALICE)
    assert card.hook.text == "1 species recorded here you haven't caught yet"
    assert card.new_to_you.count == 1


def test_nothing_recorded_means_no_new_to_you_and_the_honest_hook(db):
    mouth = spot("river_mouth", attrs={"lake": None}, title="Where the Credit meets the lake")
    card = build_spot_card(db, mouth, ALICE)
    assert card.new_to_you is None
    assert card.hook.kind == "honest_empty"


def test_the_shared_parts_of_the_card_do_not_change_with_the_user(db):
    record_three(db)
    catch(db, ALICE, "creek chub")
    shared = build_spot_card(db, junction()).model_dump()
    mine = build_spot_card(db, junction(), ALICE).model_dump()
    for key in ("might_find", "why_gather", "getting_there", "meta"):
        assert mine[key] == shared[key]
    # Even the species the user has caught are still listed as recorded here.
    assert "Creek Chub" in [s["name"] for s in mine["might_find"]["species"]]


# ── protected species ────────────────────────────────────────────────────────


def test_a_listed_species_is_never_new_to_you_and_the_care_message_still_wins(db):
    inat(db, *DACE, near=ECKARDT)
    inat(db, *CHUB, near=ECKARDT)
    card = build_spot_card(db, eckardt(), ALICE)
    assert card.hook.kind == "protected_species"
    assert names(card) == ["Creek Chub"]
    assert card.new_to_you.count == 1
    blob = json.dumps(card.model_dump()).lower()
    assert "redside" not in blob and "clinostomus" not in blob


def test_a_spot_with_only_a_listed_species_has_nothing_new(db):
    inat(db, *DACE, near=ECKARDT)
    card = build_spot_card(db, eckardt(), ALICE)
    assert card.new_to_you is None
    assert card.hook.kind == "protected_species"


# ── nothing is shared between users ──────────────────────────────────────────


def test_one_users_catches_never_change_another_users_card(db):
    record_three(db)
    before_bob = build_spot_card(db, junction(), BOB).model_dump()
    catch(db, ALICE, "creek chub")
    catch(db, ALICE, "rainbow darter")
    alice = build_spot_card(db, junction(), ALICE)
    bob = build_spot_card(db, junction(), BOB)
    assert bob.model_dump() == before_bob
    assert bob.new_to_you.count == 3
    assert alice.new_to_you.count == 1
    assert names(alice) == ["Smallmouth Bass"]
    assert caught_keys(db, BOB) == frozenset()


def test_catches_are_read_fresh_each_time_not_remembered(db):
    inat(db, *CHUB)
    assert build_spot_card(db, junction(), ALICE).new_to_you.count == 1
    catch(db, ALICE, "creek chub")
    assert build_spot_card(db, junction(), ALICE).new_to_you is None
    db["catches"].delete_where("user_id = ?", [ALICE])
    assert build_spot_card(db, junction(), ALICE).new_to_you.count == 1


def test_the_card_model_validates_with_and_without_the_block(db):
    record_three(db)
    for user in (None, ALICE):
        card = build_spot_card(db, junction(), user)
        assert SpotCard.model_validate(card.model_dump()).spot_id == junction().spot_id


WORLD = "-180,-90,180,90"


# ── the new_to_me filter ─────────────────────────────────────────────────────


def two_spots(db):
    """A junction with fish records and an Eckardt spot with only a listed fish."""
    replace_spots(db, "CA-ON", [junction(), eckardt()])
    inat(db, *CHUB)
    inat(db, *DACE, near=ECKARDT)


def ids(body):
    return [f["id"] for f in body["features"]]


def test_new_to_me_keeps_only_spots_with_something_new_and_never_counts_listed_fish(db):
    two_spots(db)
    body = spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)
    assert ids(body) == [junction().spot_id]  # Eckardt's only fish is listed
    assert body["meta"]["count"] == 1
    assert body["meta"]["new_to_me"] is True
    prop = body["features"][0]["properties"]["new_to_you"]
    assert prop == {"count": 1, "text": "1 species recorded here you haven't caught yet"}


def test_new_to_me_drops_a_spot_once_the_user_has_caught_everything_there(db):
    two_spots(db)
    catch(db, ALICE, "creek chub")
    assert spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)["features"] == []
    # ...and still returns it to someone who has not.
    assert ids(spots_geojson(db, max_tier=3, new_to_me=True, user_id=BOB)) == [junction().spot_id]


def test_new_to_me_without_a_user_is_ignored(db):
    two_spots(db)
    body = spots_geojson(db, max_tier=3, new_to_me=True)
    assert set(ids(body)) == {junction().spot_id, eckardt().spot_id}
    assert "new_to_me" not in body["meta"]
    assert all("new_to_you" not in f["properties"] for f in body["features"])


def test_the_plain_list_carries_no_per_user_property(db):
    two_spots(db)
    body = spots_geojson(db, max_tier=3, user_id=ALICE)
    assert all("new_to_you" not in f["properties"] for f in body["features"])


def test_the_filter_and_the_card_agree(db):
    record_three(db)
    replace_spots(db, "CA-ON", [junction()])
    catch(db, ALICE, "smallmouth bass")
    card = build_spot_card(db, junction(), ALICE)
    feature = spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)["features"][0]
    assert feature["properties"]["new_to_you"]["count"] == card.new_to_you.count == 2
    assert feature["properties"]["new_to_you"]["text"] == card.hook.text


def test_new_to_me_combines_with_the_kind_filter(db):
    two_spots(db)
    body = spots_geojson(db, kinds=["below_dam"], max_tier=3, new_to_me=True, user_id=ALICE)
    assert body["features"] == []


# ── labels ───────────────────────────────────────────────────────────────────


def test_the_labels_serve_every_new_to_you_string():
    payload = spot_labels.labels_payload()
    assert payload["new_to_you"]["hook_one"] == "1 species recorded here you haven't caught yet"
    assert payload["new_to_you"]["hook_many"].format(n=3) == (
        "3 species recorded here you haven't caught yet"
    )
    assert payload["filters"]["new_to_me"]["chip"] == "New to me"
    strings = set(spot_labels.all_strings())
    assert spot_labels.NEW_TO_YOU_NOTE in strings
    assert spot_labels.NEW_TO_ME_FILTER["chip"] in strings


# ── over HTTP ────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch, _template_db):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app, get_current_user

    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    api_db = database.get_db()
    upsert_species_ranges(
        api_db,
        [
            species_row(*reversed(CHUB)),
            species_row(*reversed(DARTER)),
            species_row(*reversed(DACE), sara_status="Endangered"),
        ],
    )
    two_spots(api_db)
    inat(api_db, *DARTER)
    who = {"id": ALICE}
    app.dependency_overrides[get_current_user] = lambda: dict(who)
    test_client = TestClient(app)
    test_client.who = who
    test_client.db = api_db
    yield test_client
    app.dependency_overrides.pop(get_current_user, None)


def test_the_spot_endpoint_serves_the_hook_for_the_caller_and_is_not_cacheable(client):
    response = client.get(f"/map/spots/{junction().spot_id}")
    card = response.json()["card"]
    assert card["hook"]["kind"] == "new_to_you"
    assert card["hook"]["text"] == "2 species recorded here you haven't caught yet"
    assert {s["name"] for s in card["new_to_you"]["species"]} == {"Rainbow Darter", "Creek Chub"}
    assert response.headers["cache-control"] == "private, no-store"


def test_over_http_one_user_cannot_see_another_users_progress(client):
    catch(client.db, ALICE, "creek chub")
    catch(client.db, ALICE, "rainbow darter")
    path = f"/map/spots/{junction().spot_id}"
    client.who["id"] = ALICE
    alice = client.get(path).json()["card"]
    client.who["id"] = BOB
    bob = client.get(path).json()["card"]
    assert alice["new_to_you"] is None and alice["hook"]["kind"] == "structure"
    assert bob["new_to_you"]["count"] == 2
    listing = client.get(f"/map/spots?bbox={WORLD}&tier=3&new_to_me=true")
    assert ids(listing.json()) == [junction().spot_id]
    client.who["id"] = ALICE
    assert client.get(f"/map/spots?bbox={WORLD}&tier=3&new_to_me=true").json()["features"] == []


def test_new_to_me_over_http_is_private_and_the_plain_list_is_unchanged(client):
    listing = client.get(f"/map/spots?bbox={WORLD}&tier=3&new_to_me=true")
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "private, no-store"
    assert listing.json()["meta"]["new_to_me"] is True
    plain = client.get("/map/spots?tier=3")
    assert "cache-control" not in plain.headers
    assert set(ids(plain.json())) == {junction().spot_id, eckardt().spot_id}


def test_new_to_me_must_be_a_boolean(client):
    assert client.get(f"/map/spots?bbox={WORLD}&new_to_me=maybe").status_code == 422


def test_new_to_me_without_a_bbox_is_rejected(client):
    response = client.get("/map/spots?tier=3&new_to_me=true")
    assert response.status_code == 422
    assert response.json()["detail"] == "new_to_me needs a bbox"
    assert client.get("/map/spots?tier=3&new_to_me=false").status_code == 200
