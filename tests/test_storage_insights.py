"""Tests for behavioral insights storage layer."""

import pytest

from src.models.behavioral_insight import BehavioralInsight
from src.storage.database import ensure_schema
from src.storage.insights import (
    contradict_insight,
    get_insight,
    insert_insight,
    mark_user_verified,
    query_insights,
    refine_insight,
)

try:
    from sqlite_utils import Database
except ImportError:
    Database = None


def _make_db(tmp_path):
    from sqlite_utils import Database as DB

    db = DB(tmp_path / "test.db")
    ensure_schema(db)
    return db


def _insight(**overrides) -> BehavioralInsight:
    base = dict(
        user_id=1,
        species="brook trout",
        condition_type="behavioral",
        condition_context="post-cold-front",
        conclusion="Brook trout feed aggressively after a cold front in streams under 15°C.",
        confidence="medium",
        source_type="trip_log",
        source_detail="8 personal outings",
        evidence_count=8,
    )
    base.update(overrides)
    return BehavioralInsight(**base)


def test_insert_and_get(tmp_path):
    db = _make_db(tmp_path)
    new_id = insert_insight(db, _insight())
    assert isinstance(new_id, int)
    fetched = get_insight(db, new_id)
    assert fetched is not None
    assert fetched.species == "brook trout"
    assert fetched.version == 1
    assert fetched.is_current is True


def test_get_nonexistent(tmp_path):
    db = _make_db(tmp_path)
    assert get_insight(db, 9999) is None


def test_refine_increments_version(tmp_path):
    db = _make_db(tmp_path)
    old_id = insert_insight(db, _insight())

    refined = _insight(conclusion="Updated: brook trout feed 30-60 min post-front at sub-15°C.")
    new_id = refine_insight(db, old_id, refined)

    old = get_insight(db, old_id)
    new = get_insight(db, new_id)

    assert old is not None and new is not None
    assert old.is_current is False
    assert new.is_current is True
    assert new.version == old.version + 1


def test_refine_sets_contradicted_by(tmp_path):
    db = _make_db(tmp_path)
    old_id = insert_insight(db, _insight())
    new_id = refine_insight(db, old_id, _insight(conclusion="Refined conclusion."))

    old = get_insight(db, old_id)
    assert old is not None
    assert old.contradicted_by == new_id


def test_refine_nonexistent_raises(tmp_path):
    db = _make_db(tmp_path)
    with pytest.raises(ValueError, match="No insight with id=9999"):
        refine_insight(db, 9999, _insight())


def test_query_current_only(tmp_path):
    db = _make_db(tmp_path)
    old_id = insert_insight(db, _insight())
    refine_insight(db, old_id, _insight(conclusion="Refined."))

    results = query_insights(db, species="brook trout", current_only=True, user_id=1)
    assert len(results) == 1
    assert results[0].is_current is True


def test_query_all_versions(tmp_path):
    db = _make_db(tmp_path)
    old_id = insert_insight(db, _insight())
    refine_insight(db, old_id, _insight(conclusion="Refined."))

    results = query_insights(db, species="brook trout", current_only=False, user_id=1)
    assert len(results) == 2


def test_query_condition_type_filter(tmp_path):
    db = _make_db(tmp_path)
    insert_insight(db, _insight(condition_type="behavioral"))
    insert_insight(db, _insight(condition_type="habitat", conclusion="Prefer riffle edges."))

    behavioral = query_insights(db, species="brook trout", condition_type="behavioral", user_id=1)
    assert len(behavioral) == 1
    assert behavioral[0].condition_type == "behavioral"


def test_query_case_insensitive(tmp_path):
    db = _make_db(tmp_path)
    insert_insight(db, _insight(species="Brook Trout"))

    results = query_insights(db, species="brook trout", user_id=1)
    assert len(results) == 1

    results2 = query_insights(db, species="BROOK", user_id=1)
    assert len(results2) == 1


def test_mark_user_verified(tmp_path):
    db = _make_db(tmp_path)
    insight_id = insert_insight(db, _insight())
    mark_user_verified(db, insight_id)
    fetched = get_insight(db, insight_id)
    assert fetched is not None
    assert fetched.user_verified is True


def test_contradict_insight(tmp_path):
    db = _make_db(tmp_path)
    old_id = insert_insight(db, _insight())
    new_id = insert_insight(db, _insight(conclusion="Newer conclusion."))
    contradict_insight(db, old_id=old_id, new_id=new_id)
    old = get_insight(db, old_id)
    assert old is not None
    assert old.contradicted_by == new_id


def test_empty_query(tmp_path):
    db = _make_db(tmp_path)
    results = query_insights(db, species="walleye", user_id=1)
    assert results == []


# ── cross-user isolation ──────────────────────────────────────────────────────
#
# behavioral_insights is a per-user table. query_insights filtered on species and
# is_current but not user_id, so one angler's stored conclusions surfaced in
# another's conflict checks — the same leak class found in coaching.py, and live
# in the request path via chat.py's check_recommendation_conflicts.


def test_query_insights_is_scoped_to_one_user(tmp_path):
    db = _make_db(tmp_path)
    mine = insert_insight(db, _insight())
    theirs = insert_insight(db, _insight(conclusion="Someone else's conclusion entirely."))
    db["behavioral_insights"].update(theirs, {"user_id": 2})
    db["behavioral_insights"].update(mine, {"user_id": 1})

    got = query_insights(db, species="brook trout", user_id=1)
    assert [i.conclusion for i in got] == [_insight().conclusion]

    other = query_insights(db, species="brook trout", user_id=2)
    assert len(other) == 1
    assert "Someone else" in other[0].conclusion


def test_check_conflicts_does_not_see_another_users_insights(tmp_path):
    from src.storage.insights import check_conflicts

    db = _make_db(tmp_path)
    theirs = insert_insight(db, _insight(lat=43.40, lng=-79.75))
    db["behavioral_insights"].update(theirs, {"user_id": 2})

    # user 1 has recorded nothing, so nothing of theirs can conflict
    assert check_conflicts(db, "brook trout", lat=43.40, lng=-79.75, user_id=1) == []
    assert len(check_conflicts(db, "brook trout", lat=43.40, lng=-79.75, user_id=2)) == 1
