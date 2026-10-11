"""Every tunable number and every user-facing word for Personal-map pins.

One module on purpose. The match indicator's name and layout are still an
open decision, and the rating anchors may be reworded after use on the water;
when they change, they change here and nowhere else. `GET /pins/labels`
serves this module to the web app, so the UI holds no copies of its own.
"""

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
    }
