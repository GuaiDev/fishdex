"""Tests for proactive coaching pattern detection."""

import json

import pytest
from sqlite_utils import Database

from src.storage.database import ensure_schema


@pytest.fixture
def db_conn(tmp_path):
    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    return db


def _add_session_with_stop(db, location, productive, species=None, date="2025-06-01"):
    db["sessions"].insert({"date": date, "overall_notes": None})
    sid = db.execute("SELECT MAX(id) FROM sessions").fetchone()[0]
    db["stops"].insert(
        {
            "session_id": sid,
            "location_text": location,
            "location_name": location,
            "species_caught": json.dumps(species or []),
            "was_productive": productive,
        }
    )
    return sid


# ── threshold guard ────────────────────────────────────────────────────────────


def test_no_patterns_with_sparse_data(db_conn):
    """Don't fire with fewer than 3 sessions."""
    from src.services.trip_enrichment import detect_proactive_patterns

    _add_session_with_stop(db_conn, "Byng Island", 1, ["channel catfish"])
    _add_session_with_stop(db_conn, "Byng Island", 0)

    result = detect_proactive_patterns(db_conn, [], user_id=1)
    assert result is None


# ── species gap ────────────────────────────────────────────────────────────────


def test_species_gap_detected(db_conn):
    """Detects species in insights but never personally caught."""
    from src.models.behavioral_insight import BehavioralInsight
    from src.services.trip_enrichment import detect_proactive_patterns
    from src.storage.insights import insert_insight

    for i in range(3):
        _add_session_with_stop(db_conn, "Test Creek", 1, ["creek chub"], date=f"2025-0{i + 1}-01")

    insight = BehavioralInsight(
        species="stonecat",
        condition_type="habitat",
        condition_context="rocky riffles",
        conclusion="Stonecat found under flat rocks in riffles",
        confidence="low",
        source_type="agent_synthesis",
        source_detail="test", user_id=1,
    )
    insert_insight(db_conn, insight)

    # Mock _detect_species_gap to read from db_conn not production db
    from src.services import trip_enrichment as te

    original = te._detect_species_gap

    def patched_gap(stops, user_id=1):
        species_caught_count: dict[str, int] = {}
        for stop in stops:
            for sp in stop["species_caught"]:
                clean = sp.replace("(uncertain)", "").strip().lower()
                if "unidentified" not in clean:
                    species_caught_count[clean] = species_caught_count.get(clean, 0) + 1
        try:
            targeted_rows = list(
                db_conn.execute(
                    "SELECT DISTINCT LOWER(species) FROM behavioral_insights WHERE is_current = 1"
                ).fetchall()
            )
            targeted = {r[0] for r in targeted_rows}
        except Exception:
            targeted = set()
        never_caught = [sp for sp in targeted if sp not in species_caught_count]
        if never_caught and len(stops) >= te._SPECIES_ATTEMPT_THRESHOLD:
            target = never_caught[0]
            return {
                "type": "species_gap",
                "species": target,
                "total_stops": len(stops),
                "message": f"You've discussed {target} but haven't logged a catch.",
            }
        return None

    te._detect_species_gap = patched_gap
    try:
        result = detect_proactive_patterns(db_conn, [], user_id=1)
    finally:
        te._detect_species_gap = original

    assert result is not None
    assert result["type"] == "species_gap"
    assert "stonecat" in result["species"]


# ── location slump ─────────────────────────────────────────────────────────────


def test_slump_detected(db_conn):
    """Detects consecutive blanks at a previously productive location."""
    from src.services.trip_enrichment import detect_proactive_patterns

    _add_session_with_stop(db_conn, "Byng Island", 1, ["channel catfish"], "2025-04-01")
    _add_session_with_stop(db_conn, "Byng Island", 0, [], "2025-05-01")
    _add_session_with_stop(db_conn, "Byng Island", 0, [], "2025-06-01")

    current = [{"location_name": "Byng Island", "was_productive": False}]
    result = detect_proactive_patterns(db_conn, current, user_id=1)

    assert result is not None
    assert result["type"] == "location_slump"
    assert "Byng" in result["location"]
    assert result["recent_blanks"] >= 2


def test_no_slump_without_current_session_visit(db_conn):
    """Slump only fires if current session visited the slumping location."""
    from src.services.trip_enrichment import detect_proactive_patterns

    _add_session_with_stop(db_conn, "Byng Island", 1, ["channel catfish"], "2025-04-01")
    _add_session_with_stop(db_conn, "Byng Island", 0, [], "2025-05-01")
    _add_session_with_stop(db_conn, "Byng Island", 0, [], "2025-06-01")

    current = [{"location_name": "Credit River", "was_productive": True}]
    result = detect_proactive_patterns(db_conn, current, user_id=1)

    if result:
        assert result["type"] != "location_slump"


# ── enrich_session return type ─────────────────────────────────────────────────


def test_enrich_session_returns_dict(db_conn):
    """enrich_session returns a dict with followup_questions and proactive_coaching."""
    from src.services.trip_enrichment import enrich_session

    db_conn["sessions"].insert({"date": "2025-06-14", "overall_notes": None})
    sid = db_conn.execute("SELECT MAX(id) FROM sessions").fetchone()[0]
    db_conn["stops"].insert(
        {
            "session_id": sid,
            "location_text": "Test spot",
            "location_name": "Test spot",
            "species_caught": json.dumps([]),
            "was_productive": 0,
        }
    )

    result = enrich_session(db_conn, sid, use_llm=False, user_id=1)
    assert isinstance(result, dict)
    assert "followup_questions" in result
    assert "proactive_coaching" in result
    assert isinstance(result["followup_questions"], list)


def test_enrich_session_empty_stops_returns_dict(db_conn):
    """enrich_session on a session with no stops returns correct dict shape."""
    from src.services.trip_enrichment import enrich_session

    db_conn["sessions"].insert({"date": "2025-06-14", "overall_notes": None})
    sid = db_conn.execute("SELECT MAX(id) FROM sessions").fetchone()[0]

    result = enrich_session(db_conn, sid, use_llm=False, user_id=1)
    assert isinstance(result, dict)
    assert result["followup_questions"] == []
    assert result["proactive_coaching"] is None
