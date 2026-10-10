"""One angler's insights and prompt context must never reach another.

Companion to test_privacy_isolation.py. Covers behavioral_insights (written,
read, and updated by trips) and the chat system prompt (built from a shared
profile file and the legacy trips table that belong to user 1). No model
calls; all databases are temporary.
"""

import json
import types
from datetime import date

import pytest

from src.models.behavioral_insight import BehavioralInsight
from src.models.trip import Trip
from src.services.trip_enrichment import (
    enrich_session,
    match_insights_to_stop,
    update_insight_confidence,
)
from src.storage.database import get_db
from src.storage.insights import (
    check_conflicts,
    get_insight,
    insert_insight,
    query_insights,
    refine_insight,
)
from src.storage.trips import insert_trip

LAT, LNG = 43.5, -79.7


@pytest.fixture()
def db(tmp_path):
    return get_db(tmp_path / "test.db")


def _insight(user_id: int, species: str = "Smallmouth Bass", **kw) -> BehavioralInsight:
    base = dict(
        species=species,
        condition_type="habitat",
        condition_context="riffle",
        conclusion=f"user {user_id}'s conclusion about {species}",
        confidence="low",
        source_type="agent_synthesis",
        source_detail="test",
        evidence_count=0,
        lat=LAT,
        lng=LNG,
        user_id=user_id,
    )
    base.update(kw)
    return BehavioralInsight(**base)


def _stop(db, user_id: int, species: list[str], productive: bool = True) -> dict:
    session_id = db["sessions"].insert({"date": "2026-06-10", "user_id": user_id}).last_pk
    stop_id = (
        db["stops"]
        .insert(
            {
                "session_id": session_id,
                "user_id": user_id,
                "lat": LAT,
                "lng": LNG,
                "location_text": "test water",
                "species_caught": json.dumps(species),
                "was_productive": 1 if productive else 0,
            }
        )
        .last_pk
    )
    return db["stops"].get(stop_id)


# ── G1: insights carry and are filtered by user_id ───────────────────────────


def test_insight_is_stored_under_its_own_user(db):
    id1 = insert_insight(db, _insight(1))
    id2 = insert_insight(db, _insight(2))
    assert db["behavioral_insights"].get(id1)["user_id"] == 1
    assert db["behavioral_insights"].get(id2)["user_id"] == 2
    assert get_insight(db, id2).user_id == 2


def test_each_user_reads_only_their_own_insights(db):
    insert_insight(db, _insight(1))
    insert_insight(db, _insight(2))
    assert [i.user_id for i in query_insights(db, "bass", user_id=1)] == [1]
    assert [i.user_id for i in query_insights(db, "bass", user_id=2)] == [2]
    assert query_insights(db, "bass", user_id=3) == []
    assert [i.user_id for i in check_conflicts(db, "bass", LAT, LNG, user_id=2)] == [2]


def test_agent_record_tool_writes_and_reads_per_user(tmp_path, monkeypatch):
    import src.services.insights as svc
    import src.storage.database as db_mod
    from src.agent.tools import execute_tool

    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(svc, "get_db", lambda: get_db(tmp_path / "test.db"))
    db = get_db(tmp_path / "test.db")

    inputs = dict(
        species="Rock Bass",
        condition_type="habitat",
        condition_context="undercut bank",
        conclusion="holds under the bank",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="test",
        evidence_count=2,
        lat=LAT,
        lng=LNG,
    )
    execute_tool("record_behavioral_insight", inputs, user_id=2)

    rows = list(db["behavioral_insights"].rows)
    assert [r["user_id"] for r in rows] == [2]
    assert query_insights(db, "rock bass", user_id=1) == []
    assert len(query_insights(db, "rock bass", user_id=2)) == 1

    got = json.loads(svc.get_behavioral_insights_for_agent("Rock Bass", user_id=1))
    assert got["count"] == 0
    got = json.loads(svc.get_behavioral_insights_for_agent("Rock Bass", user_id=2))
    assert got["count"] == 1


def test_refinement_keeps_the_owner_and_never_crosses_users(tmp_path, monkeypatch):
    import src.services.insights as svc

    monkeypatch.setattr(svc, "get_db", lambda: get_db(tmp_path / "test.db"))
    db = get_db(tmp_path / "test.db")
    old1 = insert_insight(db, _insight(1, confidence="low"))
    old2 = insert_insight(db, _insight(2, confidence="low"))

    # User 2 records a contradicting insight at the same place.
    out = json.loads(
        svc.record_behavioral_insight_for_agent(
            species="Smallmouth Bass",
            condition_type="habitat",
            condition_context="riffle",
            conclusion="this contradicts the earlier note",
            confidence="high",
            source_type="agent_synthesis",
            source_detail="test",
            evidence_count=3,
            lat=LAT,
            lng=LNG,
            user_id=2,
        )
    )
    assert out["refined_prior_insights"] == [old2]
    # The id reported is the new row, not a global MAX(id).
    new = get_insight(db, out["insight_id"])
    assert new.user_id == 2 and new.version == 2
    # User 1's insight is untouched.
    assert get_insight(db, old1).is_current is True
    assert get_insight(db, old1).contradicted_by is None


def test_reported_id_is_the_refined_row_even_if_another_user_inserts_later(tmp_path, monkeypatch):
    import src.services.insights as svc
    import src.storage.insights as store

    monkeypatch.setattr(svc, "get_db", lambda: get_db(tmp_path / "test.db"))
    db = get_db(tmp_path / "test.db")
    old = insert_insight(db, _insight(1))

    real_refine = store.refine_insight

    def refine_then_other_user_inserts(db_, prior_id, new_insight):
        new_id = real_refine(db_, prior_id, new_insight)
        insert_insight(db_, _insight(2, species="Walleye"))  # lands after, MAX(id) moves
        return new_id

    monkeypatch.setattr(store, "refine_insight", refine_then_other_user_inserts)
    out = json.loads(
        svc.record_behavioral_insight_for_agent(
            species="Smallmouth Bass",
            condition_type="habitat",
            condition_context="riffle",
            conclusion="this contradicts the earlier note",
            confidence="high",
            source_type="agent_synthesis",
            source_detail="test",
            evidence_count=3,
            lat=LAT,
            lng=LNG,
            user_id=1,
        )
    )
    assert out["refined_prior_insights"] == [old]
    assert get_insight(db, out["insight_id"]).species == "Smallmouth Bass"


def test_refine_insight_refuses_another_users_row(db):
    old = insert_insight(db, _insight(1))
    with pytest.raises(ValueError):
        refine_insight(db, old, _insight(2))
    assert get_insight(db, old).is_current is True


def test_trip_log_insights_belong_to_the_logging_user(db):
    from src.services.trip_logger import _maybe_insert_species_insights

    parsed = {"date": "2026-06-10", "species_caught": ["Creek Chub"], "location_description": "x"}
    assert _maybe_insert_species_insights(db, parsed, 123, "Hidden Creek", user_id=2) == 1
    assert query_insights(db, "creek chub", user_id=1) == []
    assert len(query_insights(db, "creek chub", user_id=2)) == 1


def test_existing_rows_without_an_owner_stay_user_1(tmp_path):
    from src.storage.database import migrate_user_fields

    db = get_db(tmp_path / "test.db")
    insert_insight(db, _insight(1))
    db.execute("UPDATE behavioral_insights SET user_id = NULL")
    db.conn.commit()
    migrate_user_fields(db)
    assert len(query_insights(db, "bass", user_id=1)) == 1
    assert query_insights(db, "bass", user_id=2) == []


# ── G2: a trip can only touch its own user's insights ────────────────────────


def test_other_users_trip_matches_none_of_user_1s_insights(db):
    insert_insight(db, _insight(1))
    stop = _stop(db, user_id=2, species=["Smallmouth Bass"])
    assert match_insights_to_stop(db, stop, user_id=2) == []
    assert len(match_insights_to_stop(db, stop, user_id=1)) == 1


def test_blank_stop_by_user_2_does_not_match_user_1s_insights(db):
    insert_insight(db, _insight(1))
    stop = _stop(db, user_id=2, species=[], productive=False)
    assert match_insights_to_stop(db, stop, user_id=2) == []


def test_user_2_trip_cannot_change_user_1_insight(db):
    iid = insert_insight(db, _insight(1, confidence="low", evidence_count=0))
    insight = get_insight(db, iid)
    for productive in (True, False):
        with pytest.raises(ValueError):
            update_insight_confidence(db, insight, productive, {}, client=None, user_id=2)
    after = get_insight(db, iid)
    assert (after.evidence_count, after.confidence, after.source_detail) == (0, "low", "test")
    assert after.is_current is True


def test_enrich_session_for_user_2_leaves_user_1_insights_alone(db):
    iid = insert_insight(db, _insight(1, confidence="low", evidence_count=0))
    stop = _stop(db, user_id=2, species=["Smallmouth Bass"])
    result = enrich_session(db, stop["session_id"], client=None, user_id=2)
    assert result["followup_questions"] == []
    after = get_insight(db, iid)
    assert after.evidence_count == 0 and after.last_validated == get_insight(db, iid).last_validated
    assert after.confidence == "low"


def test_enrich_session_still_updates_the_owners_own_insight(db):
    iid = insert_insight(db, _insight(2, confidence="low", evidence_count=0))
    stop = _stop(db, user_id=2, species=["Smallmouth Bass"])
    enrich_session(db, stop["session_id"], client=None, user_id=2)
    assert get_insight(db, iid).evidence_count == 1


def test_contradiction_refinement_stays_with_the_owner(db, monkeypatch):
    import src.services.trip_enrichment as te

    monkeypatch.setattr(te, "_synthesize_nuanced_conclusion", lambda *a, **k: "nuanced")
    monkeypatch.setattr(te, "CONTRADICTIONS_TO_REFINE", 1)
    iid = insert_insight(db, _insight(2))
    new = update_insight_confidence(db, get_insight(db, iid), False, {}, client=None, user_id=2)
    assert new.user_id == 2 and new.version == 2
    assert query_insights(db, "bass", user_id=1) == []


# ── G3: user 2's prompt carries none of user 1's profile or trips ────────────


def test_assemble_without_profile_omits_profile_and_trips():
    from src.agent.system_prompt import assemble
    from src.jurisdictions.registry import get_jurisdiction

    out = assemble("TEMPLATE", None, [], get_jurisdiction("CA-ON"))
    assert "## Your angler" not in out
    assert "## Recent trips" not in out
    assert "## Active jurisdiction" in out


class _FakeClient:
    """Records what would have been sent to the model; never calls it."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
        block = types.SimpleNamespace(type="text", text="ok")
        return types.SimpleNamespace(model="fake", usage=usage, content=[block])


def _user_1_secrets(db, profile_path):
    from src.models.profile import Location, UserProfile
    from src.storage.profile import save_profile

    save_profile(
        UserProfile(
            home_jurisdiction="CA-ON",
            home_location=Location(name="Zebulon Falls Marina", lat=44.12345, lng=-80.54321),
            target_species=["Burbot-of-Secrets"],
            fishing_style="night-fishing-only",
            preferences="my private preference sentinel",
            gear=[{"rod": "SecretRod9000"}],
        ),
        path=profile_path,
    )
    insert_trip(
        db,
        Trip(
            date=date(2026, 6, 1),
            jurisdiction="CA-ON",
            location_name="Sentinel Pond",
            notes="user one's private trip note",
        ),
    )
    return [
        "Zebulon Falls",
        "44.12345",
        "-80.54321",
        "Burbot-of-Secrets",
        "night-fishing-only",
        "private preference sentinel",
        "SecretRod9000",
        "Sentinel Pond",
        "private trip note",
    ]


def _run_pipeline_for(user_id, tmp_path, monkeypatch):
    import src.agent.chat as chat
    import src.storage.database as db_mod
    import src.storage.profile as profile_mod

    db_path = tmp_path / "test.db"
    profile_path = tmp_path / "profile.json"
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(profile_mod, "PROFILE_PATH", profile_path)
    secrets = _user_1_secrets(get_db(db_path), profile_path)

    client = _FakeClient()
    monkeypatch.setattr(chat, "get_client", lambda: client)
    monkeypatch.setattr(chat, "get_model", lambda: "fake")
    result = chat._run_full_pipeline([{"role": "user", "content": "hi"}], "sess", user_id=user_id)
    assert result["reply"] == "ok"
    (call,) = client.calls
    # Everything that reaches the model: system prompt and tool descriptions.
    sent = call["system"] + json.dumps(call["tools"])
    return sent, secrets


def test_user_2_prompt_contains_none_of_user_1s_profile_or_trips(tmp_path, monkeypatch):
    sent, secrets = _run_pipeline_for(2, tmp_path, monkeypatch)
    leaked = [s for s in secrets if s in sent]
    assert leaked == []
    assert "Current user: 2" in sent


def test_user_1_prompt_still_gets_profile_and_trips(tmp_path, monkeypatch):
    sent, secrets = _run_pipeline_for(1, tmp_path, monkeypatch)
    for s in ("Zebulon Falls", "Sentinel Pond", "private trip note", "SecretRod9000"):
        assert s in sent
