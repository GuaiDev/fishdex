"""Admin dashboard data — usage, tool activity, ingest coverage, catch-level
stats, and a rough Sonnet-vs-Haiku API cost estimate.

Served behind GET /admin/dashboard (X-Api-Key protected, see src/api/main.py).
Pure read-only aggregation over existing tables — no new schema.
"""

from typing import Any

from sqlite_utils.db import Database

_DAY_WINDOWS = (7, 30)

# ~1 decimal degree grid cell for the "location cluster" grouping — coarser
# than the synthesis cache's own ~100m cache-key rounding (3 decimals), since
# clustering many nearby cache entries into one "place" is more useful for a
# dashboard than showing every ~100m grid cell as its own row.
_CLUSTER_DECIMALS = 1


def build_dashboard(db: Database) -> dict[str, Any]:
    return {
        "users": _user_stats(db),
        "top_locations": _top_locations(db),
        "tool_usage": _tool_usage(db),
        "ingest_status": _ingest_status(db),
        "api_cost_estimate": _api_cost_estimate(db),
        "catch_stats": _catch_stats(db),
    }


def _user_stats(db: Database) -> dict[str, Any]:
    total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    messages_by_window: dict[str, int] = {}
    for days in _DAY_WINDOWS:
        row = db.execute(
            "SELECT COUNT(*) FROM chat_messages "
            "WHERE role = 'user' AND created_at >= datetime('now', ?)",
            [f"-{days} days"],
        ).fetchone()
        messages_by_window[f"last_{days}_days"] = row[0]

    total_messages = db.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE role = 'user'"
    ).fetchone()[0]

    return {
        "total_users": total_users,
        "messages_sent": {**messages_by_window, "all_time": total_messages},
    }


def _top_locations(db: Database, limit: int = 10) -> list[dict[str, Any]]:
    """Top queried locations from the synthesis cache, ranked by total
    hit_count (cache hits + the initial compute).

    segment_synthesis is the only table in this codebase keyed on "what
    location did a user ask about" (see src/services/synthesis_cache.py) —
    trip/catch logs record where the user actually fished, which is a
    related but different signal from what they asked the bot about.

    Rows with coordinates are clustered by rounded lat/lng. Rows without
    (extract_location_from_message returns a bare name when the user gives
    no coordinates — see src/agent/router.py) are grouped by lowercased
    location_name instead and would be silently dropped by a coordinate-only
    grouping. Checked against this project's real usage: every entry in the
    synthesis cache today is name-only, so a lat/lng-only version of this
    function would always return an empty list in practice.
    """
    if "segment_synthesis" not in db.table_names():
        return []

    rows = db.execute("SELECT lat, lng, location_name, hit_count FROM segment_synthesis").fetchall()

    clusters: dict[tuple, dict[str, Any]] = {}
    for lat, lng, location_name, hit_count in rows:
        queries = (hit_count or 0) + 1  # +1 for the initial compute, not just re-hits
        if lat is not None and lng is not None:
            key = ("geo", round(lat, _CLUSTER_DECIMALS), round(lng, _CLUSTER_DECIMALS))
        elif location_name:
            key = ("name", location_name.strip().lower())
        else:
            continue

        entry = clusters.setdefault(
            key,
            {
                "lat": round(lat, _CLUSTER_DECIMALS) if lat is not None else None,
                "lng": round(lng, _CLUSTER_DECIMALS) if lng is not None else None,
                "query_count": 0,
                "distinct_cache_entries": 0,
                "sample_location_name": location_name,
            },
        )
        entry["query_count"] += queries
        entry["distinct_cache_entries"] += 1
        if location_name and not entry["sample_location_name"]:
            entry["sample_location_name"] = location_name

    ranked = sorted(clusters.values(), key=lambda e: e["query_count"], reverse=True)
    return ranked[:limit]


def _tool_usage(db: Database) -> list[dict[str, Any]]:
    if "tool_usage" not in db.table_names():
        return []
    rows = db.execute(
        "SELECT tool_name, COUNT(*) AS calls, "
        "SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failures "
        "FROM tool_usage GROUP BY tool_name ORDER BY calls DESC"
    ).fetchall()
    return [{"tool_name": r[0], "calls": r[1], "failures": r[2]} for r in rows]


def _ingest_status(db: Database) -> dict[str, Any]:
    """Combine recent run diagnostics with observation coverage.

    Coverage counts include only observations and gbif_observations; the run
    ledger covers additional sources without scanning their stored datasets.
    """
    status: dict[str, Any] = {"observations": [], "gbif_observations": [], "runs": []}

    if "ingest_runs" in db.table_names():
        rows = db.execute(
            "SELECT id, source, jurisdiction, area, started_at, finished_at, fetched, stored, "
            "skipped, empty_count, empty_share, status, error FROM ingest_runs "
            "ORDER BY id DESC LIMIT 100"
        ).fetchall()
        status["runs"] = [
            {
                "id": r[0],
                "source": r[1],
                "jurisdiction": r[2],
                "area": r[3],
                "started_at": r[4],
                "finished_at": r[5],
                "fetched": r[6],
                "stored": r[7],
                "skipped": r[8],
                "empty_count": r[9],
                "empty_share": r[10],
                "status": r[11],
                "error": r[12],
            }
            for r in rows
        ]

    if "observations" in db.table_names():
        rows = db.execute(
            "SELECT source, jurisdiction, COUNT(*) FROM observations "
            "GROUP BY source, jurisdiction ORDER BY COUNT(*) DESC"
        ).fetchall()
        status["observations"] = [
            {"source": r[0], "jurisdiction": r[1], "count": r[2]} for r in rows
        ]

    if "gbif_observations" in db.table_names():
        # gbif_observations has no source column — every row is GBIF by definition.
        rows = db.execute(
            "SELECT jurisdiction, COUNT(*) FROM gbif_observations "
            "GROUP BY jurisdiction ORDER BY COUNT(*) DESC"
        ).fetchall()
        status["gbif_observations"] = [
            {"source": "GBIF", "jurisdiction": r[0], "count": r[1]} for r in rows
        ]

    return status


def _catch_stats(db: Database, species_limit: int = 10, pb_limit: int = 10) -> dict[str, Any]:
    """Catch-level visibility (all users): volume, species mix, personal
    bests, and catches-per-session — this was previously only checkable by
    SSHing into the Railway container and querying the SQLite file directly.

    species counts every logged catch (species_confirmed or not — "logged"
    is about volume of activity, not whether the user has since confirmed
    the AI-suggested species). personal_bests only exists once the
    multi-catch logging UI's structured size field has been used at least
    once for a species — see trip_logger._maybe_update_personal_best.

    Both totals sum `count` (defaulting to 1 for rows the pre-multi-catch
    NL-only path never set) rather than counting rows: a single catch card
    logged as "3 shorthead redhorse" is one row but three fish, and a naive
    COUNT(*) silently reported it as one, making a real catch invisible next
    to older single-fish rows. Grouped case-insensitively for the same
    reason personal_bests already normalizes its key (.strip().lower()) —
    "Shorthead Redhorse" (typed directly, no NL match) and "shorthead
    redhorse" (from the NL parser) are the same species and must not split
    into two rows here.
    """
    if "catches" not in db.table_names():
        return {
            "total_catches": 0,
            "by_species": [],
            "personal_bests": {"users_with_a_pb": 0, "top_10_largest": []},
            "avg_catches_per_session": 0.0,
        }

    total_catches = db.execute("SELECT SUM(COALESCE(count, 1)) FROM catches").fetchone()[0] or 0

    by_species = db.execute(
        "SELECT LOWER(TRIM(species)) AS species_key, SUM(COALESCE(count, 1)) AS n "
        "FROM catches GROUP BY species_key ORDER BY n DESC LIMIT ?",
        [species_limit],
    ).fetchall()

    users_with_a_pb = 0
    top_largest: list[dict[str, Any]] = []
    if "personal_bests" in db.table_names():
        users_with_a_pb = db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM personal_bests"
        ).fetchone()[0]
        rows = db.execute(
            "SELECT pb.species, pb.best_size_cm, u.username "
            "FROM personal_bests pb LEFT JOIN users u ON u.id = pb.user_id "
            "ORDER BY pb.best_size_cm DESC LIMIT ?",
            [pb_limit],
        ).fetchall()
        top_largest = [{"species": r[0], "biggest_size_cm": r[1], "username": r[2]} for r in rows]

    total_sessions = (
        db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        if "sessions" in db.table_names()
        else 0
    )
    avg_catches_per_session = round(total_catches / total_sessions, 2) if total_sessions else 0.0

    return {
        "total_catches": total_catches,
        "by_species": [{"species": r[0], "count": r[1]} for r in by_species],
        "personal_bests": {
            "users_with_a_pb": users_with_a_pb,
            "top_10_largest": top_largest,
        },
        "avg_catches_per_session": avg_catches_per_session,
    }


def _api_cost_estimate(db: Database) -> dict[str, Any]:
    """Model spend by model, all-time and last 30 days, read from the `llm_usage` ledger.

    Every model call writes one ledger row with its own estimated cost (see
    src/llm/pricing.py), so this sums what was recorded rather than re-pricing.
    A row with no known rate has a NULL cost and is counted as unpriced, never
    as free. Estimate only — Anthropic's usage dashboard is the source of truth.
    """
    if "llm_usage" not in db.table_names():
        return {"note": "llm_usage table not present", "by_model": [], "total_usd": 0.0}

    select = (
        "SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), "
        "SUM(est_usd), SUM(est_usd IS NULL), SUM(web_searches) FROM llm_usage"
    )
    windows = {
        "all_time": f"{select} GROUP BY model",
        "last_30_days": f"{select} WHERE ts >= datetime('now', '-30 days') GROUP BY model",
    }

    result: dict[str, Any] = {}
    for window_name, sql in windows.items():
        by_model = []
        total_usd = 0.0
        unpriced_calls = 0
        for model, calls, inp, out, usd, unpriced, searches in db.execute(sql).fetchall():
            unpriced_calls += unpriced or 0
            total_usd += usd or 0.0
            by_model.append(
                {
                    "model": model,
                    "calls": calls,
                    "input_tokens": inp or 0,
                    "output_tokens": out or 0,
                    "web_searches": searches or 0,
                    "estimated_usd": None if unpriced == calls else round(usd or 0.0, 4),
                }
            )
        result[window_name] = {
            "by_model": sorted(by_model, key=lambda m: m["calls"], reverse=True),
            "total_usd": round(total_usd, 4),
            "unpriced_calls": unpriced_calls,
        }

    result["by_caller_30_days"] = [
        {
            "caller": c,
            "calls": n,
            "estimated_usd": None if u is None else round(u, 4),
            "unpriced_calls": unpriced,
        }
        for c, n, u, unpriced in db.execute(
            "SELECT caller, COUNT(*), SUM(est_usd), COUNT(*) - COUNT(est_usd) FROM llm_usage "
            "WHERE ts >= datetime('now', '-30 days') GROUP BY caller ORDER BY SUM(est_usd) DESC"
        ).fetchall()
    ]
    result["by_user_30_days"] = [
        {
            "user_id": uid,
            "calls": n,
            "estimated_usd": None if u is None else round(u, 4),
            "unpriced_calls": unpriced,
        }
        for uid, n, u, unpriced in db.execute(
            "SELECT user_id, COUNT(*), SUM(est_usd), COUNT(*) - COUNT(est_usd) FROM llm_usage "
            "WHERE ts >= datetime('now', '-30 days') GROUP BY user_id ORDER BY SUM(est_usd) DESC"
        ).fetchall()
    ]
    result["pricing_basis"] = {
        "note": "Estimate only, not a bill. Priced per call by src/llm/pricing.py at write time.",
    }
    return result
