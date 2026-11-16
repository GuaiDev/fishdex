"""Tests for YouTube transcript ingest module. No real API calls."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.ingest.community.youtube_ingest import (
    _is_location_query,
    chunk_text,
    fetch_transcript,
    ingest_query,
    search_knowledge,
)
from src.storage.database import get_db

# --- Pure unit tests (no DB, no network) ---


def test_location_query_detection():
    assert _is_location_query("Grand River Ontario catfish") is True
    assert _is_location_query("Thames River redhorse") is True
    assert _is_location_query("Ontario walleye regulations") is True
    assert _is_location_query("how to tie a palomar knot") is False
    assert _is_location_query("bass fishing pond techniques") is False


def test_chunk_text_basic():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_text(text, chunk_size=400, overlap=50)
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert 350 <= len(chunk.split()) <= 400


def test_chunk_text_overlap():
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(text, chunk_size=400, overlap=50)
    # Last 50 words of chunk 0 should be first 50 words of chunk 1
    end_of_chunk0 = chunks[0].split()[-50:]
    start_of_chunk1 = chunks[1].split()[:50]
    assert end_of_chunk0 == start_of_chunk1


def test_chunk_text_short_input():
    text = "just a few words here"
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_text_empty():
    assert chunk_text("") == []


def test_fetch_transcript_no_transcript(monkeypatch):
    from youtube_transcript_api import NoTranscriptFound

    mock_list = MagicMock()
    mock_list.find_manually_created_transcript.side_effect = Exception("no manual")
    mock_list.find_generated_transcript.side_effect = NoTranscriptFound(
        "vid", ["en"], {}
    )

    def fake_list(self, video_id):
        return mock_list

    monkeypatch.setattr(
        "src.ingest.community.youtube_ingest.YouTubeTranscriptApi.list",
        fake_list,
    )
    result = fetch_transcript("vid123")
    assert result is None


# --- DB-backed tests (use tmp_path to avoid polluting production DB) ---


@pytest.fixture
def tmp_db(fast_db):
    return fast_db


def test_ingest_deduplication(tmp_db, monkeypatch):
    """Running the same video twice should not double-insert."""
    video = {
        "video_id": "abc123",
        "title": "Test Catfish Video",
        "channel_name": "TestChannel",
        "published_at": "2024-01-01T00:00:00Z",
        "url": "https://www.youtube.com/watch?v=abc123",
    }
    monkeypatch.setattr(
        "src.ingest.community.youtube_ingest.search_youtube", lambda q, max_results: [video]
    )
    monkeypatch.setattr(
        "src.ingest.community.youtube_ingest.fetch_transcript",
        lambda vid: "catfish on the grand river use cut bait bottom rig",
    )
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    result1 = ingest_query("catfish test", tmp_db, max_videos=1)
    result2 = ingest_query("catfish test", tmp_db, max_videos=1)

    assert result1["ingested"] == 1
    assert result2["ingested"] == 0
    assert result2["skipped_duplicate"] == 1

    source_count = tmp_db.execute(
        "SELECT COUNT(*) FROM knowledge_sources WHERE source_id = 'abc123'"
    ).fetchone()[0]
    assert source_count == 1


def test_ingest_no_transcript_skipped(tmp_db, monkeypatch):
    video = {
        "video_id": "notr123",
        "title": "Silent Video",
        "channel_name": "TestChannel",
        "published_at": "2024-01-01T00:00:00Z",
        "url": "https://www.youtube.com/watch?v=notr123",
    }
    monkeypatch.setattr(
        "src.ingest.community.youtube_ingest.search_youtube", lambda q, max_results: [video]
    )
    monkeypatch.setattr(
        "src.ingest.community.youtube_ingest.fetch_transcript", lambda vid: None
    )
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    result = ingest_query("test", tmp_db, max_videos=1)
    assert result["ingested"] == 0
    assert result["skipped_no_transcript"] == 1


def test_chunks_stored_per_video(tmp_db, monkeypatch):
    video = {
        "video_id": "chunk123",
        "title": "Chunked Video",
        "channel_name": "TestChannel",
        "published_at": "2024-01-01T00:00:00Z",
        "url": "https://www.youtube.com/watch?v=chunk123",
    }
    long_transcript = " ".join(["fishing"] * 900)
    monkeypatch.setattr(
        "src.ingest.community.youtube_ingest.search_youtube", lambda q, max_results: [video]
    )
    monkeypatch.setattr(
        "src.ingest.community.youtube_ingest.fetch_transcript",
        lambda vid: long_transcript,
    )
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    ingest_query("chunked test", tmp_db, max_videos=1)

    chunk_count = tmp_db.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
    assert chunk_count > 1


def test_search_knowledge_returns_results(tmp_db, monkeypatch):
    video = {
        "video_id": "srch123",
        "title": "Catfish Bottom Rig Tutorial",
        "channel_name": "TestChannel",
        "published_at": "2024-01-01T00:00:00Z",
        "url": "https://www.youtube.com/watch?v=srch123",
    }
    monkeypatch.setattr(
        "src.ingest.community.youtube_ingest.search_youtube", lambda q, max_results: [video]
    )
    monkeypatch.setattr(
        "src.ingest.community.youtube_ingest.fetch_transcript",
        lambda vid: "channel catfish bite best at night use stink bait or cut shad bottom rig",
    )
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    ingest_query("catfish", tmp_db, max_videos=1)

    results = search_knowledge("catfish bait", tmp_db, top_k=3)
    assert len(results) > 0
    assert "chunk_text" in results[0]
    assert "title" in results[0]
    assert "url" in results[0]


def test_search_knowledge_empty_db(tmp_db):
    results = search_knowledge("walleye", tmp_db, top_k=5)
    assert results == []
