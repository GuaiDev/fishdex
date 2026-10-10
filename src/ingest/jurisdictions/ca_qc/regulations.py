"""Quebec sportfishing regulations — PDF ingestion STUB.

TODO: Implement PDF download and parse.

Quebec publishes annual fishing regulations in both French and English.
Find the current year's PDF at:
  https://www.quebec.ca/en/tourism-recreation-sport/sporting-and-outdoor-activities/sport-fishing/printable-versions

The general rules PDF covers all of Quebec; zone-specific supplements cover
individual zones (1–29 in the southern regulation scheme, plus northern zones).

Implementation plan (same pattern as MNRF regulations adapter):
  1. Fetch the quebec.ca printable-versions page
  2. Extract the PDF link for "General rules" with regex or BeautifulSoup
  3. Download PDF, cache 365 days
  4. Parse with pdfplumber — split by zone number or section headers
  5. Write to regulation_chunks with jurisdiction='CA-QC', regulation_year=YEAR

Quebec zone identifiers are numeric (Zone 1 through Zone 29 for southern zones,
plus distinct northern/salmon zones). Use zone number as the integer zone field.

Table: regulation_chunks (shared schema)
"""

import logging

logger = logging.getLogger(__name__)


def fetch_regulations() -> list[dict]:
    """Stub — returns empty list with a TODO warning.

    See module docstring for implementation plan.
    TODO: https://www.quebec.ca/en/tourism-recreation-sport/sporting-and-outdoor-activities/sport-fishing/printable-versions
    """
    logger.warning(
        "QC regulations: adapter not yet implemented — returning 0 chunks. "
        "Find the current-year PDF at "
        "https://www.quebec.ca/en/tourism-recreation-sport/sporting-and-outdoor-activities/"
        "sport-fishing/printable-versions and implement using ca_on/regulations.py as the pattern."
    )
    return []
