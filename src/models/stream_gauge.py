from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class StreamGaugeReading(BaseModel):
    station_id: str
    station_name: str
    river_name: str | None
    lat: float
    lng: float
    jurisdiction: str
    water_level_m: float | None
    discharge_cms: float | None
    level_trend: Literal["rising", "falling", "stable"] | None
    discharge_trend: Literal["rising", "falling", "stable"] | None
    level_grade: str | None
    reading_datetime: datetime
    fetched_at: datetime = Field(default_factory=datetime.now)
    # 24hr baseline means — used by service layer for condition classification, not stored in DB
    level_24hr_mean_m: float | None = None
    discharge_24hr_mean_cms: float | None = None


class StreamGaugeSummary(BaseModel):
    station_id: str
    station_name: str
    river_name: str | None
    current_level_m: float | None
    current_discharge_cms: float | None
    level_trend: Literal["rising", "falling", "stable"] | None
    discharge_trend: Literal["rising", "falling", "stable"] | None
    condition_note: str
    fishing_note: str
    distance_km: float
    reading_datetime: datetime
    fetched_at: datetime


class NearestGauge(BaseModel):
    """The closest active WSC station that has a current reading, and how far it is.

    The nearest gauge is not always on the same river as the place asking, so
    the distance travels with the reading.
    """

    reading: StreamGaugeReading
    distance_km: float
