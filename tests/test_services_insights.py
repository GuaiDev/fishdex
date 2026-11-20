"""Tests for the behavioral insights service layer."""

import json

from src.models.behavioral_insight import BehavioralInsight
from src.storage.database import ensure_schema
from src.storage.insights import insert_insight


def _make_db(tmp_path):
    from sqlite_utils import Database

    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    return db


def _seed_insight(db, **overrides) -> int:
    base = dict(
        user_id=1,
        species="brook trout",
        condition_type="behavioral",
        condition_context="post-cold-front",
        conclusion="Brook trout feed aggressively after a cold front.",
        confidence="medium",
        source_type="trip_log",
        source_detail="8 outings",
        evidence_count=8,
    )
    base.update(overrides)
    return insert_insight(db, BehavioralInsight(**base))


def test_get_insights_empty(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr("src.services.insights.get_db", lambda: db)

    from src.services.insights import get_behavioral_insights_for_agent

    result = json.loads(get_behavioral_insights_for_agent(species="walleye", user_id=1))
    assert result["count"] == 0
    assert "note" in result


def test_get_insights_returns_data(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    _seed_insight(db)
    monkeypatch.setattr("src.services.insights.get_db", lambda: db)

    from src.services.insights import get_behavioral_insights_for_agent

    result = json.loads(get_behavioral_insights_for_agent(species="brook trout", user_id=1))
    assert result["count"] == 1
    insight = result["insights"][0]
    assert insight["conclusion"] == "Brook trout feed aggressively after a cold front."
    assert insight["confidence"] == "medium"
    assert insight["source_type"] == "trip_log"


def test_get_insights_condition_type_filter(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    _seed_insight(db, condition_type="behavioral")
    _seed_insight(db, condition_type="habitat", conclusion="Prefer riffle edges with cobble.")
    monkeypatch.setattr("src.services.insights.get_db", lambda: db)

    from src.services.insights import get_behavioral_insights_for_agent

    result = json.loads(
        get_behavioral_insights_for_agent(species="brook trout", condition_type="habitat",
            user_id=1,
        )
    )
    assert result["count"] == 1
    assert result["insights"][0]["condition_type"] == "habitat"


def test_record_unverified_blocked(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr("src.services.insights.get_db", lambda: db)

    from src.services.insights import record_behavioral_insight_for_agent

    result = json.loads(
        record_behavioral_insight_for_agent(
            species="brook trout",
            condition_type="behavioral",
            condition_context="post-cold-front",
            conclusion="Maybe they feed more? Not sure.",
            confidence="unverified",
            source_type="agent_synthesis",
            source_detail="just a guess",
            evidence_count=0, user_id=1,
        )
    )
    assert "error" in result
    assert "unverified" in result["error"]

    # Confirm nothing was written
    from src.storage.insights import query_insights

    assert query_insights(db, species="brook trout", user_id=1) == []


def test_record_valid_insight(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr("src.services.insights.get_db", lambda: db)

    from src.services.insights import record_behavioral_insight_for_agent

    result = json.loads(
        record_behavioral_insight_for_agent(
            species="brook trout",
            condition_type="behavioral",
            condition_context="post-cold-front",
            conclusion=(
                "Brook trout feed aggressively 30-60 min after a cold front in streams under 15C."
            ),
            confidence="medium",
            source_type="trip_log",
            source_detail="8 personal outings Credit River spring 2026",
            evidence_count=8,
            jurisdiction="CA-ON", user_id=1,
        )
    )
    assert result["success"] is True
    assert isinstance(result["insight_id"], int)
    assert result["confidence"] == "medium"

    # Confirm it was written
    from src.storage.insights import query_insights

    stored = query_insights(db, species="brook trout", user_id=1)
    assert len(stored) == 1
    assert stored[0].jurisdiction == "CA-ON"
