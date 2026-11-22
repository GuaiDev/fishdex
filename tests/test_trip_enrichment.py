"""Tests for trip enrichment service."""

import json

from sqlite_utils import Database

from src.models.behavioral_insight import BehavioralInsight
from src.storage.database import ensure_schema, migrate_behavioral_insights, migrate_stops
from src.storage.insights import get_insight, insert_insight


def _make_db(tmp_path) -> Database:
    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    migrate_behavioral_insights(db)
    migrate_stops(db)
    return db


def _catfish_insight(**overrides) -> BehavioralInsight:
    base = dict(
        user_id=1,
        species="channel catfish",
        condition_type="temporal",
        condition_context="midday-summer",
        conclusion="test conclusion",
        confidence="low",
        source_type="agent_synthesis",
        source_detail="test",
        evidence_count=0,
    )
    base.update(overrides)
    return BehavioralInsight(**base)


# ── pure-function tests (no DB needed) ────────────────────────────────────────


def test_get_season():
    from src.services.trip_enrichment import get_season

    assert get_season(6) == "summer"
    assert get_season(1) == "winter"
    assert get_season(4) == "spring"
    assert get_season(10) == "fall"
    assert get_season(12) == "winter"
    assert get_season(9) == "fall"


def test_identify_condition_gaps_weather():
    from src.services.trip_enrichment import identify_condition_gaps

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="temporal",
        condition_context="midday-summer",
        conclusion="Fishes best post-rain with overcast skies",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        user_id=1,
    )
    stop = {"weather_notes": None, "technique": "cut bait", "water_level": "normal"}
    gaps = identify_condition_gaps(stop, insight)
    assert "weather" in gaps


def test_identify_no_gaps_when_populated():
    from src.services.trip_enrichment import identify_condition_gaps

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="temporal",
        condition_context="midday-summer",
        conclusion="Fishes best post-rain with overcast skies",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        user_id=1,
    )
    stop = {
        "weather_notes": "overcast, rain the night before",
        "technique": "slip sinker",
        "water_level": "normal",
        "water_clarity": "slightly turbid",
    }
    gaps = identify_condition_gaps(stop, insight)
    assert "weather" not in gaps


def test_identify_technique_gap():
    from src.services.trip_enrichment import identify_condition_gaps

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="gear",
        condition_context="cut-bait",
        conclusion="Cut bait outperforms worm rigs in warm water",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        user_id=1,
    )
    stop = {"technique": None, "gear": None}
    gaps = identify_condition_gaps(stop, insight)
    assert "technique" in gaps


def test_identify_time_of_day_gap():
    from src.services.trip_enrichment import identify_condition_gaps

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="temporal",
        condition_context="midday",
        conclusion="Best bite at midday in deep holes",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        user_id=1,
    )
    stop = {"notes": "fished the pool for an hour"}
    gaps = identify_condition_gaps(stop, insight)
    assert "time_of_day" in gaps


def test_identify_no_time_gap_when_noted():
    from src.services.trip_enrichment import identify_condition_gaps

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="temporal",
        condition_context="midday",
        conclusion="Best bite at midday in deep holes",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        user_id=1,
    )
    stop = {"notes": "was there at midday, very productive"}
    gaps = identify_condition_gaps(stop, insight)
    assert "time_of_day" not in gaps


def test_build_followup_question_weather_gap():
    from src.services.trip_enrichment import build_followup_question

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="temporal",
        condition_context="test",
        conclusion="Fishes best post-rain",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        user_id=1,
    )
    stop = {"location_name": "Willoway Park"}
    question = build_followup_question(stop, insight, ["weather"], was_productive=True)
    assert question is not None
    assert "weather" in question.lower() or "rain" in question.lower()


def test_build_followup_question_blank_trip():
    from src.services.trip_enrichment import build_followup_question

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="temporal",
        condition_context="test",
        conclusion="Fishes best post-rain",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        user_id=1,
    )
    stop = {"location_name": "Grand River"}
    question = build_followup_question(stop, insight, ["weather"], was_productive=False)
    assert question is not None
    assert "blanked" in question.lower() or "weather" in question.lower()


def test_build_followup_question_no_gaps_returns_none():
    from src.services.trip_enrichment import build_followup_question

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="temporal",
        condition_context="test",
        conclusion="Fishes best post-rain",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        user_id=1,
    )
    stop = {"location_name": "Grand River"}
    question = build_followup_question(stop, insight, [], was_productive=True)
    assert question is None


# ── DB-backed tests ────────────────────────────────────────────────────────────


def test_confidence_escalation(tmp_path):
    from src.services.trip_enrichment import update_insight_confidence

    db = _make_db(tmp_path)
    insight_id = insert_insight(db, _catfish_insight(evidence_count=1))  # one away from threshold
    insight = get_insight(db, insight_id)

    updated = update_insight_confidence(
        db,
        insight,
        was_productive=True,
        stop={},
        use_llm=False,
        user_id=1,
    )
    assert updated.confidence == "medium"


def test_confidence_escalation_not_yet(tmp_path):
    from src.services.trip_enrichment import update_insight_confidence

    db = _make_db(tmp_path)
    insight_id = insert_insight(db, _catfish_insight(evidence_count=0))
    insight = get_insight(db, insight_id)

    updated = update_insight_confidence(
        db,
        insight,
        was_productive=True,
        stop={},
        use_llm=False,
        user_id=1,
    )
    assert updated.confidence == "low"  # not yet at threshold
    assert updated.evidence_count == 1


def test_contradiction_does_not_immediately_change_confidence(tmp_path):
    from src.services.trip_enrichment import update_insight_confidence

    db = _make_db(tmp_path)
    insight_id = insert_insight(db, _catfish_insight(confidence="high", evidence_count=5))
    insight = get_insight(db, insight_id)

    updated = update_insight_confidence(
        db,
        insight,
        was_productive=False,
        stop={},
        use_llm=False,
        user_id=1,
    )
    assert updated.confidence == "high"  # one contradiction doesn't drop confidence


def test_contradiction_appends_note(tmp_path):
    from src.services.trip_enrichment import update_insight_confidence

    db = _make_db(tmp_path)
    insight_id = insert_insight(db, _catfish_insight(source_detail="original detail"))
    insight = get_insight(db, insight_id)

    updated = update_insight_confidence(
        db,
        insight,
        was_productive=False,
        stop={},
        use_llm=False,
        user_id=1,
    )
    assert "[contradicted" in (updated.source_detail or "")


def test_second_contradiction_triggers_refinement(tmp_path):
    from src.services.trip_enrichment import update_insight_confidence

    db = _make_db(tmp_path)
    # Pre-load one contradiction into source_detail
    insight_id = insert_insight(
        db,
        _catfish_insight(
            confidence="medium",
            source_detail="original [contradicted 2026-01-01]",
        ),
    )
    insight = get_insight(db, insight_id)

    updated = update_insight_confidence(
        db,
        insight,
        was_productive=False,
        stop={},
        use_llm=False,
        user_id=1,
    )
    # After second contradiction: refinement fires, is_current flips, new record has confidence=low
    assert updated.confidence == "low"
    # Old insight should now be non-current
    old = get_insight(db, insight_id)
    assert old is not None
    assert old.is_current is False


def test_enrich_session_returns_question_for_known_spot(tmp_path):
    from src.services.trip_enrichment import enrich_session

    db = _make_db(tmp_path)

    # Insert an insight about channel catfish at a known location
    from src.storage.insights import insert_insight as ins

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="temporal",
        condition_context="midday-summer",
        conclusion="Fishes best post-rain with overcast skies at this pool",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        lat=42.917,
        lng=-79.774,
        condition_season="summer",
        location_name="Willoway Park",
        user_id=1,
    )
    ins(db, insight)

    # Create a session + stop near that insight (no weather notes)
    session_id = db["sessions"].insert({"date": "2026-06-15", "date_approx": None}).last_pk
    db["stops"].insert(
        {
            "session_id": session_id,
            "location_text": "Willoway Park",
            "location_name": "Willoway Park",
            "lat": 42.917,
            "lng": -79.774,
            "species_caught": json.dumps(["channel catfish"]),
            "was_productive": 1,
            "technique": None,
            "gear": None,
            "water_level": None,
            "water_clarity": None,
            "weather_notes": None,
            "notes": None,
        }
    )

    result = enrich_session(db, session_id, use_llm=False, user_id=1)
    questions = result["followup_questions"]
    assert len(questions) == 1
    q = questions[0]["question"]
    assert "channel catfish" in q.lower() or "catfish" in q.lower()
    assert "weather" in q.lower() or "rain" in q.lower()


def test_blank_trip_matches_by_location(tmp_path):
    """Blank stop at a known location triggers contradiction matching by proximity."""
    from src.services.trip_enrichment import match_insights_to_stop

    db = _make_db(tmp_path)

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="temporal",
        condition_context="midday-summer",
        conclusion="Good midday spot",
        confidence="high",
        source_type="agent_synthesis",
        source_detail="test",
        lat=42.917,
        lng=-79.774,
        user_id=1,
    )
    insert_insight(db, insight)

    stop = {
        "session_id": None,
        "species_caught": "[]",
        "party_species_caught": "[]",
        "was_productive": 0,
        "lat": 42.917,
        "lng": -79.774,
    }
    matched = match_insights_to_stop(db, stop, user_id=1)
    assert len(matched) >= 1


def test_enrich_session_no_match_returns_empty(tmp_path):
    from src.services.trip_enrichment import enrich_session

    db = _make_db(tmp_path)

    session_id = db["sessions"].insert({"date": "2026-06-15", "date_approx": None}).last_pk
    db["stops"].insert(
        {
            "session_id": session_id,
            "location_text": "Some creek",
            "location_name": "Some creek",
            "lat": 43.5,
            "lng": -80.5,
            "species_caught": json.dumps(["creek chub"]),
            "was_productive": 1,
            "technique": None,
            "gear": None,
            "water_level": None,
            "water_clarity": None,
            "weather_notes": None,
            "notes": None,
        }
    )

    result = enrich_session(db, session_id, use_llm=False, user_id=1)
    assert result["followup_questions"] == []


# ── cross-user isolation ──────────────────────────────────────────────────────
#
# Pattern detection read every angler's stops and insights. Across users it
# would invent slumps and species gaps out of other people's trips.


def _add_stop(db, user_id, lat=43.40, lng=-79.75, species=("channel catfish",), productive=True):
    db["sessions"].insert({"date": "2025-06-14", "user_id": user_id})
    sid = db.execute("SELECT MAX(id) FROM sessions").fetchone()[0]
    db["stops"].insert(
        {
            "session_id": sid,
            "user_id": user_id,
            "location_text": "Byng Island",
            "location_name": "Byng Island",
            "lat": lat,
            "lng": lng,
            "species_caught": json.dumps(list(species)),
            "party_species_caught": json.dumps([]),
            "was_productive": 1 if productive else 0,
        },
        alter=True,
    )


def test_stop_summary_only_returns_the_requested_users_stops(tmp_path):
    from src.services.trip_enrichment import _get_all_stops_summary

    db = _make_db(tmp_path)
    for _ in range(3):
        _add_stop(db, user_id=2)

    assert _get_all_stops_summary(db, user_id=1) == []
    assert len(_get_all_stops_summary(db, user_id=2)) == 3


def test_insight_matching_ignores_another_users_insights(tmp_path):
    from src.services.trip_enrichment import match_insights_to_stop

    db = _make_db(tmp_path)
    other = insert_insight(db, _catfish_insight(lat=43.40, lng=-79.75))
    db["behavioral_insights"].update(other, {"user_id": 2})

    blank_stop = {
        "species_caught": [],
        "was_productive": 0,
        "lat": 43.40,
        "lng": -79.75,
        "location_name": "Byng Island",
        "session_id": None,
        "technique": None,
    }
    assert match_insights_to_stop(db, blank_stop, user_id=1) == []


def test_pattern_detection_does_not_count_another_users_sessions(tmp_path):
    """The session-count gate decided whether to run at all, unfiltered."""
    from src.services.trip_enrichment import detect_proactive_patterns

    db = _make_db(tmp_path)
    for _ in range(20):
        _add_stop(db, user_id=2)

    assert detect_proactive_patterns(db, [], user_id=1) is None


def test_enrich_session_does_not_read_another_owners_stops(fast_db):
    from src.services.trip_enrichment import enrich_session

    session_id = fast_db["sessions"].insert({"date": "2026-06-01", "user_id": 1}).last_pk
    fast_db["stops"].insert({"session_id": session_id, "user_id": 1, "location_text": "Creek"})
    assert enrich_session(fast_db, session_id, use_llm=False, user_id=2) == {
        "followup_questions": [],
        "proactive_coaching": None,
    }
