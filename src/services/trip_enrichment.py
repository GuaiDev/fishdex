"""
Trip enrichment service.
After a session is logged, matches stops against behavioral insights,
identifies condition gaps, and prepares follow-up questions.
"""
import json
from datetime import datetime

from sqlite_utils import Database

from src.models.behavioral_insight import BehavioralInsight
from src.storage.insights import (
    _haversine_km,
    _row_to_insight,
    check_conflicts,
    get_insight,
    refine_insight,
)

# How many confirmations needed to escalate confidence
CONFIRMATIONS_TO_ESCALATE = {
    "low": 2,     # 2 confirmations → medium
    "medium": 4,  # 4 confirmations → high
    "high": 999,  # already at max
}

# How many contradictions to trigger refinement
CONTRADICTIONS_TO_REFINE = 2


def get_season(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "fall"


def match_insights_to_stop(db: Database, stop: dict, user_id: int = 1) -> list:
    """Find behavioral insights that match this stop.

    Productive stops: match by species + location proximity + season.
    Blank stops with coordinates: match by location only to flag contradictions.
    Returns list of matching insight dicts.
    """
    species_list = json.loads(stop.get("species_caught") or "[]")
    lat = stop.get("lat")
    lng = stop.get("lng")
    was_productive = bool(stop.get("was_productive"))

    session_id = stop.get("session_id")
    season = None
    if session_id is not None:
        try:
            session_rows = list(db["sessions"].rows_where("id = ?", [session_id]))
            if session_rows:
                date_str = session_rows[0].get("date")
                if date_str:
                    month = datetime.fromisoformat(date_str).month
                    season = get_season(month)
        except Exception:
            pass

    matched = []

    if species_list:
        for species in species_list:
            clean_species = species.replace("(uncertain)", "").strip()
            conflicts = check_conflicts(
                db, clean_species,
                lat=lat, lng=lng,
                condition_season=season,
                radius_km=1.0,
                user_id=user_id,
            )
            for insight in conflicts:
                matched.append({
                    "insight": insight,
                    "species": clean_species,
                    "season": season,
                })
    elif not was_productive and lat is not None and lng is not None:
        # Blank stop with known coords — check for location-based contradictions
        all_insights = list(db["behavioral_insights"].rows_where(
            "user_id = ? AND is_current = 1 AND lat IS NOT NULL AND lng IS NOT NULL",
            [user_id],
        ))
        for row in all_insights:
            if _haversine_km(lat, lng, row["lat"], row["lng"]) <= 1.0:
                insight = _row_to_insight(row)
                matched.append({
                    "insight": insight,
                    "species": insight.species,
                    "season": season,
                })

    return matched


def identify_condition_gaps(stop: dict, insight: BehavioralInsight) -> list[str]:
    """Compare what we know about this stop against what the insight cares about.

    Returns a list of condition fields we're missing that would be useful.
    """
    gaps = []
    conclusion_lower = (insight.conclusion or "").lower()

    weather_keywords = ["rain", "overcast", "sunny", "cloud", "storm", "front", "pressure"]
    if any(kw in conclusion_lower for kw in weather_keywords):
        if not stop.get("weather_notes"):
            gaps.append("weather")

    technique_keywords = ["cut bait", "worm", "corn", "lure", "jig", "rig", "packbait"]
    if any(kw in conclusion_lower for kw in technique_keywords):
        if not stop.get("technique") and not stop.get("gear"):
            gaps.append("technique")

    water_keywords = ["high water", "low water", "clear", "turbid", "flood", "flow"]
    if any(kw in conclusion_lower for kw in water_keywords):
        if not stop.get("water_level") and not stop.get("water_clarity"):
            gaps.append("water_conditions")

    time_keywords = ["dawn", "dusk", "midday", "morning", "evening", "night"]
    if any(kw in conclusion_lower for kw in time_keywords):
        notes_lower = (stop.get("notes") or "").lower()
        if not any(kw in notes_lower for kw in time_keywords):
            gaps.append("time_of_day")

    return gaps


def build_followup_question(
    stop: dict,
    insight: BehavioralInsight,
    gaps: list[str],
    was_productive: bool,
) -> str | None:
    """Build one focused follow-up question based on the most important gap.

    Returns None if no question is needed.
    """
    if not gaps:
        return None

    location = stop.get("location_name") or stop.get("location_text") or "that spot"
    species = insight.species

    if "weather" in gaps:
        if was_productive:
            return (
                f"You caught {species} at {location} — what were conditions like? "
                f"Overcast or sunny? Any recent rain before the trip?"
            )
        else:
            return (
                f"You blanked for {species} at {location} — what was the weather like? "
                f"Was it sunny and stable, or had there been rain recently?"
            )

    if "technique" in gaps:
        if was_productive:
            return (
                f"Nice catch at {location} — what were you using for {species}? "
                f"Bait, lure, or rig?"
            )
        else:
            return f"No {species} at {location} this time — what technique were you using?"

    if "water_conditions" in gaps:
        return (
            f"What were water conditions like at {location}? "
            f"High/low/normal flow, and clear or turbid?"
        )

    if "time_of_day" in gaps:
        return f"What time of day were you fishing {location} for {species}?"

    return None


def update_insight_confidence(
    db: Database,
    insight: BehavioralInsight,
    was_productive: bool,
    stop: dict,
    client,
    user_id: int = 1,
) -> BehavioralInsight:
    """Update insight confidence based on whether the trip confirmed or contradicted it.

    A trip can only touch its own angler's insights; anything else is refused
    rather than silently updated.
    """
    if insight.user_id != user_id:
        raise ValueError(
            f"Insight {insight.id} belongs to user {insight.user_id}; "
            f"a trip by user {user_id} cannot update it"
        )
    current_confidence = insight.confidence
    evidence_count = insight.evidence_count or 0

    if was_productive:
        new_evidence = evidence_count + 1
        threshold = CONFIRMATIONS_TO_ESCALATE.get(current_confidence, 999)

        if new_evidence >= threshold:
            new_confidence = {
                "low": "medium",
                "medium": "high",
                "high": "high",
            }.get(current_confidence, current_confidence)
        else:
            new_confidence = current_confidence

        db["behavioral_insights"].update(insight.id, {
            "evidence_count": new_evidence,
            "confidence": new_confidence,
            "last_validated": datetime.now().isoformat(),
        })
        return get_insight(db, insight.id)

    else:
        current_detail = insight.source_detail or ""
        contradiction_count = current_detail.count("[contradicted")

        if contradiction_count + 1 >= CONTRADICTIONS_TO_REFINE:
            new_conclusion = _synthesize_nuanced_conclusion(insight, stop, client)
            new_insight = BehavioralInsight(
                species=insight.species,
                condition_type=insight.condition_type,
                condition_context=insight.condition_context,
                conclusion=new_conclusion,
                recommendation=insight.recommendation,
                confidence="low",
                source_type="agent_synthesis",
                source_detail=f"Refined after {contradiction_count + 1} contradictions",
                evidence_count=0,
                lat=insight.lat,
                lng=insight.lng,
                location_name=insight.location_name,
                condition_season=insight.condition_season,
                jurisdiction=insight.jurisdiction,
                user_id=insight.user_id,
            )
            new_id = refine_insight(db, insight.id, new_insight)
            return get_insight(db, new_id)
        else:
            contradiction_note = f" [contradicted {datetime.now().date()}]"
            db["behavioral_insights"].update(insight.id, {
                "source_detail": current_detail + contradiction_note,
            })
            return get_insight(db, insight.id)


def _synthesize_nuanced_conclusion(
    insight: BehavioralInsight,
    contradicting_stop: dict,
    client,
) -> str:
    """Use Haiku to synthesize a more nuanced conclusion after contradictions.

    The contradicting stop used to arrive here as `json.dumps(stop)` — the
    whole database row, internal ids and photo EXIF coordinates included, with
    nothing to tell the model which fields were the angler's observations and
    which were bookkeeping. It goes through the renderer now, like everything
    else that reaches a model.

    The insight's own provenance travels with it too. This function rewrites a
    conclusion and stores the result as `agent_synthesis`; the thing being
    rewritten may itself have been agent_synthesis, or may have come from a
    survey, and those are not the same claim to be revising.
    """
    if client is None:
        return insight.conclusion + " (contradicted — conditions may matter)"

    from src.services.context.render import render_logged_stop, render_recorded_insight
    from src.services.context.user import as_recorded_insight

    prompt = (
        f"A fishing insight has been contradicted by a recent trip. "
        f"Write a more nuanced 2-3 sentence conclusion that acknowledges both "
        f"the original finding and the contradiction.\n\n"
        f"Original insight: {render_recorded_insight(as_recorded_insight(insight))}\n\n"
        f"The trip that contradicted it:\n"
        f"{render_logged_stop(contradicting_stop)}\n\n"
        f"A field marked unrecorded was not written down — do not treat it as "
        f"evidence that the condition was absent.\n\n"
        f"Write ONLY the revised conclusion, no preamble."
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def enrich_session(
    db: Database,
    session_id: int,
    client,
    user_id: int = 1,
) -> dict:
    """Main entry point. Called after a session is logged.

    Returns dict with followup_questions (max 1) and proactive_coaching (or None).
    """
    stops = list(db["stops"].rows_where("session_id = ?", [session_id]))

    if not stops:
        return {"followup_questions": [], "proactive_coaching": None}

    questions = []

    for stop in stops:
        was_productive = bool(stop.get("was_productive"))
        matched = match_insights_to_stop(db, stop, user_id=user_id)

        for match in matched:
            insight = match["insight"]
            update_insight_confidence(db, insight, was_productive, stop, client, user_id=user_id)

            gaps = identify_condition_gaps(stop, insight)
            question = build_followup_question(stop, insight, gaps, was_productive)
            if question:
                questions.append({
                    "question": question,
                    "insight_id": insight.id,
                    "stop_id": stop.get("id"),
                    "species": match["species"],
                })

    # Prioritise follow-up questions about unproductive stops at known spots
    questions.sort(key=lambda q: (
        0 if "blanked" in q["question"].lower() or "no " in q["question"].lower() else 1
    ))

    # Proactive coaching — check for cross-session patterns
    proactive = None
    try:
        current_stops_dicts = [dict(s) for s in stops]
        proactive = detect_proactive_patterns(db, current_stops_dicts, user_id=user_id)
    except Exception:
        pass  # Non-fatal — never block enrichment for coaching errors

    return {
        "followup_questions": questions[:1],
        "proactive_coaching": proactive,
    }


# ── Proactive coaching thresholds ─────────────────────────────────────────────

_MIN_SESSIONS_FOR_PATTERNS = 3
_SPECIES_ATTEMPT_THRESHOLD = 3
_SLUMP_THRESHOLD = 2
_LOW_SUCCESS_RATE = 0.40


def _get_all_stops_summary(db: Database, user_id: int = 1) -> list[dict]:
    """Return a lightweight summary of this angler's stops for pattern analysis.

    "All stops" means all of *theirs*. Pattern detection across users would
    invent slumps and species gaps out of other people's trips.
    """
    rows = list(db.execute("""
        SELECT st.location_name, st.location_text, st.species_caught,
               st.was_productive, st.technique, st.lat, st.lng,
               s.date, s.date_approx
        FROM stops st
        JOIN sessions s ON st.session_id = s.id
        WHERE st.user_id = ?
        ORDER BY s.date DESC, st.id DESC
    """, [user_id]).fetchall())

    stops = []
    for r in rows:
        stops.append({
            "location_name": r[0] or r[1] or "unknown",
            "species_caught": json.loads(r[2] or "[]"),
            "was_productive": bool(r[3]),
            "technique": r[4],
            "lat": r[5],
            "lng": r[6],
            "date": r[7] or r[8] or "unknown",
        })
    return stops


def _detect_species_gap(stops: list[dict], user_id: int = 1) -> dict | None:
    """Detect species in behavioral insights that the user has never personally caught."""
    species_caught_count: dict[str, int] = {}
    total_stops = len(stops)

    for stop in stops:
        for sp in stop["species_caught"]:
            clean = sp.replace("(uncertain)", "").strip().lower()
            if "unidentified" not in clean:
                species_caught_count[clean] = species_caught_count.get(clean, 0) + 1

    try:
        from src.storage.database import get_db
        db = get_db()
        targeted_species = list(db.execute("""
            SELECT DISTINCT LOWER(species) FROM behavioral_insights
            WHERE is_current = 1 AND user_id = ?
        """, [user_id]).fetchall())
        targeted = {r[0] for r in targeted_species}
    except Exception:
        targeted = set()

    never_caught = [sp for sp in targeted if sp not in species_caught_count]

    if never_caught and total_stops >= _SPECIES_ATTEMPT_THRESHOLD:
        priority = [s for s in never_caught if s not in
                    ("unidentified shiner sp.", "unidentified chub sp.")]
        target = priority[0] if priority else never_caught[0]
        return {
            "type": "species_gap",
            "species": target,
            "total_stops": total_stops,
            "message": (
                f"You've discussed {target} in previous sessions but haven't "
                f"logged a personal catch yet across {total_stops} logged stops. "
                f"Want me to diagnose what might be missing?"
            ),
        }
    return None


def _detect_location_slump(stops: list[dict], current_session_stops: list[dict]) -> dict | None:
    """Detect consecutive unproductive stops at a previously productive location.

    Only fires if the current session included a stop at that location.
    """
    current_locations = set(
        s.get("location_name", "").lower()
        for s in current_session_stops
        if s.get("location_name")
    )

    if not current_locations:
        return None

    for location in current_locations:
        if location == "unknown" or len(location) < 3:
            continue

        location_stops = [
            s for s in stops
            if location in s["location_name"].lower()
        ]

        if len(location_stops) < _SLUMP_THRESHOLD + 1:
            continue

        productive_stops = [s for s in location_stops if s["was_productive"]]
        recent_stops = location_stops[:_SLUMP_THRESHOLD + 1]
        recent_unproductive = [s for s in recent_stops if not s["was_productive"]]

        if productive_stops and len(recent_unproductive) >= _SLUMP_THRESHOLD:
            success_rate = len(productive_stops) / len(location_stops)
            return {
                "type": "location_slump",
                "location": location_stops[0]["location_name"],
                "recent_blanks": len(recent_unproductive),
                "total_visits": len(location_stops),
                "historical_success_rate": round(success_rate, 2),
                "message": (
                    f"You've blanked at {location_stops[0]['location_name']} "
                    f"{len(recent_unproductive)} times recently, though historically "
                    f"it's been productive ({int(success_rate * 100)}% success rate "
                    f"across {len(location_stops)} visits). "
                    f"Want me to look at what's changed?"
                ),
            }
    return None


def _detect_technique_pattern(stops: list[dict]) -> dict | None:
    """Detect techniques that appear only in productive stops."""
    if len(stops) < 5:
        return None

    productive = [s for s in stops if s["was_productive"] and s.get("technique")]
    unproductive = [s for s in stops if not s["was_productive"] and s.get("technique")]

    if not productive or not unproductive:
        return None

    prod_techniques: dict[str, int] = {}
    for s in productive:
        t = s["technique"].lower()[:50]
        prod_techniques[t] = prod_techniques.get(t, 0) + 1

    unprod_techniques: dict[str, int] = {}
    for s in unproductive:
        t = s["technique"].lower()[:50]
        unprod_techniques[t] = unprod_techniques.get(t, 0) + 1

    productive_only = [
        t for t, count in prod_techniques.items()
        if count >= 2 and t not in unprod_techniques
    ]

    if productive_only:
        top_technique = max(productive_only, key=lambda t: prod_techniques[t])
        return {
            "type": "technique_pattern",
            "technique": top_technique,
            "productive_count": prod_techniques[top_technique],
            "message": (
                f"Looking at your logs, '{top_technique}' has only appeared "
                f"in productive sessions ({prod_techniques[top_technique]}× success, "
                f"0× blanks). Want me to look at when and where it works best?"
            ),
        }
    return None


def detect_proactive_patterns(
    db: Database,
    current_session_stops: list[dict],
    user_id: int = 1,
) -> dict | None:
    """Check for meaningful patterns across all trip history.

    Returns ONE pattern dict if a threshold is crossed, else None.
    Priority: slump > species gap > technique pattern.
    """
    try:
        session_count = db.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", [user_id]
        ).fetchone()[0]
    except Exception:
        return None

    if session_count < _MIN_SESSIONS_FOR_PATTERNS:
        return None

    all_stops = _get_all_stops_summary(db, user_id=user_id)

    slump = _detect_location_slump(all_stops, current_session_stops)
    if slump:
        return slump

    gap = _detect_species_gap(all_stops, user_id=user_id)
    if gap:
        return gap

    technique = _detect_technique_pattern(all_stops)
    if technique:
        return technique

    return None
