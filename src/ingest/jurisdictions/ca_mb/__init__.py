# Manitoba (CA-MB)
#
# Province-specific adapters:
#   Stocking  → ca_mb/stocking.py
#       Manitoba Waterbody Stocking Records (ArcGIS FeatureServer, with coords)
#   Hydro     → ca_mb/hydro_network.py
#       WaLLAS Reference "Watercourses (NTS 1:20,000)" (ArcGIS MapServer layer 18)
#
# Covered by federal/global sources:
#   iNaturalist, GBIF, WSC gauges, OSM, eBird    → src/ingest/global/
#   CABIN benthic (all provinces)                 → src/ingest/jurisdictions/ca_on/benthic.py
#   DataStream water quality (Lake Winnipeg basin)
#       → src/ingest/jurisdictions/ca_national/datastream_water_quality.py
#   DFO SAR critical habitat
#       → src/ingest/jurisdictions/ca_national/dfo_critical_habitat.py
#
# Still stubbed (no usable machine-readable source found as of 2026):
#   Water quality → ca_mb/water_quality.py
#       MB beach monitoring (E. coli/microcystin) exists but is public-health,
#       not habitat chemistry; provincial stream/lake chemistry is by request.
#   Fish observations — no provincial fish-observation layer:
#       * "Manitoba Waterbody Data" FeatureServer has an empty SPECIES field.
#       * Manitoba Wildlife Atlas is an internal database with no public API.
#       * The DFO Lake Winnipeg trawl survey is federal, out of province scope.
#   Regulations: https://www.gov.mb.ca/sd/fish_and_wildlife/fishing/index.html (PDF)
