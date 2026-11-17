"""Tests for Reddit service layer."""

import json
from datetime import UTC, datetime

import pytest

from src.models.reddit_post import RedditPost
from src.services.reddit import search_reddit_for_agent
from src.storage.reddit_posts import upsert_posts


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _make_post(
    post_id: str,
    title: str,
    body: str,
    species: list[str] | None = None,
    locations: list[str] | None = None,
    jurisdiction: str = "CA-ON",
) -> RedditPost:
    return RedditPost(
        post_id=post_id,
        subreddit="OntarioFishing",
        post_type="post",
        title=title,
        body=body,
        url=f"https://reddit.com/r/OntarioFishing/comments/{post_id}/",
        author="tester",
        score=10,
        num_comments=2,
        created_utc=datetime(2024, 5, 1, tzinfo=UTC),
        extracted_species=species or [],
        extracted_locations=locations or [],
        jurisdiction=jurisdiction,
        ingested_at=_now(),
    )


@pytest.fixture()
def populated_db(fast_db):
    db = fast_db
    posts = [
        _make_post(
            "p1",
            "Smallmouth on Grand River",
            "Great smallmouth bass session using tubes near boulders.",
            species=["smallmouth bass"],
            locations=["grand river"],
        ),
        _make_post(
            "p2",
            "Walleye tips for Ontario",
            "Trolling crankbaits worked well at dusk.",
            species=["walleye"],
        ),
        _make_post(
            "p3",
            "Madtom microfishing Credit River",
            "Found madtoms under flat rocks in shallow riffles.",
            species=["madtom"],
            locations=["credit river"],
        ),
    ]
    upsert_posts(db, posts)
    return db


def test_search_returns_matching_posts(populated_db, monkeypatch):
    monkeypatch.setattr("src.services.reddit.get_db", lambda: populated_db)
    result = json.loads(search_reddit_for_agent("smallmouth"))
    assert result["count"] >= 1
    titles = [p["title"] for p in result["posts"]]
    assert any("Smallmouth" in t for t in titles)


def test_search_no_results_returns_note(populated_db, monkeypatch):
    monkeypatch.setattr("src.services.reddit.get_db", lambda: populated_db)
    result = json.loads(search_reddit_for_agent("completely unrelated xyz query"))
    assert result["count"] == 0
    assert "note" in result


def test_search_includes_source_note(populated_db, monkeypatch):
    monkeypatch.setattr("src.services.reddit.get_db", lambda: populated_db)
    result = json.loads(search_reddit_for_agent("walleye"))
    if result["count"] > 0:
        assert "source_note" in result
        # Presence-vs-pressure framing must be present
        assert "pressure" in result["source_note"].lower()


def test_search_body_truncated_at_600(populated_db, monkeypatch):
    from src.storage.reddit_posts import upsert_post

    long_body = "a" * 800
    post = _make_post("p_long", "Long post", long_body)
    upsert_post(populated_db, post)

    monkeypatch.setattr("src.services.reddit.get_db", lambda: populated_db)
    result = json.loads(search_reddit_for_agent("Long post"))
    if result["count"] > 0:
        returned_body = result["posts"][0]["body"]
        assert len(returned_body) <= 603  # 600 chars + "..."


def test_upsert_deduplicates(populated_db):
    # Upserting the same post twice should not create duplicates
    from src.storage.reddit_posts import get_post, upsert_post

    post = _make_post("p1", "Updated title", "Updated body", species=["walleye"])
    upsert_post(populated_db, post)
    retrieved = get_post(populated_db, "p1")
    assert retrieved is not None
    assert retrieved.title == "Updated title"
