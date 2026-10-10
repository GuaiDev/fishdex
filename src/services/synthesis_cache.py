"""
Synthesis cache: compute expensive cross-dataset location analysis once,
store it, reuse forever. Lazy — only computes spots people actually ask about.

Jurisdiction safety
--------------------
The coordinate path (_cache_key's "geo:" prefix, rounded to a ~100m grid)
and the proximity fallback (haversine within radius_km) are inherently safe
across jurisdictions: two real locations 150m apart can never be in
different provinces. The risk is the name-only path — when a message gives
no coordinates (extract_location_from_message in src/agent/router.py
explicitly extracts a bare location_name in that case), the cache key is
just "name:{lowercased name}" with no jurisdiction info at all. Canadian
hydronyms repeat constantly across provinces (Mill Creek, Beaver Creek,
Bear Creek, Trout Lake, ...) — without a check, a synthesis computed for an
Ontario "Mill Creek" could be served back verbatim (wrong regulations,
wrong species predictions, wrong water quality) for a BC "Mill Creek" query
that happens to produce the same extracted name or a fuzzy-matching subset.

Fix: every stored/looked-up entry carries a `jurisdiction` column, derived
via jurisdiction_for_coords() whenever lat/lng are known (or passed
explicitly). A candidate match is only trusted if neither side has a known
jurisdiction, or both sides agree — this doesn't change the cache_key format
(existing cached entries keep matching exactly as before), it just refuses
to serve a candidate whose jurisdiction is known to conflict.

Residual limitation, not fully solved: two purely name-only queries (no
coordinates ever resolved for either) with the same extracted name but in
different provinces still collide, since neither has a derivable
jurisdiction. Fixing that fully needs geocoding a bare place name before
the cache is even consulted, which no code path in this project does today.

Per-user isolation
-------------------
A cached reply is the agent's full answer, built from the asker's own
catches and visits ("records" and "history" in the chat_place bundle). It is
therefore personal, and is only ever served back to the user it was built for.
Every entry carries `user_id`, the user is part of `cache_key`, and every
lookup (exact, proximity and fuzzy name) filters on `user_id = ?`. Rows
written before the column existed have user_id NULL, which matches no user,
so they are never served. `user_id` is a required argument on purpose: a
default would let a caller forget it and quietly share replies again.
"""

import json
import math
from datetime import datetime

from sqlite_utils import Database


def _jurisdiction_for(lat: float | None, lng: float | None) -> str | None:
    if lat is None or lng is None:
        return None
    from src.jurisdictions.geo import jurisdiction_for_coords

    jur = jurisdiction_for_coords(lat, lng)
    return jur if jur and jur != "UNKNOWN" else None


def _compatible(a: str | None, b: str | None) -> bool:
    """Two jurisdictions are compatible unless both are known and differ."""
    return a is None or b is None or a == b


def _location_key(lat: float | None, lng: float | None, location_name: str | None) -> str:
    """Location part of the key. Round coords to ~100m grid so nearby queries hit."""
    if lat is not None and lng is not None:
        return f"geo:{round(lat, 3)},{round(lng, 3)}"
    if location_name:
        return f"name:{location_name.strip().lower()}"
    return "unknown"


def _cache_key(
    lat: float | None, lng: float | None, location_name: str | None, user_id: int
) -> str:
    """Per-user cache key: the user is part of the key, so two users never share a row."""
    return f"u{user_id}:{_location_key(lat, lng, location_name)}"


def get_cached_synthesis(
    db: Database,
    *,
    user_id: int,
    lat: float | None = None,
    lng: float | None = None,
    location_name: str | None = None,
    radius_km: float = 0.15,
    jurisdiction: str | None = None,
) -> dict | None:
    """
    Check the cache for an existing synthesis near this location.
    Returns the cached synthesis dict, or None on miss.
    """
    key = _cache_key(lat, lng, location_name, user_id)
    jur = jurisdiction or _jurisdiction_for(lat, lng)

    # rows_where returns dicts automatically
    try:
        row = next(
            db["segment_synthesis"].rows_where("cache_key = ? AND user_id = ?", [key, user_id]),
            None,
        )
    except Exception:
        row = None

    if row and _compatible(jur, row.get("jurisdiction")):
        db.execute(
            "UPDATE segment_synthesis SET hit_count = hit_count + 1 WHERE cache_key = ?",
            [key],
        )
        db.conn.commit()
        return {
            "synthesis": row["synthesis"],
            "location_name": row.get("location_name"),
            "computed_at": row.get("computed_at"),
            "cache_hit": True,
        }

    if lat is not None and lng is not None:
        try:
            candidates = list(
                db["segment_synthesis"].rows_where(
                    "lat IS NOT NULL AND lng IS NOT NULL AND user_id = ?", [user_id]
                )
            )
        except Exception:
            candidates = []
        for c in candidates:
            if not _compatible(jur, c.get("jurisdiction")):
                continue
            d = _haversine_km(lat, lng, c["lat"], c["lng"])
            if d <= radius_km:
                db.execute(
                    "UPDATE segment_synthesis SET hit_count = hit_count + 1 WHERE id = ?",
                    [c["id"]],
                )
                db.conn.commit()
                return {
                    "synthesis": c["synthesis"],
                    "location_name": c.get("location_name"),
                    "computed_at": c.get("computed_at"),
                    "cache_hit": True,
                }

    # Fuzzy name match: one name is a subset of words of the other.
    # This is the riskiest path (see module docstring) — jurisdiction
    # compatibility is required, not just a nice-to-have here.
    if location_name:
        try:
            all_entries = list(
                db["segment_synthesis"].rows_where(
                    "location_name IS NOT NULL AND user_id = ?", [user_id]
                )
            )
        except Exception:
            all_entries = []
        query_words = set(location_name.strip().lower().split())
        for c in all_entries:
            if not _compatible(jur, c.get("jurisdiction")):
                continue
            c_words = set((c["location_name"] or "").strip().lower().split())
            if query_words and c_words and (query_words <= c_words or c_words <= query_words):
                db.execute(
                    "UPDATE segment_synthesis SET hit_count = hit_count + 1 WHERE id = ?",
                    [c["id"]],
                )
                db.conn.commit()
                return {
                    "synthesis": c["synthesis"],
                    "location_name": c.get("location_name"),
                    "computed_at": c.get("computed_at"),
                    "cache_hit": True,
                }

    return None


def store_synthesis(
    db: Database,
    synthesis: str,
    *,
    user_id: int,
    lat: float | None = None,
    lng: float | None = None,
    location_name: str | None = None,
    data_sources: list[str] | None = None,
    jurisdiction: str | None = None,
) -> None:
    """Store a freshly computed synthesis in the cache."""
    key = _cache_key(lat, lng, location_name, user_id)
    jur = jurisdiction or _jurisdiction_for(lat, lng)
    # Raw SQL avoids sqlite-utils upsert quirks with AUTOINCREMENT primary keys
    db.execute(
        """
        INSERT OR REPLACE INTO segment_synthesis
            (cache_key, user_id, lat, lng, location_name, jurisdiction, synthesis,
             data_sources, computed_at, hit_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        [
            key,
            user_id,
            lat,
            lng,
            location_name,
            jur,
            synthesis,
            json.dumps(data_sources or []),
            datetime.now().isoformat(),
        ],
    )
    db.conn.commit()


def invalidate_cache(
    db: Database,
    lat: float | None = None,
    lng: float | None = None,
    location_name: str | None = None,
    radius_km: float = 0.15,
) -> int:
    """Delete cache entries for a location when insights are updated. Returns number deleted."""
    deleted = 0
    key = _location_key(lat, lng, location_name)

    # Exact key match. Keys are "u<user>:<location key>"; new facts about a
    # place make every user's cached reply for it stale, so this covers all users.
    try:
        result = db.execute(
            "DELETE FROM segment_synthesis WHERE cache_key = ? OR cache_key LIKE ?",
            [key, f"u%:{key}"],
        )
        deleted += result.rowcount
    except Exception:
        pass

    # Fuzzy name match
    if location_name:
        try:
            words = [w.lower() for w in location_name.split() if len(w) > 3]
            for word in words:
                result = db.execute(
                    "DELETE FROM segment_synthesis WHERE LOWER(location_name) LIKE ?", [f"%{word}%"]
                )
                deleted += result.rowcount
            db.conn.commit()
        except Exception:
            pass

    # Proximity match
    if lat is not None and lng is not None:
        try:
            candidates = list(
                db["segment_synthesis"].rows_where("lat IS NOT NULL AND lng IS NOT NULL")
            )
            for c in candidates:
                if _haversine_km(lat, lng, c["lat"], c["lng"]) <= radius_km:
                    db.execute("DELETE FROM segment_synthesis WHERE id = ?", [c["id"]])
                    deleted += 1
            db.conn.commit()
        except Exception:
            pass

    if deleted:
        where = location_name or lat
        print(f"[CACHE] Invalidated {deleted} synthesis cache entry/entries for {where}")
    return deleted


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))
