"""Special-here: species recorded more often at a spot than along the rest of its river.

The rule is about *frequency*, so every test builds a river with a known split of
records and checks which species stand out by construction. The skip rule (both
sides need a minimum number of records, otherwise no line at all) is tested at
the boundary of each named constant.
"""

import json
import re
from datetime import date

import pytest

from src.models.context import SpecialHere
from src.models.gbif_observation import GBIFObservation
from src.models.observation import Observation
from src.models.spot_card import SpotCard
from src.models.stretch import StretchSegment
from src.models.stretch_anchor import StretchSegmentLine
from src.services import spot_card as spot_card_service
from src.services.context import render, special
from src.services.context.special import (
    CORRIDOR_KM,
    HERE_RADIUS_KM,
    MAX_SPECIES,
    MIN_RECORDS_ELSEWHERE,
    MIN_RECORDS_HERE,
    MIN_SHARE_GAP,
    MIN_SHARE_RATIO,
    MIN_SPECIES_RECORDS_HERE,
    special_here,
)
from src.services.spot_card import build_spot_card
from src.storage.gbif_observations import upsert_gbif_observations
from src.storage.observations import upsert_observations
from src.storage.species_ranges import upsert_species_ranges
from src.storage.stretch_anchors import replace_anchors
from src.storage.stretches import replace_stretches
from tests.test_spot_card import ECKARDT, _ids, inat, junction, species_row
from tests.test_spots import JURISDICTION, stretch

HERE = (43.5, -79.6)  # the spot (tests.test_spot_card.junction's default)
UPSTREAM = (43.5, -79.45)  # about 12 km east along the river
OFF_RIVER = (43.62, -79.45)  # about 13 km north of the line: not "along" it


@pytest.fixture
def river(db):
    """A 24 km river, west to east along 43.5N, and the stretch it belongs to."""
    st = stretch("credit-lower", "Lower Credit", "Credit River")
    replace_stretches(
        db,
        JURISDICTION,
        [st],
        [StretchSegment(ogf_id=1, stretch_id=st.stretch_id, jurisdiction=JURISDICTION, seq=0)],
    )
    replace_anchors(
        db,
        JURISDICTION,
        [],
        [
            StretchSegmentLine(
                ogf_id=1,
                stretch_id=st.stretch_id,
                jurisdiction=JURISDICTION,
                name="Credit River",
                geometry={"type": "LineString", "coordinates": [[-79.6, 43.5], [-79.3, 43.5]]},
            )
        ],
    )
    return db


CHUB = ("Semotilus atromaculatus", "Creek Chub")
SUCKER = ("Catostomus commersonii", "White Sucker")
DARTER = ("Etheostoma caeruleum", "Rainbow Darter")
DACE = ("Rhinichthys atratulus", "Blacknose Dace")


def add(db, species, n, at, survey=False, when=None, **extra):
    """n records of one species near a point, in one write (a write per record is slow)."""
    when = date.fromisoformat(when or ("2019-07-10" if survey else "2024-06-03"))
    if survey:
        upsert_gbif_observations(
            db,
            [
                GBIFObservation(
                    gbif_key=next(_ids),
                    species=species[0],
                    common_name=species[1],
                    taxon_key=1,
                    lat=at[0] - 0.003,
                    lng=at[1] - 0.003,
                    observed_on=when,
                    dataset_name="Fish Atlas",
                    basis_of_record="HUMAN_OBSERVATION",
                    jurisdiction="CA-ON",
                    event_id=f"event-{next(_ids)}",
                )
                for _ in range(n)
            ],
        )
    else:
        upsert_observations(
            db,
            [
                Observation(
                    observation_id=next(_ids),
                    species=species[0],
                    common_name=species[1],
                    lat=at[0] + 0.004,
                    lng=at[1] + 0.004,
                    observed_on=when,
                    quality_grade="research",
                    jurisdiction="CA-ON",
                    **extra,
                )
                for _ in range(n)
            ],
        )


def fill(db, here: dict, elsewhere: dict, survey=False):
    """Records by species: {species: count} here and along the rest of the river."""
    for sp, n in here.items():
        add(db, sp, n, HERE, survey)
    for sp, n in elsewhere.items():
        add(db, sp, n, UPSTREAM, survey)


def names(result):
    return [s.common_name for s in result.species]


# ── the constants ────────────────────────────────────────────────────────────


def test_the_thresholds_are_named_constants_with_the_documented_values():
    assert (MIN_RECORDS_HERE, MIN_RECORDS_ELSEWHERE) == (10, 30)
    assert MIN_SPECIES_RECORDS_HERE == 3
    assert (MIN_SHARE_RATIO, MIN_SHARE_GAP) == (2.0, 0.05)
    assert MAX_SPECIES == 3
    assert CORRIDOR_KM == 2.0


def test_here_means_the_same_radius_the_card_uses():
    assert HERE_RADIUS_KM == spot_card_service.CARD_RADIUS_KM


# ── frequency, not count ─────────────────────────────────────────────────────


def test_a_species_with_a_clearly_higher_share_here_is_named(river):
    # Creek chub: 12 of 20 here (60%) against 10 of 60 along the river (17%).
    fill(river, {CHUB: 12, SUCKER: 8}, {CHUB: 10, SUCKER: 50})
    result = special_here(river, junction())
    assert result.status == "found"
    assert names(result) == ["Creek Chub"]
    chub = result.species[0]
    assert (chub.here_records, chub.here_total) == (12, 20)
    assert (chub.elsewhere_records, chub.elsewhere_total) == (10, 60)
    assert result.river == "Credit River"
    assert result.evidence == "sighting"


def test_a_big_count_with_an_ordinary_share_is_not_named(river):
    """30 chub records here is a lot, and exactly as common here as along the river."""
    fill(river, {CHUB: 30, SUCKER: 30}, {CHUB: 60, SUCKER: 60})
    result = special_here(river, junction())
    assert result.status == "none_stands_out"
    assert result.species == []


def test_a_small_count_with_a_high_share_is_named(river):
    """Frequency, not count: 4 of 12 here is a third of what was recorded."""
    fill(river, {DARTER: 4, SUCKER: 8}, {DARTER: 1, SUCKER: 39})
    result = special_here(river, junction())
    assert names(result) == ["Rainbow Darter"]


def test_the_share_must_be_at_least_twice_the_share_elsewhere(river):
    # 40% here against 25% elsewhere: higher, but not "clearly" (ratio 1.6).
    fill(river, {CHUB: 8, SUCKER: 12}, {CHUB: 15, SUCKER: 45})
    assert special_here(river, junction()).status == "none_stands_out"


def test_the_share_gap_must_be_at_least_five_points(river):
    # 5% here against 1% elsewhere: five times as common, but under the 5-point gap.
    fill(river, {DACE: 3, CHUB: 57}, {DACE: 1, CHUB: 99})
    assert special_here(river, junction()).status == "none_stands_out"


def test_a_species_needs_a_few_records_here_not_two(river):
    """Two records is an anecdote, even if the river has none of them elsewhere."""
    fill(river, {DACE: MIN_SPECIES_RECORDS_HERE - 1, CHUB: 18}, {CHUB: 40})
    assert special_here(river, junction()).status == "none_stands_out"
    add(river, DACE, 1, HERE)
    assert names(special_here(river, junction())) == ["Blacknose Dace"]


def test_a_species_recorded_nowhere_else_is_named_with_none_elsewhere(river):
    fill(river, {DARTER: 5, CHUB: 15}, {CHUB: 40})
    result = special_here(river, junction())
    assert result.species[0].elsewhere_records == 0
    card = build_spot_card(river, junction())
    line = card.might_find.special_here.species[0]
    assert "none along the rest of the Credit River" in line.text


def test_at_most_three_species_ordered_by_the_size_of_the_gap(river):
    here = {CHUB: 8, SUCKER: 6, DARTER: 5, DACE: 4, ("Esox lucius", "Northern Pike"): 3}
    fill(river, here, {("Lepomis gibbosus", "Pumpkinseed"): 60})
    result = special_here(river, junction())
    assert len(result.species) == MAX_SPECIES
    shares = [s.here_share - s.elsewhere_share for s in result.species]
    assert shares == sorted(shares, reverse=True)
    assert names(result) == ["Creek Chub", "White Sucker", "Rainbow Darter"]


# ── skip when either side is weak ────────────────────────────────────────────


def test_nothing_is_said_with_too_few_records_here(river):
    fill(river, {CHUB: MIN_RECORDS_HERE - 1}, {SUCKER: 100})
    result = special_here(river, junction())
    assert result.status == "too_few_records_here"
    assert result.species == []
    assert result.here_total == MIN_RECORDS_HERE - 1


def test_the_line_appears_at_exactly_the_minimum_here(river):
    fill(river, {CHUB: MIN_RECORDS_HERE}, {SUCKER: 100})
    assert special_here(river, junction()).status == "found"


def test_nothing_is_said_with_too_few_records_along_the_river(river):
    fill(river, {CHUB: 20}, {SUCKER: MIN_RECORDS_ELSEWHERE - 1})
    result = special_here(river, junction())
    assert result.status == "too_few_records_along_river"
    assert result.species == []
    assert result.elsewhere_total == MIN_RECORDS_ELSEWHERE - 1


def test_the_line_appears_at_exactly_the_minimum_along_the_river(river):
    fill(river, {CHUB: 20}, {SUCKER: MIN_RECORDS_ELSEWHERE})
    assert special_here(river, junction()).status == "found"


def test_a_weak_comparison_leaves_the_card_without_the_line(river):
    fill(river, {CHUB: 5}, {SUCKER: 100})
    card = build_spot_card(river, junction())
    assert card.might_find.special_here is None
    assert card.hook.kind != "distinctive"
    assert card.meta.special_here == "too_few_records_here"


def test_a_spot_with_no_river_section_gets_no_comparison(river):
    fill(river, {CHUB: 20}, {SUCKER: 100})
    off = junction()
    off = off.model_copy(update={"water_id": "no-such-river"})
    assert special_here(river, off).status == "no_river_section"
    assert (
        special_here(river, off.model_copy(update={"water_id": None})).status == "no_river_section"
    )


def test_a_missing_line_is_not_a_clean_result(river):
    """The stretch exists but its drawn line is gone: say so rather than compare with nothing."""
    replace_anchors(river, JURISDICTION, [], [])
    assert special_here(river, junction()).status == "no_river_section"


# ── where "along the river" is ───────────────────────────────────────────────


def test_records_far_from_the_river_line_do_not_count_as_along_it(river):
    fill(river, {CHUB: 20}, {})
    add(river, SUCKER, 100, OFF_RIVER)
    result = special_here(river, junction())
    assert result.status == "too_few_records_along_river"
    assert result.elsewhere_total == 0


def test_records_within_the_corridor_of_the_line_count(river):
    fill(river, {CHUB: 20}, {})
    add(river, SUCKER, MIN_RECORDS_ELSEWHERE, (43.5 + 1.0 / 111.0, -79.45))  # 1 km off the line
    assert special_here(river, junction()).status == "found"


def test_the_rest_of_the_river_excludes_the_spots_own_records(river):
    """A spot is not compared with itself: here is not part of elsewhere."""
    fill(river, {CHUB: 20}, {})
    assert special_here(river, junction()).elsewhere_total == 0


def test_fuzzed_records_count_on_neither_side(river):
    fill(river, {CHUB: 12}, {SUCKER: 40})
    base = special_here(river, junction())
    add(river, DARTER, 50, HERE, is_obscured=True)
    add(river, DARTER, 50, UPSTREAM, is_obscured=True)
    after = special_here(river, junction())
    assert (after.here_total, after.elsewhere_total) == (base.here_total, base.elsewhere_total)


# ── one record per visit ─────────────────────────────────────────────────────


def test_one_person_logging_a_fish_many_times_on_one_day_counts_once(river):
    """The first real-data run: fifty stonecat from one observer would have made it 'special'."""
    fill(river, {CHUB: 12, SUCKER: 8}, {SUCKER: 40})
    add(river, DARTER, 50, HERE, observer_id=777)  # same person, same day, 50 times
    result = special_here(river, junction())
    assert "Rainbow Darter" not in names(result)
    assert result.here_total == 21  # 12 + 8 + the darter, once


def test_the_same_person_on_different_days_counts_each_day(river):
    fill(river, {CHUB: 10}, {SUCKER: 40})
    for day in range(1, 6):
        add(river, DARTER, 3, HERE, observer_id=777, when=f"2024-06-{day:02d}")
    result = special_here(river, junction())
    assert {s.common_name: s.here_records for s in result.species}["Rainbow Darter"] == 5


def test_different_people_on_one_day_each_count(river):
    fill(river, {CHUB: 10}, {SUCKER: 40})
    for who in range(1, 5):
        add(river, DARTER, 2, HERE, observer_id=who)
    result = special_here(river, junction())
    assert {s.common_name: s.here_records for s in result.species}["Rainbow Darter"] == 4


def test_a_survey_haul_counts_each_fish_once(river):
    """Several rows of one species in one haul are one record: the haul found that fish."""
    upsert_gbif_observations(
        river,
        [
            GBIFObservation(
                gbif_key=next(_ids),
                species=DARTER[0],
                common_name=DARTER[1],
                taxon_key=1,
                lat=43.5,
                lng=-79.6,
                observed_on=date.fromisoformat("2019-07-10"),
                dataset_name="Fish Atlas",
                basis_of_record="HUMAN_OBSERVATION",
                jurisdiction="CA-ON",
                event_id="one-haul",
            )
            for _ in range(40)
        ],
    )
    assert special_here(river, junction()).here_total == 1


def test_the_card_says_each_fish_is_counted_once_per_person_per_day(river):
    fill(river, {CHUB: 12, SUCKER: 8}, {CHUB: 10, SUCKER: 50})
    block = build_spot_card(river, junction()).might_find.special_here
    assert "one for each fish per person per day" in block.basis_text


# ── surveys outrank photos ───────────────────────────────────────────────────


def test_survey_records_are_used_alone_when_both_sides_have_enough(river):
    # Surveys say darters stand out; photos say chub do. Surveys win, and photos are not mixed in.
    fill(river, {DARTER: 6, SUCKER: 6}, {DARTER: 3, SUCKER: 30}, survey=True)
    fill(river, {CHUB: 30}, {SUCKER: 100})
    result = special_here(river, junction())
    assert result.evidence == "survey"
    assert names(result) == ["Rainbow Darter"]
    assert (result.here_total, result.elsewhere_total) == (12, 33)


def test_too_few_surveys_fall_back_to_sightings_alone(river):
    fill(river, {DARTER: 4}, {SUCKER: 5}, survey=True)  # not enough surveys on either side
    fill(river, {CHUB: 15, SUCKER: 5}, {SUCKER: 40})
    result = special_here(river, junction())
    assert result.evidence == "sighting"
    assert names(result) == ["Creek Chub"]
    assert (result.here_total, result.elsewhere_total) == (20, 40)


def test_the_card_says_which_kind_of_record_the_counts_come_from(river):
    fill(river, {DARTER: 12}, {SUCKER: 40}, survey=True)
    block = build_spot_card(river, junction()).might_find.special_here
    assert block.evidence == "survey"
    assert block.basis_text == render.SPECIAL_BASIS["survey"]


# ── the card ─────────────────────────────────────────────────────────────────


def test_the_hook_says_recorded_more_often_never_lives_here(river):
    fill(river, {CHUB: 12, SUCKER: 8}, {CHUB: 10, SUCKER: 50})
    card = build_spot_card(river, junction())
    assert card.hook.kind == "distinctive"
    assert card.hook.text == (
        "Creek Chub is recorded more often here than along the rest of the Credit River."
    )
    blob = json.dumps(card.model_dump()).lower()
    assert "lives here" not in blob and "live here" not in blob


def test_the_hook_counts_the_other_species_when_several_stand_out(river):
    fill(river, {CHUB: 8, SUCKER: 6, DARTER: 6}, {DACE: 60})
    card = build_spot_card(river, junction())
    assert card.hook.text.startswith("Creek Chub and 2 more are recorded more often here")
    assert len(card.might_find.special_here.species) == 3


def test_the_special_block_carries_the_label_the_shares_and_the_caveat(river):
    fill(river, {CHUB: 12, SUCKER: 8}, {CHUB: 10, SUCKER: 50})
    block = build_spot_card(river, junction()).might_find.special_here
    assert block.title == "Recorded more often here"
    assert block.label == "Recorded more often here than along the rest of the Credit River"
    assert block.radius_km == 2.0
    (line,) = block.species
    assert line.name == "Creek Chub"
    assert line.scientific_name == "Semotilus atromaculatus"
    assert line.text == (
        "12 of 20 records here (60%), against 17% along the rest of the Credit River"
    )
    assert "Records follow where people look as well as where fish are" in block.caveat


def test_a_card_with_a_special_line_still_validates_and_reports_its_status(river):
    fill(river, {CHUB: 12, SUCKER: 8}, {CHUB: 10, SUCKER: 50})
    card = build_spot_card(river, junction())
    assert SpotCard.model_validate(card.model_dump()).meta.special_here == "found"


def test_the_card_makes_no_seasonal_claim_in_any_special_string(river):
    fill(river, {CHUB: 12, SUCKER: 8}, {CHUB: 10, SUCKER: 50})
    banned = re.compile(
        r"\b(spring|summer|autumn|fall|winter|january|february|march|april|may|june|july|"
        r"august|september|october|november|december|season\w*|month\w*|hot|best|popular)\b",
        re.IGNORECASE,
    )
    card = build_spot_card(river, junction())
    texts = [card.hook.text, *vars_of(card.might_find.special_here)]
    for text in texts:
        assert not banned.search(text), text
    fixed = [render.SPECIAL_TITLE, render.SPECIAL_LABEL, render.SPECIAL_HOOK, render.SPECIAL_LINE]
    fixed += [render.SPECIAL_CAVEAT, *render.SPECIAL_BASIS.values()]
    for text in fixed:
        assert not banned.search(text), text


def vars_of(block):
    out = [block.title, block.label, block.basis_text, block.caveat]
    out += [s.text for s in block.species]
    return out


def test_protected_species_replace_the_line_and_are_never_named(river):
    """Eckardt Creek's rule still comes first: a listed fish nearby means a care message only."""
    upsert_species_ranges(
        river, [species_row("Redside Dace", "Clinostomus elongatus", sara_status="Endangered")]
    )
    fill(river, {CHUB: 12, SUCKER: 8}, {CHUB: 10, SUCKER: 50})
    inat(river, "Clinostomus elongatus", "Redside Dace", near=HERE)
    card = build_spot_card(river, junction())
    assert card.hook.kind == "protected_species"
    assert card.might_find.special_here is None
    assert "redside" not in json.dumps(card.model_dump()).lower()


def test_a_listed_species_is_never_named_even_with_the_highest_share():
    here = [special._Record("Clinostomus elongatus", "Redside Dace", 43.5, -79.6, "sighting")] * 15
    here += [special._Record(CHUB[0], CHUB[1], 43.5, -79.6, "sighting")] * 5
    elsewhere = [special._Record("Lepomis gibbosus", "Pumpkinseed", 43.5, -79.4, "sighting")] * 60
    result = special._compare(here, elsewhere, "Credit River", frozenset({"clinostomus elongatus"}))
    assert "Redside Dace" not in names(result)
    assert result.here_total == 20  # still a real record, counted in the total


def test_the_line_reads_no_personal_data():
    """A card is the same for everyone: the comparison takes no user and reads no logged catches."""
    import inspect

    assert set(inspect.signature(special_here).parameters) == {"db", "spot", "protected"}
    source = inspect.getsource(special)
    assert "FROM stops" not in source and "user_id" not in source


def test_the_same_records_always_give_the_same_card(river):
    fill(river, {CHUB: 8, SUCKER: 6, DARTER: 6}, {DACE: 60})
    a = build_spot_card(river, junction()).model_dump()
    b = build_spot_card(river, junction()).model_dump()
    assert a == b


def test_quiet_water_has_no_comparison(river):
    fill(river, {CHUB: 12}, {SUCKER: 40})
    from tests.test_spot_card import spot

    card = build_spot_card(
        river,
        spot(
            "quiet_water",
            attrs={},
            structure_type="quiet_reach",
            title="A quiet part of Credit River",
        ),
    )
    assert card.meta.special_here == "not_applicable"
    assert card.might_find.special_here is None


def test_the_comparison_result_is_a_plain_model_of_counts():
    assert SpecialHere(status="no_river_section", radius_km=2.0).species == []
    with pytest.raises(Exception):
        SpecialHere(status="maybe", radius_km=2.0)


def test_eckardt_is_still_a_care_message_with_a_river_in_place(river):
    """The worked example from CLAUDE.md survives the new rule being in the chain."""
    upsert_species_ranges(
        river, [species_row("Redside Dace", "Clinostomus elongatus", sara_status="Endangered")]
    )
    inat(river, "Clinostomus elongatus", "Redside Dace", near=ECKARDT)
    eck = junction(lat=ECKARDT[0], lng=ECKARDT[1])
    assert build_spot_card(river, eck).hook.kind == "protected_species"
