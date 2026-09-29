"""Pydantic model for a single GBIF species occurrence record."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class GBIFObservation(BaseModel):
    gbif_key: int
    species: str
    common_name: str | None = None
    taxon_key: int
    lat: float
    lng: float
    observed_on: date | None = None
    country_code: str | None = None
    dataset_name: str | None = None
    basis_of_record: str
    coordinate_uncertainty_m: float | None = None
    jurisdiction: str
    ingested_at: datetime = Field(default_factory=datetime.now)

    # ── licensing and attribution ─────────────────────────────────────────────
    # GBIF licences are set per DATASET, not per record, and are restricted to
    # three: CC0, CC BY and CC BY-NC. They arrive as legalcode URIs rather than
    # short codes, so both forms are kept: `license_code` normalised into the
    # same vocabulary the iNaturalist model uses, so one filter can span both
    # corpora, and `license_uri` verbatim so the exact grant is auditable.
    license_code: str | None = None
    """Normalised: 'cc0', 'cc-by', 'cc-by-nc'. None = not stated by the publisher."""

    license_uri: str | None = None
    """The raw legalcode URI exactly as GBIF returned it."""

    dataset_key: str | None = None
    """GBIF dataset UUID. The licence attaches here, so this is the audit key."""

    rights_holder: str | None = None
    recorded_by: str | None = None

    # ── survey provenance ─────────────────────────────────────────────────────
    # What separates a standardised survey from somebody's phone photo. GBIF
    # returns these and they were previously dropped at parse, so a boat
    # electrofishing record from a fisheries survey landed in the corpus
    # indistinguishable from a casual iNaturalist upload. They are the fields
    # that let a model weight effort-based records above opportunistic ones —
    # and the only route to a real absence, which presence-only data cannot
    # produce at all.
    sampling_protocol: str | None = None
    """Gear or method, verbatim from GBIF — e.g. "boat electrofisher", "seine",
    "fyke net". A GBIF-indexed facet, so it is also queryable upstream.
    None means the publisher did not state one, NOT that no gear was used."""

    event_id: str | None = None
    """Identifier of the sampling event this record belongs to. Records sharing
    an event_id were collected together, which is what makes effort-corrected
    absence inference possible."""

    sampling_effort: str | None = None
    """Free-text effort description as published — e.g. "20 minutes", "3 passes".
    Unstructured by design upstream; kept verbatim rather than parsed into a
    number we would be inventing."""

    sample_size_value: float | None = None
    sample_size_unit: str | None = None
    """Structured effort where the publisher supplied it, e.g. 100.0 / "metre".
    Kept as a value+unit pair because the unit is not assumable."""
