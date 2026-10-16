"""Shared set-up for the match indicator and Today view tests.

Everything here builds a throwaway world in a temporary database: users, pins,
logged trips with the conditions stored for them, and canned "right now"
readings in place of Open-Meteo and the WSC gauge. No test using it reaches
the network or the real database.
"""

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.auth import _create_token
from src.models.stream_gauge import NearestGauge, StreamGaugeReading
from src.services.trip_logger import log_session
from src.storage import pins as store

client = TestClient(app)

SPOT = (43.4000, -79.7500)
NORTH = (SPOT[0] + 0.1, SPOT[1])  # about 11 km from SPOT
FAR = (SPOT[0] + 0.3, SPOT[1])  # about 33 km
EAST = (SPOT[0], SPOT[1] + 0.05)  # about 4 km
WEST_OF_HOME = (SPOT[0] - 0.2, SPOT[1])  # about 22 km
GAUGE_NAME = "CREDIT RIVER AT STREETSVILLE"


def isolate(tmp_path, monkeypatch) -> None:
    """Point the app at a temporary database and switch off trip enrichment."""
    import src.services.trip_enrichment as enrichment
    import src.services.trip_enrichment_conditions as conditions
    import src.storage.database as database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_fishing.db")
    monkeypatch.setattr(enrichment, "enrich_session", lambda *a, **k: {})
    monkeypatch.setattr(conditions, "enrich_session_conditions", lambda *a, **k: None)


class Canned:
    """What the weather feed and the nearest gauge say right now.

    Change an attribute before the call under test. `lookups` counts how many
    times either live source was asked, so a test can prove a path made none.
    """

    def __init__(self) -> None:
        self.air = 10.0
        self.wind = 14.0
        self.pressure = 1011.0
        self.weather_age = timedelta(minutes=10)
        self.flow = 18.3
        self.level = 0.52
        self.station = GAUGE_NAME
        self.gauge_age = timedelta(minutes=5)
        self.lookups = 0

    def weather(self, **kw) -> str:
        self.lookups += 1
        seen = datetime.now(UTC) - self.weather_age
        return json.dumps(
            {
                "time": seen.replace(tzinfo=None).isoformat(timespec="minutes"),
                "temperature_c": self.air,
                "wind_speed_kmh": self.wind,
                "precipitation_mm": 0.2,
                "pressure_hpa": self.pressure,
                "pressure_trend": "falling",
            }
        )

    def gauge(self, *a, **k) -> NearestGauge:
        self.lookups += 1
        return NearestGauge(
            reading=StreamGaugeReading(
                station_id="02HB001",
                station_name=self.station,
                river_name="Credit River",
                lat=43.58,
                lng=-79.71,
                jurisdiction="CA-ON",
                water_level_m=self.level,
                discharge_cms=self.flow,
                level_trend="stable",
                discharge_trend="rising",
                level_grade=None,
                reading_datetime=datetime.now(UTC) - self.gauge_age,
            ),
            distance_km=8.2,
        )

    def install(self, monkeypatch) -> "Canned":
        monkeypatch.setattr("src.services.weather.get_conditions_for_agent", self.weather)
        monkeypatch.setattr("src.services.stream_gauge.nearest_gauge", self.gauge)
        return self


class Angler:
    """One user of the temporary database, with helpers to give them pins and trips."""

    def __init__(self, db, user_id: int, name: str) -> None:
        self.db = db
        self.id = user_id
        self.name = name
        if not db["users"].count_where("id = ?", [user_id]):
            db["users"].insert({"id": user_id, "username": name, "role": "beta"})
        self.headers = {"Authorization": f"Bearer {_create_token(db, user_id)}"}

    def pin(self, name: str, point=SPOT, favourite: bool = False) -> int:
        body = {"name": name, "lat": point[0], "lng": point[1], "is_favourite": favourite}
        response = client.post("/pins", json=body, headers=self.headers)
        assert response.status_code == 201, response.text
        return response.json()["pin"]["id"]

    def trip(
        self,
        when: str,
        rating: int | None,
        point=SPOT,
        conditions: dict | None = None,
        stops: int = 1,
    ) -> int:
        """Log a trip at `point` (linking to a pin there), rate it, store its conditions."""
        logged = log_session(
            {
                "date": when,
                "stops": [
                    {"location_text": f"stop {i}", "lat": point[0], "lng": point[1]}
                    for i in range(stops)
                ],
            },
            self.db,
            user_id=self.id,
        )
        session_id = logged["session_id"]
        if rating is not None:
            assert store.update_session(self.db, self.id, session_id, {"rating": rating})
        if conditions is not None:
            self.db["session_conditions"].insert(
                {"session_id": session_id, "user_id": self.id, **conditions}, alter=True
            )
        return session_id

    def recall(self, pin_id: int, rating: int | None = 5) -> None:
        response = client.post(
            f"/pins/{pin_id}/recalled",
            json={"date_approx": "2019", "species": ["brook trout"], "rating": rating},
            headers=self.headers,
        )
        assert response.status_code == 201, response.text

    def get(self, path: str, **params):
        return client.get(path, headers=self.headers, params=params)


def weather_trip(air: float, wind: float = 14.0, pressure: float = 1011.0, **extra) -> dict:
    """A trip's stored weather (and any extra columns)."""
    return {"air_temp_c": air, "wind_speed_kmh": wind, "pressure_hpa": pressure, **extra}


def gauge_snapshot(flow: float, level: float = 0.5, station: str = GAUGE_NAME) -> dict:
    """The gauge columns of a trip logged after the snapshot was introduced."""
    return {
        "flow_cms": flow,
        "level_m": level,
        "gauge_station_id": "02HB001",
        "gauge_station_name": station,
    }


def april_trips(angler: Angler, point, air_temps) -> None:
    """Three rated April trips (5, 4, 4) with the given air temperatures."""
    for (day, rating), air in zip(((5, 5), (19, 4), (26, 4)), air_temps, strict=True):
        angler.trip(
            f"2026-04-{day:02d}",
            rating,
            point=point,
            conditions=weather_trip(air) | gauge_snapshot(18.0),
        )


def build_today_world(angler: Angler) -> dict[str, int]:
    """Five pins: two matchable, one with a single rated trip, one wishlist, one recalled-only.

    Against Canned's defaults (10 °C, 14 km/h, 1011 hPa), Bronte pool is a
    similar day and Credit mouth, fished in May in very different weather, is not.
    """
    ids = {"bronte": angler.pin("Bronte pool", SPOT)}
    ids["credit"] = angler.pin("Credit mouth", NORTH, favourite=True)
    ids["humber"] = angler.pin("Humber", FAR)
    ids["wishlist"] = angler.pin("Wishlist", EAST)
    ids["old"] = angler.pin("Old haunt", WEST_OF_HOME)
    angler.recall(ids["old"])

    april_trips(angler, SPOT, (8.0, 10.0, 12.0))
    for day, rating in ((10, 5), (17, 4), (30, 4)):
        angler.trip(
            f"2026-05-{day:02d}",
            rating,
            point=NORTH,
            conditions=weather_trip(25.0, wind=40.0, pressure=990.0),
        )
    angler.trip("2026-10-10", 5, point=FAR, conditions=weather_trip(10.0))  # one rated trip only
    return ids
