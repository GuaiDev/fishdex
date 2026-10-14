"""Tests for the single renderer.

These assert the *answer*, not the shape. A test that checks a string is
non-empty would pass on a renderer that silently dropped every source, which
is the exact failure mode this module exists to prevent — so each test names
the substring it demands.
"""

from src.models.context import (
    AccessSlice,
    ConditionsSlice,
    ContextField,
    DerivedPattern,
    EmptyReason,
    ExploreResponse,
    ExploreResult,
    HistorySlice,
    Place,
    PlaceContext,
    Provenance,
    ProvenanceKind,
    RecordsSlice,
    SpeciesContext,
    SpeciesRecord,
    StructureSlice,
    UserLayer,
    WaterSlice,
)
from src.services.context import render


def _place() -> Place:
    return Place(
        query="Bronte Creek",
        name="Bronte Creek",
        lat=43.4012,
        lng=-79.7101,
        radius_km=5.0,
        jurisdiction="CA-ON",
    )


# -- provenance always travels with the value ----------------------------------


def test_recorded_value_renders_with_its_source():
    water = WaterSlice(
        substrate=ContextField.recorded(
            "limestone bedrock",
            source="MRD 128",
            meaning="runs clearer, holds temperature better",
        )
    )
    out = render.render_water(water)
    assert "limestone bedrock" in out
    assert "runs clearer" in out
    assert "MRD 128" in out


def test_inference_is_marked_as_reasoning_not_a_record():
    ctx = SpeciesContext(
        species="Least Darter",
        habitat_note=ContextField.inferred("prefers vegetated margins"),
        sar_alert=False,
        targeting_guidance_suppressed=False,
        sar_reason="verified",
    )
    out = render.render_species_context(ctx)
    assert "reasoning, no source" in out


def test_web_provenance_renders_as_unverified():
    records = RecordsSlice(
        species=[
            SpeciesRecord(
                species="Redside Dace",
                provenance=Provenance(
                    kind=ProvenanceKind.WEB,
                    source="TRCA report",
                    url="https://trca.ca/x",
                ),
            )
        ],
        total_count=1,
        radius_km=5.0,
        escalated_to_web=True,
    )
    out = render.render_records(records)
    assert "web, unverified" in out
    assert "live web search" in out


# -- empty always says why -----------------------------------------------------


def test_empty_records_carry_the_reason_not_just_absence():
    out = render.render_records(
        RecordsSlice(radius_km=5.0, empty_reason=EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
    )
    assert "source_does_not_cover_area" in out
    assert "statement about the corpus" in out


def test_unpopulated_field_is_distinguished_from_no_coverage():
    structure = StructureSlice(
        stream_order=ContextField.empty(EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE)
    )
    out = render.render_structure(structure)
    assert "not populated in them" in out
    assert "does not cover" not in out


def test_untouched_field_is_omitted_rather_than_reported_as_a_gap():
    """A slice the bundle never filled must not read as missing data."""
    out = render.render_access(
        AccessSlice(parking=ContextField.recorded("2 lots", source="OSM"))
    )
    assert "parking" in out
    assert "trails" not in out


def test_live_lookup_failure_reads_as_transient():
    out = render.render_conditions(
        ConditionsSlice(
            water_temp_c=ContextField.empty(EmptyReason.LIVE_LOOKUP_FAILED)
        )
    )
    assert "try again" in out


# -- record age ----------------------------------------------------------------


def test_old_record_is_flagged_but_still_shown():
    records = RecordsSlice(
        species=[
            SpeciesRecord(
                species="Silver Shiner",
                most_recent="2001-05-04",
                provenance=Provenance(kind=ProvenanceKind.RECORD, source="GBIF"),
            )
        ],
        total_count=1,
        radius_km=5.0,
    )
    out = render.render_records(records, today_year=2026)
    assert "2001-05-04" in out
    assert "may no longer be there" in out


def test_recent_record_is_not_flagged_as_old():
    records = RecordsSlice(
        species=[
            SpeciesRecord(
                species="Creek Chub",
                most_recent="2024-08-01",
                provenance=Provenance(kind=ProvenanceKind.RECORD, source="iNaturalist"),
            )
        ],
        total_count=1,
        radius_km=5.0,
    )
    out = render.render_records(records, today_year=2026)
    assert "may no longer be there" not in out


def test_obscured_observation_is_labelled_not_hidden():
    records = RecordsSlice(
        species=[
            SpeciesRecord(
                species="Redside Dace",
                is_obscured=True,
                provenance=Provenance(kind=ProvenanceKind.RECORD, source="iNaturalist"),
            )
        ],
        total_count=1,
        radius_km=5.0,
    )
    out = render.render_records(records)
    assert "iNaturalist" in out, "attribution must never be reduced"
    assert "fuzzed" in out, "precision must be reduced"


# -- conservation --------------------------------------------------------------


def test_sar_flag_precedes_everything_and_suppresses_targeting():
    ctx = SpeciesContext(
        species="Redside Dace",
        habitat_note=ContextField.inferred("cool headwater streams"),
        sar_alert=True,
        status_known_listed=True,
        sar_reason="Listed: Endangered (source: SARA Schedule 1).",
    )
    out = render.render_species_context(ctx)
    flag_at = out.index("Conservation flag")
    habitat_at = out.index("habitat")
    assert flag_at < habitat_at
    assert "Do not suggest how to target" in out


def test_unverified_status_cautions_without_forbidding():
    """Every species is unverified today. A refusal on all of them is no rule."""
    ctx = SpeciesContext(
        species="Yellow Perch",
        habitat_note=ContextField.inferred("weed edges"),
        sar_alert=True,
        status_known_listed=False,
        sar_reason="Conservation status has not been verified.",
    )
    out = render.render_species_context(ctx)
    assert "Conservation flag" in out
    assert "unverified" in out
    assert "Do not suggest how to target" not in out


# -- history: blanks are first-class -------------------------------------------


def test_blanks_are_printed_not_implied():
    out = render.render_history(
        HistorySlice(visits=5, productive_visits=2, blanks=3, species_caught=["chub"])
    )
    assert "3 blank" in out


def test_no_history_says_which_kind_of_empty():
    out = render.render_history(
        HistorySlice(empty_reason=EmptyReason.USER_NEVER_FISHED_HERE)
    )
    assert "user_never_fished_here" in out


# -- explore -------------------------------------------------------------------


def test_explore_surfaces_ties_as_arbitrary_ordering():
    resp = ExploreResponse(
        results=[
            ExploreResult(
                ogf_id=i,
                lat=43.0,
                lng=-79.0,
                score=0.5,
                observation_pressure=0.1,
                access_score=0.4,
            )
            for i in range(2)
        ],
        tied_at_top=340,
    )
    out = render.render_explore(resp)
    assert "340 candidates share the top score" in out
    assert "arbitrary" in out


def test_explore_states_the_score_is_not_a_fish_prediction():
    resp = ExploreResponse(
        results=[
            ExploreResult(
                ogf_id=1,
                lat=43.0,
                lng=-79.0,
                score=0.5,
                observation_pressure=0.1,
                access_score=0.4,
            )
        ]
    )
    out = render.render_explore(resp)
    assert "NOT that fish are here" in out


def test_explore_reports_gate_exclusions_rather_than_hiding_them():
    resp = ExploreResponse(
        results=[
            ExploreResult(
                ogf_id=1,
                lat=43.0,
                lng=-79.0,
                score=0.5,
                observation_pressure=0.1,
                access_score=0.4,
            )
        ],
        excluded_count=4,
        excluded_examples=["dissolved oxygen 2.1 mg/L — below any fish tolerance"],
    )
    out = render.render_explore(resp)
    assert "4 candidate(s) excluded" in out
    assert "2.1 mg/L" in out


# -- user layer ----------------------------------------------------------------


def test_unclaimable_pattern_is_shown_and_marked():
    layer = UserLayer(
        user_id=1,
        total_sessions=2,
        total_stops=2,
        patterns=[
            DerivedPattern(
                statement="more productive in stained water",
                sample_size=1,
                comparison_size=1,
            )
        ],
    )
    out = render.render_user_layer(layer)
    assert "NOT yet claimable" in out


def test_claimable_pattern_is_marked_claimable():
    layer = UserLayer(
        user_id=1,
        total_sessions=8,
        total_stops=12,
        patterns=[
            DerivedPattern(
                statement="more productive in stained water",
                sample_size=7,
                comparison_size=5,
                confidence="high",
            )
        ],
    )
    out = render.render_user_layer(layer)
    assert "claimable" in out
    assert "NOT yet claimable" not in out


def test_targets_are_labelled_as_derived_not_configured():
    layer = UserLayer(
        user_id=1,
        total_sessions=4,
        total_stops=9,
        target_species=["greater redhorse"],
    )
    out = render.render_user_layer(layer)
    assert "from logs, not configured" in out


def test_the_user_layer_never_prints_a_skill_level():
    from src.models.angler_profile import AnglerProfile, BlendedValue, ConceptState

    def bv(v):
        return BlendedValue(value=v, confidence=0.3)

    layer = UserLayer(
        user_id=1,
        total_sessions=4,
        total_stops=9,
        profile=AnglerProfile(
            user_id=1,
            orientation=bv(0.4),
            size_focus=bv(0.5),
            micro_focus=bv(0.1),
            concepts={
                "confluence": ConceptState(term_id="confluence", state="familiar"),
                "dam": ConceptState(term_id="dam", state="introduced"),
            },
        ),
    )
    out = render.render_user_layer(layer)
    assert "0.40" in out and "confidence 0.30" in out
    assert "say plainly): confluence" in out
    assert "define briefly): dam" in out
    for word in ("expertise", "novice", "beginner", "advanced", "expert"):
        assert word not in out.lower()


# -- whole context -------------------------------------------------------------


def test_place_context_renders_only_the_slices_the_bundle_populated():
    ctx = PlaceContext(
        place=_place(),
        records=RecordsSlice(radius_km=5.0, empty_reason=EmptyReason.NO_RECORDS_IN_RADIUS),
        bundle="map_tap",
    )
    out = render.render_place_context(ctx)
    assert "Bronte Creek" in out
    assert "Species recorded" in out
    assert "Conditions now" not in out
    assert "Your history here" not in out


# -- access placeholders must not read as readings ------------------------------

def _explore_result(ogf_id: int, measured: bool | None, score: float = 0.62):
    """Built the way explore() builds them, so the mapping itself is under test."""
    from src.services.context import _access_field

    return ExploreResult(
        ogf_id=ogf_id,
        lat=43.5,
        lng=-79.8,
        score=0.4,
        observation_pressure=0.02,
        access_score=score,
        access_is_measured=measured,
        access=_access_field(score, measured),
    )


def test_placeholder_access_is_named_not_printed_as_a_number():
    """A default is not a measurement, and 0.27 looks exactly like one.

    Segments outside the ~55 km OSM footprint have no access data at all and
    normalise to a mid-range value. Printing that as "access 0.27" is the
    barrier-fabrication bug in the ranking function: a default presented as a
    reading, except here it silently reorders results instead of stating a
    false fact outright.
    """
    resp = ExploreResponse(
        results=[_explore_result(1, False, score=0.27)],
        results_on_placeholder_access=1,
    )
    out = render.render_explore(resp)
    assert "0.27" not in out
    assert "does not cover this area" in out, "wording comes from _EMPTY_PHRASING"
    assert "Access is unmapped for 1 of these 1" in out


def test_measured_access_prints_its_number_with_a_so_what():
    resp = ExploreResponse(results=[_explore_result(1, True, score=0.62)])
    out = render.render_explore(resp)
    assert "access 0.62" in out
    assert "OSM access points" in out, "a value never appears without its source"
    assert "expect a walk in" in out, "and never without its meaning"


def test_the_rest_of_the_ranking_is_not_disclaimed_away():
    """Pressure, structure and remoteness are real outside the footprint."""
    resp = ExploreResponse(
        results=[_explore_result(1, False, score=0.27)],
        results_on_placeholder_access=1,
    )
    out = render.render_explore(resp)
    assert "pressure 0.02" in out
    assert "What ranked them" in out


def test_unknown_coverage_is_not_reported_as_remoteness():
    """A stale cache is a pipeline gap. Saying "outside the footprint" invents
    a fact about the water from a missing parquet column."""
    resp = ExploreResponse(
        results=[_explore_result(1, None, score=0.27)],
        results_with_unknown_access_coverage=1,
    )
    out = render.render_explore(resp)
    assert "this field is not populated in them" in out
    assert "does not cover this area" not in out
    assert "Recomputing access scores would settle it" in out


def test_the_remedy_for_a_pipeline_gap_is_not_a_shell_command():
    """An operator instruction in angler-facing prose is a debug line."""
    resp = ExploreResponse(
        results=[_explore_result(1, None)],
        results_with_unknown_access_coverage=1,
    )
    out = render.render_explore(resp)
    assert "make compute-access" not in out
    assert "parquet" not in out


def test_the_three_access_states_render_differently():
    resp = ExploreResponse(
        results=[
            _explore_result(1, True),
            _explore_result(2, False),
            _explore_result(3, None),
        ],
        results_on_placeholder_access=1,
        results_with_unknown_access_coverage=1,
    )
    out = render.render_explore(resp)
    assert "access 0.62" in out
    assert "does not cover this area" in out
    assert "this field is not populated in them" in out
    assert "Access is unmapped for 1 of these 3" in out
    assert "For 1 of these 3" in out
