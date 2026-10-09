"""Tests for synthesis cache routing — location extraction and cache store/retrieve."""

import pytest

from src.services.synthesis_cache import get_cached_synthesis, store_synthesis
from src.storage.database import get_db

# ── db fixture ────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_conn(tmp_path):
    # Real schema (via ensure_schema/migrations), not a hand-rolled CREATE TABLE —
    # a hand-rolled copy previously drifted from the real schema and would have
    # silently failed to catch the jurisdiction column being added.
    return get_db(tmp_path / "test.db")


# ── location extraction tests (live Haiku calls) ──────────────────────────────


def test_location_extraction_with_name():
    from src.agent.router import extract_location_from_message

    result = extract_location_from_message(
        "Why does Willoway Park on the Grand River hold channel catfish during midday heat?"
    )
    assert isinstance(result, dict)
    assert "lat" in result
    assert "location_name" in result
    if result["location_name"]:
        assert (
            "willoway" in result["location_name"].lower()
            or "park" in result["location_name"].lower()
        )


def test_location_extraction_with_coords():
    from src.agent.router import extract_location_from_message

    result = extract_location_from_message("What's the habitat like at 42.917, -79.774?")
    assert isinstance(result, dict)
    assert "lat" in result


def test_location_extraction_no_location():
    from src.agent.router import extract_location_from_message

    result = extract_location_from_message("What bait should I use for bass?")
    assert isinstance(result, dict)
    assert "lat" in result
    assert "location_name" in result


# ── cache store / retrieve tests (no network calls) ───────────────────────────


def test_store_and_retrieve_synthesis(db_conn):
    store_synthesis(
        db_conn,
        synthesis="Willoway Park has marshy OHN tributaries that concentrate catfish.",
        user_id=1,
        lat=42.917,
        lng=-79.774,
        location_name="Willoway Park",
    )
    result = get_cached_synthesis(db_conn, user_id=1, lat=42.917, lng=-79.774)
    assert result is not None
    assert result["cache_hit"] is True
    assert "catfish" in result["synthesis"]


def test_cache_miss_returns_none(db_conn):
    result = get_cached_synthesis(
        db_conn, user_id=1, lat=43.5, lng=-80.5, location_name="Nowhere Creek"
    )
    assert result is None


def test_cache_hit_increments_count(db_conn):
    store_synthesis(
        db_conn,
        synthesis="Test synthesis content.",
        user_id=1,
        location_name="Test Location",
    )
    # First hit
    get_cached_synthesis(db_conn, user_id=1, location_name="Test Location")
    row = db_conn.execute(
        "SELECT hit_count FROM segment_synthesis WHERE location_name = ?",
        ["Test Location"],
    ).fetchone()
    assert row[0] == 1

    # Second hit
    get_cached_synthesis(db_conn, user_id=1, location_name="Test Location")
    row = db_conn.execute(
        "SELECT hit_count FROM segment_synthesis WHERE location_name = ?",
        ["Test Location"],
    ).fetchone()
    assert row[0] == 2


def test_nearby_coord_hits_cache(db_conn):
    """A query ~50m away from a cached entry should still hit via radius search."""
    store_synthesis(
        db_conn,
        synthesis="Spotted close to Byng Island.",
        user_id=1,
        lat=43.000,
        lng=-80.000,
    )
    # 0.001 degree ≈ 100m — within default 150m radius
    result = get_cached_synthesis(db_conn, user_id=1, lat=43.001, lng=-80.001)
    assert result is not None
    assert result["cache_hit"] is True


# ── cross-jurisdiction collision safety ────────────────────────────────────────
# Canadian hydronyms repeat constantly across provinces (Mill Creek, Beaver
# Creek, Trout Lake, ...). extract_location_from_message returns a bare name
# with no coordinates when the user doesn't give any — these tests guard
# against an Ontario synthesis being served back for a same-named BC query.


def test_name_only_entries_auto_tagged_with_jurisdiction_from_coords(db_conn):
    """Even a name-only lookup carries a derivable jurisdiction once the
    caller also has coordinates for the same query."""
    store_synthesis(
        db_conn,
        synthesis="Ontario Mill Creek: warm, silty, chub and creek chub water.",
        user_id=1,
        lat=43.20,
        lng=-79.90,  # CA-ON
        location_name="Mill Creek",
    )
    row = db_conn.execute(
        "SELECT jurisdiction FROM segment_synthesis WHERE location_name = ?",
        ["Mill Creek"],
    ).fetchone()
    assert row[0] == "CA-ON"


def test_exact_name_key_does_not_cross_jurisdictions_when_both_known(db_conn):
    """Same cache_key ('name:mill creek') could theoretically arise for two
    different real creeks — if we know both jurisdictions and they differ,
    the cached entry must not be served."""
    store_synthesis(
        db_conn,
        synthesis="Ontario Mill Creek synthesis.",
        user_id=1,
        location_name="Mill Creek",
        jurisdiction="CA-ON",
    )
    result = get_cached_synthesis(
        db_conn, user_id=1, location_name="Mill Creek", jurisdiction="CA-BC"
    )
    assert result is None


def test_exact_name_key_still_hits_when_jurisdiction_matches(db_conn):
    store_synthesis(
        db_conn,
        synthesis="BC Mill Creek synthesis.",
        user_id=1,
        location_name="Mill Creek",
        jurisdiction="CA-BC",
    )
    result = get_cached_synthesis(
        db_conn, user_id=1, location_name="Mill Creek", jurisdiction="CA-BC"
    )
    assert result is not None
    assert "BC Mill Creek" in result["synthesis"]


def test_exact_name_key_still_hits_when_neither_side_has_jurisdiction(db_conn):
    """Preserves existing behaviour for the fully-unknown case (the one
    residual limitation documented in synthesis_cache.py's module docstring)."""
    store_synthesis(
        db_conn,
        synthesis="Some creek synthesis, no coordinates ever resolved.",
        user_id=1,
        location_name="Beaver Creek",
    )
    result = get_cached_synthesis(db_conn, user_id=1, location_name="Beaver Creek")
    assert result is not None


def test_fuzzy_name_match_blocked_across_known_jurisdictions(db_conn):
    """Fuzzy word-subset matching is the riskiest path — a BC query for
    'Bow River' must not match a cached Alberta 'Bow River Calgary' entry."""
    store_synthesis(
        db_conn,
        synthesis="Alberta Bow River Calgary: trout, catch and release.",
        user_id=1,
        lat=51.05,
        lng=-114.07,  # CA-AB
        location_name="Bow River Calgary",
    )
    result = get_cached_synthesis(
        db_conn, user_id=1, location_name="Bow River", jurisdiction="CA-BC"
    )
    assert result is None


def test_fuzzy_name_match_still_works_within_same_jurisdiction(db_conn):
    store_synthesis(
        db_conn,
        synthesis="Alberta Bow River Calgary: trout, catch and release.",
        user_id=1,
        lat=51.05,
        lng=-114.07,  # CA-AB
        location_name="Bow River Calgary",
    )
    result = get_cached_synthesis(
        db_conn, user_id=1, location_name="Bow River", jurisdiction="CA-AB"
    )
    assert result is not None


def test_coordinate_proximity_match_blocked_across_known_jurisdictions(db_conn):
    """Defense in depth: even though 150m can't realistically cross a
    provincial border, an explicit conflicting jurisdiction must still win."""
    store_synthesis(
        db_conn,
        synthesis="Some synthesis.",
        user_id=1,
        lat=43.000,
        lng=-80.000,  # CA-ON
    )
    result = get_cached_synthesis(db_conn, user_id=1, lat=43.001, lng=-80.001, jurisdiction="CA-BC")
    assert result is None
