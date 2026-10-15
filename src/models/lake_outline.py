"""The outline of one curated lake, matched from OHN Waterbody polygons.

A lake name in the curation file ("Rice Lake", "Lake Ontario") is resolved to
the polygon — or set of polygons — that draws it, by position and never by name
alone (names repeat across unrelated water). The result carries everything the
map needs to say where the shape came from: the source OGF_IDs, the OHN name,
the record's effective date, and the licence.
"""

from dataclasses import dataclass, field

# How the polygon was chosen for the curated name, recorded so a weak match is
# visible rather than silently published.
#   name_match   an OHN polygon's stripped name equals the curated name
#   no_match     nothing near a mouth carries the name, so no outline is drawn. A bay
#                is not drawn as the lake it opens off.
MatchBasis = str

# an OHN polygon matched to a lake by name, unioned for the Great Lakes.
MATCH_NAME = "name_match"
MATCH_NONE = "no_match"


@dataclass(frozen=True)
class LakeOutline:
    """One curated lake's outline, or the reason it has none."""

    curated_name: str
    matched: bool
    geometry: dict | None = None
    """GeoJSON Polygon or MultiPolygon, unioned when the lake is sliced in the source."""
    ogf_ids: list[int] = field(default_factory=list)
    ohn_name: str | None = None
    effective_datetime: str | None = None
    match_basis: MatchBasis = MATCH_NONE
    distance_m: float | None = None
    """Approximate metres from the nearest curated mouth to the outline (0 if inside)."""
    licence: str = ""
    licence_url: str = ""
    note: str = ""
    lookup_failed: bool = False
    """An upstream fetch for this lake failed (timeout, error body). The outline is then empty
    or only partly widened, which says nothing about the lake: re-running the build retries
    only what failed, because every fetch that succeeded is cached."""
