"""New York (US-NY) — named stub: a bounding box and a name, no adapters yet."""

from src.jurisdictions.plugin import BBox, JurisdictionPlugin

PLUGIN = JurisdictionPlugin(
    code="US-NY",
    name="New York",
    country="US",
    bbox=BBox(40.5, 45.0, -79.8, -71.8, priority=30),
    status_authorities=("US ESA",),
)
