"""Pydantic model for one species' reference image and where it came from."""

from typing import Literal

from pydantic import BaseModel, model_validator

ImageStatus = Literal["approved", "placeholder", "rejected"]

# What an approved image must be able to answer for itself. A licence claim with
# no evidence URL, or an image with no hash to say which original it came from,
# is not provenance, so such a row cannot be called approved.
APPROVED_REQUIRED_FIELDS = (
    "source_name",
    "source_url",
    "creator",
    "licence",
    "licence_evidence_url",
    "retrieved_date",
    "original_sha256",
    "processing_version",
)


class SpeciesImage(BaseModel):
    species_id: str
    """Scientific name, canonicalised: the id the FishDex API uses for a species."""
    common_name: str
    status: ImageStatus
    image_path: str
    """Carousel image, relative to the static root (WebP; a PNG sits beside it)."""
    thumb_path: str
    """Tile image, relative to the static root (WebP; a PNG sits beside it)."""
    source_name: str = ""
    source_url: str = ""
    creator: str = ""
    licence: str = ""
    """The licence sentence as the source page states it."""
    licence_evidence_url: str = ""
    retrieved_date: str = ""
    original_sha256: str = ""
    processing_version: str = ""
    match_confidence: str = ""
    name_as_published: str = ""
    """The scientific name the source used, when it differs or is stated at all."""

    @model_validator(mode="after")
    def _approved_needs_provenance(self) -> "SpeciesImage":
        if self.status == "approved":
            missing = [f for f in APPROVED_REQUIRED_FIELDS if not getattr(self, f).strip()]
            if missing:
                raise ValueError(
                    f"approved image for {self.species_id} lacks provenance: {', '.join(missing)}"
                )
        return self
