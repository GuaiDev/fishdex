"""Pydantic models for the explore map's spots: the unit a person taps.

A spot is a reason to go to one place on the water: where a river runs out into
a lake, where a creek joins a river, the water just below a dam, an official
fishing spot. It is *derived* from the stretch anchors (services/spots.py) and
rebuilt, never edited by hand. Parking, trailheads, boat launches and road
crossings are not spots; they ride along on the nearest ones as `getting_there`
and `bridges`, because access says how to reach water and nothing about fish.

A spot id is `{kind}:{coord_key}`, built from the feature's own coordinates and
nothing else — no stretch id — so a saved spot survives a rebuild, a stretch
rename, or the same place turning up under two stretches.

Spots are located features, not fish predictions. `signals` is a bag of
separate numbers on purpose; `tier` ranks spots for map density only and is not
a quality claim (CLAUDE.md: presence vs. pressure, never one score).
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.models.jurisdiction import JurisdictionCode

SpotKind = Literal["river_mouth", "creek_junction", "below_dam", "quiet_water", "public_fishing"]
SpotTier = Literal[1, 2, 3]
# Kinds the classifier builds from stretch anchors. quiet_water comes from the
# explore() ranking, which is a later phase.
ANCHOR_SPOT_KINDS: tuple[SpotKind, ...] = (
    "river_mouth",
    "creek_junction",
    "below_dam",
    "public_fishing",
)

SpotSourceKind = Literal["ohn_topology", "osm", "curation"]

_ID_PATTERN = re.compile(
    r"^(river_mouth|creek_junction|below_dam|quiet_water|public_fishing):n?\d+_n?\d+$"
)


class SpotSource(BaseModel):
    """Where a spot's facts came from, so a surveyed fact reads apart from a derived one."""

    kind: SpotSourceKind
    ref: str  # "ogf:123,456" (OHN segments), "way/123" (OSM element), "curation:<stretch id>"
    anchor_ids: list[str] = Field(default_factory=list)  # extraction rows merged into the spot
    built_at: str


class AccessRef(BaseModel):
    """A way of reaching a spot: parking, a trailhead, a launch, or a road crossing.

    OpenStreetMap, volunteer-mapped; `distance_m` is straight-line from the spot.
    """

    kind: Literal["parking", "trail_head", "boat_launch", "road_crossing"]
    name: str | None = None
    lat: float
    lng: float
    distance_m: int = Field(ge=0)
    source_ref: str  # the OSM element
    # Tags worth showing: access, fee, capacity, operator, surface, ref ...
    attributes: dict = Field(default_factory=dict)


class Spot(BaseModel):
    """A located reason to go, as stored in `spots` and served to the map."""

    spot_id: str
    kind: SpotKind
    jurisdiction: JurisdictionCode
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    # The river section it sits on — context ("more spots on this river"), not
    # part of the spot's identity. A confluence listed under two stretches keeps
    # one of them here and the other in structure_attrs["also_on_water_ids"].
    water_id: str | None = None
    water_name: str | None = None
    title: str = Field(min_length=1)
    subtitle: str | None = None
    tier: SpotTier
    # Internal, for code and the bot: lake_mouth / confluence / dam / weir /
    # fishing_spot. The user sees the kind's chip, not this.
    structure_type: str
    structure_attrs: dict = Field(default_factory=dict)
    signals: dict = Field(default_factory=dict)
    # Nearest parking, trailheads and boat launches; and nearest road crossings.
    getting_there: list[AccessRef] = Field(default_factory=list)
    bridges: list[AccessRef] = Field(default_factory=list)
    sources: list[SpotSource] = Field(default_factory=list)
    built_at: str

    @field_validator("spot_id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError(f"spot_id must be '<kind>:<lon>_<lat>' coordinate key, got {v!r}")
        return v

    @field_validator("title")
    @classmethod
    def _title_never_unnamed(cls, v: str) -> str:
        if not v.strip() or v.lstrip().lower().startswith("unnamed"):
            raise ValueError("a spot title is built from the named river, never 'Unnamed ...'")
        return v

    @model_validator(mode="after")
    def _id_matches_kind(self):
        if not self.spot_id.startswith(f"{self.kind}:"):
            raise ValueError(f"spot_id {self.spot_id!r} does not start with kind {self.kind!r}")
        return self


# ── build diagnostics ─────────────────────────────────────────────────────────


class SpotBuildReport(BaseModel):
    """What classify() did with its input, so a thin map reads as thin and not as empty.

    The same pattern as AnchorBuildReport: a count that separates "nothing
    there" from "found and filtered" belongs in the return value.
    """

    jurisdiction: JurisdictionCode
    anchors_in: int = 0
    spots_total: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_tier: dict[str, int] = Field(default_factory=dict)
    # Candidates seen and deliberately not published, by reason.
    dropped: dict[str, int] = Field(default_factory=dict)
    # Spots published at tier 3 (hidden by default) because they failed a gate,
    # by reason. Held back, not dropped: a tier 3 spot is still a row.
    held_back: dict[str, int] = Field(default_factory=dict)
    # Titles built from a fallback because the source had no name: a share that
    # grows run over run means the names are thinning, not the water.
    title_fallbacks: dict[str, int] = Field(default_factory=dict)
    # Access anchors attached to spots, and spots with no access found nearby.
    access_anchors: int = 0
    spots_without_access: int = 0

    def drop(self, reason: str, n: int = 1) -> None:
        if n > 0:  # a zero would read as a reason that fired
            self.dropped[reason] = self.dropped.get(reason, 0) + n

    def hold_back(self, reason: str, n: int = 1) -> None:
        if n > 0:  # a zero would read as a reason that fired
            self.held_back[reason] = self.held_back.get(reason, 0) + n

    def fallback(self, reason: str, n: int = 1) -> None:
        if n > 0:  # a zero would read as a reason that fired
            self.title_fallbacks[reason] = self.title_fallbacks.get(reason, 0) + n


class SpotClassification(BaseModel):
    """classify()'s return: the spots and the account of how they were chosen."""

    spots: list[Spot]
    report: SpotBuildReport
