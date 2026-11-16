"""Ohio (US-OH) — named stub: a bounding box and a name, no adapters yet."""

from src.jurisdictions.plugin import BBox, JurisdictionPlugin

PLUGIN = JurisdictionPlugin(
    code="US-OH",
    name="Ohio",
    country="US",
    bbox=BBox(38.4, 42.3, -84.8, -80.5, priority=40),
    status_authorities=("US ESA",),
)
