"""Behavioral insights CRUD via sqlite-utils."""

import math
from datetime import datetime
from typing import Any

from sqlite_utils.db import Database

from src.models.behavioral_insight import BehavioralInsight


def insert_insight(db: Database, insight: BehavioralInsight) -> int:
    row = _to_row(insight)
    row.pop("id", None)
    return db["behavioral_insights"].insert(row).last_pk  # type: ignore[return-value]


def refine_insight(db: Database, prior_id: int, new_insight: BehavioralInsight) -> int:
    """Version out `prior_id` and store `new_insight` as its successor.

    An insight can only be refined by its own owner: the successor must carry
    the same user_id as the row it replaces, or one angler's write would
    retire another's conclusions.
    """
    prior = get_insight(db, prior_id)
    if prior is None:
        raise ValueError(f"No insight with id={prior_id}")
    if new_insight.user_id != prior.user_id:
        raise ValueError(
            f"Insight {prior_id} belongs to user {prior.user_id}; "
            f"user {new_insight.user_id} cannot refine it"
        )
    db["behavioral_insights"].update(prior_id, {"is_current": 0})
    new_insight.version = prior.version + 1
    new_insight.is_current = True
    new_id = insert_insight(db, new_insight)
    contradict_insight(db, old_id=prior_id, new_id=new_id)
    return new_id


def get_insight(db: Database, insight_id: int) -> BehavioralInsight | None:
    rows = list(db["behavioral_insights"].rows_where("id = ?", [insight_id]))
    if not rows:
        return None
    return _row_to_insight(rows[0])


def query_insights(
    db: Database,
    species: str,
    condition_type: str | None = None,
    current_only: bool = True,
    *,
    user_id: int,
) -> list[BehavioralInsight]:
    # behavioral_insights is a per-user table (see migrate_user_fields). Without
    # this filter one angler's stored conclusions surface in another's conflict
    # checks — the same leak that was found in coaching.py.
    where = "user_id = ? AND LOWER(species) LIKE ?"
    params: list[Any] = [user_id, f"%{species.lower()}%"]

    if condition_type:
        where += " AND condition_type = ?"
        params.append(condition_type)

    if current_only:
        where += " AND is_current = 1"

    rows = db["behavioral_insights"].rows_where(where, params, order_by="created_at desc")
    return [_row_to_insight(r) for r in rows]


_SHARED_COLUMNS = (
    "species", "condition_type", "condition_context", "conclusion", "confidence",
    "source_type", "source_detail", "evidence_count", "jurisdiction", "lat", "lng",
    "recommendation", "condition_season", "location_name", "last_validated", "created_at",
)  # fmt: skip


def insert_shared_insight(db: Database, insight: BehavioralInsight) -> int | None:
    """Store a seeded insight everyone sees. Returns None if it is already there.

    Shared insights have no owner: they come from a seed file, not from anyone's log,
    so they never enter a user's own insights or the user layer derived from them.
    """
    row = _to_row(insight)
    existing = db["shared_insights"].rows_where(
        "LOWER(species) = ? AND condition_context = ?",
        [insight.species.lower(), insight.condition_context],
    )
    if next(existing, None) is not None:
        return None
    return db["shared_insights"].insert({k: row[k] for k in _SHARED_COLUMNS}).last_pk  # type: ignore[return-value]


def query_shared_insights(
    db: Database,
    species: str,
    condition_type: str | None = None,
    *,
    owned_by: int | None = None,
) -> list[BehavioralInsight]:
    """Seeded insights matching a species, newest first.

    With `owned_by`, a shared insight the user already holds as their own current
    insight (same species and context, e.g. the copy an older seed run wrote under
    user 1) is left out so it is not listed twice.
    """
    if "shared_insights" not in db.table_names():
        return []
    where = "LOWER(species) LIKE ?"
    params: list[Any] = [f"%{species.lower()}%"]
    if condition_type:
        where += " AND condition_type = ?"
        params.append(condition_type)
    out = []
    for r in db["shared_insights"].rows_where(where, params, order_by="created_at desc"):
        if owned_by is not None and next(
            db["behavioral_insights"].rows_where(
                "user_id = ? AND LOWER(species) = ? AND condition_context = ? AND is_current = 1",
                [owned_by, r["species"].lower(), r["condition_context"]],
            ),
            None,
        ):
            continue
        out.append(_row_to_insight({**r, "user_id": 0, "is_current": 1, "user_verified": 0}))
    return out


def mark_user_verified(db: Database, insight_id: int) -> None:
    db["behavioral_insights"].update(insight_id, {"user_verified": 1})


def contradict_insight(db: Database, old_id: int, new_id: int) -> None:
    db["behavioral_insights"].update(old_id, {"contradicted_by": new_id})


def _to_row(insight: BehavioralInsight) -> dict[str, Any]:
    return {
        "id": insight.id,
        "user_id": insight.user_id,
        "species": insight.species,
        "condition_type": insight.condition_type,
        "condition_context": insight.condition_context,
        "conclusion": insight.conclusion,
        "confidence": insight.confidence,
        "source_type": insight.source_type,
        "source_detail": insight.source_detail,
        "evidence_count": insight.evidence_count,
        "version": insight.version,
        "is_current": 1 if insight.is_current else 0,
        "contradicted_by": insight.contradicted_by,
        "user_verified": 1 if insight.user_verified else 0,
        "jurisdiction": insight.jurisdiction,
        "lat": insight.lat,
        "lng": insight.lng,
        "recommendation": insight.recommendation,
        "condition_season": insight.condition_season,
        "location_name": insight.location_name,
        "last_validated": insight.last_validated.isoformat(),
        "created_at": insight.created_at.isoformat(),
    }


def _row_to_insight(row: dict[str, Any]) -> BehavioralInsight:
    d = dict(row)
    d["is_current"] = bool(d["is_current"])
    d["user_verified"] = bool(d["user_verified"])
    d["last_validated"] = datetime.fromisoformat(d["last_validated"])
    d["created_at"] = datetime.fromisoformat(d["created_at"])
    return BehavioralInsight.model_validate(d)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Approximate distance in km between two lat/lng points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def check_conflicts(
    db: Database,
    species: str,
    lat: float | None = None,
    lng: float | None = None,
    condition_season: str | None = None,
    radius_km: float = 1.0,
    *,
    user_id: int,
) -> list[BehavioralInsight]:
    """Find existing current insights that might conflict with a new recommendation.

    Matches on species + proximity (if lat/lng provided) + season (if provided).
    Scoped to one angler: a conflict against someone else's insight is noise.
    """
    candidates = query_insights(db, species=species, current_only=True, user_id=user_id)
    if not candidates:
        return []

    conflicts = []
    for insight in candidates:
        if lat is not None and lng is not None:
            if insight.lat is not None and insight.lng is not None:
                dist = _haversine_km(lat, lng, insight.lat, insight.lng)
                if dist > radius_km:
                    continue
            # No location on stored insight → treat as general, always include

        if condition_season and insight.condition_season:
            if (insight.condition_season != condition_season
                    and insight.condition_season != "any"
                    and condition_season != "any"):
                continue

        conflicts.append(insight)

    return conflicts


def check_conflicts_for_agent(
    db: Database,
    species: str,
    lat: float | None = None,
    lng: float | None = None,
    condition_season: str | None = None,
    *,
    user_id: int,
) -> str:
    """Agent-facing conflict check. Returns JSON describing any conflicts found."""
    import json

    conflicts = check_conflicts(db, species, lat, lng, condition_season, user_id=user_id)
    if not conflicts:
        return json.dumps({"conflicts": [], "safe_to_recommend": True})

    return json.dumps({
        "conflicts": [
            {
                "id": c.id,
                "condition_type": c.condition_type,
                "condition_context": c.condition_context,
                "recommendation": c.recommendation,
                "conclusion": c.conclusion[:300],
                "confidence": c.confidence,
                "user_verified": c.user_verified,
                "location_name": c.location_name,
                "condition_season": c.condition_season,
            }
            for c in conflicts
        ],
        "safe_to_recommend": False,
        "note": (
            f"Found {len(conflicts)} existing insight(s) for {species} "
            "at or near this location/season. Review before making new recommendations."
        ),
    })
