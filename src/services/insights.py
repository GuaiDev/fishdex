"""Agent-facing service for behavioral insights."""

import json
from pathlib import Path

from src.models.behavioral_insight import BehavioralInsight
from src.storage.database import get_db
from src.storage.insights import insert_insight, query_insights

_DISPERSAL_SEED_PATH = Path("data/processed/dispersal_insights.json")


def get_behavioral_insights_for_agent(
    species: str,
    condition_type: str | None = None,
    user_id: int = 1,
) -> str:
    db = get_db()
    insights = query_insights(
        db, species=species, condition_type=condition_type, current_only=True, user_id=user_id
    )

    if not insights:
        msg = f"No behavioral insights stored for '{species}'"
        if condition_type:
            msg += f" with condition_type='{condition_type}'"
        return json.dumps(
            {"species": species, "condition_type": condition_type, "count": 0, "note": msg}
        )

    return json.dumps(
        {
            "species": species,
            "condition_type": condition_type,
            "count": len(insights),
            "insights": [
                {
                    "id": i.id,
                    "condition_type": i.condition_type,
                    "condition_context": i.condition_context,
                    "conclusion": i.conclusion,
                    "confidence": i.confidence,
                    "source_type": i.source_type,
                    "evidence_count": i.evidence_count,
                    "user_verified": i.user_verified,
                    "jurisdiction": i.jurisdiction,
                    "last_validated": i.last_validated.isoformat(),
                }
                for i in insights
            ],
        }
    )


def seed_dispersal_insights() -> int:
    """Load waterfowl-dispersal behavioral insights from seed file; skip duplicates.

    Returns the number of newly inserted records (0 if already seeded).
    """
    if not _DISPERSAL_SEED_PATH.exists():
        return 0
    raw = json.loads(_DISPERSAL_SEED_PATH.read_text(encoding="utf-8"))
    db = get_db()
    count = 0
    for entry in raw:
        existing = list(
            db["behavioral_insights"].rows_where(
                "user_id = 1 AND LOWER(species) = ? AND condition_context = ? AND is_current = 1",
                [entry["species"].lower(), entry["condition_context"]],
            )
        )
        if not existing:
            insight = BehavioralInsight(**entry)
            insert_insight(db, insight)
            count += 1
    return count


def record_behavioral_insight_for_agent(
    species: str,
    condition_type: str,
    condition_context: str,
    conclusion: str,
    confidence: str,
    source_type: str,
    source_detail: str,
    evidence_count: int,
    jurisdiction: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    recommendation: str | None = None,
    condition_season: str | None = None,
    location_name: str | None = None,
    user_id: int = 1,
) -> str:
    if confidence == "unverified":
        return json.dumps(
            {
                "error": (
                    "Cannot store unverified conclusions. "
                    "Set confidence to 'low', 'medium', or 'high' with supporting evidence."
                )
            }
        )

    insight = BehavioralInsight(
        species=species,
        condition_type=condition_type,  # type: ignore[arg-type]
        condition_context=condition_context,
        conclusion=conclusion,
        confidence=confidence,  # type: ignore[arg-type]
        source_type=source_type,  # type: ignore[arg-type]
        source_detail=source_detail,
        evidence_count=evidence_count,
        jurisdiction=jurisdiction,
        lat=lat,
        lng=lng,
        recommendation=recommendation,
        condition_season=condition_season,
        location_name=location_name,
        user_id=user_id,
    )

    db = get_db()

    # Check for conflicting current insights at same location/season
    from src.storage.insights import check_conflicts, refine_insight
    conflicts = check_conflicts(
        db, species,
        lat=lat, lng=lng,
        condition_season=condition_season,
        radius_km=1.0,
        user_id=user_id,
    )

    # Auto-refine if new insight directly contradicts an existing one
    # Only refine if the new insight has equal or higher confidence
    CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
    new_rank = CONFIDENCE_RANK.get(confidence, 0)

    refined_ids = []
    for conflict in conflicts:
        # Only version out if:
        # 1. Same condition_type (temporal vs temporal, not temporal vs habitat)
        # 2. New insight is explicitly contradicting (check for keywords in conclusion)
        # 3. New confidence >= existing confidence (don't downgrade with weak data)
        contradiction_keywords = [
            "contradict", "contrary", "reversal", "opposite", "unlike prior",
            "completely", "overturns", "not the case", "wrong", "incorrect",
            "not just", "also viable", "not only"
        ]
        is_contradiction = any(kw in conclusion.lower() for kw in contradiction_keywords)
        same_type = conflict.condition_type == condition_type
        confidence_ok = new_rank >= CONFIDENCE_RANK.get(conflict.confidence, 0)

        if is_contradiction and same_type and confidence_ok:
            # refine_insight inserts the new row and returns its id; a global
            # MAX(id) here would pick up another user's insert made in between.
            insight_id = refine_insight(db, conflict.id, insight)
            refined_ids.append(conflict.id)
            if lat is not None or location_name is not None:
                try:
                    from src.services.synthesis_cache import invalidate_cache
                    invalidate_cache(db, lat=lat, lng=lng, location_name=location_name)
                except Exception:
                    pass
            return json.dumps({
                "success": True,
                "insight_id": insight_id,
                "species": species,
                "condition_type": condition_type,
                "conclusion": conclusion,
                "confidence": confidence,
                "refined_prior_insights": refined_ids,
                "note": f"Versioned out {len(refined_ids)} conflicting insight(s)"
            })

    # No contradiction found — standard insert
    insight_id = insert_insight(db, insight)
    if lat is not None or location_name is not None:
        try:
            from src.services.synthesis_cache import invalidate_cache
            invalidate_cache(db, lat=lat, lng=lng, location_name=location_name)
        except Exception:
            pass
    return json.dumps(
        {
            "success": True,
            "insight_id": insight_id,
            "species": species,
            "condition_type": condition_type,
            "conclusion": conclusion,
            "confidence": confidence,
        }
    )


def check_conflicts_for_agent_service(
    species: str,
    lat: float | None = None,
    lng: float | None = None,
    condition_season: str | None = None,
    user_id: int = 1,
) -> str:
    from src.storage.insights import check_conflicts_for_agent

    db = get_db()
    return check_conflicts_for_agent(
        db, species, lat, lng, condition_season, user_id=user_id
    )
