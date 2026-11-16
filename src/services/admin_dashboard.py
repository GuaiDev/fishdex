"""Admin dashboard data — usage, tool activity, ingest coverage, catch-level
stats, and a rough Sonnet-vs-Haiku API cost estimate.

Served behind GET /admin/dashboard (X-Api-Key protected, see src/api/main.py).
Pure read-only aggregation over existing tables — no new schema.
"""

from typing import Any

from sqlite_utils.db import Database

# $/1M tokens (input, output). Matched by substring against api_usage.model,
# since the exact model string changes across releases (e.g.
# "claude-haiku-4-5-20251001", "claude-sonnet-4-6") but always contains the
# tier name. Verified against the Claude API pricing table as of 2026-07:
# Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5, Opus (any) $5/$25 per 1M tokens.
# This is an ESTIMATE for budgeting, not a bill — Anthropic's own usage
# dashboard is the source of truth.
_PRICING_PER_MILLION: dict[str, tuple[float, float]] = {
    "haiku": (1.00, 5.00),
    "sonnet": (3.00, 15.00),
    "opus": (5.00, 25.00),
}

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


def _rate_for_model(model: str) -> tuple[float, float] | None:
    name = (model or "").lower()
    for tier, rates in _PRICING_PER_MILLION.items():
        if tier in name:
            return rates
    return None


def _api_cost_estimate(db: Database) -> dict[str, Any]:
    """Approximate cost by model tier (Sonnet vs Haiku vs Opus), all-time and
    last 30 days. Estimate only — see module docstring on _PRICING_PER_MILLION.
    """
    if "api_usage" not in db.table_names():
        return {"note": "api_usage table not present", "by_model": [], "total_usd": 0.0}

    windows = {
        "all_time": "SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens) "
        "FROM api_usage GROUP BY model",
        "last_30_days": "SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens) "
        "FROM api_usage WHERE timestamp >= datetime('now', '-30 days') GROUP BY model",
    }

    result: dict[str, Any] = {}
    for window_name, sql in windows.items():
        by_model = []
        total_usd = 0.0
        unpriced_calls = 0
        for model, calls, input_tokens, output_tokens in db.execute(sql).fetchall():
            input_tokens = input_tokens or 0
            output_tokens = output_tokens or 0
            rates = _rate_for_model(model)
            if rates is None:
                unpriced_calls += calls
                by_model.append(
                    {
                        "model": model,
                        "calls": calls,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "estimated_usd": None,
                    }
                )
                continue
            in_rate, out_rate = rates
            cost = (input_tokens / 1_000_000 * in_rate) + (output_tokens / 1_000_000 * out_rate)
            total_usd += cost
            by_model.append(
                {
                    "model": model,
                    "calls": calls,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "estimated_usd": round(cost, 4),
                }
            )
        result[window_name] = {
            "by_model": sorted(by_model, key=lambda m: m["calls"], reverse=True),
            "total_usd": round(total_usd, 4),
            "unpriced_calls": unpriced_calls,
        }

    result["pricing_basis"] = {
        "note": (
            "Estimate only, not a bill. $/1M tokens (input, output), matched by "
            "substring against the model name."
        ),
        "rates": {
            tier: {"input_per_million": r[0], "output_per_million": r[1]}
            for tier, r in _PRICING_PER_MILLION.items()
        },
    }
    return result
