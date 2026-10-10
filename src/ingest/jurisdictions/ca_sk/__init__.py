# Saskatchewan (CA-SK)
#
# Province-specific adapters:
#   Hydro        → ca_sk/hydro_network.py
#       SaskGIS Hydrography watercourse layer (ArcGIS MapServer 121)
#   Fish obs     → ca_sk/fish_observations.py
#       SK Fish Atlas (FishAtlasHabiSask) joined to Species Info points
#   Stocking     → ca_sk/stocking.py
#       SK Fish Stocking History joined to Species Info points
#   Water quality → ca_sk/water_quality.py
#       Long Term Water Chemistry (historical, WSA)
#
# Covered by federal/global sources:
#   iNaturalist, GBIF, WSC gauges, OSM, eBird    → src/ingest/global/
#   CABIN benthic (all provinces)                 → src/ingest/jurisdictions/ca_on/benthic.py
#   DataStream water quality
#       → src/ingest/jurisdictions/ca_national/datastream_water_quality.py
#   DFO SAR critical habitat
#       → src/ingest/jurisdictions/ca_national/dfo_critical_habitat.py
#
# Not yet built (as of 2026):
#   Regulations — published as a PDF guide only:
#       https://www.saskatchewan.ca/residents/environment-public-health-and-safety/
#       fish-and-wildlife/fish/fishing-regulations
