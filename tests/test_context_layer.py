"""Tests for the central context layer.

All synthetic, all against a tmp_path database — never the production DB.
"""

import json

import pytest

from src.models.context import (
    ContextField,
    DerivedPattern,
    EmptyReason,
    Provenance,
    ProvenanceKind,
)
from src.services.context import _BUNDLES, describe, translate
from src.services.context import place as place_mod
from src.services.context.escalation import BLOCKED_DOMAINS, filter_web_results, is_blocked
from src.services.context.user import build_user_layer
from src.storage.database import get_db


@pytest.fixture
def db(tmp_path):
    return get_db(tmp_path / "ctx.db")


def _add_stop(
    db,
    *,
    user_id=1,
    lat=43.40,
    lng=-79.75,
    name="Bronte Creek",
    species=("creek chub",),
    productive=True,
    technique="drift",
    clarity=None,
    date="2025-06-14",
):
    db["sessions"].insert({"date": date, "user_id": user_id})
    sid = db.execute("SELECT MAX(id) FROM sessions").fetchone()[0]
    db["stops"].insert({
        "session_id": sid,
        "user_id": user_id,
        "location_text": name,
        "location_name": name,
        "lat": lat,
        "lng": lng,
        "species_caught": json.dumps(list(species)),
        "party_species_caught": json.dumps([]),
        "was_productive": 1 if productive else 0,
        "technique": technique,
        "water_clarity": clarity,
    })


# ── provenance and empty-reason contract ──────────────────────────────────────


def test_web_provenance_can_never_be_verified():
    p = Provenance(kind=ProvenanceKind.WEB, source="example.com", verified=True)
    assert p.verified is False


def test_record_provenance_stays_verified():
    p = Provenance(kind=ProvenanceKind.RECORD, source="iNaturalist")
    assert p.verified is True


def test_inference_renders_distinctly_from_record():
    """A principle must not read like a recorded fact."""
    inferred = ContextField.inferred("cats hold deeper", meaning="26C water")
    recorded = ContextField.recorded("channel catfish", source="iNaturalist")
    assert "no source" in inferred.explain()
    assert "no source" not in recorded.explain()


def test_all_four_empty_reasons_render_differently():
    rendered = {ContextField.empty(r).explain() for r in EmptyReason}
    assert len(rendered) == len(EmptyReason)
    assert not any("no data" == t for t in rendered)


def test_empty_field_is_empty_and_valued_field_is_not():
    assert ContextField.empty(EmptyReason.WEB_SEARCH_EMPTY).is_empty
    assert not ContextField.recorded(1, source="OHN").is_empty


# ── translation: the "so what" rule ───────────────────────────────────────────


def test_translation_gives_meaning_to_useful_values():
    assert "clearer" in translate.substrate("bedrock")
    assert "barrier lies upstream" in translate.barriers(2, 0)
    assert "year-round" in translate.ept_proportion(0.42)


def test_translation_refuses_values_with_no_so_what():
    """If you can't write the 'so what', don't show the number."""
    assert translate.conductivity(340.0) is None
    assert translate.turbidity_fnu(12.0) is None
    assert translate.pressure_trend("steady") is None
    assert translate.ph(7.2) is None  # mid-range pH tells an angler nothing
    assert translate.thermal_regime("unknown") is None


def test_translation_surfaces_ph_only_at_extremes():
    assert translate.ph(4.9) is not None
    assert translate.ph(9.6) is not None


def test_barrier_translation_distinguishes_direction():
    both = translate.barriers(1, 1)
    up_only = translate.barriers(2, 0)
    down_only = translate.barriers(0, 2)
    assert len({both, up_only, down_only}) == 3
    assert "downstream" in down_only


# ── escalation blocklist ──────────────────────────────────────────────────────


def test_blocklist_catches_subdomains_and_bare_domains():
    for url in (
        "https://fishbrain.com/spot/1",
        "https://www.fishbrain.com/x",
        "https://m.facebook.com/groups/1",
        "http://instagram.com/p/abc",
    ):
        assert is_blocked(url), url


def test_blocklist_does_not_overmatch_lookalike_domains():
    assert not is_blocked("https://notfishbrain.com/report")
    assert not is_blocked("https://inaturalist.org/observations/1")


def test_blocklist_rejects_malformed_urls():
    assert is_blocked("")
    assert is_blocked("not-a-url")


def test_filter_web_results_drops_blocked_sources():
    kept = filter_web_results([
        {"url": "https://inaturalist.org/a"},
        {"url": "https://fishbrain.com/b"},
        {"url": "https://ontario.ca/c"},
    ])
    assert [r["url"] for r in kept] == [
        "https://inaturalist.org/a",
        "https://ontario.ca/c",
    ]


def test_every_ethics_rule_domain_is_blocked():
    """CLAUDE.md names these explicitly; the list must not silently shrink."""
    for d in ("instagram.com", "facebook.com", "tiktok.com", "fishbrain.com", "fishangler.com"):
        assert d in BLOCKED_DOMAINS


# ── place resolution ──────────────────────────────────────────────────────────


def test_resolve_from_latlng(db):
    p = place_mod.resolve(db, lat=43.4, lng=-79.75)
    assert p is not None
    assert p.resolved_by == "latlng"
    assert p.lat == pytest.approx(43.4)


def test_resolve_from_user_logged_name(db):
    _add_stop(db, name="Byng Island")
    p = place_mod.resolve(db, query="byng")
    assert p is not None
    assert p.resolved_by == "user_log"
    assert p.name == "Byng Island"


def test_resolve_unknown_name_returns_none_not_a_guess(db):
    """An unresolvable name must fail honestly, not resolve to the wrong river."""
    assert place_mod.resolve(db, query="Nonexistent Brook") is None


def _add_segment(db, ogf_id, name, lat, lng, stream_order=3):
    """OHN segments carry geometry as WKT, not a centroid pair."""
    db["stream_segments"].insert({
        "ogf_id": ogf_id,
        "name": name,
        "watercourse_type": "Stream",
        "geom_wkt": f"LINESTRING({lng - 0.001} {lat - 0.001}, {lng + 0.001} {lat + 0.001})",
        "stream_order": stream_order,
        "jurisdiction": "CA-ON",
    })


def test_segment_centroid_is_averaged_from_wkt(db):
    _add_segment(db, 77, "Test Creek", 43.5, -79.8)
    p = place_mod.resolve(db, segment_id=77)
    assert p.lat == pytest.approx(43.5, abs=1e-6)
    assert p.lng == pytest.approx(-79.8, abs=1e-6)


def test_resolve_prefers_the_users_own_name_for_a_place(db):
    """If someone logs 'the dam' six times, that phrase means their dam."""
    _add_segment(db, 99, "The Dam Creek", 45.0, -80.0)
    _add_stop(db, name="the dam", lat=43.4, lng=-79.75)
    p = place_mod.resolve(db, query="the dam")
    assert p.resolved_by == "user_log"
    assert p.lat == pytest.approx(43.4)


def test_resolve_by_segment_id(db):
    _add_segment(db, 1234, "Sixteen Mile Creek", 43.45, -79.70, stream_order=4)
    p = place_mod.resolve(db, segment_id=1234)
    assert p is not None
    assert p.resolved_by == "segment_id"
    assert p.name == "Sixteen Mile Creek"


def test_resolve_falls_back_to_ohn_name_when_user_has_no_log(db):
    _add_segment(db, 555, "Sixteen Mile Creek", 43.45, -79.70)
    p = place_mod.resolve(db, query="sixteen mile")
    assert p is not None
    assert p.resolved_by == "name"
    assert "OHN" in (p.resolution_note or "")


def test_resolve_missing_segment_id_returns_none(db):
    _add_segment(db, 1, "x", 43.0, -79.0)
    assert place_mod.resolve(db, segment_id=404040) is None


def test_stream_order_reaches_structure_slice(db):
    _add_segment(db, 4242, "Order Creek", 43.4, -79.75, stream_order=4)
    ctx = describe(db, lat=43.4, lng=-79.75, caller="map_tap")
    assert ctx.structure.stream_order.value == 4
    assert ctx.structure.stream_order.meaning is not None


def test_resolve_with_no_input_returns_none(db):
    assert place_mod.resolve(db) is None


# ── describe() bundling ───────────────────────────────────────────────────────


def test_map_tap_bundle_omits_live_conditions(db):
    """A map tap should be free — no live API call for conditions."""
    ctx = describe(db, lat=43.4, lng=-79.75, caller="map_tap")
    assert ctx is not None
    assert ctx.conditions is None
    assert ctx.records is not None


def test_full_bundle_populates_every_slice(db):
    ctx = describe(db, lat=43.4, lng=-79.75, caller="full")
    for name in ("records", "water", "structure", "access", "history"):
        assert getattr(ctx, name) is not None, name


def test_bundles_are_defined_for_every_caller_type(db):
    for caller in _BUNDLES:
        ctx = describe(db, lat=43.4, lng=-79.75, caller=caller)
        assert ctx is not None
        assert ctx.bundle == caller


def test_describe_returns_none_for_unresolvable_place(db):
    """Unresolvable is a different failure from 'resolved but empty'."""
    assert describe(db, query="Nowhere At All") is None


def test_describe_of_unfished_water_says_so_specifically(db):
    ctx = describe(db, lat=50.0, lng=-90.0, caller="full")
    assert ctx.history.empty_reason is EmptyReason.USER_NEVER_FISHED_HERE
    assert ctx.history.visits == 0


def test_history_counts_blanks_separately(db):
    _add_stop(db, productive=True, species=("creek chub",))
    _add_stop(db, productive=False, species=())
    ctx = describe(db, lat=43.40, lng=-79.75, caller="full")
    assert ctx.history.visits == 2
    assert ctx.history.productive_visits == 1
    assert ctx.history.blanks == 1


def test_history_is_isolated_between_users(db):
    """Same leak class as the coaching bug — must not recur here."""
    _add_stop(db, user_id=2, name="Byng Island", species=("channel catfish",))
    ctx = describe(db, lat=43.40, lng=-79.75, caller="full", user_id=1)
    assert ctx.history.visits == 0
    assert ctx.history.empty_reason is EmptyReason.USER_NEVER_FISHED_HERE


def test_records_are_isolated_between_users(db):
    _add_stop(db, user_id=2, species=("channel catfish",))
    ctx = describe(db, lat=43.40, lng=-79.75, caller="full", user_id=1)
    names = [r.species.lower() for r in ctx.records.species]
    assert "channel catfish" not in names


def test_user_catches_become_records_with_provenance(db):
    _add_stop(db, user_id=1, species=("channel catfish",))
    ctx = describe(db, lat=43.40, lng=-79.75, caller="full", user_id=1)
    rec = next(r for r in ctx.records.species if r.species.lower() == "channel catfish")
    assert rec.provenance.kind is ProvenanceKind.RECORD
    assert "your logged catch" in rec.provenance.source


def test_empty_records_distinguishes_no_coverage_from_no_sightings(db):
    """With no corpus at all, the reason is coverage — not an empty radius."""
    ctx = describe(db, lat=43.4, lng=-79.75, caller="map_tap")
    assert ctx.records.empty_reason is EmptyReason.SOURCE_DOES_NOT_COVER_AREA

    db["observations"].insert({
        "observation_id": 1, "species": "Semotilus atromaculatus", "common_name": "Creek Chub",
        "lat": 10.0, "lng": 10.0, "observed_on": "2025-01-01", "source": "iNaturalist",
        "jurisdiction": "CA-ON",
    })
    ctx2 = describe(db, lat=43.4, lng=-79.75, caller="map_tap")
    assert ctx2.records.empty_reason is EmptyReason.NO_RECORDS_IN_RADIUS


# ── user_layer ────────────────────────────────────────────────────────────────


def test_user_layer_empty_log_reports_a_gap(db):
    layer = build_user_layer(db, user_id=1)
    assert layer.total_stops == 0
    assert layer.known_gaps


def test_target_species_inferred_from_repeated_logging(db):
    for _ in range(3):
        _add_stop(db, species=("greater redhorse",))
    _add_stop(db, species=("bluegill",))
    layer = build_user_layer(db, user_id=1)
    assert "greater redhorse" in layer.target_species
    assert "bluegill" not in layer.target_species  # one catch is not a target


def test_expertise_is_demonstrated_not_declared(db):
    for sp in ("tadpole madtom", "rainbow darter", "northern redbelly dace"):
        _add_stop(db, species=(sp,))
    layer = build_user_layer(db, user_id=1)
    assert layer.expertise == "advanced"


def test_beginner_log_does_not_read_as_advanced(db):
    for _ in range(3):
        _add_stop(db, species=("bluegill",))
    assert build_user_layer(db, user_id=1).expertise in {"novice", "intermediate"}


def test_user_layer_is_isolated_between_users(db):
    for _ in range(4):
        _add_stop(db, user_id=2, species=("channel catfish",))
    layer = build_user_layer(db, user_id=1)
    assert layer.total_stops == 0
    assert layer.species_logged == []


def test_personal_pattern_needs_both_arms_of_a_comparison():
    """'You do better in stained water' requires trips in both conditions."""
    one_sided = DerivedPattern(statement="x", sample_size=9, comparison_size=0)
    assert not one_sided.is_claimable

    both = DerivedPattern(statement="x", sample_size=3, comparison_size=2)
    assert both.is_claimable


def test_single_condition_log_yields_no_claimable_pattern(db):
    for _ in range(5):
        _add_stop(db, clarity="stained", productive=True)
    layer = build_user_layer(db, user_id=1)
    assert all(not p.is_claimable for p in layer.patterns)


def test_pattern_becomes_claimable_with_a_comparison_set(db):
    for _ in range(4):
        _add_stop(db, clarity="stained", productive=True)
    for _ in range(3):
        _add_stop(db, clarity="clear", productive=False)
    layer = build_user_layer(db, user_id=1)
    stained = [p for p in layer.patterns if "stained" in p.statement]
    assert stained and stained[0].is_claimable


def test_known_gaps_name_the_consequence_not_just_the_gap(db):
    for _ in range(4):
        _add_stop(db, clarity=None, technique=None)
    gaps = build_user_layer(db, user_id=1).known_gaps
    assert any("can't" in g for g in gaps)


def test_explore_reports_ties_at_the_top(monkeypatch, db):
    """Coarse terms produce large ties; the response must admit that."""
    import pandas as pd

    import src.services.context as ctx_mod

    tied = pd.DataFrame({
        "ogf_id": [1, 2, 3],
        "centroid_lat": [43.4, 43.41, 43.42],
        "centroid_lng": [-79.75, -79.76, -79.77],
        "stream_order": [3, 3, 3],
        "watercourse_name": ["A", "B", "C"],
        "watercourse_type": ["Stream"] * 3,
        "untapped_score_balanced": [1.89, 1.89, 0.5],
        "observation_pressure": [0.1, 0.1, 0.4],
        "access_score": [0.3, 0.3, 0.3],
        "is_confluence_segment": [False] * 3,
        "do_median_mgl": [float("nan")] * 3,
    })
    monkeypatch.setattr(
        "src.services.untapped_potential.load_cached_untapped", lambda: tied
    )
    resp = ctx_mod.explore(db, lat=43.41, lng=-79.76, radius_km=20)
    assert resp.tied_at_top == 2


def test_blank_rate_is_computed(db):
    _add_stop(db, productive=True)
    _add_stop(db, productive=False)
    assert build_user_layer(db, user_id=1).blank_rate == pytest.approx(0.5)


# ── regressions found by running against real data ────────────────────────────
#
# Synthetic fixtures passed while all of these were broken. Each one is a bug
# that only appeared once describe() ran over a real corpus.


def test_empty_barriers_table_never_fabricates_free_passage(db):
    """The severe one: an empty table must not become a confident claim.

    ensure_schema() creates `barriers` whether or not it was ever ingested.
    Counting 0 up / 0 down there produced a record-tagged "fish can move
    through freely" — an assertion of fact built from no evidence at all.
    """
    assert "barriers" in db.table_names()
    assert db["barriers"].count == 0

    ctx = describe(db, lat=52.0, lng=-87.0, caller="map_tap")
    up = ctx.structure.barriers_upstream
    assert up.is_empty
    assert up.empty_reason is EmptyReason.SOURCE_DOES_NOT_COVER_AREA
    assert up.provenance is None, "an absent value must carry no provenance"
    assert "freely" not in (up.meaning or "")


def test_every_context_field_carries_a_reason_when_empty(db):
    """No field may render the internal 'no value and no reason' fallback."""
    ctx = describe(db, lat=52.0, lng=-87.0, caller="full")
    for slice_name in ("water", "structure", "access"):
        sl = getattr(ctx, slice_name)
        for fname, f in sl.__dict__.items():
            if f.is_empty:
                assert f.empty_reason is not None, f"{slice_name}.{fname} has no empty_reason"
                assert "this is a bug" not in f.explain(), f"{slice_name}.{fname}"


def test_substrate_reads_the_real_geology_field(db):
    """GeologyUnit exposes substrate_class; the code read substrate_category.

    The mismatch meant real substrate was fetched, then silently discarded.
    """
    db["geology_units"].insert({
        "unit_id": "G1", "tile_id": "T1", "unit_code": "coarse", "unit_name": "coarse",
        "primary_material": "coarse", "substrate_class": "coarse", "jurisdiction": "CA-ON",
        "centroid_lat": 43.40, "centroid_lng": -79.75,
        "bbox_minx": -79.8, "bbox_miny": 43.3, "bbox_maxx": -79.7, "bbox_maxy": 43.5,
    }, alter=True)
    ctx = describe(db, lat=43.40, lng=-79.75, caller="map_tap")
    assert ctx.water.substrate.value == "coarse"
    assert "gravel" in (ctx.water.substrate.meaning or "")


def test_measured_but_unremarkable_ph_is_not_reported_as_absent(db):
    """A mid-range pH was measured. Calling that 'nothing recorded' is false."""
    db["water_quality_readings"].insert({
        "record_id": "W1", "station_id": "S1", "station_name": "t",
        "lat": 43.40, "lng": -79.75, "jurisdiction": "CA-ON",
        "sampled_at": "2024-06-01", "do_mgl": None, "ph": 7.2,
        "temp_c": None, "conductivity_us_cm": None, "turbidity_fnu": None,
    }, alter=True)
    ph = describe(db, lat=43.40, lng=-79.75, caller="map_tap").water.ph
    assert ph.is_empty
    assert ph.empty_reason is EmptyReason.RECORDED_BUT_NOT_DECISION_RELEVANT
    assert "nothing recorded" not in ph.explain()


def test_stream_order_gap_is_a_radius_gap_not_a_coverage_gap(db):
    """OHN covers Ontario. Holding segments but none nearby is NOT 'not covered'."""
    _add_segment(db, 1, "Far Away Creek", 45.0, -80.0)
    order = describe(db, lat=43.40, lng=-79.75, caller="map_tap").structure.stream_order
    assert order.is_empty
    assert order.empty_reason is EmptyReason.NO_RECORDS_IN_RADIUS


def test_place_resolution_does_not_truncate_the_segment_scan(db):
    """Resolution must not depend on row order.

    segments_near() capped the SCAN at 5000 rows, so with a full Ontario table
    a place could resolve to zero segments purely because its rows sat late in
    the table — and then report itself as uncovered.
    """
    from src.services.context.place import segments_near

    for i in range(60):
        _add_segment(db, 9000 + i, f"Filler {i}", 46.0 + i * 0.001, -81.0)
    _add_segment(db, 12345, "Target Creek", 43.40, -79.75)  # inserted last

    found = segments_near(db, 43.40, -79.75, 5)
    assert 12345 in found, "a late-inserted segment must still resolve"


# ── regressions found on the full real corpus ─────────────────────────────────


def _add_barrier(db, lat, lng, ogf_id=1):
    db["barriers"].insert({
        "ogf_id": ogf_id, "barrier_type": "dam",
        "geom_wkt": f"POINT({lng} {lat})",
        "nearest_segment_ogf_id": ogf_id, "snap_distance_m": 10.0,
        "jurisdiction": "CA-ON",
    }, alter=True, pk="ogf_id", replace=True)


def test_barrier_absence_requires_local_coverage_not_just_a_nonempty_table(db):
    """The fabrication bug's second form.

    Checking only that `barriers` has rows is not enough: ingest is radius
    limited, so a corpus covering just southern Ontario would let a query at
    52N assert "fish can move through freely" from no local evidence.
    """
    _add_barrier(db, 43.40, -79.75)          # southern Ontario only
    _add_segment(db, 1, "Far Creek", 52.0, -87.0)

    far = describe(db, lat=52.0, lng=-87.0, caller="map_tap").structure.barriers_upstream
    assert far.is_empty
    assert far.empty_reason is EmptyReason.SOURCE_DOES_NOT_COVER_AREA
    assert far.provenance is None
    assert "freely" not in (far.meaning or "")


def test_barrier_absence_is_a_real_claim_where_coverage_exists(db):
    """Symmetry check: with local coverage, zero barriers IS a fact."""
    _add_barrier(db, 43.40, -79.75)
    near = describe(db, lat=43.42, lng=-79.76, caller="map_tap").structure.barriers_upstream
    assert not near.is_empty
    assert near.provenance.kind is ProvenanceKind.RECORD


def test_unpopulated_column_is_not_reported_as_absent_data(db):
    """OHN ingest populated 0 of 20,339 stream_order values on the real corpus.

    Segments exist here, so "nothing recorded within the search radius" would
    blame the world for a gap in our own pipeline.
    """
    db["stream_segments"].insert({
        "ogf_id": 501, "name": "No Order Creek", "watercourse_type": "Stream",
        "geom_wkt": "LINESTRING(-79.751 43.399, -79.749 43.401)",
        "stream_order": None, "jurisdiction": "CA-ON",
    }, alter=True)
    order = describe(db, lat=43.40, lng=-79.75, caller="map_tap").structure.stream_order
    assert order.is_empty
    assert order.empty_reason is EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE
    assert "nothing recorded" not in order.explain()


def test_no_segments_at_all_still_reads_as_a_radius_gap(db):
    """Distinct from the above: segments exist in the corpus, just not here."""
    _add_segment(db, 601, "Elsewhere Creek", 45.0, -80.0, stream_order=3)
    order = describe(db, lat=43.40, lng=-79.75, caller="map_tap").structure.stream_order
    assert order.empty_reason is EmptyReason.NO_RECORDS_IN_RADIUS


def test_conditions_assigns_every_field_even_when_the_live_source_fails(db, monkeypatch):
    """Found by running a real query: three fields rendered the bug string.

    build_conditions only set air_temp_c and pressure_trend, and only on the
    success path. A failed weather fetch left flow/water_temp/air_temp
    default-constructed — no value AND no reason.
    """
    import src.services.context.slices as slices

    def boom(**kwargs):
        raise RuntimeError("weather unreachable")

    def gauge_down(*args, **kwargs):
        raise RuntimeError("gauge unreachable")

    monkeypatch.setattr("src.services.weather.get_conditions_for_agent", boom)
    monkeypatch.setattr("src.services.stream_gauge.nearest_gauge", gauge_down)

    ctx = describe(db, lat=43.5474, lng=-80.1973, caller="coach")
    for fname, f in ctx.conditions.__dict__.items():
        assert f.empty_reason is not None, f"conditions.{fname} has no empty_reason"
        assert "this is a bug" not in f.explain(), fname

    # a transient outage must not read as permanent missing coverage
    assert ctx.conditions.air_temp_c.empty_reason is EmptyReason.LIVE_LOOKUP_FAILED
    assert "try again" in ctx.conditions.air_temp_c.explain()
    assert slices  # module imported for the monkeypatch target


# ── bugs the real corpus exposed that synthetic fixtures could not ────────────
#
# Both of these were found by running describe() against the full 20k-segment
# database, not by any test in this file. A small fixture has one creek called
# Sixteen Mile Creek and one record per species, so neither failure had
# anywhere to occur.


def test_ambiguous_name_resolves_nearest_home_and_says_so(db, monkeypatch):
    """Ontario has 33 Sixteen Mile Creeks. Picking row one is picking at random."""
    monkeypatch.setattr(place_mod, "_home_point", lambda: (43.4675, -79.6877))

    _add_segment(db, 1, "Sixteen Mile Creek", 43.0779, -79.4465)  # near Jordan
    _add_segment(db, 2, "Sixteen Mile Creek", 43.5140, -79.8844)  # near Oakville

    p = place_mod.resolve(db, query="Sixteen Mile Creek")
    assert p is not None
    assert p.lat == pytest.approx(43.5140, abs=0.01)
    assert "other watercourse(s) match this name" in (p.resolution_note or "")


def test_exact_name_beats_a_nearer_substring_match(db, monkeypatch):
    """'Sixteen Mile Creek' must not resolve to East Sixteen Mile Creek."""
    monkeypatch.setattr(place_mod, "_home_point", lambda: (43.4675, -79.6877))

    _add_segment(db, 1, "East Sixteen Mile Creek", 43.4700, -79.6900)  # closer
    _add_segment(db, 2, "Sixteen Mile Creek", 43.5140, -79.8844)  # further

    p = place_mod.resolve(db, query="Sixteen Mile Creek")
    assert p.name == "Sixteen Mile Creek"


def test_merged_record_keeps_date_source_and_precision_together(db):
    """One record's date must not be shown under another record's source.

    A 2025 obscured iNaturalist sighting and a 1979 precise museum specimen of
    the same species merged into "most recent 2025-10-29 [GBIF, 1979-09-06]" —
    the recent date wearing the old record's attribution, because precision and
    recency were being resolved by two independent rules.
    """
    from src.models.context import SpeciesRecord
    from src.services.context.slices import _merge

    found: dict = {}
    _merge(
        found,
        SpeciesRecord(
            species="Semotilus atromaculatus",
            most_recent="2025-10-29",
            is_obscured=True,
            provenance=Provenance(
                kind=ProvenanceKind.RECORD, source="iNaturalist", date="2025-10-29"
            ),
        ),
    )
    _merge(
        found,
        SpeciesRecord(
            species="Semotilus atromaculatus",
            most_recent="1979-09-06",
            is_obscured=False,
            provenance=Provenance(
                kind=ProvenanceKind.RECORD, source="GBIF/Fish", date="1979-09-06"
            ),
        ),
    )

    rec = found["semotilus atromaculatus"]
    assert rec.count == 2, "both records still counted"
    assert rec.most_recent == "2025-10-29"
    assert rec.provenance.date == "2025-10-29"
    assert rec.provenance.source == "iNaturalist"
    assert rec.is_obscured is True, "precision describes the record being shown"
