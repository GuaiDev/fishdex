"""Manitoba water quality — STUB.

No provincial physico-chemical water-quality layer with machine-readable
dissolved oxygen / pH / temperature / conductivity was found as of 2026.
Sources checked (all live, all rejected for the reason given):

  1. Manitoba Government ArcGIS Online
       "Beach Water Quality Monitoring Sample Results"
       https://services.arcgis.com/mMUesHYPkXjaFGfS/arcgis/rest/services/
       Beach_Water_Quality_Monitoring_Sample_Results_view/FeatureServer/0
     Real, queryable, point geometry. Measures E. coli density, microcystin
     and blue-green algae counts at recreational beaches, with advisory flags.
     NOT wired in: these are public-health advisory parameters, not the
     DO/pH/temperature/conductivity habitat constraints the shared
     `water_quality_readings` schema and the context layer's water slice model.
     Storing them would put NULLs in every chemistry column and imply a
     habitat reading that was never taken.

  2. Manitoba Sustainable Development surface-water quality programmes collect
     stream/lake chemistry, but publish it through a request form rather than a
     bulk/API endpoint.

  3. DataStream (ca_national adapter) covers the Lake Winnipeg basin and is the
     practical source for Manitoba water quality — use
     src/ingest/jurisdictions/ca_national/datastream_water_quality.py.

Table: water_quality_readings (shared schema).
"""

import logging

logger = logging.getLogger(__name__)


def fetch_water_quality_readings(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[dict]:
    """Stub — returns an empty list with a warning.

    Manitoba has no publicly queryable physico-chemical water-quality layer;
    see the module docstring for the sources checked and the alternatives.
    """
    logger.warning(
        "MB water_quality: no public DO/pH/temp/conductivity layer as of 2026 — "
        "returning 0 readings. Beach monitoring exists but is E. coli/microcystin "
        "(public-health), not habitat chemistry. Use DataStream "
        "(ca_national/datastream_water_quality.py) for the Lake Winnipeg basin."
    )
    return []
