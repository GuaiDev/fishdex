"""Michigan (US-MI) — named stub: a bounding box and a name, no adapters yet."""

from src.jurisdictions.plugin import BBox, JurisdictionPlugin

PLUGIN = JurisdictionPlugin(
    code="US-MI",
    name="Michigan",
    country="US",
    bbox=BBox(41.7, 48.3, -90.4, -82.1, priority=20),
    status_authorities=("US ESA",),
)
