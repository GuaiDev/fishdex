"""Pennsylvania (US-PA) — named stub: a bounding box and a name, no adapters yet."""

from src.jurisdictions.plugin import BBox, JurisdictionPlugin

PLUGIN = JurisdictionPlugin(
    code="US-PA",
    name="Pennsylvania",
    country="US",
    bbox=BBox(39.7, 42.3, -80.5, -74.7, priority=70),
    status_authorities=("US ESA",),
)
