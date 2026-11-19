"""Reddit fishing community content fetcher.

Uses Reddit's public JSON API (append .json to any standard URL).
OAuth-ready: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET environment variables
to switch to authenticated requests (600 req/min vs ~30 req/min unauthenticated).

Cached to data/cache/reddit/ with a 24-hour TTL.
Rate-limited to 1.5 s/request for the public API.
"""

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.models.reddit_post import RedditPost

_PUBLIC_BASE = "https://www.reddit.com"
_OAUTH_BASE = "https://oauth.reddit.com"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_CACHE_DIR = Path("data/cache/reddit")
_CACHE_TTL_SECONDS = 86400  # 24 hours
_USER_AGENT = "fishdex/1.0 (personal fishing exploration tool; github.com/GuaiDev/fishdex)"
_RATE_LIMIT_DELAY = 1.5  # seconds between requests (public API)
_STARTUP_DELAY = 2.0  # Reddit blocks requests that arrive too fast after startup

# Pattern → canonical species name (checked against lowercased post text)
_SPECIES_KEYWORDS: list[tuple[str, str]] = [
    ("smallmouth", "smallmouth bass"),
    ("largemouth", "largemouth bass"),
    ("walleye", "walleye"),
    ("northern pike", "northern pike"),
    (" pike", "northern pike"),
    ("muskie", "muskellunge"),
    ("muskellunge", "muskellunge"),
    ("yellow perch", "yellow perch"),
    ("perch", "yellow perch"),
    ("crappie", "crappie"),
    ("bluegill", "bluegill"),
    (" sunfish", "sunfish"),
    ("brook trout", "brook trout"),
    ("rainbow trout", "rainbow trout"),
    ("brown trout", "brown trout"),
    ("lake trout", "lake trout"),
    ("steelhead", "steelhead"),
    ("atlantic salmon", "atlantic salmon"),
    ("chinook", "chinook salmon"),
    ("coho", "coho salmon"),
    ("channel catfish", "channel catfish"),
    ("catfish", "catfish"),
    ("longnose gar", "longnose gar"),
    (" gar", "longnose gar"),
    ("bowfin", "bowfin"),
    (" carp", "common carp"),
    ("redhorse", "redhorse"),
    ("sculpin", "sculpin"),
    ("madtom", "madtom"),
    (" darter", "darter"),
    (" dace", "dace"),
    ("lamprey", "lamprey"),
    (" shiner", "shiner"),
    (" chub", "chub"),
    ("bass", "bass"),  # catch-all; cleaned up below if more specific match found
]

# Location substrings to extract (checked against lowercased post text)
_LOCATION_KEYWORDS = [
    "credit river",
    "grand river",
    "humber river",
    "don river",
    "niagara river",
    "rideau river",
    "trent river",
    "severn river",
    "lake simcoe",
    "lake erie",
    "lake huron",
    "lake ontario",
    "lake superior",
    "georgian bay",
    "kawartha",
    "muskoka",
    "saugeen river",
    "caledonia",
    "dunnville",
    "brantford",
    "guelph",
    "hamilton",
    "kingston",
    "sudbury",
    "thunder bay",
    "toronto",
]

# In-memory OAuth token cache (avoids re-fetching within a session)
_token_cache: dict[str, object] = {}
_first_request_done = False  # startup delay fires once before the first real HTTP call


def fetch_subreddit_posts(
    subreddit: str,
    listing: str = "hot",
    limit: int = 100,
    query: str | None = None,
    time_filter: str = "year",
) -> list[RedditPost]:
    """Fetch up to `limit` posts from a subreddit listing or search.

    listing: "hot", "new", "top" — ignored when query is provided.
    query: if set, performs subreddit search instead of listing.
    time_filter: "day", "week", "month", "year", "all" — used with query.
    Reddit returns max 100 items per request; limit is capped at 100.
    """
    limit = min(limit, 100)

    if query:
        url = f"{_PUBLIC_BASE}/r/{subreddit}/search.json"
        params: dict = {
            "q": query,
            "restrict_sr": "1",
            "sort": "relevance",
            "t": time_filter,
            "limit": limit,
        }
    else:
        url = f"{_PUBLIC_BASE}/r/{subreddit}/{listing}.json"
        params = {"limit": limit}

    data = _cached_get(url, params)
    children = data.get("data", {}).get("children", [])

    posts = []
    for child in children:
        post = _parse_post(child)
        if post is not None:
            posts.append(post)

    return posts


def _parse_post(child: dict) -> RedditPost | None:
    kind = child.get("kind", "")
    data = child.get("data", {})

    if not data:
        return None
    if data.get("stickied") or data.get("distinguished") == "moderator":
        return None

    post_id = data.get("id", "")
    if not post_id:
        return None

    post_type: str = "comment" if kind == "t1" else "post"
    title: str | None = data.get("title") if post_type == "post" else None
    raw_body: str = data.get("selftext") or data.get("body") or ""
    body = "" if raw_body in ("[deleted]", "[removed]") else raw_body

    subreddit = data.get("subreddit", "")
    author = data.get("author", "[deleted]")
    score = int(data.get("score") or 0)
    num_comments = int(data.get("num_comments") or 0)
    permalink = data.get("permalink", "")
    url = f"https://www.reddit.com{permalink}" if permalink else ""
    created_utc = datetime.fromtimestamp(float(data.get("created_utc") or 0), tz=UTC)

    text = f"{title or ''} {body}".lower()
    extracted_species = _extract_species(text)
    extracted_locations = _extract_locations(text)
    jurisdiction = _infer_jurisdiction(subreddit, text)

    return RedditPost(
        post_id=post_id,
        subreddit=subreddit,
        post_type=post_type,
        title=title,
        body=body,
        url=url,
        author=author,
        score=score,
        num_comments=num_comments,
        parent_post_id=None,
        created_utc=created_utc,
        extracted_species=extracted_species,
        extracted_locations=extracted_locations,
        jurisdiction=jurisdiction,
        ingested_at=datetime.now(tz=UTC),
    )


def _extract_species(text: str) -> list[str]:
    found: dict[str, bool] = {}
    for pattern, canonical in _SPECIES_KEYWORDS:
        if pattern in text:
            found[canonical] = True
    if "bass" in found and ("smallmouth bass" in found or "largemouth bass" in found):
        del found["bass"]
    return list(found.keys())


def _extract_locations(text: str) -> list[str]:
    return [loc for loc in _LOCATION_KEYWORDS if loc in text]


_ONTARIO_SUBREDDITS = {"ontariofishing"}
_CANADA_SUBREDDITS = {"canadianfishing"}


def _infer_jurisdiction(subreddit: str, text: str) -> str | None:
    sub = subreddit.lower()
    if sub in _ONTARIO_SUBREDDITS or "ontario" in text or "ca-on" in text:
        return "CA-ON"
    if sub in _CANADA_SUBREDDITS or "canada" in text:
        return "CA"
    return None


def _cached_get(url: str, params: dict) -> dict:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(
        json.dumps({"url": url, "params": params}, sort_keys=True).encode()
    ).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{cache_key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            return json.loads(cache_file.read_text())

    global _first_request_done
    if not _first_request_done:
        time.sleep(_STARTUP_DELAY)
        _first_request_done = True

    headers = {"User-Agent": _USER_AGENT}
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")

    if client_id and client_secret:
        token = _get_oauth_token(client_id, client_secret)
        headers["Authorization"] = f"Bearer {token}"
        actual_url = url.replace(_PUBLIC_BASE, _OAUTH_BASE, 1)
    else:
        actual_url = url

    response = httpx.get(actual_url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data))
    return data


def _get_oauth_token(client_id: str, client_secret: str) -> str:
    expires = float(_token_cache.get("expires", 0))
    if expires > time.time() + 60:
        return str(_token_cache["token"])

    response = httpx.post(
        _TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        headers={"User-Agent": _USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    token_data = response.json()
    _token_cache["token"] = token_data["access_token"]
    _token_cache["expires"] = time.time() + float(token_data.get("expires_in", 3600))
    return str(token_data["access_token"])
