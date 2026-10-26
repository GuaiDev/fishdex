"""Every tunable number and every user-facing word for Personal-map pins.

One module on purpose. The match indicator's name and layout are still an
open decision, and the rating anchors may be reworded after use on the water;
when they change, they change here and nowhere else. `GET /pins/labels`
serves this module to the web app, so the UI holds no copies of its own.
"""

from src.models.freshness import GAUGE_STALE_S, WATER_QUALITY_STALE_S, WEATHER_STALE_S

# A logged stop links to a pin by itself only when exactly one of the user's
# pins lies within this distance. With two or more, the user picks; with none,
# nothing links.
LINK_RADIUS_M = 150

# The match indicator (phase 3) is shown only once a pin has at least this
# many rated trips. Below it, the pin shows its conditions and nothing else.
MIN_RATED_TRIPS_FOR_MATCH = 3

# The optional post-trip rating. Stored as the integer key; NULL means the
# user skipped it, which is never read as a low rating.
RATING_LABELS: dict[int, str] = {
    1: "skunked",
    2: "slow",
    3: "decent",
    4: "great",
    5: "best of the year",
}

STATE_LABELS: dict[str, str] = {
    "saved": "Saved",
    "visited": "Visited",
}

# How each link to a pin is described next to the trip it links. "recalled"
# is not a pin_links row: it marks an "I've fished here before" record.
LINK_KIND_LABELS: dict[str, str] = {
    "confirmed": "linked by you",
    "inferred": "linked automatically",
    "recalled": "recalled",
}

# Shown on every recalled record and on a pin whose only history is recalled.
RECALLED_MARKER = "recalled"

# Placeholder name for the phase-3 match indicator. Its final name and UI
# structure are an open decision; nothing renders it yet.
MATCH_INDICATOR_LABEL = "Conditions match (placeholder)"


# -- the conditions panel (GET /pins/{id}/conditions) ---------------------------
#
# Each row of the panel is one field of the context layer's conditions slice.
# Order here is display order. `group` ties rows into the four things the panel
# is about; the unit is what the number is in, None for words.
CONDITION_FIELDS: tuple[dict, ...] = (
    {"field": "air_temp_c", "label": "Air temperature", "unit": "°C", "group": "weather"},
    {"field": "wind_speed_kmh", "label": "Wind", "unit": "km/h", "group": "weather"},
    {"field": "precipitation_mm", "label": "Precipitation", "unit": "mm", "group": "weather"},
    {"field": "pressure_hpa", "label": "Air pressure", "unit": "hPa", "group": "weather"},
    {"field": "pressure_trend", "label": "Pressure trend", "unit": None, "group": "weather"},
    {"field": "flow_cms", "label": "Flow", "unit": "m³/s", "group": "water_flow"},
    {"field": "flow_trend", "label": "Flow trend", "unit": None, "group": "water_flow"},
    {"field": "level_m", "label": "Water level", "unit": "m", "group": "water_flow"},
    {"field": "level_trend", "label": "Level trend", "unit": None, "group": "water_flow"},
    {
        "field": "water_temp_c",
        "label": "Water temperature",
        "unit": "°C",
        "group": "water_temperature",
    },
    {
        "field": "dissolved_oxygen_mgl",
        "label": "Dissolved oxygen",
        "unit": "mg/L",
        "group": "water_quality",
    },
    {"field": "ph", "label": "pH", "unit": None, "group": "water_quality"},
)

# A gauge row is named for the water the gauge is on, never for the pin's.
# `{water}` is the station's own river or creek. When that is not the pin's
# water the distance joins the label, so the row cannot be read as the pin's
# river: "Flow on Cooksville Creek, 6.4 km away". Without a water name the
# plain label above stays and the station name carries the identification.
GAUGE_WATER_LABELS: dict[str, str] = {
    "flow_cms": "Flow on {water}",
    "flow_trend": "Flow trend on {water}",
    "level_m": "Water level on {water}",
    "level_trend": "Level trend on {water}",
}
GAUGE_DISTANCE_SUFFIX = ", {km:g} km away"

CONDITION_GROUP_LABELS: dict[str, str] = {
    "weather": "Weather",
    "water_flow": "Water level and flow",
    "water_temperature": "Water temperature",
    "water_quality": "Water quality",
}

# Why a row has no value, in the user's words. Every EmptyReason has one: a row
# never falls back to a bare "no data".
EMPTY_REASON_LABELS: dict[str, str] = {
    "no_records_in_radius": "Nothing recorded near this pin",
    "user_never_fished_here": "You have not logged a trip here",
    "web_search_empty": "A web search found nothing",
    "source_does_not_cover_area": "This data source does not cover this area",
    "live_lookup_failed": "The live lookup could not be reached just now. Try again",
    "field_not_populated_by_source": "The source has a reading here but not this value",
    "suppressed_on_conservation_grounds": "Withheld on conservation grounds",
    "recorded_but_not_decision_relevant": "Measured, but it would not change your plan",
    "no_live_source": "No live source exists, and no dated sample is on record nearby",
}

# Words for the trend rows. The gauge says "stable"; the panel says "steady".
# There is no "normal": a normal needs a seasonal baseline we do not hold.
TREND_LABELS: dict[str, str] = {"rising": "Rising", "falling": "Falling", "steady": "Steady"}

STALE_LABEL = "stale"
STALE_NOTE = "older than this source's limit: shown, but not counted as current"


def stale_limits_s() -> dict[str, int]:
    """How old each source may be before the panel marks it stale, in seconds."""
    return {
        "weather": WEATHER_STALE_S,
        "gauge": GAUGE_STALE_S,
        "water_quality": WATER_QUALITY_STALE_S,
    }


def age_label(age_s: int | None) -> str | None:
    """A reading's age in words: "12 min ago", "3 h ago", "41 days ago"."""
    if age_s is None:
        return None
    if age_s < 90:
        return "just now"
    if age_s < 90 * 60:
        return f"{round(age_s / 60)} min ago"
    if age_s < 48 * 3600:
        return f"{round(age_s / 3600)} h ago"
    return f"{round(age_s / 86400)} days ago"


def labels_payload() -> dict:
    """The constants and labels the web app needs, as one JSON object."""
    return {
        "link_radius_m": LINK_RADIUS_M,
        "min_rated_trips_for_match": MIN_RATED_TRIPS_FOR_MATCH,
        "rating_labels": {str(k): v for k, v in RATING_LABELS.items()},
        "state_labels": STATE_LABELS,
        "link_kind_labels": LINK_KIND_LABELS,
        "recalled_marker": RECALLED_MARKER,
        "match_indicator_label": MATCH_INDICATOR_LABEL,
        "condition_fields": list(CONDITION_FIELDS),
        "gauge_water_labels": GAUGE_WATER_LABELS,
        "gauge_distance_suffix": GAUGE_DISTANCE_SUFFIX,
        "condition_group_labels": CONDITION_GROUP_LABELS,
        "empty_reason_labels": EMPTY_REASON_LABELS,
        "trend_labels": TREND_LABELS,
        "stale_label": STALE_LABEL,
        "stale_note": STALE_NOTE,
        "stale_limits_s": stale_limits_s(),
    }
