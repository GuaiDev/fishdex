"""The spot card: hook rules, "you might find", "why fish gather", "getting there".

Everything runs against throwaway databases and hand-built spots; the card is
deterministic text, so the expected answer is known by construction. The
species-at-risk gate has the Eckardt Creek case from CLAUDE.md as its worked
example, and a lint test scans every string the card can say.
"""

import json
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.models.gbif_observation import GBIFObservation
from src.models.observation import Observation
from src.models.species_range import SpeciesRange
from src.models.spot import AccessRef, Spot
from src.models.spot_card import HOOK_MAX_CHARS, CardHook, SpotCard
from src.services import spot_card as spot_card_service
from src.services import spot_labels, terms
from src.services.context import render, translate
from src.services.spot_card import build_spot_card
from src.storage.gbif_observations import upsert_gbif_observations
from src.storage.observations import upsert_observations
from src.storage.species_ranges import upsert_species_ranges

BUILT = "2026-10-11T00:00:00+00:00"

# Eckardt Creek (Rouge tributary, Markham): managed for Redside Dace, which is
# listed. Coordinates are approximate; the test needs a place, not a survey.
ECKARDT = (43.870, -79.263)


@pytest.fixture(scope="module")
def _template_db(tmp_path_factory):
    from src.storage.database import get_db

    path = tmp_path_factory.mktemp("card_template") / "template.db"
    get_db(path).conn.close()
    return path


@pytest.fixture
def db(tmp_path, _template_db):
    from src.storage.database import get_db

    path = tmp_path / "card.db"
    shutil.copy(_template_db, path)
    return get_db(path)


def spot(
    kind="creek_junction",
    lat=43.5,
    lng=-79.6,
    title=None,
    attrs=None,
    structure_type=None,
    getting_there=(),
    bridges=(),
):
    key = f"n{abs(round(lng * 1e5))}_{round(lat * 1e5)}"
    return Spot(
        spot_id=f"{kind}:{key}",
        kind=kind,
        jurisdiction="CA-ON",
        lat=lat,
        lng=lng,
        water_id="credit-lower",
        water_name="Lower Credit",
        title=title or "Where Sheldon Creek joins the Credit River",
        subtitle="Credit River · Lower Credit",
        tier=1,
        structure_type=structure_type
        or {
            "creek_junction": "confluence",
            "river_mouth": "lake_mouth",
            "below_dam": "dam",
            "public_fishing": "fishing_spot",
        }[kind],
        structure_attrs=attrs if attrs is not None else {},
        getting_there=list(getting_there),
        bridges=list(bridges),
        built_at=BUILT,
    )


def junction(**kw):
    attrs = {"joining_upstream_km": 6.2, "stretch_upstream_km": 71.0, "stretch_flows_on": True}
    return spot("creek_junction", attrs=attrs, **kw)


def ref(kind, metres, name=None, **attributes):
    return AccessRef(
        kind=kind,
        name=name,
        lat=43.5,
        lng=-79.6,
        distance_m=metres,
        source_ref=f"way/{kind}{metres}",
        attributes=attributes,
    )


_ids = iter(range(1, 10_000))


def inat(db, scientific, common, when="2024-06-03", near=(43.5, -79.6), **extra):
    upsert_observations(
        db,
        [
            Observation(
                observation_id=next(_ids),
                species=scientific,
                common_name=common,
                lat=near[0] + 0.004,
                lng=near[1] + 0.004,
                observed_on=date.fromisoformat(when),
                quality_grade="research",
                jurisdiction="CA-ON",
                **extra,
            )
        ],
    )


def gbif(db, scientific, common, when, near=(43.5, -79.6), **extra):
    upsert_gbif_observations(
        db,
        [
            GBIFObservation(
                gbif_key=next(_ids),
                species=scientific,
                common_name=common,
                taxon_key=1,
                lat=near[0] - 0.003,
                lng=near[1] - 0.003,
                observed_on=date.fromisoformat(when),
                dataset_name="Fish Atlas",
                basis_of_record="HUMAN_OBSERVATION",
                jurisdiction="CA-ON",
                **extra,
            )
        ],
    )


def species_row(common, scientific, **status):
    return SpeciesRange(
        species=common,
        scientific_name=scientific,
        native_to_ontario=True,
        native_to_great_lakes=True,
        general_range="Southern Ontario",
        jurisdictions_present=["CA-ON"],
        **status,
    )


def walk(value, skip=()):
    """Every string in a nested payload, except under the skipped keys."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in skip:
                yield from walk(item, skip)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from walk(item, skip)


# ── the hook chain ────────────────────────────────────────────────────────────


def test_hook_chain_order_and_the_slot_that_is_not_built_yet():
    assert [r.kind for r in render.HOOK_CHAIN] == [
        "protected_species",
        "new_to_you",
        "distinctive",
        "structure",
        "honest_empty",
    ]
    unbuilt = {r.kind for r in render.HOOK_CHAIN if r.build is None}
    assert (
        unbuilt == set()
    )  # "new_to_you": tests/test_new_to_you.py; "distinctive": test_special_here.py


def test_unbuilt_and_per_user_hook_slots_show_nothing_to_an_anonymous_card(db):
    """No placeholder text: with records and nothing else to say, the hook is the kind."""
    inat(db, "Semotilus atromaculatus", "Creek Chub")
    card = build_spot_card(
        db, spot("public_fishing", attrs={}, title="Public fishing spot on Credit")
    )
    assert card.hook.kind == "kind_description"
    assert card.hook.text == "Places that volunteer mappers have marked as set aside for fishing."
    assert "new to you" not in json.dumps(card.model_dump()).lower()


def test_honest_empty_when_nothing_is_recorded_and_the_structure_has_nothing_specific(db):
    card = build_spot_card(
        db, spot("river_mouth", attrs={"lake": None}, title="Where the Credit meets the lake")
    )
    assert card.hook.kind == "honest_empty"
    assert card.hook.text == "No fish records within 2 km, so this is a place to find out."
    assert card.might_find.species == []
    assert card.might_find.empty_text.startswith("We hold no fish records for this area yet")


def test_honest_empty_says_so_when_records_exist_elsewhere(db):
    inat(db, "Semotilus atromaculatus", "Creek Chub", near=(10.0, 10.0))
    card = build_spot_card(
        db, spot("river_mouth", attrs={"lake": None}, title="Where the Credit meets the lake")
    )
    assert card.hook.kind == "honest_empty"
    assert card.might_find.empty_text.startswith("No fish records within 2 km.")
    assert "few people have looked" in card.might_find.empty_text


def test_structure_hook_comes_from_the_records_about_the_water(db):
    card = build_spot_card(db, junction())
    assert card.hook.kind == "structure"
    assert card.hook.text == (
        "Two streams meet here, one fed by about 6.2 km of channel upstream "
        "and the other by about 71 km."
    )
    mouth = build_spot_card(
        db,
        spot(
            "river_mouth",
            attrs={"lake": "Lake Ontario"},
            title="Where the Credit meets Lake Ontario",
        ),
    )
    assert mouth.hook.text == "A river running out into Lake Ontario."
    dam = build_spot_card(
        db,
        spot(
            "below_dam",
            attrs={"dam_kind": "weir", "tailwater_verified": True},
            title="Below the Mill weir",
        ),
    )
    assert dam.hook.text == "The water just downstream of a weir."


def test_an_unverified_dam_does_not_claim_the_water_below_it(db):
    card = build_spot_card(
        db,
        spot(
            "below_dam",
            attrs={"dam_kind": "dam", "tailwater_verified": False},
            title="At the dam on the Credit River",
        ),
    )
    assert card.hook.kind == "honest_empty"
    assert any("could not confirm" in line for line in card.getting_there.unverified)


def test_a_hook_is_two_lines_at_most():
    with pytest.raises(ValidationError):
        CardHook(title="t", kind="structure", text="x" * (HOOK_MAX_CHARS + 1))


# ── the species-at-risk gate (Eckardt Creek) ──────────────────────────────────


def eckardt():
    return spot(
        "creek_junction",
        lat=ECKARDT[0],
        lng=ECKARDT[1],
        title="Where Eckardt Creek joins the Rouge River",
        attrs={"joining_upstream_km": 4.0, "stretch_upstream_km": 60.0},
    )


def test_eckardt_creek_hook_is_a_care_message_and_the_species_is_never_named(db):
    upsert_species_ranges(
        db,
        [
            species_row("Redside Dace", "Clinostomus elongatus", sara_status="Endangered"),
            species_row("Creek Chub", "Semotilus atromaculatus", sara_status="Not at Risk"),
        ],
    )
    inat(db, "Clinostomus elongatus", "Redside Dace", near=ECKARDT)
    gbif(db, "Clinostomus elongatus", "Redside Dace", "2019-05-02", near=ECKARDT)
    inat(db, "Semotilus atromaculatus", "Creek Chub", near=ECKARDT)

    card = build_spot_card(db, eckardt())

    assert card.hook.kind == "protected_species"
    assert card.hook.text == (
        "Protected fish have been recorded near here. Handle any fish with care and release it."
    )
    # Not in "You might find", not counted, not anywhere else in the payload.
    assert [s.name for s in card.might_find.species] == ["Creek Chub"]
    assert card.might_find.note == "Protected species recorded nearby are not listed."
    assert card.meta.species_withheld_protected == 1
    blob = json.dumps(card.model_dump()).lower()
    assert "redside" not in blob and "clinostomus" not in blob
    assert "1 fish records within 2 km" in blob  # only the Creek Chub, not the protected records


def test_a_protected_species_is_withheld_even_when_it_is_the_only_one(db):
    upsert_species_ranges(
        db, [species_row("Redside Dace", "Clinostomus elongatus", sara_status="Endangered")]
    )
    inat(db, "Clinostomus elongatus", "Redside Dace", near=ECKARDT)
    card = build_spot_card(db, eckardt())
    assert card.hook.kind == "protected_species"
    assert card.might_find.species == []
    assert card.might_find.empty_text == "No other fish records within 2 km."
    assert not card.why_gather.facts or all(
        "records within" not in f.text for f in card.why_gather.facts
    )


def test_the_gate_uses_status_known_listed_not_sar_alert(db):
    """sar_alert is true for every species in the file; gating on it would hide every fish."""
    upsert_species_ranges(
        db,
        [
            # Unverified "Not at Risk": sar_alert is True, status_known_listed is False.
            species_row("Creek Chub", "Semotilus atromaculatus", sara_status="Not at Risk"),
            # Unverified but affirmative listing: still a reason to hold back.
            species_row("Redside Dace", "Clinostomus elongatus", cosewic_status="Endangered"),
        ],
    )
    from src.services.context import describe_species

    assert describe_species(db, "Creek Chub").sar_alert is True
    assert describe_species(db, "Creek Chub").status_known_listed is False
    assert describe_species(db, "Redside Dace").status_known_listed is True

    inat(db, "Semotilus atromaculatus", "Creek Chub")
    card = build_spot_card(db, junction())
    assert card.hook.kind != "protected_species"
    assert [s.name for s in card.might_find.species] == ["Creek Chub"]

    inat(db, "Clinostomus elongatus", "Redside Dace")
    assert build_spot_card(db, junction()).hook.kind == "protected_species"


def test_species_the_file_does_not_know_are_shown_but_counted_as_unchecked(db):
    inat(db, "Etheostoma exile", "Iowa Darter")
    card = build_spot_card(db, junction())
    assert [s.name for s in card.might_find.species] == ["Iowa Darter"]
    assert card.meta.species_status_unchecked == 1


def test_the_gate_matches_the_exact_species_not_a_near_miss(db):
    """'Dace' must not inherit Redside Dace's listing, nor clear it."""
    upsert_species_ranges(
        db,
        [
            species_row("Redside Dace", "Clinostomus elongatus", sara_status="Endangered"),
            species_row("Blacknose Dace", "Rhinichthys atratulus", sara_status="Not at Risk"),
        ],
    )
    inat(db, "Rhinichthys atratulus", "Blacknose Dace")
    card = build_spot_card(db, junction())
    assert card.hook.kind != "protected_species"
    assert [s.name for s in card.might_find.species] == ["Blacknose Dace"]


# ── you might find ───────────────────────────────────────────────────────────


def test_species_carry_source_and_date_and_the_not_a_guarantee_line(db):
    inat(db, "Semotilus atromaculatus", "Creek Chub", when="2024-06-03")
    block = build_spot_card(db, junction()).might_find
    assert block.title == "You might find"
    assert block.caveat == "Recorded nearby, not a catch guarantee."
    fish = block.species[0]
    assert (fish.name, fish.scientific_name) == ("Creek Chub", "Semotilus atromaculatus")
    assert (fish.source, fish.date, fish.cite) == (
        "iNaturalist",
        "2024-06-03",
        "iNaturalist, 2024-06-03",
    )
    assert fish.evidence == "sighting"


def test_surveys_rank_above_photos_even_when_older_and_the_top_five_are_kept(db):
    inat(db, "Species a", "Photo Newest", when="2025-08-01")
    inat(db, "Species b", "Photo Newer", when="2025-07-01")
    inat(db, "Species c", "Photo Old", when="2020-01-01")
    inat(db, "Species d", "Photo Older", when="2019-01-01")
    gbif(
        db,
        "Species e",
        "Surveyed Fish",
        "2015-05-05",
        event_id="evt-1",
        sampling_protocol="electrofishing",
    )
    inat(db, "Species f", "Photo Oldest", when="2018-01-01")
    inat(db, "Species g", "Photo Ancient", when="2017-01-01")

    block = build_spot_card(db, junction()).might_find

    assert [s.name for s in block.species][0] == "Surveyed Fish"
    assert block.species[0].evidence == "survey"
    assert [s.name for s in block.species][1:] == [
        "Photo Newest",
        "Photo Newer",
        "Photo Old",
        "Photo Older",
    ]
    assert len(block.species) == 5
    assert block.more_text == "and 2 more species recorded nearby"


def test_a_survey_record_is_cited_even_when_a_newer_photo_exists(db):
    gbif(db, "Species e", "Mixed Fish", "2015-05-05", event_id="evt-1")
    inat(db, "Species e", "Mixed Fish", when="2025-05-05")
    fish = build_spot_card(db, junction()).might_find.species[0]
    assert fish.evidence == "survey"
    assert fish.date == "2015-05-05"
    assert fish.note == "Older record"


def test_fuzzed_records_are_not_within_two_km(db):
    inat(db, "Semotilus atromaculatus", "Creek Chub", is_obscured=True, geoprivacy="obscured")
    card = build_spot_card(
        db, spot("river_mouth", attrs={}, title="Where the Credit meets the lake")
    )
    assert card.might_find.species == []
    assert card.meta.species_withheld_imprecise == 1
    assert (
        card.hook.text
        == "Fish records near here are too fuzzy to place, so this is a place to find out."
    )
    assert card.might_find.note == "Records with a deliberately fuzzy location are left out."


def test_a_card_is_the_same_for_every_user(db):
    """The records slice folds in the angler's own catches; a card must not."""
    db["sessions"].insert({"date": "2025-06-14", "user_id": 1})
    db["stops"].insert(
        {
            "session_id": 1,
            "user_id": 1,
            "location_text": "x",
            "lat": 43.5,
            "lng": -79.6,
            "species_caught": json.dumps(["northern pike"]),
            "party_species_caught": json.dumps([]),
            "was_productive": 1,
        }
    )
    inat(db, "Semotilus atromaculatus", "Creek Chub")
    card = build_spot_card(db, junction())
    assert [s.name for s in card.might_find.species] == ["Creek Chub"]
    assert "pike" not in json.dumps(card.model_dump()).lower()
    assert spot_card_service.NO_USER == 0
    assert build_spot_card(db, junction()).model_dump() == card.model_dump()


# ── why fish gather here ─────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["river_mouth", "creek_junction", "below_dam", "public_fishing"])
def test_the_general_reasoning_is_the_dictionary_entry_labelled_as_inference(db, kind):
    card = build_spot_card(db, spot(kind, attrs={}))
    general = card.why_gather.general
    assert general.text == translate.WHY_FISH_GATHER[kind]
    assert general.label == "General reasoning, not a record about this spot"
    assert general.provenance == "inference"
    assert translate.why_fish_gather(kind).provenance.kind.value == "inference"


def test_every_kind_has_one_two_sentence_entry():
    assert set(translate.WHY_FISH_GATHER) == set(spot_labels.KIND_ORDER)
    for text in translate.WHY_FISH_GATHER.values():
        assert len(re.findall(r"[.!?](?:\s|$)", text)) == 2, text


def test_data_facts_are_listed_apart_and_tagged_as_records(db):
    inat(db, "Semotilus atromaculatus", "Creek Chub")
    inat(db, "Etheostoma exile", "Iowa Darter")
    block = build_spot_card(db, junction()).why_gather
    assert block.facts_title == "What our records say about this spot"
    assert all(f.provenance == "record" and f.source for f in block.facts)
    texts = [f.text for f in block.facts]
    assert (
        "The smaller stream has about 6.2 km of channel upstream of this point; "
        "the larger has about 71 km." in texts
    )
    assert "2 fish records within 2 km, covering 2 species." in texts
    assert block.general.text not in texts


def test_no_record_text_rides_inside_a_record_field_anymore():
    """The sentences about why fish gather moved to the dictionary; records state facts."""
    assert (
        translate.barriers(2, 0)
        == "a mapped barrier lies upstream of here and none lies downstream"
    )
    for text in (translate.barriers(0, 2), translate.barriers(1, 1), translate.barriers(0, 0)):
        assert "fish" not in text


def test_a_weir_gets_its_glossary_entry_and_other_cards_get_none(db):
    weir = build_spot_card(
        db,
        spot(
            "below_dam",
            attrs={"dam_kind": "weir", "tailwater_verified": True},
            title="Below the Mill weir",
        ),
    )
    assert [t.term for t in weir.terms] == ["weir"]
    assert [t.term_id for t in weir.terms] == ["weir"]
    assert build_spot_card(db, junction()).terms == []


# ── getting there ────────────────────────────────────────────────────────────


def test_getting_there_lists_the_nearest_of_each_kind_with_distance(db):
    s = spot(
        "below_dam",
        attrs={"dam_kind": "dam", "tailwater_verified": True},
        title="Below the Paris Dam",
        getting_there=[
            ref("parking", 262, access="yes", fee="no", surface="asphalt"),
            ref("parking", 150),
            ref("trail_head", 1200, name="Rail Trail"),
            ref("boat_launch", 640),
        ],
        bridges=[ref("road_crossing", 171, name="William Street")],
    )
    block = build_spot_card(db, s).getting_there
    assert [i.text for i in block.items] == [
        "Parking about 150 m away",
        "Trailhead (Rail Trail) about 1.2 km away",
        "Boat launch about 640 m away",
        "Public road crossing (William Street) about 170 m away",
    ]
    assert block.title == "Getting there"
    assert block.empty_text is None
    assert "Distances are in a straight line, not by road or trail." in block.unverified
    assert any("OpenStreetMap" in line for line in block.unverified)


def test_parking_tags_are_shown_as_mapped_not_as_fact(db):
    s = junction(getting_there=[ref("parking", 262, access="yes", fee="no", surface="asphalt")])
    item = build_spot_card(db, s).getting_there.items[0]
    assert item.details == ["mapped as open to the public", "mapped as free", "asphalt"]


def test_no_access_nearby_is_said_plainly_with_what_is_unverified(db):
    block = build_spot_card(db, junction()).getting_there
    assert block.items == []
    assert (
        block.empty_text
        == "No parking, trailhead or boat launch found within 1.5 km in our map data."
    )
    assert "A missing one may just be unmapped" in " ".join(block.unverified)
    assert (
        block.unverified[-1]
        == "Fishing rules and permission to cross private land are not checked here."
    )


def test_crown_land_flag_has_three_honest_states(db):
    assert build_spot_card(db, junction()).getting_there.crown_land.state == "not_checked"
    db["crown_land"].insert({"crown_id": "far", "centroid_lat": 10.0, "centroid_lng": 10.0})
    assert build_spot_card(db, junction()).getting_there.crown_land.state == "none_found"
    db["crown_land"].insert({"crown_id": "near", "centroid_lat": 43.51, "centroid_lng": -79.59})
    flag = build_spot_card(db, junction()).getting_there.crown_land
    assert flag.state == "mapped"
    assert flag.source == "Ontario Crown Land"
    assert "check local rules" in flag.text


# ── no model, no network ─────────────────────────────────────────────────────


def test_building_a_card_makes_no_model_call(db, monkeypatch):
    import anthropic

    def boom(*a, **k):
        raise AssertionError("a card must not call a model")

    monkeypatch.setattr(anthropic.Anthropic, "__init__", boom)
    monkeypatch.setattr(anthropic.AsyncAnthropic, "__init__", boom)
    inat(db, "Semotilus atromaculatus", "Creek Chub")
    assert build_spot_card(db, junction()).hook.kind == "structure"


# ── the jargon lint ──────────────────────────────────────────────────────────

BANNED = re.compile(
    r"\b(stretch(es)?|anchors?|confluences?|tailwaters?|access points?|ingest(ed)?|topology|"
    r"segments?|OHN|ogf|stream order|"
    # presence is not pressure: the card may say "recorded", never "good"
    r"hot|hot ?spots?|best|prime|productive|popular|honey ?holes?|sure bet|will catch|"
    # seasons and gear are not claimed in this phase
    r"lures?|baits?|rods?|reels?|flies|fly|jigs?|spring|summer|autumn|winter)\b",
    re.IGNORECASE,
)
# Machine-readable fields that carry ids, names from sources or enum values.
NOT_TEXT = {
    "spot_id",
    "cache_key",
    "source",
    "scientific_name",
    "kind",
    "evidence",
    "provenance",
    "state",
    "date",
    "cite",
    "term",
}


def every_card(db):
    """Cards in every state the renderer has: each kind, empty, full, protected, no access."""
    upsert_species_ranges(
        db,
        [
            species_row("Redside Dace", "Clinostomus elongatus", sara_status="Endangered"),
            species_row("Creek Chub", "Semotilus atromaculatus", sara_status="Not at Risk"),
        ],
    )
    inat(db, "Semotilus atromaculatus", "Creek Chub", near=(43.6, -79.5))
    gbif(db, "Etheostoma exile", "Iowa Darter", "2012-04-01", near=(43.6, -79.5), event_id="e1")
    inat(db, "Clinostomus elongatus", "Redside Dace", near=ECKARDT)
    near = dict(lat=43.6, lng=-79.5)
    access = [ref("parking", 150, access="yes", fee="no"), ref("boat_launch", 800)]
    spots = [
        spot(
            "river_mouth",
            attrs={"lake": "Lake Ontario"},
            title="Where the Credit meets Lake Ontario",
            **near,
        ),
        spot(
            "river_mouth",
            attrs={},
            title="Where the Credit flows into the lake",
            lat=44.5,
            lng=-80.5,
        ),
        junction(getting_there=access, **near),
        spot(
            "below_dam",
            attrs={
                "dam_kind": "weir",
                "tailwater_verified": True,
                "operator": "Conservation Authority",
            },
            title="Below the Mill weir",
            getting_there=access,
            bridges=[ref("road_crossing", 90, name="Main Street")],
            **near,
        ),
        spot(
            "below_dam",
            attrs={"dam_kind": "dam", "tailwater_verified": False},
            title="At the dam on the Credit River",
            **near,
        ),
        spot("public_fishing", attrs={}, title="Public fishing spot on the Credit River", **near),
        spot(
            "public_fishing",
            attrs={},
            title="Public fishing spot on the Credit River",
            lat=44.5,
            lng=-80.5,
        ),
        eckardt(),
    ]
    return [build_spot_card(db, s) for s in spots]


def test_no_jargon_or_pressure_words_in_any_rendered_card_string(db):
    cards = every_card(db)
    assert {c.hook.kind for c in cards} >= {
        "structure",
        "honest_empty",
        "protected_species",
        "kind_description",
    }
    allowed = {terms.TERMS[t]["definition"] for t in terms.CARD_LINKED_TERMS}
    for card in cards:
        allowed |= {t.text for t in card.terms}
        for text in walk(card.model_dump(), skip=NOT_TEXT):
            if text in allowed:
                continue
            assert not BANNED.search(text), f"{card.spot_id}: {text!r}"


def test_no_jargon_in_any_fixed_string_the_renderer_owns():
    fixed = [v for name, v in vars(render).items() if name.isupper() and isinstance(v, str)]
    fixed += list(render.CARD_TITLES.values()) + list(translate.WHY_FISH_GATHER.values())
    fixed += [translate.GENERAL_REASONING_LABEL]
    assert len(fixed) > 20
    for text in fixed:
        assert not BANNED.search(text), text


def test_the_term_registry_is_the_only_place_the_hard_words_are_allowed():
    linked = {t: terms.TERMS[t]["definition"] for t in terms.CARD_LINKED_TERMS}
    banned_in_definitions = {t for t, text in linked.items() if BANNED.search(text)}
    assert banned_in_definitions == {"confluence", "tailwater"}


def test_every_hook_fits_two_lines(db):
    for card in every_card(db):
        assert len(card.hook.text) <= HOOK_MAX_CHARS


# ── the API ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch, _template_db):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app, get_current_user
    from src.storage.spots import replace_spots

    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    api_db = database.get_db()
    replace_spots(api_db, "CA-ON", [junction(), eckardt()])
    upsert_species_ranges(
        api_db, [species_row("Redside Dace", "Clinostomus elongatus", sara_status="Endangered")]
    )
    inat(api_db, "Clinostomus elongatus", "Redside Dace", near=ECKARDT)
    inat(api_db, "Semotilus atromaculatus", "Creek Chub")
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_the_spot_endpoint_returns_all_four_resolved_blocks(client):
    spot_id = junction().spot_id
    response = client.get(f"/map/spots/{spot_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Where Sheldon Creek joins the Credit River"  # the spot is still there
    card = body["card"]
    assert card["spot_id"] == spot_id
    assert {"hook", "might_find", "why_gather", "getting_there"} <= set(card)
    assert card["hook"]["title"] == "Why it's interesting"
    assert card["might_find"]["species"][0]["name"] == "Creek Chub"
    assert card["why_gather"]["general"]["provenance"] == "inference"
    assert card["getting_there"]["crown_land"]["state"] == "not_checked"
    assert card["meta"]["cache_key"] == f"{spot_id}@{BUILT}"
    # The hook depends on who asks, so the response is never kept.
    assert response.headers["cache-control"] == "private, no-store"


def test_the_spot_endpoint_withholds_the_protected_species_over_http(client):
    response = client.get(f"/map/spots/{eckardt().spot_id}")
    assert response.json()["card"]["hook"]["kind"] == "protected_species"
    assert "redside" not in response.text.lower()


def test_an_unknown_spot_is_still_a_404(client):
    assert client.get("/map/spots/creek_junction:n0_0").status_code == 404


# ── the fixture the separate UI builds against ───────────────────────────────


def fixture_cards():
    path = Path(__file__).parent / "fixtures" / "spots_api_responses.json"
    entries = json.loads(path.read_text())["requests"]
    return [
        (e, e["response"]["card"])
        for e in entries
        if e["status"] == 200 and "card" in e["response"]
    ]


def test_the_fixture_shows_a_protected_card_an_honest_empty_card_and_a_card_with_fish():
    cards = fixture_cards()
    kinds = {card["hook"]["kind"] for _, card in cards}
    assert {"protected_species", "honest_empty", "structure"} <= kinds
    assert any(card["might_find"]["species"] for _, card in cards)
    for entry, card in cards:
        assert SpotCard.model_validate(card).spot_id == entry["response"]["spot_id"]


def test_the_fixture_protected_card_names_the_listed_fish_nowhere():
    (entry, card), *_ = [c for c in fixture_cards() if c[1]["hook"]["kind"] == "protected_species"]
    assert card["meta"]["species_withheld_protected"] >= 1
    blob = json.dumps(entry["response"]).lower()
    assert "redside" not in blob and "clinostomus" not in blob
    assert card["might_find"]["species"], "the other fish recorded nearby are still listed"
    assert "EXAMPLE" in entry["note"]


def test_the_fixture_cards_pass_the_same_lint():
    allowed = {terms.TERMS[t]["definition"] for t in terms.CARD_LINKED_TERMS}
    for _, card in fixture_cards():
        allowed |= {t["text"] for t in card["terms"]}
        for text in walk(card, skip=NOT_TEXT):
            assert text in allowed or not BANNED.search(text), text


# ── quiet water and special-here in the fixture ──────────────────────────────


def test_the_fixture_has_quiet_water_with_its_caveats_listed_last():
    path = Path(__file__).parent / "fixtures" / "spots_api_responses.json"
    entries = json.loads(path.read_text())["requests"]
    listing = next(e for e in entries if e["request"] == "GET /map/spots?kind=quiet_water")
    features = listing["response"]["features"]
    assert features and all(f["properties"]["kind"] == "quiet_water" for f in features)
    for f in features:
        caveats = f["properties"]["caveats"]
        assert caveats[0]["text"] == spot_labels.QUIET_RECORDS_CAVEAT
        measured = f["properties"]["signals"]["access_is_measured"]
        assert (len(caveats) == 1) == (measured is True)
    detail = next(e for e in entries if e["request"].startswith("GET /map/spots/quiet_water:"))
    card = SpotCard.model_validate(detail["response"]["card"])
    assert [c.kind for c in card.caveats][0] == "records"
    assert card.meta.special_here == "not_applicable"


def test_the_fixture_shows_a_recorded_more_often_here_card_built_from_real_records():
    (entry, card), *_ = [c for c in fixture_cards() if c[1]["hook"]["kind"] == "distinctive"]
    block = card["might_find"]["special_here"]
    assert card["meta"]["special_here"] == "found"
    assert block["label"].startswith("Recorded more often here than along the rest of the")
    assert "real iNaturalist and GBIF records" in entry["note"]
    for line in block["species"]:
        assert line["here_total"] >= 10 and line["elsewhere_total"] >= 30
        assert line["here_records"] >= 3
        assert line["here_records"] / line["here_total"] >= 2 * (
            line["elsewhere_records"] / line["elsewhere_total"]
        )
    assert "lives here" not in json.dumps(card).lower()
