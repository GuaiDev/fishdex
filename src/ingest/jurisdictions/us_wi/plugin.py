"""Wisconsin (US-WI) — named stub: a bounding box and a name, no adapters yet."""

from src.jurisdictions.plugin import BBox, JurisdictionPlugin

PLUGIN = JurisdictionPlugin(
    code="US-WI",
    name="Wisconsin",
    country="US",
    bbox=BBox(42.5, 47.1, -92.9, -86.2, priority=60),
    status_authorities=("US ESA",),
)
