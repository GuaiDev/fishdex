"""Agent-facing service for Reddit community content."""

import json
import logging

import httpx

from src.storage.database import get_db
from src.storage.reddit_posts import search_posts, upsert_posts

log = logging.getLogger(__name__)


def search_reddit_for_agent(
    query: str,
    species: str | None = None,
    jurisdiction: str | None = None,
    limit: int = 10,
) -> str:
    db = get_db()
    posts = search_posts(
        db, query=query, species_filter=species, jurisdiction=jurisdiction, limit=limit
    )

    if not posts:
        return json.dumps(
            {
                "query": query,
                "count": 0,
                "note": (
                    "No Reddit posts found matching this query. "
                    "Run `make ingest` to populate the Reddit community database, "
                    "or the database may not contain content for this topic yet."
                ),
            }
        )

    return json.dumps(
        {
            "query": query,
            "count": len(posts),
            "source_note": (
                "Community fishing reports. "
                "High post volume on a spot reflects angler pressure, not fish abundance. "
                "Low results may indicate low angler presence rather than fish absence."
            ),
            "posts": [
                {
                    "post_id": p.post_id,
                    "subreddit": p.subreddit,
                    "title": p.title,
                    "body": p.body[:600] + ("..." if len(p.body) > 600 else ""),
                    "url": p.url,
                    "score": p.score,
                    "created_utc": p.created_utc.isoformat(),
                    "extracted_species": p.extracted_species,
                    "extracted_locations": p.extracted_locations,
                    "jurisdiction": p.jurisdiction,
                }
                for p in posts
            ],
        }
    )


def fetch_and_store(
    subreddits_hot: list[str] | None = None,
    subreddits_search: dict[str, list[str]] | None = None,
    limit_per_subreddit: int = 100,
) -> int:
    """Fetch posts from target subreddits and store them. Returns count processed."""
    from src.ingest.community.reddit import fetch_subreddit_posts
    from src.services.ingest_runs import record_counts, record_error

    if subreddits_hot is None:
        subreddits_hot = ["OntarioFishing", "CanadianFishing"]
    if subreddits_search is None:
        subreddits_search = {
            "Fishing": ["ontario fishing", "ontario bass", "ontario trout", "ontario walleye"],
            "FlyFishing": ["ontario"],
            "Microfishing": ["ontario", "darter", "madtom", "dace"],
        }

    db = get_db()
    total = 0

    try:
        for subreddit in subreddits_hot:
            posts = fetch_subreddit_posts(subreddit, listing="hot", limit=limit_per_subreddit)
            record_counts(fetched=1, empty=int(not posts))
            written = upsert_posts(db, posts)
            record_counts(stored=written, skipped=len(posts) - written)
            total += written
            new_posts = fetch_subreddit_posts(subreddit, listing="new", limit=50)
            record_counts(fetched=1, empty=int(not new_posts))
            written = upsert_posts(db, new_posts)
            record_counts(stored=written, skipped=len(new_posts) - written)
            total += written

        for subreddit, queries in subreddits_search.items():
            for q in queries:
                posts = fetch_subreddit_posts(subreddit, query=q, limit=50, time_filter="year")
                record_counts(fetched=1, empty=int(not posts))
                written = upsert_posts(db, posts)
                record_counts(stored=written, skipped=len(posts) - written)
                total += written

    except httpx.HTTPStatusError as exc:
        record_error(exc)
        status = exc.response.status_code
        if status == 403:
            log.warning("Reddit fetch blocked (403) — API credentials may be required. Skipping.")
        elif status == 429:
            log.warning("Reddit rate-limited (429) — try again later. Skipping.")
        else:
            log.warning("Reddit fetch failed (%d) — skipping.", status)
        return total

    return total
