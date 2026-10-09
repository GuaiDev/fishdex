"""Quebec sportfishing regulations — STUB (no machine-readable source exists).

Checked 2026-10 for a current official API, WFS/ArcGIS REST service, or
CSV/XLSX/JSON download carrying Quebec's sport-fishing rules (zone limits,
seasons, size and bait rules). None exists:

  * quebec.ca "Printable versions of the fishing rules"
    https://www.quebec.ca/en/tourism-recreation-sport/sporting-and-outdoor-activities/sport-fishing/printable-versions
    links only PDFs: a general-rules PDF, a salmon-rivers map, and one
    ``carte-zone-peche-zone-NN-en.pdf`` map per fishing zone. Nothing else.
  * Données Québec (CKAN package_search for "pêche", "zone de pêche", "zonage pêche",
    "règlement de pêche", "pêche sportive", "limites de capture", "poisson"):
    no regulation or fishing-zone dataset. The nearest hits are not rules —
    "Guide de consommation du poisson de pêche sportive en eau douce" (mercury
    advisories), "Territoires fauniques structurés" (ZEC/pourvoirie boundaries),
    "Faune aquatique exotique envahissante" and "Aires de répartition — faune".
  * geo.environnement.gouv.qc.ca ArcGIS REST (Biodiversite, Reference, Eau folders):
    no fishing-zone or regulation layer.

The rules are published as PDFs and maps. The project does not scrape PDFs or
map-only portals, so this stays a stub until the province publishes the rules as
data. Revisit by re-running the package searches above.

Quebec zone identifiers are numeric (Zone 1 through Zone 29, plus the salmon
rivers). Whatever adapter replaces this should write to ``regulation_chunks``
(shared schema) with jurisdiction='CA-QC' and the zone number as the integer
zone, text stored as published (French and English both exist).
"""

import logging

logger = logging.getLogger(__name__)


def fetch_regulations() -> list[dict]:
    """Stub — returns an empty list and says why."""
    logger.warning(
        "QC regulations: no machine-readable source exists (quebec.ca and Données Québec "
        "publish the rules only as PDFs and maps) — returning 0 chunks. "
        "See the module docstring for what was checked."
    )
    return []
