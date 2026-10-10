from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ConditionType = Literal["behavioral", "habitat", "temporal", "gear"]
Confidence = Literal["high", "medium", "low", "unverified"]
SourceType = Literal[
    "agent_synthesis",
    "tactical_rules",
    "inat_pattern",
    "mnrf_survey",
    "reddit_pattern",
    "trip_log",
    "user_correction",
]


class BehavioralInsight(BaseModel):
    id: int | None = None
    user_id: int = 1
    species: str
    condition_type: ConditionType
    condition_context: str
    conclusion: str
    confidence: Confidence = "unverified"
    source_type: SourceType
    source_detail: str
    evidence_count: int = 0
    version: int = 1
    is_current: bool = True
    contradicted_by: int | None = None
    user_verified: bool = False
    jurisdiction: str | None = None
    lat: float | None = None
    lng: float | None = None
    recommendation: str | None = None
    condition_season: str | None = None
    location_name: str | None = None
    last_validated: datetime = Field(default_factory=datetime.now)
    created_at: datetime = Field(default_factory=datetime.now)

    @field_validator("conclusion")
    @classmethod
    def conclusion_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("conclusion must not be empty")
        return v
