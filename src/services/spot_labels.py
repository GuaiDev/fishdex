"""Every user-facing word the spots map shows, in one place.

The web app never hardcodes these. It reads them from `GET /map/spots/labels`
(`labels_payload()` below), so the wording can change, and a different front end
can be built against the API, without touching the client.

What is here: the chip for each kind of spot, the one line that says what the
kind is, the title templates the classifier fills in, the names of the
getting-there layers, and the one paragraph of "why fish gather here" per kind.

That paragraph is general reasoning about a kind of water, not a record about
the spot, and it is served labelled as such. Exactly two sentences per kind, no
seasonal claims and no gear advice (the project removed its last unsourced
tactics module). Words a beginner would not know — confluence, tailwater,
anchor, stretch, OHN — are kept out of every string; a test enforces it.

Phase 3 of the spots plan moves the paragraphs behind `render.py` and
`translate.py`; until then this module is the only source, and nothing else in
the codebase writes spot text.
"""

from typing import TypedDict

from src.models.spot import SpotKind

GENERAL_REASONING_LABEL = "General reasoning, not a record about this spot"
# Provenance vocabulary of src/models/context.py, for the paragraph.
GENERAL_REASONING_PROVENANCE = "inference"


class KindLabels(TypedDict):
    chip: str
    one_liner: str
    why_fish_gather: str
    # Quiet water is a ranking of reaches, not a located structure: the UI
    # draws it apart from the others and lists it last.
    distinct: bool


KIND_ORDER: tuple[SpotKind, ...] = (
    "river_mouth",
    "creek_junction",
    "below_dam",
    "public_fishing",
    "quiet_water",
)

KINDS: dict[SpotKind, KindLabels] = {
    "river_mouth": {
        "chip": "River mouths",
        "one_liner": "Where a river runs out into a lake.",
        "why_fish_gather": (
            "Fish that live in the lake pass through here to feed or to run upstream. "
            "The river also carries food out into open water."
        ),
        "distinct": False,
    },
    "creek_junction": {
        "chip": "Where creeks join",
        "one_liner": "Two streams meeting. Different temperature, speed and food mix at the join.",
        "why_fish_gather": (
            "Two flows mixing makes a seam between fast and slow water, which fish use "
            "for shelter with food drifting past. A creek that runs cooler than the river can "
            "also give fish a place to ease out of warm water."
        ),
        "distinct": False,
    },
    "below_dam": {
        "chip": "Below dams",
        "one_liner": "The water just downstream of a dam or weir.",
        "why_fish_gather": (
            "A dam or weir blocks fish from moving further upstream, so they gather in the "
            "water just below it. That water is churned up and rich in oxygen."
        ),
        "distinct": False,
    },
    "public_fishing": {
        "chip": "Public fishing spots",
        "one_liner": "Places that volunteer mappers have marked as set aside for fishing.",
        "why_fish_gather": (
            "Someone has mapped this as a place to fish, usually because the water is easy "
            "and allowed to reach. That is about access, so it says nothing by itself about "
            "what is swimming there."
        ),
        "distinct": False,
    },
    "quiet_water": {
        "chip": "Quiet water",
        "one_liner": (
            "Reaches with few public observation records, which may mean few anglers "
            "or just few observers."
        ),
        "why_fish_gather": (
            "These reaches have few public observation records. That can mean fewer anglers, "
            "few observers or hard access, and it never tells you whether fish are there."
        ),
        "distinct": True,
    },
}

# Under the River mouths chip, per the report's wording question.
RIVER_MOUTH_SUBLINE = "Where a river meets a lake"

# Getting-there layer: what each kind of access is called on a card and a legend.
ACCESS_LABELS: dict[str, str] = {
    "parking": "Parking",
    "trail_head": "Trailhead",
    "boat_launch": "Boat launch",
    "road_crossing": "Road crossing",
}
LAYERS: dict[str, dict] = {
    "getting_there": {
        "label": "Getting there",
        "kinds": ["parking", "trail_head", "boat_launch"],
        "default_on": True,
        "note": "Mapped by OpenStreetMap volunteers; not checked on the ground.",
    },
    "bridges": {
        "label": "Bridges",
        "kinds": ["road_crossing"],
        "default_on": False,
        "note": "Public road crossings. A way to reach small water, not a place to fish.",
    },
}

# ── water bodies ──────────────────────────────────────────────────────────────
# The rivers, creeks and lakes the spots sit on. `chip` is the plural for a filter,
# `label` the singular for a card, `unnamed` what to show when no source gave the
# water a name (the API never sends a made-up name; it sends this phrase beside a
# null one).

WATER_BODY_TYPE_ORDER = ("river", "creek", "lake", "pond", "great_lake")
WATER_BODY_TYPES: dict[str, dict[str, str]] = {
    "river": {"chip": "Rivers", "label": "River", "unnamed": "A river with no name on file"},
    "creek": {"chip": "Creeks", "label": "Creek", "unnamed": "A creek with no name on file"},
    "lake": {"chip": "Lakes", "label": "Lake", "unnamed": "A lake with no name on file"},
    "pond": {"chip": "Ponds", "label": "Pond", "unnamed": "A pond with no name on file"},
    "great_lake": {
        "chip": "Great Lakes",
        "label": "Great Lake",
        "unnamed": "A Great Lake with no name on file",
    },
}
# Where a water's name came from, in words for a "source" line under a name.
NAME_SOURCE_LABELS: dict[str, str] = {
    "curated": "Named in this app's own list of fishing waters",
    "curated_region": "Taken from the area the river runs through",
    "ohn": "Ontario government water map",
    "osm": "OpenStreetMap volunteers",
    "unknown": "No name on file",
}
# A lake or pond with no outline on file is shown as a place, not a shape.
NO_OUTLINE_NOTE = "No outline is on file for this water, so it has no shape on the map yet."


# ── shoreline features ─────────────────────────────────────────────────────────
# The inlets, outlets, islands, points and peninsulas a lake's outline shows.
# `label` is the singular name on a card, `chip` the plural legend entry,
# `one_liner` the sentence under the feature. A shape read off the outline's
# edge says so ("from the shoreline outline"); a stream junction is its own line
# ending at the shore.

FEATURE_KIND_ORDER = ("inlet", "outlet", "island", "point", "peninsula")
FEATURE_KINDS: dict[str, dict[str, str]] = {
    "inlet": {
        "label": "Inlet",
        "chip": "Inlets",
        "one_liner": "A mapped stream flows in here.",
    },
    "outlet": {
        "label": "Outlet",
        "chip": "Outlets",
        "one_liner": "The lake drains out here.",
    },
    "island": {"label": "Island", "chip": "Islands", "one_liner": "Land inside the water."},
    "point": {"label": "Point", "chip": "Points", "one_liner": "A point of land reaches out."},
    "peninsula": {
        "label": "Peninsula",
        "chip": "Peninsulas",
        "one_liner": "A long point of land reaches out.",
    },
}
# What a derivation is, in words. Both come from the same Ontario government
# water map; the outline is the water's own edge, a stream its own line.
FEATURE_DERIVATION_LABELS: dict[str, str] = {
    "shoreline_outline": "from the shoreline outline",
    "watercourse_endpoint": "where a mapped stream meets the shore",
}
FEATURE_READ_FROM_OUTLINE = "Read from the shoreline outline"
FEATURE_READ_FROM_MAP = "Read from the Ontario government water map"


def shoreline_feature_note(kind: str, derivation: str, name: str | None = None) -> str:
    """The sentence shown under a shoreline feature, assembled from the vocabulary."""
    base = FEATURE_KINDS[kind]["one_liner"]
    if derivation == "watercourse_endpoint":
        if name:
            return f"{base} {name} is mapped here. {FEATURE_READ_FROM_MAP}."
        return f"{base} {FEATURE_READ_FROM_MAP}."
    return f"{base} {FEATURE_READ_FROM_OUTLINE}."


# ── titles ────────────────────────────────────────────────────────────────────
# Filled in by services/spots.py. {river} and {tributary} already carry their
# article ("the Credit River", "Sheldon Creek"). A title is never built from a
# type word alone, and never starts with "Unnamed".

TITLES = {
    "river_mouth": "Where {river} meets {lake}",
    "river_mouth_lake_unknown": "Where {river} flows into the lake",
    "creek_junction": "Where {tributary} joins {river}",
    "creek_junction_stretch_ends": "Where {river} joins {larger}",
    "below_dam": "Below {dam}",
    "below_dam_unnamed": "Below the {dam_kind} on {river}",
    "at_dam": "At {dam}",
    "at_dam_unnamed": "At the {dam_kind} on {river}",
    "public_fishing": "{name}",
    "public_fishing_unnamed": "Public fishing spot on {river}",
}
# Words for an unnamed joining channel, by how much water drains into it.
UNNAMED_TRIBUTARY = {
    "small": "a small creek",
    "medium": "a creek",
    "large": "a river",
}
UNNAMED_LARGER_RIVER = "a larger river"


def labels_payload() -> dict:
    """The whole vocabulary as JSON: what `GET /map/spots/labels` serves."""
    return {
        "kinds": [
            {
                "kind": kind,
                "chip": KINDS[kind]["chip"],
                "one_liner": KINDS[kind]["one_liner"],
                "distinct": KINDS[kind]["distinct"],
                "why_fish_gather": {
                    "text": KINDS[kind]["why_fish_gather"],
                    "label": GENERAL_REASONING_LABEL,
                    "provenance": GENERAL_REASONING_PROVENANCE,
                },
            }
            for kind in KIND_ORDER
        ],
        "river_mouth_subline": RIVER_MOUTH_SUBLINE,
        "water_body_types": [{"type": t, **WATER_BODY_TYPES[t]} for t in WATER_BODY_TYPE_ORDER],
        "name_sources": NAME_SOURCE_LABELS,
        "no_outline_note": NO_OUTLINE_NOTE,
        "shoreline_features": [
            {"kind": kind, **FEATURE_KINDS[kind]} for kind in FEATURE_KIND_ORDER
        ],
        "shoreline_derivations": FEATURE_DERIVATION_LABELS,
        "layers": LAYERS,
        "access_labels": ACCESS_LABELS,
    }


def all_strings() -> list[str]:
    """Every user-facing string above, flat — for the jargon lint test."""
    out: list[str] = [GENERAL_REASONING_LABEL, RIVER_MOUTH_SUBLINE]
    for entry in KINDS.values():
        out += [entry["chip"], entry["one_liner"], entry["why_fish_gather"]]
    out += list(ACCESS_LABELS.values())
    for entry in WATER_BODY_TYPES.values():
        out += list(entry.values())
    out += list(NAME_SOURCE_LABELS.values()) + [NO_OUTLINE_NOTE]
    for entry in FEATURE_KINDS.values():
        out += list(entry.values())
    out += list(FEATURE_DERIVATION_LABELS.values())
    out += [FEATURE_READ_FROM_OUTLINE, FEATURE_READ_FROM_MAP]
    for layer in LAYERS.values():
        out += [layer["label"], layer["note"]]
    out += list(TITLES.values()) + list(UNNAMED_TRIBUTARY.values()) + [UNNAMED_LARGER_RIVER]
    return out
