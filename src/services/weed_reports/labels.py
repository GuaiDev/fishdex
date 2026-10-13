"""Every tunable number and every user-facing word for weed reports.

One module on purpose. `GET /map/weed-reports/labels` serves it to the web app,
so the UI holds no copies of its own. A weed report is the user's own sighting,
so the one thing every read says is that it is reported and not verified.
"""

# How far a report may sit from the water body's drawn shape and still count as
# on it. A river or creek is drawn as a simplified centre line, so a bank-side
# edge is a stretch away from it; a lake outline is a polygon, so anything
# inside is on it and this only forgives a surveyed shoreline that is slightly off.
NEAR_LINE_M = 150
NEAR_OUTLINE_M = 100

# A weed edge is along a shore or a bank. A "line" longer than this is a
# mistake (or a different thing), not an edge.
MAX_LINE_LENGTH_M = 10_000

KIND_LABEL = "Weed edge"
# On every report, on the map layer and in lists. Said the same way everywhere.
STATUS_LABEL = "reported, not verified"
PROVENANCE_LABEL = "Reported by you"
LAYER_LABEL = "Your weed edges"
LAYER_NOTE = (
    "Weed edges you reported. Only you can see them. They are your sightings, "
    "not checked against anything else."
)
SHAPE_LABELS: dict[str, str] = {
    "Point": "A spot on the edge",
    "LineString": "A line along the edge",
}


def date_label(observed_on: str) -> str:
    """'seen 2026-10-04'. The date as the person gave it, never reworded."""
    return f"seen {observed_on}"


def caption(observed_on: str) -> str:
    """The one line a map pin or a list row shows."""
    return f"{KIND_LABEL} · {STATUS_LABEL} · {date_label(observed_on)}"


def labels_payload() -> dict:
    """The constants and labels the web app needs, as one JSON object."""
    return {
        "kind_label": KIND_LABEL,
        "status_label": STATUS_LABEL,
        "provenance_label": PROVENANCE_LABEL,
        "layer": {"label": LAYER_LABEL, "note": LAYER_NOTE, "default_on": True},
        "shape_labels": SHAPE_LABELS,
        "near_line_m": NEAR_LINE_M,
        "near_outline_m": NEAR_OUTLINE_M,
        "max_line_length_m": MAX_LINE_LENGTH_M,
    }
