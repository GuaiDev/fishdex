"""Coaching tests.

Rewritten to assert what reaches the model rather than what comes back from
it. The old versions called the live API and asserted `len(result) > 100`,
which fails without a network key and would have passed on a coach that
hallucinated every word of its answer. The prompt is the thing this service
actually builds, so the prompt is the thing under test.
"""

import json

import pytest

from src.storage.database import get_db


@pytest.fixture
def db_conn(tmp_path):
    # get_db() applies ensure_schema + every migration, matching production.
    # ensure_schema() alone omits user_id on stops/sessions, which hid the
    # cross-user data leak these tests now cover.
    return get_db(tmp_path / "test.db")


@pytest.fixture
def captured(monkeypatch):
    """Stub the API and hand back whatever prompt the service built.

    Both API paths are stubbed. The coach bundle escalates, so an empty corpus
    fires a live web search on the way to building the prompt — that is the
    intended production behaviour and exactly what a test must not do.
    """
    import src.services.coaching as coaching
    from src.models.context import EmptyReason

    monkeypatch.setattr(
        "src.services.context.escalation.escalate_records",
        lambda **kwargs: ([], EmptyReason.WEB_SEARCH_EMPTY),
    )
    monkeypatch.setattr(
        "src.services.weather.get_conditions_for_agent",
        lambda **kwargs: '{"temperature_c": 18.0, "pressure_trend": "falling"}',
    )

    box: dict = {}

    def _complete(role, **kwargs):
        box["prompt"] = kwargs["messages"][0]["content"]
        return type("R", (), {"content": [type("B", (), {"text": "stub response"})()]})()

    monkeypatch.setattr(coaching.llm, "complete", _complete)
    return box


def _insert_stop(
    db,
    user_id: int,
    location: str,
    species: list[str],
    lat: float | None = None,
    lng: float | None = None,
    **extra,
) -> None:
    db["sessions"].insert(
        {"date": "2025-06-14", "overall_notes": None, "user_id": user_id}
    )
    session_id = db.execute("SELECT MAX(id) FROM sessions").fetchone()[0]
    row = {
        "session_id": session_id,
        "user_id": user_id,
        "location_text": location,
        "location_name": location,
        "lat": lat,
        "lng": lng,
        "species_caught": json.dumps(species),
        "party_species_caught": json.dumps([]),
        "was_productive": 1,
        "technique": "Santee Cooper rig",
        "gear": "cutbait",
        "notes": f"logged by user {user_id}",
    }
    row.update(extra)
    db["stops"].insert(row)


# -- honest empties ------------------------------------------------------------


def test_species_coaching_with_no_catches_says_so_in_the_prompt(db_conn, captured):
    from src.services.coaching import get_species_coaching

    get_species_coaching(db_conn, "madtom")
    assert "Nothing logged" in captured["prompt"]


def test_unresolvable_location_does_not_call_the_model_at_all(db_conn, captured):
    """A place we cannot place is not a coaching question — it is a bad input."""
    from src.services.coaching import get_location_coaching

    result = get_location_coaching(db_conn, "Nonexistent Creek")
    assert "couldn't resolve" in result
    assert "prompt" not in captured


def test_location_coaching_reaches_the_corpus_when_the_user_has_no_history(
    db_conn, captured
):
    """Never having fished somewhere is not a reason to refuse the question.

    The old version returned "No logged trips found" and stopped, discarding
    everything the corpus knows about the water — which is the more useful
    half of the answer for a place the angler has not been to yet.
    """
    from src.services.coaching import get_location_coaching

    # A mapped water feature, and no logged trip by anyone. The place resolves
    # from the corpus alone, which is the case the old early-return threw away.
    db_conn["water_features"].insert(
        {
            "osm_id": "w1",
            "feature_type": "stream",
            "name": "Sixteen Mile Creek",
            "lat": 43.4675,
            "lng": -79.6877,
        },
        alter=True,
    )

    get_location_coaching(db_conn, "Sixteen Mile Creek", user_id=1)
    prompt = captured["prompt"]
    assert "Sixteen Mile Creek" in prompt
    assert "Species recorded" in prompt
    assert "user_never_fished_here" in prompt
    assert "no_records_in_radius" in prompt or "source_does_not_cover_area" in prompt


# -- user isolation ------------------------------------------------------------


def test_location_coaching_excludes_other_users(db_conn, captured):
    """A user's coaching must not read another user's stops."""
    from src.services.coaching import get_location_coaching

    _insert_stop(
        db_conn, user_id=2, location="Byng Island", species=["channel catfish"],
        lat=42.9, lng=-79.6,
    )

    result = get_location_coaching(db_conn, "Byng Island", user_id=1)
    leaked = captured.get("prompt", "") + result
    assert "logged by user 2" not in leaked
    assert "Santee Cooper" not in leaked


def test_species_coaching_excludes_other_users(db_conn, captured):
    """Another user's catches must not reach the coaching prompt."""
    from src.services.coaching import get_species_coaching

    _insert_stop(
        db_conn, user_id=2, location="Byng Island", species=["channel catfish"]
    )

    get_species_coaching(db_conn, "channel catfish", user_id=1)
    prompt = captured["prompt"]
    assert "Byng Island" not in prompt
    assert "logged by user 2" not in prompt
    assert "Nothing logged" in prompt


# -- the angler's own data does reach the prompt -------------------------------


def test_species_coaching_carries_the_setup_that_worked(db_conn, captured):
    from src.services.coaching import get_species_coaching

    _insert_stop(
        db_conn,
        user_id=1,
        location="Byng Island",
        species=["channel catfish"],
        gear="half chub cutbait, 3/0 circle hook",
        water_level="normal",
        water_clarity="turbid",
        notes="5lb channel cat at 10am",
    )

    get_species_coaching(db_conn, "channel catfish", "How do I find bigger fish?", 1)
    prompt = captured["prompt"]
    assert "Byng Island" in prompt
    assert "Santee Cooper rig" in prompt
    assert "turbid" in prompt
    assert "How do I find bigger fish?" in prompt


def test_stored_insights_arrive_with_their_source(db_conn, captured):
    """An insight the assistant invented must not read like a survey finding."""
    from src.services.coaching import get_species_coaching

    _insert_stop(
        db_conn, user_id=1, location="Byng Island", species=["channel catfish"]
    )
    db_conn["behavioral_insights"].insert(
        {
            "species": "channel catfish",
            "condition_type": "behavioral",
            "condition_context": "warm water",
            "conclusion": "hold deeper above 24C",
            "confidence": "medium",
            "source_type": "agent_synthesis",
            "source_detail": "assistant reasoning",
            "evidence_count": 0,
            "is_current": 1,
            "user_id": 1,
            "created_at": "2025-07-01",
        },
        alter=True,
    )

    get_species_coaching(db_conn, "channel catfish", user_id=1)
    prompt = captured["prompt"]
    assert "hold deeper above 24C" in prompt
    assert "reasoning, no source" in prompt


# -- conservation --------------------------------------------------------------


def test_listed_species_triggers_the_targeting_refusal(db_conn, captured, monkeypatch):
    import src.services.coaching as coaching
    from src.models.context import ContextField, SpeciesContext

    monkeypatch.setattr(
        coaching,
        "describe_species",
        lambda db, name: SpeciesContext(
            species=name,
            habitat_note=ContextField.inferred("cool headwater streams"),
            sar_alert=True,
            status_known_listed=True,
            sar_reason="Listed: Endangered.",
        ),
    )

    coaching.get_species_coaching(db_conn, "Redside Dace", user_id=1)
    prompt = captured["prompt"]
    assert "CONSERVATION OVERRIDE" in prompt
    assert "catch-and-release is not an exemption" in prompt


def test_unverified_but_unlisted_species_is_cautioned_not_refused(db_conn, captured):
    """A rule that fires on every fish in Ontario protects nothing.

    All 69 species in the local file are unverified, so gating the refusal on
    `sar_alert` would refuse coaching for yellow perch. The refusal is gated on
    an affirmative listing signal instead; the caution still goes through.
    """
    from src.services.coaching import get_species_coaching

    get_species_coaching(db_conn, "yellow perch", user_id=1)
    prompt = captured["prompt"]
    assert "CONSERVATION OVERRIDE" not in prompt


# -- the epistemic rule is in the prompt ---------------------------------------


def test_the_prompt_states_the_comparison_set_rule(db_conn, captured):
    from src.services.coaching import get_species_coaching

    get_species_coaching(db_conn, "creek chub", user_id=1)
    prompt = captured["prompt"]
    assert "NOT yet claimable" in prompt or "claimable" in prompt
    assert "Blanks are not failures" in prompt
