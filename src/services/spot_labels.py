"""Every user-facing word the spots map shows, in one place.

The web app never hardcodes these. It reads them from `GET /map/spots/labels`
(`labels_payload()` below), so the wording can change, and a different front end
can be built against the API, without touching the client.

What is here: the chip for each kind of spot, the one line that says what the
kind is, the title templates the classifier fills in, and the names of the
getting-there layers.

The "why fish gather here" paragraph per kind is not written here. It is
general reasoning about a kind of water, so it lives in
`services/context/translate.py` as inferred text with a fixed label, and this
module only passes it through to the labels payload. The text of a spot card
itself is produced by `render_spot()` in `services/context/render.py` and
nowhere else. Words a beginner would not know — confluence, tailwater, anchor,
stretch, OHN — are kept out of every string; a test enforces it.
"""

from typing import TypedDict

from src.models.spot import SpotKind
from src.services import terms
from src.services.context import translate

GENERAL_REASONING_LABEL = translate.GENERAL_REASONING_LABEL
# Provenance vocabulary of src/models/context.py, for the paragraph.
GENERAL_REASONING_PROVENANCE = "inference"


class KindLabels(TypedDict):
    chip: str
    one_liner: str
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
        "distinct": False,
    },
    "creek_junction": {
        "chip": "Where creeks join",
        "one_liner": "Two streams meeting. Different temperature, speed and food mix at the join.",
        "distinct": False,
    },
    "below_dam": {
        "chip": "Below dams",
        "one_liner": "The water just downstream of a dam or weir.",
        "distinct": False,
    },
    "public_fishing": {
        "chip": "Public fishing spots",
        "one_liner": "Places that volunteer mappers have marked as set aside for fishing.",
        "distinct": False,
    },
    "quiet_water": {
        "chip": "Quiet water",
        "one_liner": (
            "Reaches with few public observation records, which may mean few anglers "
            "or just few observers."
        ),
        "distinct": True,
    },
}

# Under the River mouths chip, per the report's wording question.
RIVER_MOUTH_SUBLINE = "Where a river meets a lake"

# "New to you": per-user, so the words live here and the client shows them as given.
# A count and a prompt, never a score: no rank, no total across spots, nothing shared.
NEW_TO_YOU_TITLE = "New to you"
NEW_TO_YOU_HOOK_ONE = "1 species recorded here you haven't caught yet"
NEW_TO_YOU_HOOK_MANY = "{n} species recorded here you haven't caught yet"
NEW_TO_YOU_TAP_PROMPT = "Tap to see which"
NEW_TO_YOU_NOTE = "Based on the catches you have confirmed. Only you see this."
NEW_TO_ME_FILTER = {
    "chip": "New to me",
    "one_liner": "Spots with fish recorded nearby that you haven't caught yet.",
}


def new_to_you_hook(n: int) -> str:
    return NEW_TO_YOU_HOOK_ONE if n == 1 else NEW_TO_YOU_HOOK_MANY.format(n=n)


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

FEATURE_KIND_ORDER = ("inlet", "outlet", "island", "point", "peninsula", "drop_off", "hump")
FEATURE_KINDS: dict[str, dict[str, str]] = {
    "inlet": {
        "label": "Inlet",
        "chip": "Inlets",
        "one_liner": "A mapped stream flows in here.",
    },
    "outlet": {
        "label": "Outlet",
        "chip": "Outlets",
        "one_liner": "A mapped stream starts at the shore here and flows away.",
    },
    "island": {"label": "Island", "chip": "Islands", "one_liner": "Land inside the water."},
    "point": {"label": "Point", "chip": "Points", "one_liner": "A point of land reaches out."},
    "peninsula": {
        "label": "Peninsula",
        "chip": "Peninsulas",
        "one_liner": "A long point of land reaches out.",
    },
    # Read off a lake's surveyed depth contours, not its outline. A beginner does
    # not know either word; the one line says what each is in plain terms.
    "drop_off": {
        "label": "Drop-off",
        "chip": "Drop-offs",
        "one_liner": "The bottom falls away quickly here.",
    },
    "hump": {
        "label": "Hump",
        "chip": "Humps",
        "one_liner": "A shallower rise in the bottom.",
    },
}
# What a derivation is, in words. The outline is the water's own edge, a stream
# its own line, and the contours come from a government lake survey.
FEATURE_DERIVATION_LABELS: dict[str, str] = {
    "shoreline_outline": "from the shoreline outline",
    "watercourse_endpoint": "where a mapped stream meets the shore",
    "bathymetry_contours": "from a lake survey",
}
FEATURE_READ_FROM_OUTLINE = "Read from the shoreline outline"
FEATURE_READ_FROM_MAP = "Read from the Ontario government water map"
# Shown when a survey's own date is missing on the contour and its index.
SURVEY_DATE_UNKNOWN = "survey date unknown"
# Depth is recorded relative to the level the lake was at on survey day, not
# today, and the source states no unit. This is a data note, never a card line.
BATHYMETRY_DATA_NOTE = (
    "Depth is measured at the survey's own water level, not today's. "
    "The source does not state the unit; metres is taken from the values."
)


def shoreline_feature_note(
    kind: str, derivation: str, name: str | None = None, attributes: dict | None = None
) -> str:
    """The sentence shown under a shoreline feature, assembled from the vocabulary.

    A contour-derived feature names its survey ("from a 1970 survey, transect"),
    or says the date is unknown, using the facts carried in `attributes`.
    """
    base = FEATURE_KINDS[kind]["one_liner"]
    if derivation == "bathymetry_contours":
        return f"{base} {_survey_phrase(attributes or {})}."
    if derivation == "watercourse_endpoint":
        if name:
            return f"{base} {name} is mapped here. {FEATURE_READ_FROM_MAP}."
        return f"{base} {FEATURE_READ_FROM_MAP}."
    return f"{base} {FEATURE_READ_FROM_OUTLINE}."


def _survey_phrase(attributes: dict) -> str:
    """'From a 1970 survey, transect' or 'From a survey, survey date unknown'."""
    year = attributes.get("survey_year")
    method = attributes.get("survey_method")
    if year:
        tail = f", {method}" if method else ""
        return f"From a {year} survey{tail}"
    return f"From a survey, {SURVEY_DATE_UNKNOWN}"


# ── sections ──────────────────────────────────────────────────────────────────
# The overview map draws water as sections: one shape per curated river section and
# one per lake with an outline. A tap opens the section's pins. `kind` says what the
# shape is; the water's own type word ("River", "Lake") comes from WATER_BODY_TYPES.

SECTION_KINDS: dict[str, dict[str, str]] = {
    "river_section": {
        "label": "River section",
        "one_liner": "One named run of a river or creek. Tap it to see what is along it.",
    },
    "lake": {
        "label": "Lake",
        "one_liner": "A lake drawn from its outline. Tap it to see what is on it.",
    },
}
# The kinds of thing a section holds, as `pin_type`. Spots, shoreline features and
# access points are named by their own kind's label; the weed edge by the weed
# report's own words.
PIN_TYPES: dict[str, str] = {
    "spot": "Spot",
    "feature": "Shoreline or depth feature",
    "getting_there": "Getting there",
    "weed_report": "Weed edge you reported",
}
# `meta.notes` on the sections list when no river section is built.
SECTIONS_NOT_BUILT = (
    "No river sections are built yet, so there are no sections to draw. "
    "The sections are made when the explore map's rivers are built."
)
# A water with no outline gets a label and no shape; this is the line under it.
SECTION_LABEL_ONLY_NOTE = "No outline is on file for this water, so it is a name on the map only."
# The weed edges counted on a section are the caller's own, said where they are counted.
SECTION_WEED_COUNT_NOTE = "Weed edges you reported. Only you see them."

# How a count reads in a section's one-line `summary`: (one, many). Spot kinds in
# KIND_ORDER first, then shoreline and depth features, then the caller's weed edges.
SECTION_COUNT_PHRASES: dict[str, tuple[str, str]] = {
    "river_mouth": ("river mouth", "river mouths"),
    "creek_junction": ("place where creeks join", "places where creeks join"),
    "below_dam": ("spot below a dam", "spots below dams"),
    "public_fishing": ("public fishing spot", "public fishing spots"),
    "quiet_water": ("quiet-water spot", "quiet-water spots"),
    "inlet": ("inlet", "inlets"),
    "outlet": ("outlet", "outlets"),
    "island": ("island", "islands"),
    "point": ("point", "points"),
    "peninsula": ("peninsula", "peninsulas"),
    "drop_off": ("drop-off", "drop-offs"),
    "hump": ("hump", "humps"),
    "weed_report": ("weed edge you reported", "weed edges you reported"),
}
# Shown when a section has nothing counted in it.
SECTION_EMPTY_SUMMARY = "Nothing mapped here yet"


def section_summary(
    spot_counts: dict[str, int], feature_counts: dict[str, int], weed_reports: int
) -> str:
    """A section's counts in words: '9 spots below dams, 4 places where creeks join'."""
    parts: list[str] = []
    ordered = [(k, spot_counts.get(k, 0)) for k in KIND_ORDER]
    ordered += [(k, feature_counts.get(k, 0)) for k in FEATURE_KIND_ORDER]
    ordered.append(("weed_report", weed_reports))
    for key, n in ordered:
        if n:
            one, many = SECTION_COUNT_PHRASES[key]
            parts.append(f"{n} {one if n == 1 else many}")
    return ", ".join(parts) if parts else SECTION_EMPTY_SUMMARY


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

# ── quiet water ───────────────────────────────────────────────────────────────
# Quiet water is a reach with few public records, found by the explore ranking.
# Its titles name the water; a reach with no name of its own is titled by the
# nearest named water, with "near", never by a type word alone.
QUIET_TITLES = {
    "named": "A quiet part of {name}",
    "near": "Quiet water near {name}",
}
# The three caveats a quiet-water spot can carry. "records" always applies;
# exactly one of the access ones applies when access was not measured.
CAVEAT_KINDS = ("records", "access")
QUIET_RECORDS_CAVEAT = (
    "Few public records here can mean few anglers, or just few observers. "
    "It says nothing about whether fish are there."
)
ACCESS_NOT_MEASURED = (
    "Access not measured here: our road and parking map does not reach this far, "
    "so there is no access figure for this water."
)
ACCESS_COVERAGE_UNKNOWN = (
    "We cannot say whether access was measured here: the stored access figures "
    "predate the record of where roads and parking were mapped."
)
# `meta.notes` on the spots list, when quiet water was asked for and none is built.
QUIET_WATER_NOT_BUILT = (
    "No quiet water is built yet. It needs the explore scores for this area, "
    "which have not been worked out."
)


def caveats_for(kind: str, signals: dict) -> list[dict]:
    """The caveats that travel with a spot, as `{kind, text}`; empty for most kinds.

    Quiet water always carries the records caveat (fewer records is not fewer
    fish). It adds an access caveat unless the access figure behind it was
    measured: `access_is_measured` False means the reach is outside the mapped
    footprint, None means the cached scores predate coverage tracking. Those are
    different facts with different remedies, so they read differently.
    """
    if kind != "quiet_water":
        return []
    out = [{"kind": "records", "text": QUIET_RECORDS_CAVEAT}]
    measured = signals.get("access_is_measured")
    if measured is False:
        out.append({"kind": "access", "text": ACCESS_NOT_MEASURED})
    elif measured is None:
        out.append({"kind": "access", "text": ACCESS_COVERAGE_UNKNOWN})
    return out


def labels_payload() -> dict:
    """The whole vocabulary as JSON: what `GET /map/spots/labels` serves."""
    return {
        "kinds": [
            {
                "kind": kind,
                "chip": KINDS[kind]["chip"],
                "one_liner": KINDS[kind]["one_liner"],
                "distinct": KINDS[kind]["distinct"],
                "term_ids": list(terms.SPOT_KIND_TERMS[kind]),
                "why_fish_gather": {
                    "text": translate.WHY_FISH_GATHER[kind],
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
            {
                "kind": kind,
                **FEATURE_KINDS[kind],
                "term_ids": list(terms.SHORELINE_KIND_TERMS[kind]),
            }
            for kind in FEATURE_KIND_ORDER
        ],
        "shoreline_derivations": FEATURE_DERIVATION_LABELS,
        "new_to_you": {
            "title": NEW_TO_YOU_TITLE,
            "hook_one": NEW_TO_YOU_HOOK_ONE,
            "hook_many": NEW_TO_YOU_HOOK_MANY,
            "tap_prompt": NEW_TO_YOU_TAP_PROMPT,
            "note": NEW_TO_YOU_NOTE,
        },
        "filters": {"new_to_me": NEW_TO_ME_FILTER},
        "sections": {
            "kinds": [{"kind": k, **v} for k, v in SECTION_KINDS.items()],
            "pin_types": PIN_TYPES,
            "count_phrases": {
                k: {"one": one, "many": many} for k, (one, many) in SECTION_COUNT_PHRASES.items()
            },
            "empty_summary": SECTION_EMPTY_SUMMARY,
            "label_only_note": SECTION_LABEL_ONLY_NOTE,
            "weed_count_note": SECTION_WEED_COUNT_NOTE,
            "not_built": SECTIONS_NOT_BUILT,
        },
        "bathymetry_data_note": BATHYMETRY_DATA_NOTE,
        "quiet_water": {
            "titles": QUIET_TITLES,
            "caveats": {
                "records": QUIET_RECORDS_CAVEAT,
                "access_not_measured": ACCESS_NOT_MEASURED,
                "access_coverage_unknown": ACCESS_COVERAGE_UNKNOWN,
            },
            "not_built": QUIET_WATER_NOT_BUILT,
        },
        "layers": LAYERS,
        "access_labels": ACCESS_LABELS,
        # Every map/habitat concept, with plain and technical wording and a
        # factual definition. Which wording to show is the profile's call
        # (GET /profile/presentation), never this payload's.
        "terms": terms.terms_payload(),
    }


def all_strings() -> list[str]:
    """Every user-facing string above, flat — for the jargon lint test."""
    out: list[str] = [GENERAL_REASONING_LABEL, RIVER_MOUTH_SUBLINE]
    for entry in KINDS.values():
        out += [entry["chip"], entry["one_liner"]]
    out += list(translate.WHY_FISH_GATHER.values())
    out += list(ACCESS_LABELS.values())
    for entry in WATER_BODY_TYPES.values():
        out += list(entry.values())
    out += list(NAME_SOURCE_LABELS.values()) + [NO_OUTLINE_NOTE]
    for entry in FEATURE_KINDS.values():
        out += list(entry.values())
    out += list(FEATURE_DERIVATION_LABELS.values())
    out += [FEATURE_READ_FROM_OUTLINE, FEATURE_READ_FROM_MAP, BATHYMETRY_DATA_NOTE]
    for layer in LAYERS.values():
        out += [layer["label"], layer["note"]]
    out += [
        NEW_TO_YOU_TITLE,
        NEW_TO_YOU_HOOK_ONE,
        NEW_TO_YOU_HOOK_MANY,
        NEW_TO_YOU_TAP_PROMPT,
        NEW_TO_YOU_NOTE,
        *NEW_TO_ME_FILTER.values(),
    ]
    out += list(TITLES.values()) + list(UNNAMED_TRIBUTARY.values()) + [UNNAMED_LARGER_RIVER]
    out += list(QUIET_TITLES.values())
    for entry in SECTION_KINDS.values():
        out += list(entry.values())
    out += list(PIN_TYPES.values())
    out += [
        SECTIONS_NOT_BUILT,
        SECTION_LABEL_ONLY_NOTE,
        SECTION_WEED_COUNT_NOTE,
        SECTION_EMPTY_SUMMARY,
    ]
    for one, many in SECTION_COUNT_PHRASES.values():
        out += [one, many]
    out += [
        QUIET_RECORDS_CAVEAT,
        ACCESS_NOT_MEASURED,
        ACCESS_COVERAGE_UNKNOWN,
        QUIET_WATER_NOT_BUILT,
    ]
    return out
