"""Models for the central context layer.

Every value the layer returns is wrapped in a ContextField carrying its own
provenance and, when absent, a specific reason for being absent. That is the
whole point: honesty is a data field, not a paragraph of prompt text asking
the model to be careful.

Prose guardrails erode as prompts grow — the 359-line system prompt had
drifted into contradicting itself on tool budgets. Fields do not drift.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.models.freshness import is_stale, normalise_observed_at


class ProvenanceKind(StrEnum):
    """Where a claim came from. Rendered differently by every surface."""

    RECORD = "record"
    """A row in our corpus — iNaturalist, GBIF, a survey, the user's own log."""

    WEB = "web"
    """Live web search. Always unverified; always shown with its URL."""

    INFERENCE = "inference"
    """Reasoning only, no source. General ecological principles land here.

    Legitimate at n=1 when applied to observed conditions, but it must never
    render identically to a RECORD — that is how trust quietly erodes.
    """


class EmptyReason(StrEnum):
    """Why a field has no value. `"no data"` is not an acceptable answer.

    Surfaces render these four differently: the first is a fact about the
    water, the second is a fact about the user, the third is a fact about
    the search, and the fourth is a fact about our coverage.
    """

    NO_RECORDS_IN_RADIUS = "no_records_in_radius"
    USER_NEVER_FISHED_HERE = "user_never_fished_here"
    WEB_SEARCH_EMPTY = "web_search_empty"
    SOURCE_DOES_NOT_COVER_AREA = "source_does_not_cover_area"

    LIVE_LOOKUP_FAILED = "live_lookup_failed"
    """A live source could not be reached just now.

    Only the conditions slice is live, and this is the one empty case that is
    transient: the remedy is "try again", not "log a trip" or "we don't cover
    that". Rendering it as missing coverage would send the reader looking for
    a permanent gap that isn't there.
    """

    FIELD_NOT_POPULATED_BY_SOURCE = "field_not_populated_by_source"
    """The record exists here, but this particular field was never filled in.

    Distinct from having no data for the area: OHN covers this water and we
    hold its segments, but our ingest captured no stream order for any of
    them. That is a gap in our pipeline, not in the world, and it has a
    different fix — so it must not render as "nothing recorded here".
    """

    SUPPRESSED_ON_CONSERVATION_GROUNDS = "suppressed_on_conservation_grounds"
    """We hold this, and we are deliberately not showing it.

    Targeting guidance for a species that may be listed. The value exists and
    the source covers the area — withholding it is a policy decision, and
    rendering that as a coverage gap misreports a choice we made as a hole in
    the data. The two have opposite remedies: a coverage gap is fixed by
    ingesting more, and this is not fixed at all until the status is checked.
    """

    NO_LIVE_SOURCE = "no_live_source"
    """No source publishes this live, anywhere we can reach.

    Water temperature is the case: there is no live stream-temperature feed,
    only dated grab samples. When none of those exist near the place the field
    is empty for that reason — not "the source does not cover this area" (no
    source exists to cover it) and not "try again" (nothing will change).
    """

    RECORDED_BUT_NOT_DECISION_RELEVANT = "recorded_but_not_decision_relevant"
    """A value exists but carries no "so what" for an angler.

    A fifth case beyond the spec's four, and it earns its place: reporting a
    measured mid-range pH as "nothing recorded" would be a false statement
    about our own corpus. The data is there; it just does not change anyone's
    decision, so the number is withheld while the fact of having it is not.
    """


_EMPTY_PHRASING: dict[EmptyReason, str] = {
    EmptyReason.NO_RECORDS_IN_RADIUS: "nothing recorded within the search radius",
    EmptyReason.USER_NEVER_FISHED_HERE: "you have not logged a trip here",
    EmptyReason.WEB_SEARCH_EMPTY: "a web search turned up nothing",
    EmptyReason.SOURCE_DOES_NOT_COVER_AREA: "this data source does not cover this area",
    EmptyReason.LIVE_LOOKUP_FAILED: (
        "the live conditions lookup could not be reached just now — try again"
    ),
    EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE: (
        "we hold records here, but this field is not populated in them"
    ),
    EmptyReason.NO_LIVE_SOURCE: (
        "no live source exists for this, and no dated sample is on record nearby"
    ),
    EmptyReason.RECORDED_BUT_NOT_DECISION_RELEVANT: (
        "measured, but unremarkable enough that the number would not change your plan"
    ),
    EmptyReason.SUPPRESSED_ON_CONSERVATION_GROUNDS: (
        "withheld while this species may be listed — not missing, deliberately "
        "not shown"
    ),
}


class Provenance(BaseModel):
    """Structured sourcing for a single claim."""

    kind: ProvenanceKind
    source: str | None = None
    """'iNaturalist', 'OHN', 'PWQMN station 06008900', or a URL for web."""

    date: str | None = None
    """ISO date of the underlying record, when it has one."""

    url: str | None = None

    observed_at: str | None = None
    """When the underlying value was measured: ISO-8601 UTC, or a bare date when
    that is all the source gives. Distinct from `date`, which only says which
    record; this is what age and staleness are computed from."""

    station: str | None = None
    """The gauge or sampling station the value came from, when it is not at the
    place itself. A river gauge can sit on a different river from the pin."""

    distance_km: float | None = None
    """How far that station is from the place."""

    verified: bool = True
    """Web provenance is never verified."""

    @model_validator(mode="after")
    def _web_is_never_verified(self) -> "Provenance":
        if self.kind is ProvenanceKind.WEB:
            self.verified = False
        return self

    def describe(self) -> str:
        """One-line human sourcing, e.g. 'iNaturalist, 2024-06-03'."""
        bits = [self.source] if self.source else []
        if self.station and self.station not in (self.source or ""):
            bits.append(self.station)
        if self.distance_km is not None:
            bits.append(f"{self.distance_km:g} km away")
        when = self.observed_at or self.date
        if when:
            bits.append(f"observed {when}" if self.observed_at else when)
        text = ", ".join(bits) if bits else self.kind.value
        if self.kind is ProvenanceKind.WEB:
            return f"{text} (web, unverified)"
        if self.kind is ProvenanceKind.INFERENCE:
            return f"{text} (reasoning, no source)"
        return text


class ContextField(BaseModel):
    """A value plus why you should believe it — or why it is missing.

    Exactly one of `value` / `empty_reason` is meaningful. `meaning` carries
    the plain-language "so what": if a number has no meaning, the layer does
    not surface the number at all.
    """

    value: Any | None = None
    provenance: Provenance | None = None
    empty_reason: EmptyReason | None = None
    meaning: str | None = None

    stale: bool = False
    """Older than the stale limit for its source (src/models/freshness.py).

    A stale value is still a value: it is shown, marked, and left out of any
    match against the user's history. It is not an EmptyReason — emptying it
    would hide a real reading to say the reading is old."""

    @property
    def is_empty(self) -> bool:
        return self.value is None

    @classmethod
    def empty(cls, reason: EmptyReason) -> "ContextField":
        return cls(value=None, empty_reason=reason)

    @classmethod
    def recorded(
        cls,
        value: Any,
        source: str,
        date: str | None = None,
        meaning: str | None = None,
        observed_at: datetime | str | None = None,
        stale_after_s: int | None = None,
        station: str | None = None,
        distance_km: float | None = None,
        now: datetime | None = None,
    ) -> "ContextField":
        """A sourced value. Give `observed_at` and `stale_after_s` together to
        have staleness decided here, in one place, rather than per caller."""
        when = normalise_observed_at(observed_at)
        return cls(
            value=value,
            provenance=Provenance(
                kind=ProvenanceKind.RECORD,
                source=source,
                date=date,
                observed_at=when,
                station=station,
                distance_km=distance_km,
            ),
            meaning=meaning,
            stale=stale_after_s is not None and is_stale(when, stale_after_s, now),
        )

    @classmethod
    def inferred(cls, value: Any, meaning: str | None = None) -> "ContextField":
        return cls(
            value=value,
            provenance=Provenance(kind=ProvenanceKind.INFERENCE),
            meaning=meaning,
        )

    def explain(self) -> str:
        """Render for a prompt: either the value with its source, or why not."""
        if self.is_empty:
            reason = _EMPTY_PHRASING.get(
                self.empty_reason, "no value and no reason recorded — this is a bug"
            )
            return f"(none — {reason})"
        text = str(self.value)
        if self.meaning:
            text = f"{text} — {self.meaning}"
        if self.provenance:
            text = f"{text} [{self.provenance.describe()}]"
        if self.stale:
            text = f"{text} (stale — too old to count as current)"
        return text


class SpeciesRecord(BaseModel):
    """One species observed at a place, with how we know."""

    species: str
    common_name: str | None = None
    count: int = 1
    most_recent: str | None = None
    provenance: Provenance
    is_obscured: bool = False
    """iNaturalist geoprivacy — the point is fuzzed to ~22km, not precise."""

    survey_record: Provenance | None = None
    """The most recent record of this species that came from a standardised
    survey (known gear, an event id, a named programme) rather than a photo or a
    specimen, or None when none did. Kept apart from `provenance`, which is the
    most recent record of any kind: a surveyed haul outranks a casual photo even
    when the photo is newer, and the reader needs to be able to cite either."""


# ── describe() slices ─────────────────────────────────────────────────────────


class RecordsSlice(BaseModel):
    """Species recorded here. The only slice that escalates."""

    species: list[SpeciesRecord] = Field(default_factory=list)
    total_count: int = 0
    radius_km: float = 0.0
    empty_reason: EmptyReason | None = None
    escalated_to_web: bool = False

    piscivore_activity: ContextField = Field(default_factory=ContextField)
    """Fish-eating birds recorded here — a biological proxy, not a sighting.

    Osprey and mergansers hunt where there are fish to hunt, so their presence
    is weak positive evidence about the water. It sits in this slice because
    it is presence evidence, and it is a separate field because it is evidence
    of a different kind: nobody saw a fish."""


class WaterSlice(BaseModel):
    thermal_class: ContextField = Field(default_factory=ContextField)
    substrate: ContextField = Field(default_factory=ContextField)
    dissolved_oxygen: ContextField = Field(default_factory=ContextField)
    ph: ContextField = Field(default_factory=ContextField)
    benthic_health: ContextField = Field(default_factory=ContextField)


class StructureSlice(BaseModel):
    barriers_upstream: ContextField = Field(default_factory=ContextField)
    barriers_downstream: ContextField = Field(default_factory=ContextField)
    is_confluence: ContextField = Field(default_factory=ContextField)
    waterbody_connection: ContextField = Field(default_factory=ContextField)
    stream_order: ContextField = Field(default_factory=ContextField)


class AccessSlice(BaseModel):
    parking: ContextField = Field(default_factory=ContextField)
    trails: ContextField = Field(default_factory=ContextField)
    crown_land: ContextField = Field(default_factory=ContextField)
    access_note: ContextField = Field(default_factory=ContextField)


class ConditionsSlice(BaseModel):
    """What the water and weather are doing now, where a source exists.

    Live where a live source exists (weather, the nearest WSC gauge — never
    cached beyond an hour) and the nearest *dated* sample where none does
    (water temperature and chemistry come from PWQMN grab samples). Every value
    carries `observed_at`, and `stale` once it is past its source's limit, so
    the reader can tell a reading from this morning from one taken in 2024.

    Flow is worded as a trend (rising / falling / steady) and never as
    "normal": the gauge has no seasonal baseline here, and "normal" would be a
    claim about one.
    """

    air_temp_c: ContextField = Field(default_factory=ContextField)
    wind_speed_kmh: ContextField = Field(default_factory=ContextField)
    precipitation_mm: ContextField = Field(default_factory=ContextField)
    pressure_hpa: ContextField = Field(default_factory=ContextField)
    pressure_trend: ContextField = Field(default_factory=ContextField)

    flow_cms: ContextField = Field(default_factory=ContextField)
    flow_trend: ContextField = Field(default_factory=ContextField)
    level_m: ContextField = Field(default_factory=ContextField)
    level_trend: ContextField = Field(default_factory=ContextField)

    water_temp_c: ContextField = Field(default_factory=ContextField)
    dissolved_oxygen_mgl: ContextField = Field(default_factory=ContextField)
    ph: ContextField = Field(default_factory=ContextField)
    """Unlike `WaterSlice.ph`, a mid-range reading is kept here. That slice
    withholds numbers that tell an angler nothing; this one is a panel of
    facts, and an unremarkable pH is still the pH."""


class HistorySlice(BaseModel):
    """The user's own stops, catches and blanks here."""

    visits: int = 0
    productive_visits: int = 0
    blanks: int = 0
    species_caught: list[str] = Field(default_factory=list)
    last_visit: str | None = None
    techniques_used: list[str] = Field(default_factory=list)
    empty_reason: EmptyReason | None = None


class Place(BaseModel):
    """A resolved stretch of water.

    Anglers think in named stretches; physical data is keyed to OHN segments;
    sightings are points that do not snap cleanly. So a place is segments
    plus a radius.
    """

    query: str
    name: str | None = None
    lat: float
    lng: float
    segment_ids: list[int] = Field(default_factory=list)
    radius_km: float = 5.0
    jurisdiction: str | None = None
    resolved_by: Literal["latlng", "segment_id", "name", "user_log"] = "latlng"
    resolution_note: str | None = None


class PlaceContext(BaseModel):
    """Everything known about one stretch of water.

    Slices are populated per caller bundle — an unpopulated slice is None,
    which is different from a populated slice whose fields are empty.
    """

    place: Place
    records: RecordsSlice | None = None
    water: WaterSlice | None = None
    structure: StructureSlice | None = None
    access: AccessSlice | None = None
    conditions: ConditionsSlice | None = None
    history: HistorySlice | None = None
    bundle: str = "full"


class SpeciesContext(BaseModel):
    """What is known about a species, with each claim carrying its provenance.

    The local species file was generated during development rather than drawn
    from a registry, so its contents are INFERENCE, not RECORD. Routing them
    through ContextField is the point: previously this text reached the model
    as a bare tool result, indistinguishable from an ingested observation.
    """

    species: str
    scientific_name: str | None = None
    found: bool = True

    conservation_status: ContextField = Field(default_factory=ContextField)
    habitat_note: ContextField = Field(default_factory=ContextField)
    angling_note: ContextField = Field(default_factory=ContextField)
    native_to_ontario: ContextField = Field(default_factory=ContextField)

    sar_alert: bool = True
    """Defaults to True. Absence of evidence is not evidence of safety."""

    sar_reason: str = "not yet assessed"
    targeting_guidance_suppressed: bool = True
    """Suppresses the *corpus's own* angling text. True whenever the status is
    unverified, which is currently every species — the generated advice was
    never sourced, so none of it is trustworthy."""

    status_known_listed: bool = False
    """An affirmative listing signal exists — some authority in our data says
    this species is Special Concern or worse, verified or not.

    Separate from `sar_alert` on purpose. `sar_alert` is true for all 69
    species in the local file because none of their statuses are verified, so
    a caller that treats it as "refuse to discuss this fish" refuses to
    discuss every fish in Ontario. This field is the narrower question — is
    there anything actually pointing at a listing — and it is what a caller
    should gate a hard refusal on. The unverified caution still renders
    either way; only the refusal is narrowed."""


# ── explore() ─────────────────────────────────────────────────────────────────


class ExploreResult(BaseModel):
    """One ranked candidate. Carries no habitat-quality claim by construction."""

    ogf_id: int
    name: str | None = None
    lat: float
    lng: float
    stream_order: int | None = None
    score: float
    observation_pressure: float
    access_score: float
    access: ContextField = Field(default_factory=ContextField)
    """The access figure as a first-class context value.

    `access_score` and `access_is_measured` below are the structured data the
    map and API read. This is the same fact wrapped the way every other value
    in the layer is wrapped, so it renders through `explain()` and its empty
    cases draw their wording from `_EMPTY_PHRASING` rather than from a string
    the renderer invented for this one field."""

    access_is_measured: bool | None = None
    """Whether the access score is a reading or a placeholder.

    Three states, because there are three facts and they have three different
    remedies — the same discipline as EmptyReason:

      True  — measured; the number means something.
      False — the segment sits outside the OSM ingest footprint (~55 km of
              home), so it has no access data at all and normalised to a
              mid-range value indistinguishable from a real one.
      None  — we cannot tell, because the cached access scores predate
              coverage tracking. The remedy is `make compute-access`, not a
              longer drive.

    Defaults to None. Claiming a segment is outside the footprint when we
    simply have not checked would be its own fabrication."""

    is_confluence: bool = False
    recorded_species_nearby: list[str] = Field(default_factory=list)
    note: str | None = None


class ExploreResponse(BaseModel):
    results: list[ExploreResult] = Field(default_factory=list)
    excluded_count: int = 0
    excluded_examples: list[str] = Field(default_factory=list)
    """Gate reasons, so exclusions are visible rather than silent."""

    tied_at_top: int = 0
    """How many candidates share the top score.

    Without a habitat term the surviving terms are coarse — pressure has a
    floor, remoteness has three steps, structural bonus is a small set of
    increments — so ties are large and common. Presenting an arbitrary ten
    out of a thousand equally-ranked segments as "the best" would be a
    precision claim the score cannot support.
    """

    results_on_placeholder_access: int = 0
    """How many of `results` are known to sit outside the mapped access
    footprint, so their access figure is a default rather than a reading.
    Reported rather than filtered: pressure, structure and remoteness are all
    still real out there — the access term is not, and the reader has to know
    which part of the number they are trusting."""

    results_with_unknown_access_coverage: int = 0
    """How many of `results` we cannot classify either way, because the access
    cache predates coverage tracking. A pipeline gap, not a coverage gap — it
    is fixed by recomputing, and must not be reported as remoteness."""

    empty_reason: EmptyReason | None = None
    scoring_note: str = (
        "Ranked by observation scarcity, structure, access and remoteness. "
        "No habitat-quality term and no species prediction: a high score means "
        "few people have reported here, NOT that fish are here."
    )


# ── user_layer() ──────────────────────────────────────────────────────────────


class DerivedPattern(BaseModel):
    """A claim about the user's own tendencies. Requires a comparison set.

    General ecological principles do not live here — those are legitimate at
    n=1. This is specifically 'you do better in X', which needs both arms of
    the comparison before it can be stated.
    """

    statement: str
    sample_size: int
    comparison_size: int = 0
    confidence: Literal["low", "medium", "high"] = "low"

    @property
    def is_claimable(self) -> bool:
        """Both arms present, and enough of them to not be noise."""
        return self.sample_size >= 3 and self.comparison_size >= 2


class UserLayer(BaseModel):
    """Derived, precomputed. Never raw rows.

    Recomputed when a session is logged, not per question — the coaching path
    it replaces loaded every stop ever logged and filtered in Python on each
    request.
    """

    user_id: int
    total_sessions: int = 0
    total_stops: int = 0
    blank_rate: float | None = None
    species_logged: list[str] = Field(default_factory=list)
    target_species: list[str] = Field(default_factory=list)
    """Inferred from logging behaviour, never configured."""

    expertise: Literal["unknown", "novice", "intermediate", "advanced"] = "unknown"
    """Demonstrated, not declared. Drives register — telling an experienced
    angler something obvious destroys credibility."""

    patterns: list[DerivedPattern] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)
    """Fields missing too often to support a given kind of claim."""

    computed_at: str | None = None


# -- the user's record with one species ----------------------------------------


class RecordedInsight(BaseModel):
    """A stored behavioural insight, carrying where it came from.

    These were previously handed to the coach as bare sentences with a
    confidence word attached. An insight the assistant synthesised and one
    drawn from the user's own logs are not the same kind of claim, and the
    coach could not tell them apart — so the source travels with the text now.
    """

    conclusion: str
    confidence: str = "unverified"
    provenance: Provenance
    recommendation: str | None = None


class SpeciesHistory(BaseModel):
    """The user's own record with one species — catches and blanks alike.

    Distinct from `HistorySlice`, which is keyed to a place. This is keyed to a
    species across every place, which is the question a coach is actually
    being asked.
    """

    species: str
    caught_stops: int = 0
    blank_stops: int = 0
    """Stops that produced nothing and where nobody in the party caught this
    species. Targeting is not always recorded, so this is an upper bound on
    'tried and failed', not a count of it."""

    locations: list[str] = Field(default_factory=list)
    productive_setups: list[str] = Field(default_factory=list)
    """One line per successful stop: where, when, technique, gear, conditions."""

    last_caught: str | None = None
    insights: list[RecordedInsight] = Field(default_factory=list)
    empty_reason: EmptyReason | None = None


# -- what was realistically available at a place -------------------------------


class LocalRichness(BaseModel):
    """Which fish species are recorded around a place, and how firmly.

    The angler profile normalises what someone caught against this, so that
    living where many species occur does not by itself read as lifelisting.
    """

    lat: float
    lng: float
    radius_km: float
    species: list[str] = Field(default_factory=list)
    """Lowercased scientific names recorded within the radius."""
    common_names: dict[str, str] = Field(default_factory=dict)
    """Lowercased common name -> lowercased scientific name, where a source gave one."""
    n_records: int = 0
    richness: float = 0.0
    """The species count used for normalising: the local count, or the
    regional average when local data is thin."""
    basis: Literal["local", "regional", "none"] = "none"
    thin: bool = True
    confidence: float = 0.0
    """1.0 on local data; lower on the regional fallback; 0.0 with nothing."""
    by_source: dict[str, int] = Field(default_factory=dict)
