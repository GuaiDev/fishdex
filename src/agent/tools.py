"""The agent's tool surface.

Consolidated from 32 tools to 14. The old set exposed one tool per ingested
dataset — `get_substrate`, `get_benthic_habitat`, `get_water_quality`,
`get_stream_temperature` were four separate round trips to answer one question
about one stretch of water, each resending the full system prompt and every
prior result. Cost is input tokens times round trips, so the fan-out was the
expensive part, not the retrieval.

They collapse into `describe_place` because the context layer already bundles
them. That is the point of having a layer: the shape of the corpus stops being
the shape of the API.

What did *not* collapse, and why:

  * `get_conditions` stays separate from `describe_place`. The split is by
    cache policy, not by preference — everything in `describe_place` is static
    and cacheable, conditions are live and must never be served stale. A tool
    that sometimes fires a live fetch is a tool nobody can cache.
  * `get_regulations` stays separate. Legal facts are retrieved or refused,
    never blended into a general description of a place.
  * `find_connected_tributaries` stays separate. It is a graph traversal from a
    named watercourse, not a description of a point.

What was removed outright:

  * `get_tactical_recommendation` — generated gear advice with no source,
    handed to the model as a tool result and therefore indistinguishable from
    retrieved fact. This is the same defect that put a #16 hook recommendation
    for a sub-inch fish into the species corpus. The model may still reason
    about tackle from general principles; that reasoning renders as its own
    words rather than borrowing a record's authority.
  * `get_oldest_gbif_record` — a corpus trivia query, not a fishing question.
  * `get_piscivore_activity` — folded into the records slice, where a bird
    sighting sits next to the fish sightings it is a proxy for.
  * `get_behavioral_insights`, `get_trips_at_location`, `get_session_conditions`
    — all now inside `get_my_fishing_summary` or `describe_place`'s history.
  * `check_recommendation_conflicts` — moved server-side into
    `record_behavioral_insight`. Asking the model to remember to check for
    contradictions before writing one is a prompt guardrail; doing the check
    inside the write is not.
"""

import json
import logging
from typing import Any

from src.services.context import (
    describe,
    describe_species,
    explore,
    species_history,
    user_layer,
)
from src.services.context.render import (
    render_explore,
    render_place_context,
    render_species_context,
    render_species_history,
    render_user_layer,
)
from src.storage.database import get_db

logger = logging.getLogger(__name__)


# ── schemas ───────────────────────────────────────────────────────────────────


def _latlng(lat_desc: str, lng_desc: str) -> dict:
    return {
        "lat": {"type": "number", "description": lat_desc},
        "lng": {"type": "number", "description": lng_desc},
    }


def tool_schemas(profile: Any) -> list[dict]:
    home = getattr(profile, "home_location", None)
    lat_desc = f"Latitude (your home is {home.lat})" if home else "Latitude"
    lng_desc = f"Longitude (your home is {home.lng})" if home else "Longitude"
    ll = _latlng(lat_desc, lng_desc)

    return [
        {
            "name": "describe_place",
            "description": (
                "Everything recorded about one stretch of water: species observed "
                "there (iNaturalist, GBIF, the user's own catches, fish-eating "
                "birds as a proxy), water chemistry and thermal regime, substrate, "
                "insect life, barriers and confluences, access and parking, and "
                "the user's own visits including blanks. "
                "This is the primary tool — use it for almost any question about a "
                "specific place. It replaces separate lookups for observations, "
                "water quality, benthic health, substrate, stream temperature, "
                "access points and watershed structure. "
                "Every value comes back with its source in square brackets, and "
                "every gap comes back with a specific reason. Report both faithfully: "
                "'nothing recorded here' is a statement about our corpus, not about "
                "the water. Does NOT include live weather — use get_conditions for that."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Place name, e.g. 'Bronte Creek' or 'the dam'. Resolved "
                            "against the user's logged spots first, then the stream "
                            "network, then mapped water. Use this OR lat/lng."
                        ),
                    },
                    **ll,
                    "radius_km": {
                        "type": "number",
                        "description": "Search radius in kilometres. Default 5.",
                    },
                    "species_filter": {
                        "type": "string",
                        "description": "Optional species name to narrow the records to.",
                    },
                },
            },
        },
        {
            "name": "get_conditions",
            "description": (
                "Live and forecast conditions for a location: air temperature, "
                "barometric pressure and its trend, wind, precipitation. "
                "Separate from describe_place because this is the only thing that "
                "must never be cached. Use it whenever the question involves now, "
                "today, tomorrow or a named future day."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    **ll,
                    "when": {
                        "type": "string",
                        "description": (
                            "'now', 'today', 'tomorrow', or a weekday name. Default 'now'."
                        ),
                    },
                },
                "required": ["lat", "lng"],
            },
        },
        {
            "name": "find_water",
            "description": (
                "List mapped water features near a point — streams, rivers, lakes, "
                "ponds — with names and distances. Use for 'what water is near me', "
                "not for ranking or for details about one place. "
                "Says what water EXISTS. Says nothing about whether fish are in it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    **ll,
                    "radius_km": {"type": "number", "description": "Default 25."},
                    "feature_type": {
                        "type": "string",
                        "description": "Optional: 'stream', 'river', 'lake', 'pond'.",
                    },
                    "not_in_trip_log": {
                        "type": "boolean",
                        "description": "Only water the user has never logged a trip at.",
                    },
                },
                "required": ["lat", "lng"],
            },
        },
        {
            "name": "explore_water",
            "description": (
                "Rank stream segments the user has not fished, for exploration. "
                "Scored on how few observations exist there, structure (confluences), "
                "access and remoteness. "
                "CRITICAL: a high score means few people have reported from there. It "
                "is NOT a prediction that fish are present — there is no habitat model "
                "behind this number. Say so when presenting results. Ties at the top "
                "are common and the ordering within a tie is arbitrary."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    **ll,
                    "radius_km": {"type": "number", "description": "Default 50."},
                    "mode": {
                        "type": "string",
                        "enum": ["balanced", "adventure", "easy_access"],
                        "description": (
                            "'adventure' weights remoteness up, 'easy_access' weights "
                            "it down. Default 'balanced'."
                        ),
                    },
                    "min_stream_order": {
                        "type": "integer",
                        "description": (
                            "Default 3. Order 1-2 streams in dense urban areas are "
                            "frequently culverted and not fishable on the ground."
                        ),
                    },
                    "limit": {"type": "integer", "description": "Default 10."},
                },
                "required": ["lat", "lng"],
            },
        },
        {
            "name": "find_connected_tributaries",
            "description": (
                "Walk the stream connectivity graph from a named watercourse and "
                "return what connects to it, with barriers noted. Use for questions "
                "about fish movement, spawning runs, or 'what feeds into X'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "watercourse_name": {"type": "string"},
                    "species": {
                        "type": "string",
                        "description": "Optional species for movement context.",
                    },
                },
                "required": ["watercourse_name"],
            },
        },
        {
            "name": "dismiss_segment",
            "description": (
                "Mark an exploration candidate as not worth suggesting again — "
                "private land, culverted, already known. Scores 0.3x in future results."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "ogf_id": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["ogf_id"],
            },
        },
        {
            "name": "describe_species",
            "description": (
                "What is known about one species: conservation status, native range, "
                "habitat. Call this before giving any advice about targeting a "
                "species. "
                "Conservation status is unverified for every species in the local "
                "file, so the result carries a caution. Where the result says "
                "targeting guidance is withheld, do not work around it — Species at "
                "Risk law prohibits capture, so catch-and-release is not an exemption. "
                "Omit the name to list every species carrying a risk status instead."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "species": {
                        "type": "string",
                        "description": "Common or scientific name. Omit to list SAR species.",
                    },
                    "jurisdiction": {
                        "type": "string",
                        "description": "For the list form. Default 'CA-ON'.",
                    },
                },
            },
        },
        {
            "name": "get_regulations",
            "description": (
                "Fishing regulations for a Fisheries Management Zone: seasons, limits, "
                "size restrictions, plus the province-wide Bait and General sections. "
                "Zone is resolved by point-in-polygon against the MNRF boundary layer "
                "and fails closed — if it cannot place the point it says so rather "
                "than guessing. "
                "Never state a regulation that is not in the returned text. If the "
                "answer is withheld, say it is withheld and point the user at the "
                "official summary."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "zone": {"type": "integer", "description": "FMZ number, if known."},
                    "species": {"type": "string"},
                    **ll,
                },
            },
        },
        {
            "name": "get_stocking_history",
            "description": (
                "MNRF stocking records: species, year, life stage, numbers. "
                "Stocking is a fact about where trucks can reach, not about habitat "
                "quality — keep it separate from presence evidence when you answer."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "waterbody_name": {"type": "string"},
                    "species": {"type": "string"},
                    **ll,
                    "radius_km": {"type": "number", "description": "Default 50."},
                    "year_from": {"type": "integer"},
                },
            },
        },
        {
            "name": "get_my_fishing_summary",
            "description": (
                "The user's derived profile: how many sessions and stops, blank rate, "
                "species logged, what they appear to target, demonstrated expertise, "
                "personal patterns with their sample sizes, and which fields are too "
                "sparsely recorded to support a claim. "
                "Pass a species to also get their full record with it — every catch, "
                "the setups that worked, and stored insights with their sources. "
                "A pattern marked NOT yet claimable is a hypothesis. Do not state it "
                "as a finding about the user."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "species": {
                        "type": "string",
                        "description": (
                            "Optional: also return this angler's record with one species."
                        ),
                    }
                },
            },
        },
        {
            "name": "get_coaching",
            "description": (
                "A full coaching analysis of the user's logged history with a species "
                "or at a location. Heavier than get_my_fishing_summary — it runs its "
                "own synthesis pass. Use when the user explicitly asks what they "
                "should do differently."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "coaching_type": {"type": "string", "enum": ["species", "location"]},
                    "species": {"type": "string"},
                    "location": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["coaching_type"],
            },
        },
        {
            "name": "log_trip",
            "description": (
                "Parse and log a fishing session from the user's free-text description. "
                "Records stops, species, techniques, conditions and blanks. "
                "A blank is not a failure to tidy away — log it as reported."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "The user's own words describing the trip.",
                    }
                },
                "required": ["description"],
            },
        },
        {
            "name": "record_behavioral_insight",
            "description": (
                "Store a durable insight about how a species behaves under given "
                "conditions. Checks for contradicting stored insights first and "
                "reports any it finds — you do not need to check separately. "
                "Only record something supported by data you actually retrieved. "
                "Use source_type 'agent_synthesis' when it is your own reasoning; it "
                "will be marked as inference wherever it is shown."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "species": {"type": "string"},
                    "condition_type": {
                        "type": "string",
                        "enum": ["behavioral", "habitat", "temporal", "gear"],
                    },
                    "condition_context": {"type": "string"},
                    "conclusion": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low", "unverified"],
                    },
                    "source_type": {
                        "type": "string",
                        "enum": [
                            "agent_synthesis",
                            "tactical_rules",
                            "inat_pattern",
                            "mnrf_survey",
                            "reddit_pattern",
                            "trip_log",
                            "user_correction",
                        ],
                    },
                    "source_detail": {"type": "string"},
                    "evidence_count": {"type": "integer"},
                    "recommendation": {"type": "string"},
                    "condition_season": {"type": "string"},
                    "location_name": {"type": "string"},
                    "jurisdiction": {"type": "string"},
                    **ll,
                },
                "required": [
                    "species",
                    "condition_type",
                    "condition_context",
                    "conclusion",
                    "confidence",
                    "source_type",
                    "source_detail",
                    "evidence_count",
                ],
            },
        },
        {
            "name": "search_community",
            "description": (
                "Search community and reference text: Reddit fishing discussions, or "
                "the indexed knowledge base. Community reports measure where anglers "
                "go as much as where fish are — attribute what you take from here and "
                "do not present it as a record."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "source": {
                        "type": "string",
                        "enum": ["reddit", "knowledge_base"],
                        "description": "Default 'knowledge_base'.",
                    },
                    "species": {"type": "string"},
                    "jurisdiction": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    ]


# ── dispatch ──────────────────────────────────────────────────────────────────


def execute_tool(name: str, inputs: dict, user_id: int = 1) -> str:
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    return handler(inputs, user_id)


def _describe_place(inputs: dict, user_id: int) -> str:
    ctx = describe(
        get_db(),
        query=inputs.get("query"),
        lat=inputs.get("lat"),
        lng=inputs.get("lng"),
        radius_km=inputs.get("radius_km", 5.0),
        caller="chat_place",
        user_id=user_id,
        species_filter=inputs.get("species_filter"),
    )
    if ctx is None:
        target = inputs.get("query") or f"{inputs.get('lat')}, {inputs.get('lng')}"
        return json.dumps(
            {
                "resolved": False,
                "message": (
                    f"Could not resolve '{target}' to a stretch of water. This is a "
                    "resolution failure, not an empty result — do not report it as "
                    "'nothing found there'."
                ),
            }
        )
    return render_place_context(ctx)


def _get_conditions(inputs: dict, _user_id: int) -> str:
    from src.services.weather import get_conditions_for_agent, get_pressure_trend_for_agent

    conditions = get_conditions_for_agent(
        lat=inputs["lat"], lng=inputs["lng"], when=inputs.get("when", "now")
    )
    # Pressure trend was its own tool and its own round trip, for a value that
    # is only ever wanted alongside the conditions it explains.
    try:
        trend = get_pressure_trend_for_agent(lat=inputs["lat"], lng=inputs["lng"])
    except Exception:  # noqa: BLE001
        logger.warning("pressure trend failed", exc_info=True)
        trend = None
    return json.dumps({"conditions": _maybe_json(conditions), "pressure": _maybe_json(trend)})


def _maybe_json(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _find_water(inputs: dict, _user_id: int) -> str:
    from src.services.osm import get_access_points_for_agent, get_nearby_water_for_agent

    water = get_nearby_water_for_agent(
        lat=inputs["lat"],
        lng=inputs["lng"],
        radius_km=inputs.get("radius_km", 25),
        feature_type=inputs.get("feature_type"),
        not_in_trip_log=inputs.get("not_in_trip_log", False),
    )
    access = get_access_points_for_agent(
        lat=inputs["lat"], lng=inputs["lng"], radius_km=inputs.get("radius_km", 25)
    )
    return json.dumps({"water": _maybe_json(water), "access": _maybe_json(access)})


def _explore_water(inputs: dict, user_id: int) -> str:
    resp = explore(
        get_db(),
        lat=inputs["lat"],
        lng=inputs["lng"],
        radius_km=inputs.get("radius_km", 50),
        min_stream_order=inputs.get("min_stream_order", 3),
        limit=inputs.get("limit", 10),
        mode=inputs.get("mode", "balanced"),
        user_id=user_id,
    )
    return render_explore(resp)


def _find_tributaries(inputs: dict, _user_id: int) -> str:
    from src.services.hydrology import find_connected_tributaries_for_agent

    return find_connected_tributaries_for_agent(
        watercourse_name=inputs["watercourse_name"], species=inputs.get("species")
    )


def _dismiss_segment(inputs: dict, user_id: int) -> str:
    from datetime import datetime

    ogf_id = int(inputs["ogf_id"])
    reason = inputs.get("reason") or ""
    get_db()["dismissed_segments"].upsert(
        {
            "user_id": user_id,
            "ogf_id": ogf_id,
            "dismissed_at": datetime.now().isoformat(),
            "reason": reason,
        },
        pk=("user_id", "ogf_id"),
    )
    return json.dumps(
        {
            "success": True,
            "message": (
                f"Segment {ogf_id} dismissed ({reason or 'no reason given'}). "
                "It will score 0.3× in future exploration results."
            ),
            "ogf_id": ogf_id,
            "reason": reason,
        }
    )


def _describe_species(inputs: dict, _user_id: int) -> str:
    species = inputs.get("species")
    if not species:
        from src.services.species_ranges import get_sar_species_for_agent

        return get_sar_species_for_agent(inputs.get("jurisdiction", "CA-ON"))
    return render_species_context(describe_species(get_db(), species))


def _get_regulations(inputs: dict, _user_id: int) -> str:
    from src.services.regulations import get_regulations_for_agent

    return get_regulations_for_agent(
        zone=inputs.get("zone"),
        species=inputs.get("species"),
        lat=inputs.get("lat"),
        lng=inputs.get("lng"),
    )


def _get_stocking(inputs: dict, _user_id: int) -> str:
    from src.services.stocking import get_stocking_for_agent

    return get_stocking_for_agent(
        waterbody_name=inputs.get("waterbody_name"),
        species=inputs.get("species"),
        lat=inputs.get("lat"),
        lng=inputs.get("lng"),
        radius_km=inputs.get("radius_km", 50),
        year_from=inputs.get("year_from"),
    )


def _fishing_summary(inputs: dict, user_id: int) -> str:
    db = get_db()
    blocks = [render_user_layer(user_layer(db, user_id=user_id))]
    species = inputs.get("species")
    if species:
        blocks.append(render_species_history(species_history(db, species, user_id)))
    return "\n\n".join(blocks)


def _get_coaching(inputs: dict, user_id: int) -> str:
    from src.services.coaching import get_location_coaching, get_species_coaching

    db = get_db()
    question = inputs.get("question")
    if inputs.get("coaching_type") == "location":
        location = inputs.get("location")
        if not location:
            return "Please specify a location for location coaching."
        return get_location_coaching(db, location, question, user_id=user_id)
    species = inputs.get("species")
    if not species:
        return "Please specify a species for species coaching."
    return get_species_coaching(db, species, question, user_id=user_id)


def _log_trip(inputs: dict, user_id: int) -> str:
    from src.services.trip_logger import log_session
    from src.services.trip_parser import parse_session_from_text

    db = get_db()
    parsed = parse_session_from_text(inputs["description"], db)
    result = log_session(parsed, db, user_id=user_id)

    parts = [
        json.dumps(
            {
                "session_id": result["session_id"],
                "stops_logged": result["stops_logged"],
                "date": parsed.get("date"),
                "date_approx": parsed.get("date_approx"),
                "stops": [
                    {
                        "location": s.get("location_text"),
                        "species_caught": s.get("species_caught", []),
                        "was_productive": s.get("was_productive"),
                        "location_method": s.get("location_method"),
                    }
                    for s in parsed.get("stops", [])
                ],
                "confirmation": (
                    f"Session #{result['session_id']} logged with {result['stops_logged']} stop(s)."
                ),
            }
        )
    ]
    questions = result.get("followup_questions", [])
    if questions:
        parts.append(
            "\n\nOne quick question to improve future recommendations: " + questions[0]["question"]
        )
    proactive = result.get("proactive_coaching")
    if proactive:
        parts.append("\n\n---\n**Pattern detected:** " + proactive["message"])
    return "\n".join(parts)


def _record_insight(inputs: dict, user_id: int) -> str:
    """Write an insight, having checked for contradictions first.

    The conflict check used to be a separate tool the model was asked to
    remember to call. Moving it inside the write is the same move as putting
    escalation in Python: a rule that depends on the model choosing to invoke
    it is a rule that holds most of the time.
    """
    from src.services.insights import (
        check_conflicts_for_agent_service,
        record_behavioral_insight_for_agent,
    )

    conflicts = None
    try:
        conflicts = check_conflicts_for_agent_service(
            species=inputs["species"],
            lat=inputs.get("lat"),
            lng=inputs.get("lng"),
            condition_season=inputs.get("condition_season"),
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001 - never block the write on the check
        logger.warning("conflict check failed", exc_info=True)

    written = record_behavioral_insight_for_agent(
        species=inputs["species"],
        condition_type=inputs["condition_type"],
        condition_context=inputs["condition_context"],
        conclusion=inputs["conclusion"],
        confidence=inputs["confidence"],
        source_type=inputs["source_type"],
        source_detail=inputs["source_detail"],
        evidence_count=inputs["evidence_count"],
        jurisdiction=inputs.get("jurisdiction"),
        lat=inputs.get("lat"),
        lng=inputs.get("lng"),
        recommendation=inputs.get("recommendation"),
        condition_season=inputs.get("condition_season"),
        location_name=inputs.get("location_name"),
        user_id=user_id,
    )
    return json.dumps(
        {"recorded": _maybe_json(written), "existing_related": _maybe_json(conflicts)}
    )


def _search_community(inputs: dict, _user_id: int) -> str:
    source = inputs.get("source", "knowledge_base")
    if source == "reddit":
        from src.services.reddit import search_reddit_for_agent

        return search_reddit_for_agent(
            query=inputs["query"],
            species=inputs.get("species"),
            jurisdiction=inputs.get("jurisdiction"),
            limit=inputs.get("limit", 10),
        )
    from src.services.knowledge import search_knowledge_base_for_agent

    return search_knowledge_base_for_agent(query=inputs["query"], top_k=inputs.get("limit", 5))


_HANDLERS = {
    "describe_place": _describe_place,
    "get_conditions": _get_conditions,
    "find_water": _find_water,
    "explore_water": _explore_water,
    "find_connected_tributaries": _find_tributaries,
    "dismiss_segment": _dismiss_segment,
    "describe_species": _describe_species,
    "get_regulations": _get_regulations,
    "get_stocking_history": _get_stocking,
    "get_my_fishing_summary": _fishing_summary,
    "get_coaching": _get_coaching,
    "log_trip": _log_trip,
    "record_behavioral_insight": _record_insight,
    "search_community": _search_community,
}
