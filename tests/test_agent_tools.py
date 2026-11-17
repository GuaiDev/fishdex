"""Tests for the consolidated agent tool surface.

The old surface had 32 tools and no test asserting anything about the set as a
whole — a tool could be declared and never dispatched, or dispatched and never
declared, and nothing would notice until the model tried to call it.
"""

import json

import pytest

from src.agent import tools


class _Profile:
    home_location = None


@pytest.fixture
def db(fast_db, monkeypatch):

    database = fast_db
    monkeypatch.setattr(tools, "get_db", lambda: database)
    # describe_place must never fire a live search or a live weather fetch.
    from src.models.context import EmptyReason

    monkeypatch.setattr(
        "src.services.context.escalation.escalate_records",
        lambda **kwargs: ([], EmptyReason.WEB_SEARCH_EMPTY),
    )
    return database


# -- the set as a whole --------------------------------------------------------


def test_every_declared_tool_has_a_handler():
    declared = {t["name"] for t in tools.tool_schemas(_Profile())}
    assert declared == set(tools._HANDLERS)


def test_the_surface_is_small():
    """32 tools was one per dataset. The layer bundles them; the surface shrank."""
    assert len(tools.tool_schemas(_Profile())) <= 15


def test_retired_tools_are_gone():
    """Each of these either fabricated, or fanned one question into many calls."""
    declared = {t["name"] for t in tools.tool_schemas(_Profile())}
    for retired in (
        "get_tactical_recommendation",
        "get_recent_observations",
        "get_gbif_observations",
        "get_substrate",
        "get_benthic_habitat",
        "get_water_quality",
        "get_stream_temperature",
        "get_piscivore_activity",
        "get_oldest_gbif_record",
        "check_recommendation_conflicts",
        "get_behavioral_insights",
        "get_trips_at_location",
        "get_session_conditions",
        "find_untapped_water",
        "find_exploration_targets",
    ):
        assert retired not in declared


def test_every_schema_is_well_formed():
    for t in tools.tool_schemas(_Profile()):
        assert t["description"].strip()
        schema = t["input_schema"]
        assert schema["type"] == "object"
        for req in schema.get("required", []):
            assert req in schema["properties"], f"{t['name']} requires undeclared {req}"


def test_unknown_tool_returns_an_error_not_an_exception():
    out = json.loads(tools.execute_tool("get_tactical_recommendation", {}))
    assert "error" in out


def test_home_coordinates_reach_the_schema_descriptions():
    class _WithHome:
        class home_location:
            lat = 43.4675
            lng = -79.6877

    schemas = tools.tool_schemas(_WithHome())
    describe = next(t for t in schemas if t["name"] == "describe_place")
    assert "43.4675" in describe["input_schema"]["properties"]["lat"]["description"]


# -- describe_place ------------------------------------------------------------


def test_unresolvable_place_is_reported_as_a_resolution_failure(db):
    """Not the same as "we know nothing there", and it must not read like it."""
    out = json.loads(tools.execute_tool("describe_place", {"query": "Nowhere Creek"}))
    assert out["resolved"] is False
    assert "resolution failure" in out["message"]


def test_describe_place_renders_provenance_and_empty_reasons(db):
    db["water_features"].insert(
        {
            "osm_id": "w1",
            "feature_type": "stream",
            "name": "Bronte Creek",
            "lat": 43.40,
            "lng": -79.75,
        },
        alter=True,
    )
    out = tools.execute_tool("describe_place", {"query": "Bronte Creek"})
    assert "Bronte Creek" in out
    assert "Species recorded" in out
    assert "statement about the corpus" in out


def test_describe_place_does_not_include_live_conditions(db):
    """Conditions are a separate tool so everything else stays cacheable."""
    db["water_features"].insert(
        {
            "osm_id": "w1",
            "feature_type": "stream",
            "name": "Bronte Creek",
            "lat": 43.40,
            "lng": -79.75,
        },
        alter=True,
    )
    out = tools.execute_tool("describe_place", {"query": "Bronte Creek"})
    assert "Conditions now" not in out


# -- write paths ---------------------------------------------------------------


def test_recording_an_insight_reports_related_ones_without_a_second_tool(db, monkeypatch):
    """The conflict check moved inside the write, so it cannot be skipped."""
    calls = {"checked": False}

    def _check(**kwargs):
        calls["checked"] = True
        return json.dumps({"conflicts": []})

    monkeypatch.setattr("src.services.insights.check_conflicts_for_agent_service", _check)
    monkeypatch.setattr(
        "src.services.insights.record_behavioral_insight_for_agent",
        lambda **kwargs: json.dumps({"ok": True}),
    )

    out = json.loads(
        tools.execute_tool(
            "record_behavioral_insight",
            {
                "species": "channel catfish",
                "condition_type": "behavioral",
                "condition_context": "warm water",
                "conclusion": "hold deeper",
                "confidence": "low",
                "source_type": "agent_synthesis",
                "source_detail": "reasoning",
                "evidence_count": 0,
            },
        )
    )
    assert calls["checked"] is True
    assert "existing_related" in out


def test_a_failing_conflict_check_does_not_block_the_write(db, monkeypatch):
    monkeypatch.setattr(
        "src.services.insights.check_conflicts_for_agent_service",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "src.services.insights.record_behavioral_insight_for_agent",
        lambda **kwargs: json.dumps({"ok": True}),
    )

    out = json.loads(
        tools.execute_tool(
            "record_behavioral_insight",
            {
                "species": "creek chub",
                "condition_type": "habitat",
                "condition_context": "riffle",
                "conclusion": "holds in riffles",
                "confidence": "low",
                "source_type": "trip_log",
                "source_detail": "log",
                "evidence_count": 1,
            },
        )
    )
    assert out["recorded"] == {"ok": True}
    assert out["existing_related"] is None


def test_dismiss_segment_still_works(db):
    out = json.loads(tools.execute_tool("dismiss_segment", {"ogf_id": 42, "reason": "private"}))
    assert out["success"] is True
    assert out["ogf_id"] == 42
