"""Minnesota (US-MN) — named stub: a bounding box and a name, no adapters yet."""

from src.jurisdictions.plugin import BBox, JurisdictionPlugin

PLUGIN = JurisdictionPlugin(
    code="US-MN",
    name="Minnesota",
    country="US",
    bbox=BBox(43.5, 49.4, -97.2, -89.5, priority=50),
    status_authorities=("US ESA",),
)
