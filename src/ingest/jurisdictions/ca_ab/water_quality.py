"""Alberta water quality — STUB.

TODO: Implement when a public API becomes available.

The Alberta Environmental Monitoring, Evaluation and Reporting Agency (AEMERA)
and Alberta Environment and Protected Areas operate a Water Quality Monitoring
Network, but data is only accessible through a map-based web portal:
  https://environment.extranet.gov.ab.ca/apps/WaterQuality/dataportal/

No public REST API or bulk download is available as of 2026.

Alternative data sources for Alberta water quality:
  - WSC stream gauges (global adapter) — flow data for major rivers
  - DataStream (ca_national/datastream_water_quality.py) — covers some AB watersheds
  - CABIN benthic (expanded ca_on/benthic.py) — proxy for habitat quality

State (re-checked 2026-10-09): AEMERA portal is map-only; open.alberta.ca has
no surface-water chemistry dataset; the federal national surface-water quality
dataset holds only 13 AB sites, none near the configured AB areas. Remaining a
stub until a machine-readable provincial source appears.

Table: water_quality_readings (shared schema)
"""

import logging

logger = logging.getLogger(__name__)


def fetch_water_quality_readings(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[dict]:
    """Stub — returns empty list with a TODO warning."""
    logger.warning(
        "AB water_quality: no public API available as of 2026 — returning 0 readings. "
        "Portal (map-based, no public API): "
        "https://environment.extranet.gov.ab.ca/apps/WaterQuality/dataportal/ "
        "Re-checked 2026-10-09: open.alberta.ca has no surface-water chemistry; the "
        "federal national dataset has only 13 AB sites, none near the AB areas. "
        "Consider DataStream (ca_national/) for AB watershed coverage."
    )
    return []
