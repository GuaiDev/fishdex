"""Pydantic model for one Wikimedia Commons file, as the species-image audit sees it."""

import re

from pydantic import BaseModel, Field

# Commons tags that state a US-government work is public domain. The audit
# only accepts a file when one of these is actually transcluded on its page.
PD_USGOV_TEMPLATES = ("PD-USGov-FWS", "PD-USGov")

# Weaker evidence: the Commons uploader (not a US-government source) asserts
# the author released the work. Kept as a separate tier so it is never
# mistaken for a government tag.
PD_AUTHOR_TEMPLATES = ("PD-author",)

TIER_USGOV = "us-gov-tag"
TIER_AUTHOR = "pd-author (weaker: uploader's claim)"

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(value: str | None) -> str:
    """Commons metadata arrives as HTML fragments; the audit wants plain text."""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", value or "")).strip()


class CommonsFile(BaseModel):
    title: str
    """Full title, e.g. 'File:Smallmouth bass.jpg'."""
    page_url: str
    """The Commons file page. This is the licence evidence URL."""
    original_url: str
    width: int
    height: int
    mime: str
    artist: str = ""
    description: str = ""
    licence_short: str = ""
    """LicenseShortName as Commons reports it ('Public domain')."""
    fws_source_url: str | None = None
    """The digitalmedia.fws.gov record the Commons page says the file came from."""
    templates: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)

    @property
    def pd_template(self) -> str | None:
        """The public-domain US-government tag on the page, or None if absent."""
        for name in PD_USGOV_TEMPLATES:
            if name in self.templates:
                return name
        return None

    @property
    def licence_tier(self) -> str | None:
        if self.pd_template:
            return TIER_USGOV
        if any(t in self.templates for t in PD_AUTHOR_TEMPLATES):
            return TIER_AUTHOR
        return None

    @property
    def licence_tag(self) -> str | None:
        return self.pd_template or next(
            (t for t in PD_AUTHOR_TEMPLATES if t in self.templates), None
        )

    @property
    def is_raver(self) -> bool:
        """True when the page credits Duane Raver, by artist field or category."""
        return "raver" in self.artist.lower() or "Duane Raver" in self.categories

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"
