"""Pydantic model for a standardised fish community survey record.

The first record type in the project that carries ABUNDANCE rather than mere
presence. An iNaturalist row says somebody photographed one fish; a GBIF museum
row says a specimen exists. A survey row says how many were caught, how much
they weighed, at a named station, on a known date, by a repeatable method.

Two things follow from that and shape this model:

  - `station_name` + `visit_date` identify a sampling event. Every species
    caught in one event shares them, so the species NOT in that group are
    candidate absences at a known location and effort. Presence-only corpora
    cannot produce an absence at all, which is why the SDM work stalled at
    chance (see CLAUDE.md). Keep both fields exactly as published; they are the
    grouping key, not decoration.

  - A survey is near the top of the credibility order: below the user's own
    catch, above a casual photo. `survey_program` records which programme and
    protocol produced it so that ordering is auditable rather than assumed.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field


class FishSurveyRecord(BaseModel):
    record_id: str
    """Stable per (program, station, visit_date, species). Built by the adapter,
    not published upstream, so re-ingesting updates rather than duplicates."""

    survey_program: str
    """e.g. "TRCA RWMP". Names the programme AND its protocol, because effort
    only means something relative to a method."""

    station_name: str
    visit_date: date | None = None
    sample_year: int | None = None
    """Kept alongside visit_date: some programmes publish only the year, and a
    year is still enough to group an event."""

    watershed: str | None = None
    subwatershed: str | None = None

    lat: float | None = None
    lng: float | None = None
    jurisdiction: str = "CA-ON"

    species_common_name: str
    species_scientific_name: str | None = None
    """Resolved through species_mapping where possible. None means the common
    name did not map — which is a fact worth keeping, not a reason to drop the
    row: the count is still real."""

    total_count: int | None = None
    total_weight_g: float | None = None

    sampling_protocol: str | None = None
    """Gear, where the programme states it. Matches the field name used on
    gbif_observations so one credibility filter can span both corpora."""

    source_url: str | None = None
    ingested_at: datetime = Field(default_factory=datetime.now)
