"""User profile for the angler."""

from datetime import datetime

from pydantic import BaseModel, Field

from src.models.jurisdiction import JurisdictionCode


class Location(BaseModel):
    name: str
    lat: float
    lng: float


class UserProfile(BaseModel):
    home_jurisdiction: JurisdictionCode
    frequented_jurisdictions: list[JurisdictionCode] = Field(default_factory=list)
    home_location: Location | None = None
    target_species: list[str] = Field(default_factory=list)
    gear: list[dict] = Field(default_factory=list)
    budget: float | None = None
    fishing_style: str = ""
    preferences: str = ""
    updated_at: datetime | None = None

    @classmethod
    def default(cls) -> "UserProfile":
        """Oakville-based starter profile matching CLAUDE.md."""
        return cls(
            home_jurisdiction="CA-ON",
            home_location=Location(name="Oakville, ON", lat=43.4675, lng=-79.6877),
            target_species=[
                "smallmouth bass",
                "brook trout",
                "northern pike",
                "walleye",
            ],
            fishing_style="stream + small lakes",
            preferences=(
                "Top priority is exploration over catch optimization. "
                "Interested in all species — including microfishing targets "
                "(darters, dace, madtoms, shiners, chubs, lampreys)."
            ),
        )
