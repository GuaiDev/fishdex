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

# Placeholder name for the match indicator. Its final name and UI structure
# are an open decision; every word it uses lives in this module.
MATCH_INDICATOR_LABEL = "Conditions match (placeholder)"


# -- the match indicator (GET /pins/{id}/match, and the Today view's match sort) --
#
# The match compares today's readings with the conditions on the trips the user
# rated best at this pin. It is arithmetic over the user's own trips: no model,
# no other angler's data, no prediction. These numbers are judgements, not
# measurements, and are meant to be changed here.

# The user's best trips here are the BEST_TRIP_COUNT highest-rated ones. Trips
# tied at the cut-off rating are all kept, so the choice never depends on row
# order. A trip below MIN_BEST_RATING is never a "best" trip, however few others
# there are: a pin where every trip was slow has no good day to match against.
BEST_TRIP_COUNT = 3
MIN_BEST_RATING = 3

# A condition is compared only when at least this many of the best trips
# recorded it (a range from one trip is a single point), and the match speaks
# only when at least MIN_COMPARED_CONDITIONS of today's readings could be
# compared. The same number of best trips is needed before there is anything
# to compare against.
MIN_TRIPS_TO_COMPARE = 2
MIN_COMPARED_CONDITIONS = 2

# A reading just outside the best trips' range, by no more than the margin,
# counts as close to it. Absolute margins are in the condition's own unit; a
# relative margin is a fraction of the nearer end of the range (flow spans
# orders of magnitude, so a fixed number would mean nothing across rivers).
#
# `column` is the session_conditions column that holds the same reading as it
# stood on the trip. `source` says which staleness rule and which extra check
# the history needs:
#   weather       taken for the trip's own time; nothing more to check
#   gauge         only trips whose snapshot came from the gauge reading today
#                 are comparable: a flow from another station is another river
#   water_sample  a dated grab sample; only trips with a sample taken within
#                 WATER_QUALITY_STALE_S of the trip count
MATCH_VARIABLES: tuple[dict, ...] = (
    {"field": "air_temp_c", "column": "air_temp_c", "source": "weather", "margin": 2.0},
    {"field": "wind_speed_kmh", "column": "wind_speed_kmh", "source": "weather", "margin": 5.0},
    {"field": "pressure_hpa", "column": "pressure_hpa", "source": "weather", "margin": 3.0},
    {"field": "flow_cms", "column": "flow_cms", "source": "gauge", "relative_margin": 0.25},
    {"field": "level_m", "column": "level_m", "source": "gauge", "margin": 0.05},
    {"field": "water_temp_c", "column": "water_temp_c", "source": "water_sample", "margin": 1.5},
    {"field": "dissolved_oxygen_mgl", "column": "do_mgl", "source": "water_sample", "margin": 1.0},
    {"field": "ph", "column": "ph", "source": "water_sample", "margin": 0.3},
)

# Share of the compared conditions that must be inside (or close to) the best
# trips' range for each wording.
MATCH_SIMILAR_FRACTION = 1.0
MATCH_PARTLY_FRACTION = 0.5

# What the match says, by status. Neutral on purpose: it describes how today
# compares with the user's own good days, and does not promise a bite.
MATCH_STATUS_LABELS: dict[str, str] = {
    "similar": "Similar to your best trips",
    "partly": "Partly similar to your best trips",
    "different": "Unlike your best trips",
    "not_enough_readings": "Not enough readings to compare",
    "few_good_trips": "Too few good trips to compare",
}

# One sentence per status. {inside} of {compared} counts conditions inside or
# close to the range of the best trips.
MATCH_HEADLINES: dict[str, str] = {
    "similar": "Today looks like your best trips here: {inside} of {compared} conditions "
    "compared are in their range.",
    "partly": "Today partly looks like your best trips here: {inside} of {compared} "
    "conditions compared are in their range.",
    "different": "Today is unlike your best trips here: {inside} of {compared} conditions "
    "compared are in their range.",
    "not_enough_readings": "Too few of today's readings can be compared with your best "
    "trips here to say.",
    "few_good_trips": "Only {good} of your {rated} rated trips here were rated {rating} or "
    "better, which is too few to compare against.",
}

# The pin has fewer rated trips than MIN_RATED_TRIPS_FOR_MATCH: no match, only
# the conditions panel. {needed} is how many more rated trips would show it.
MATCH_HIDDEN_HEADLINE = "No match yet: {needed} more rated {trips} here would show one."

MATCH_BEST_TRIPS_SUMMARY = "Your {n} best trips here were in {months}."
MATCH_BEST_TRIPS_SUMMARY_NO_MONTHS = "Your {n} best trips here."
MATCH_BEST_TRIP_SUMMARY = "Your best trip here was in {months}."
MATCH_BEST_TRIP_SUMMARY_NO_MONTHS = "Your best trip here."

# {range} is "8–12°C", or a single value when the best trips agree.
MATCH_COMPARISON_LINE = "{label}: {range} on those trips, {current} now ({position})."

MATCH_POSITION_LABELS: dict[str, str] = {
    "inside": "inside that range",
    "near": "close to that range",
    "below": "below that range",
    "above": "above that range",
}

# Why a condition was left out of the comparison.
MATCH_LEFT_OUT_REASONS: dict[str, str] = {
    "stale": "today's reading is out of date",
    "unavailable": "no current reading",
    "too_few_trips": "too few of your best trips recorded it",
    "other_gauge": "your best trips used a different gauge",
    "old_samples": "no water sample was taken near the time of your best trips",
}
MATCH_LEFT_OUT_LINE = "Not compared: {items}."

MONTH_NAMES: tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


# -- the Today view (GET /pins/today) -------------------------------------------

# The keys the user can sort their pins by. The label names the key in the
# picker; every row also states, in words, why it sits where it does.
TODAY_SORT_LABELS: dict[str, str] = {
    "match": "Conditions match",
    "distance": "Distance",
    "last_visited": "Last visited",
    "favourites": "Favourites first",
}

TODAY_DISTANCE_REASON = "{distance} from here"
TODAY_LAST_VISITED_REASON = "Last fished {when}"
TODAY_NEVER_VISITED_REASON = "No logged trips here yet"
TODAY_FAVOURITE_REASON = "Marked as a favourite"
TODAY_NOT_FAVOURITE_REASON = "Not a favourite"


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
        "match": {
            "best_trip_count": BEST_TRIP_COUNT,
            "min_best_rating": MIN_BEST_RATING,
            "min_trips_to_compare": MIN_TRIPS_TO_COMPARE,
            "min_compared_conditions": MIN_COMPARED_CONDITIONS,
            "status_labels": MATCH_STATUS_LABELS,
            "position_labels": MATCH_POSITION_LABELS,
            "left_out_reasons": MATCH_LEFT_OUT_REASONS,
            "fields": [v["field"] for v in MATCH_VARIABLES],
        },
        "today_sort_labels": TODAY_SORT_LABELS,
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
