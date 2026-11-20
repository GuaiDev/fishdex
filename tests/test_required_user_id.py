"""No default user: every personal read and write names its owner (plan item P2.1)."""

import inspect
import json
import pathlib
import re
from datetime import date

import pytest
import typer

from src.models.behavioral_insight import BehavioralInsight
from src.models.catch import Catch
from src.models.trip import Trip
from src.models.user_context import UserContext
from src.services import insights as insights_svc
from src.services.context import angler_profile, user_layer
from src.storage.insights import insert_insight, query_shared_insights
from src.storage.trips import (
    get_parsed_trips,
    get_trip,
    insert_parsed_trip,
    insert_trip,
    recent_trips,
    update_trip,
)

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def _trip(location: str) -> Trip:
    return Trip(
        date=date(2026, 5, 1),
        jurisdiction="CA-ON",
        location_name=location,
        species_caught=[Catch(species="smallmouth bass")],
    )


def test_no_source_signature_defaults_user_id_to_one():
    offenders = [
        f"{p.relative_to(SRC)}:{i}"
        for p in SRC.rglob("*.py")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"user_id\s*:\s*int\s*=\s*1\b", line)
    ]
    assert offenders == []


def test_context_layer_entry_points_require_a_user(db):
    with pytest.raises(TypeError):
        user_layer(db)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        angler_profile(db)  # type: ignore[call-arg]


def test_behavioral_insight_requires_an_owner():
    with pytest.raises(Exception):
        BehavioralInsight(  # type: ignore[call-arg]
            species="x",
            condition_type="habitat",
            condition_context="c",
            conclusion="y",
            source_type="agent_synthesis",
            source_detail="d",
        )


def test_trips_belong_to_the_user_who_logged_them(db):
    mine = insert_trip(db, _trip("Credit River"), user_id=1)
    theirs = insert_trip(db, _trip("Grand River"), user_id=2)

    assert [t.location_name for t in recent_trips(db, user_id=1)] == ["Credit River"]
    assert [t.location_name for t in recent_trips(db, user_id=2)] == ["Grand River"]
    assert get_trip(db, theirs, user_id=1) is None

    update_trip(db, theirs, user_id=1, notes="hijacked")
    assert get_trip(db, theirs, user_id=2).notes == ""
    update_trip(db, mine, user_id=1, notes="mine")
    assert get_trip(db, mine, user_id=1).notes == "mine"


def test_trip_writes_and_reads_cannot_omit_the_owner(db):
    with pytest.raises(TypeError):
        insert_trip(db, _trip("x"))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        recent_trips(db)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        insert_parsed_trip(db, {})  # type: ignore[call-arg]


def test_parsed_trips_are_per_user(db):
    insert_parsed_trip(db, {"location_description": "a"}, user_id=1)
    insert_parsed_trip(db, {"location_description": "b"}, user_id=2)
    assert [t["location_description"] for t in get_parsed_trips(db, user_id=2)] == ["b"]


def test_trip_log_filter_in_find_water_is_per_user(db, monkeypatch):
    from src.services import osm

    monkeypatch.setattr(osm, "get_db", lambda: db)
    sig = inspect.signature(osm.get_nearby_water_for_agent)
    assert sig.parameters["user_id"].default is inspect.Parameter.empty


def test_seeded_insights_are_shared_not_user_one(db, monkeypatch, tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "species": "Common Carp",
                    "condition_type": "habitat",
                    "condition_context": "isolated-stormwater-pond",
                    "conclusion": "Waterfowl can carry eggs between ponds.",
                    "confidence": "high",
                    "source_type": "agent_synthesis",
                    "source_detail": "test",
                    "evidence_count": 1,
                }
            ]
        )
    )
    monkeypatch.setattr(insights_svc, "_DISPERSAL_SEED_PATH", seed)
    monkeypatch.setattr(insights_svc, "get_db", lambda: db)

    assert insights_svc.seed_dispersal_insights() == 1
    assert insights_svc.seed_dispersal_insights() == 0  # idempotent

    assert db["behavioral_insights"].count == 0  # nothing written as anyone's own
    for user_id in (1, 2):  # every user sees it, flagged as shared
        out = json.loads(
            insights_svc.get_behavioral_insights_for_agent("common carp", user_id=user_id)
        )
        assert out["count"] == 1
        assert out["insights"][0]["shared"] is True


def test_shared_insight_is_not_listed_twice_for_a_user_who_holds_a_copy(db):
    entry = BehavioralInsight(
        user_id=1,
        species="Common Carp",
        condition_type="habitat",
        condition_context="isolated-stormwater-pond",
        conclusion="c",
        source_type="agent_synthesis",
        source_detail="d",
    )
    from src.storage.insights import insert_shared_insight

    insert_shared_insight(db, entry)
    insert_insight(db, entry)

    assert query_shared_insights(db, "carp", owned_by=1) == []
    assert len(query_shared_insights(db, "carp", owned_by=2)) == 1


def test_user_context_from_row_and_demo():
    ctx = UserContext.from_row({"id": 7, "username": "ana", "role": "user"})
    assert (ctx.user_id, ctx.username, ctx.is_demo) == (7, "ana", False)
    assert ctx.daily_message_limit == 50
    demo = UserContext.from_row({"id": None, "username": "demo", "demo": True})
    assert demo.user_id is None and demo.is_demo


def test_cli_refuses_to_guess_between_users(db):
    import src.cli.main as cli

    db["users"].insert({"id": 1, "username": "jason"}, pk="id", replace=True)
    assert cli._cli_user_id(db, None) == 1  # the only user is unambiguous
    db["users"].insert({"id": 2, "username": "ana"}, pk="id", replace=True)
    with pytest.raises(typer.Exit):
        cli._cli_user_id(db, None)
    assert cli._cli_user_id(db, "ANA") == 2
    with pytest.raises(typer.Exit):
        cli._cli_user_id(db, "nobody")


def test_migration_gives_trips_an_owner_and_adds_the_shared_table(db):
    assert "user_id" in {c.name for c in db["trips"].columns}
    assert "shared_insights" in db.table_names()
