"""Presentation: profile in, per-element prominence (0..1) out.

One small function. It never selects a layout, never hides an element and
never touches a fact: it says how loudly each element should speak, and each
component decides what "loud" means. Every prominence has a floor above zero,
so everything stays reachable by tapping in, and the map always offers a
"more detail" toggle.

Orientation is pulled toward the middle by its confidence before use, so a
profile built on three trips presents almost exactly like no profile at all.
There are no modes and no "you've shifted" moments: values move as the
profile moves, gradually, once per logged session.

Groundwork: every number below is a named constant chosen by reasoning, not
measured, and is expected to change once the front end exists.
"""

from src.models.angler_profile import AnglerProfile
from src.services import terms

# Lowest prominence any element can get. Above zero: nothing is locked out.
PROMINENCE_FLOOR = 0.15
# The journal timeline is the middle of the spectrum: full at 0.5, this at the ends.
JOURNAL_EDGE = 0.4

# Map labels by concept state.
LABEL_MODE = {
    "unseen": "plain",  # plain-language wording
    "introduced": "term_with_definition",  # the term, tap for its meaning
    "familiar": "term",  # the term alone
}
CONCEPT_PROMINENCE = {"unseen": 0.35, "introduced": 0.6, "familiar": 0.85}
# Default share of map layers drawn: this with nothing familiar, 1.0 with everything.
BASE_LAYER_DENSITY = 0.4
# Water-body-first view: a fixed, balanced mix whatever the profile says.
# Creeks count as rivers; the Great Lakes are drawn but not part of the mix.
WATER_BODY_MIX = {"lake": 1 / 3, "pond": 1 / 3, "river": 1 / 3}

# Log form: always shown, always first, whatever the profile.
LOG_FORM_DEFAULTS = ("species", "place", "photo", "trip_rating")
# Detail fields, and which side of the profile opens each one by default.
LOG_FORM_DETAILS = {
    "size": "size_focus",
    "count": "lifelist",
    "target_species": "specialist",
    "technique": "specialist",
    "bait": "specialist",
    "water_clarity": "specialist",
    "water_level": "specialist",
    "water_temp": "specialist",
    "time_of_day": "specialist",
    "small_species_id_help": "micro_focus",
    "notes": "journal",
}
# A detail field starts expanded at or above this prominence.
EXPAND_AT = 0.6


def presentation(profile: AnglerProfile) -> dict:
    """Per-element prominence for the map, dex, log form and pins."""
    o = profile.orientation
    # 0 = specialist, 1 = lifelister; pulled to 0.5 by low confidence.
    lean = 0.5 + (o.value - 0.5) * o.confidence
    lifelist = _floor(lean)
    specialist = _floor(1.0 - lean)
    journal = _floor(1.0 - (1.0 - JOURNAL_EDGE) * abs(lean - 0.5) * 2)
    sides = {
        "lifelist": lifelist,
        "specialist": specialist,
        "journal": journal,
        "size_focus": _floor(profile.size_focus.value),
        "micro_focus": _floor(profile.micro_focus.value),
    }

    labels = {}
    for term_id in terms.TERM_ORDER:
        c = profile.concepts.get(term_id)
        state = c.state if c else "unseen"
        labels[term_id] = {
            "state": state,
            "label_mode": LABEL_MODE[state],
            "prominence": CONCEPT_PROMINENCE[state],
        }
    # Density is the share of *map layers* whose concept is familiar; concepts
    # with no map layer (barometric pressure) say nothing about it.
    layer_ids = [t for t in terms.TERM_ORDER if terms.TERMS[t]["mapped_from"] is not None]
    familiar_share = sum(1 for t in layer_ids if labels[t]["state"] == "familiar") / len(layer_ids)

    log_details = {
        field: {
            "prominence": sides[side],
            "start_expanded": sides[side] >= EXPAND_AT,
            "available": True,
        }
        for field, side in LOG_FORM_DETAILS.items()
    }

    return {
        "user_id": profile.user_id,
        "basis": {
            "orientation": o.value,
            "orientation_confidence": o.confidence,
            "effective_lean": round(lean, 4),
            "size_focus": profile.size_focus.value,
            "micro_focus": profile.micro_focus.value,
            "sessions_used": profile.sessions_used,
        },
        "map": {
            "labels": labels,
            "default_layer_density": round(
                BASE_LAYER_DENSITY + (1 - BASE_LAYER_DENSITY) * familiar_share, 4
            ),
            "more_detail_toggle": True,
            "water_body_mix": WATER_BODY_MIX,
        },
        "dex": {
            "family_completion": lifelist,
            "recorded_near_not_caught": lifelist,
            "lifer_markers": lifelist,
            "journal_timeline": journal,
            "seasonal_timeline": specialist,
            "catches_by_conditions": specialist,
            "species_depth": specialist,
            "size_records": sides["size_focus"],
            "small_species": sides["micro_focus"],
        },
        "log_form": {
            "defaults": {
                field: {"prominence": 1.0, "available": True} for field in LOG_FORM_DEFAULTS
            },
            "details": log_details,
            "species_order_weights": {
                "your_targets": specialist,
                "recorded_here_not_caught": lifelist,
                "recently_logged": journal,
            },
        },
        "pins": {
            "target_species_context": specialist,
            "conditions_context": specialist,
            "uncaught_recorded_cue": lifelist,
        },
    }


def _floor(v: float) -> float:
    return round(PROMINENCE_FLOOR + (1 - PROMINENCE_FLOOR) * min(max(v, 0.0), 1.0), 4)
