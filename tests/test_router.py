"""Tests for message router and synthesis cache."""

import pytest
from sqlite_utils import Database

from src.storage.database import ensure_schema


@pytest.fixture
def db_conn(tmp_path):
    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    return db


def test_classify_returns_valid_mode():
    from src.agent.router import classify_message

    result = classify_message("What knot should I use for braid to mono?")
    assert result["mode"] in ("reflex", "synthesis", "memory")


def test_classify_falls_back_to_synthesis_on_error(monkeypatch):
    from src.agent import router

    def boom(*a, **k):
        raise RuntimeError("no client")

    monkeypatch.setattr(router, "get_client", boom)
    result = router.classify_message("anything")
    assert result["mode"] == "synthesis"


def test_cache_key_stability():
    from src.services.synthesis_cache import _cache_key

    k1 = _cache_key(42.9170, -79.7746, None, 1)
    k2 = _cache_key(42.9171, -79.7745, None, 1)  # ~15m away, should round to same key
    assert k1 == k2


def test_cache_miss_returns_none(db_conn):
    from src.services.synthesis_cache import get_cached_synthesis

    result = get_cached_synthesis(db_conn, user_id=1, lat=42.9, lng=-79.7)
    assert result is None


def test_cache_store_and_hit(db_conn):
    from src.services.synthesis_cache import get_cached_synthesis, store_synthesis

    store_synthesis(
        db_conn,
        "Marshy tributaries feed this bend.",
        user_id=1,
        lat=42.917,
        lng=-79.774,
        location_name="Willoway",
    )
    result = get_cached_synthesis(db_conn, user_id=1, lat=42.917, lng=-79.774)
    assert result is not None
    assert result["cache_hit"] is True
    assert "tributaries" in result["synthesis"]


def test_cache_proximity_hit(db_conn):
    from src.services.synthesis_cache import get_cached_synthesis, store_synthesis

    store_synthesis(db_conn, "Test synthesis", user_id=1, lat=42.917, lng=-79.774)
    # Query ~100m away should still hit via proximity
    result = get_cached_synthesis(db_conn, user_id=1, lat=42.9178, lng=-79.7748, radius_km=0.15)
    assert result is not None


def test_cache_key_differs_per_user():
    from src.services.synthesis_cache import _cache_key

    assert _cache_key(42.917, -79.774, None, 1) != _cache_key(42.917, -79.774, None, 2)
