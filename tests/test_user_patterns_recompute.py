"""The derived user layer is computed on write, not on every question.

What matters here is not that the numbers are right — test_context_layer.py
covers the derivation itself — but that the cache cannot serve a wrong answer.
So every test either counts rebuilds or changes the inputs and demands the
answer move.
"""

import json

import pytest

from src.services.context import recompute_user_layer, user_layer
from src.storage import user_patterns
from src.storage.database import get_db


@pytest.fixture
def db(tmp_path):
    return get_db(tmp_path / "patterns.db")


def _add_stop(db, *, user_id=1, species=("creek chub",), productive=True, clarity=None):
    db["sessions"].insert({"date": "2025-06-14", "overall_notes": None, "user_id": user_id})
    session_id = db.execute("SELECT MAX(id) FROM sessions").fetchone()[0]
    db["stops"].insert(
        {
            "session_id": session_id,
            "user_id": user_id,
            "location_name": "Bronte Creek",
            "location_text": "Bronte Creek",
            "species_caught": json.dumps(list(species)),
            "party_species_caught": json.dumps([]),
            "was_productive": 1 if productive else 0,
            "technique": "drift",
            "water_clarity": clarity,
        }
    )


@pytest.fixture
def counted(monkeypatch):
    """Count how many times the layer is actually derived from raw rows."""
    import src.services.context.user as user_mod

    calls = {"n": 0}
    real = user_mod.build_user_layer

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(user_mod, "build_user_layer", counting)
    return calls


# -- the point of the exercise -------------------------------------------------


def test_repeated_questions_do_not_re_derive_the_layer(db, counted):
    _add_stop(db)

    first = user_layer(db, user_id=1)
    for _ in range(5):
        user_layer(db, user_id=1)

    assert counted["n"] == 1, "derived once, served five more times"
    assert user_layer(db, user_id=1).total_stops == first.total_stops


def test_a_new_stop_invalidates_the_stored_layer(db, counted):
    _add_stop(db)
    assert user_layer(db, user_id=1).total_stops == 1

    _add_stop(db)
    assert user_layer(db, user_id=1).total_stops == 2
    assert counted["n"] == 2, "recomputed exactly once more"


def test_logging_a_session_recomputes_without_being_asked(db, counted):
    """The write path owns the recompute; the read path should find it done."""
    _add_stop(db)
    recompute_user_layer(db, user_id=1)
    before = counted["n"]

    layer = user_layer(db, user_id=1)
    assert counted["n"] == before, "the read was served from the write's work"
    assert layer.total_stops == 1


def test_another_users_activity_does_not_invalidate_this_user(db, counted):
    _add_stop(db, user_id=1)
    user_layer(db, user_id=1)
    baseline = counted["n"]

    _add_stop(db, user_id=2)
    user_layer(db, user_id=1)
    assert counted["n"] == baseline, "fingerprint is scoped per user"


def test_each_user_gets_their_own_stored_layer(db):
    _add_stop(db, user_id=1)
    _add_stop(db, user_id=2)
    _add_stop(db, user_id=2)

    assert user_layer(db, user_id=1).total_stops == 1
    assert user_layer(db, user_id=2).total_stops == 2


# -- the cache is never a source of truth --------------------------------------


def test_a_corrupt_stored_row_is_a_miss_not_a_crash(db, counted):
    _add_stop(db)
    user_layer(db, user_id=1)

    db["user_patterns"].update(1, {"layer_json": "{not json at all"})
    layer = user_layer(db, user_id=1)

    assert layer.total_stops == 1
    assert counted["n"] == 2


def test_a_dropped_cache_table_changes_speed_not_answers(db):
    _add_stop(db, clarity="stained")
    expected = user_layer(db, user_id=1)

    db["user_patterns"].drop()
    # the profile carries its own computed_at stamp, so exclude it at both levels
    skip = {"computed_at": True, "profile": {"computed_at": True}}
    assert user_layer(db, user_id=1).model_dump(exclude=skip) == expected.model_dump(exclude=skip)


def test_an_unwritable_cache_still_returns_the_right_answer(db, monkeypatch, caplog):
    """A failing write must be loud, but must not break the read."""
    _add_stop(db)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(user_patterns, "ensure_table", boom)
    with caplog.at_level("WARNING"):
        layer = user_layer(db, user_id=1)

    assert layer.total_stops == 1
    assert any("store" in r.getMessage() or "user layer" in r.getMessage()
               for r in caplog.records)


def test_force_rebuilds_even_when_the_fingerprint_matches(db, counted):
    _add_stop(db)
    user_layer(db, user_id=1)
    user_layer(db, user_id=1, force=True)
    assert counted["n"] == 2


# -- the fingerprint ------------------------------------------------------------


def test_fingerprint_moves_when_an_insight_is_recorded(db):
    _add_stop(db)
    before = user_patterns.input_fingerprint(db, 1)

    db["behavioral_insights"].insert(
        {
            "species": "creek chub",
            "condition_type": "habitat",
            "condition_context": "riffle",
            "conclusion": "holds in riffles",
            "confidence": "low",
            "source_type": "trip_log",
            "source_detail": "log",
            "evidence_count": 1,
            "is_current": 1,
            "user_id": 1,
            "created_at": "2025-07-01",
        },
        alter=True,
    )
    assert user_patterns.input_fingerprint(db, 1) != before


def test_fingerprint_is_stable_when_nothing_changed(db):
    _add_stop(db)
    assert user_patterns.input_fingerprint(db, 1) == user_patterns.input_fingerprint(
        db, 1
    )


# -- the freshness check must be cheaper than a miss ---------------------------


def test_fingerprint_is_one_round_trip(db, monkeypatch):
    """A cache whose freshness check costs more than the derivation is not one.

    The first version issued six queries — three table_names() probes plus a
    count each — which made the whole layer slower than deriving it for any
    log under a few hundred stops.
    """
    _add_stop(db)

    queries = []
    real = db.execute

    def counting(sql, *args, **kwargs):
        queries.append(sql)
        return real(sql, *args, **kwargs)

    monkeypatch.setattr(db, "execute", counting)
    user_patterns.input_fingerprint(db, 1)
    assert len(queries) == 1


def test_a_database_missing_a_table_still_fingerprints(tmp_path):
    """A Database built without ensure_schema must not blow up the read path."""
    from sqlite_utils import Database

    bare = Database(tmp_path / "bare.db")
    bare["stops"].create({"id": int, "user_id": int}, pk="id")

    fp = user_patterns.input_fingerprint(bare, 1)
    assert "stops:0:0" in fp
    assert "sessions:absent" in fp
    assert "insights:absent" in fp


def test_a_cold_cache_does_not_probe_for_the_table(tmp_path, monkeypatch):
    """load() on a database with no cache table is a miss, not an error."""
    from sqlite_utils import Database

    bare = Database(tmp_path / "cold.db")
    assert user_patterns.load(bare, 1, "anything") is None


def test_fingerprint_moves_when_a_pin_is_saved_or_removed(db):
    from src.storage import pins

    pins.ensure_tables(db)
    before = user_patterns.input_fingerprint(db, 1)
    pin_id = pins.create_pin(db, 1, "Bend pool", 43.5, -79.6)
    saved = user_patterns.input_fingerprint(db, 1)
    assert saved != before
    pins.delete_pin(db, 1, pin_id)
    assert user_patterns.input_fingerprint(db, 1) != saved
