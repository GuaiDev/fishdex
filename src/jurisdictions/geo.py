"""Bounding-box jurisdiction lookup for coordinate tagging at ingest time.

The boxes come from each jurisdiction plugin's `bbox` (see
src/jurisdictions/plugin.py) and are checked in `BBox.priority` order, so the
first match wins. CA-ON and US states come first since that's home, then
western/Maritime Canadian provinces (smallest/most specific boxes), then CA-QC
last since its box is broad enough to swallow the Maritimes' longitude range if
checked first. Overlaps (e.g. Great Lakes border zones) resolve to whichever
box has the lower priority number.

A jurisdiction without a plugin bbox is tagged 'UNKNOWN' by every global
adapter (iNaturalist, GBIF, OSM, weather, WSC, eBird), regardless of what the
jurisdiction-specific adapters correctly hardcode.
"""

from src.jurisdictions.registry import bboxes


def jurisdiction_for_coords(lat: float, lng: float) -> str:
    for code, box in bboxes():
        if box.contains(lat, lng):
            return code
    return "UNKNOWN"
