"""Capture real /map/weed-reports responses into tests/fixtures/weed_reports_api_responses.json.

The file is what another tool builds the weed-report UI against (see
docs/spots-api.md): responses from the real routes, with real logins, over a
throwaway database. Rerun whenever a response shape or a label changes:

    uv run python scripts/export_weed_reports_fixture.py

Weed reports are private, so there is no real data to capture. The water bodies
are the real ones in tests/fixtures/spots_api_responses.json (the Credit River's
and Bronte Creek's actual drawn lines; Lake Ontario, which has no outline on
file). The reports are EXAMPLES made up for the fixture: points and lines laid
on those real lines, with example dates and notes. They are not sightings. A
second example user is logged in to show that nothing leaks between users.

Never touches the real database: it builds a temporary one and deletes it.
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.storage.database as database  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "weed_reports_api_responses.json"
SPOTS_FIXTURE = ROOT / "tests" / "fixtures" / "spots_api_responses.json"
BUILT_KEYS = ("display_name", "spots", "also_here_spots")


def real_water_bodies() -> list:
    from src.models.water_body import WaterBody

    entries = json.loads(SPOTS_FIXTURE.read_text())["requests"]
    wanted = ("river:credit-river", "creek:bronte-creek", "great_lake:lake-ontario")
    bodies = []
    for e in entries:
        for wb_id in wanted:
            if e["request"] == f"GET /map/water-bodies/{wb_id}" and e["status"] == 200:
                bodies.append(
                    WaterBody.model_validate(
                        {k: v for k, v in e["response"].items() if k not in BUILT_KEYS}
                    )
                )
    if len(bodies) != len(wanted):
        sys.exit("tests/fixtures/spots_api_responses.json is missing a water body this needs")
    return bodies


def vertex_on(body, index: int) -> list[float]:
    """A real vertex of a water body's drawn line, so an example sits exactly on it."""
    parts = body.geometry["coordinates"]
    flat = [p for part in parts for p in part] if isinstance(parts[0][0], list) else parts
    return flat[index]


def main() -> None:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.auth.auth import generate_invite_code, redeem_invite_code
    from src.storage.spots import replace_spots

    with tempfile.TemporaryDirectory() as scratch:
        database.DB_PATH = Path(scratch) / "weed_reports_example.db"
        db = database.get_db()
        bodies = real_water_bodies()
        by_id = {b.water_body_id: b for b in bodies}
        replace_spots(db, "CA-ON", [], bodies, [])

        def login(name: str) -> dict:
            got = redeem_invite_code(db, generate_invite_code(db), name)
            return {"Authorization": f"Bearer {got['token']}"}

        you, someone_else = login("example-you"), login("example-someone-else")
        client = TestClient(app)
        entries: list[dict] = []

        def call(method: str, path: str, note: str, who=None, body=None) -> dict:
            response = client.request(method, path, json=body, headers=who or you)
            entries.append(
                {
                    "request": f"{method} {path}",
                    **({"body": body} if body is not None else {}),
                    "note": note,
                    "status": response.status_code,
                    "response": response.json(),
                }
            )
            return response.json()

        credit, bronte = by_id["river:credit-river"], by_id["creek:bronte-creek"]
        a, b, c = (vertex_on(credit, i) for i in (10, 11, 12))

        call("GET", "/map/weed-reports/labels", "every user-facing word and tolerance")
        call("GET", "/map/weed-reports", "no reports yet: an empty list, not an error")
        point = call(
            "POST",
            "/map/weed-reports",
            "report a weed edge as a point (EXAMPLE report, not a sighting)",
            body={
                "water_body_id": "river:credit-river",
                "geometry": {"type": "Point", "coordinates": a},
                "observed_on": "2026-10-04",
                "note": "Example: weeds thin out below the riffle.",
            },
        )
        edge = call(
            "POST",
            "/map/weed-reports",
            "report a weed edge as a line along the water (EXAMPLE report, not a sighting)",
            body={
                "water_body_id": "river:credit-river",
                "geometry": {"type": "LineString", "coordinates": [b, c]},
                "observed_on": "2026-09-21",
            },
        )
        call(
            "POST",
            "/map/weed-reports",
            "a point on a different water body (EXAMPLE report, not a sighting)",
            body={
                "water_body_id": "creek:bronte-creek",
                "geometry": {"type": "Point", "coordinates": vertex_on(bronte, 3)},
                "observed_on": "2026-08-15",
                "note": "Example: lilies along the near bank.",
            },
        )
        call(
            "POST",
            "/map/weed-reports",
            "422: the position is not on or near the water body's drawn line",
            body={
                "water_body_id": "river:credit-river",
                "geometry": {"type": "Point", "coordinates": [a[0] + 0.05, a[1] + 0.05]},
                "observed_on": "2026-10-04",
            },
        )
        call(
            "POST",
            "/map/weed-reports",
            "422: a water body with no outline on file cannot take a report",
            body={
                "water_body_id": "great_lake:lake-ontario",
                "geometry": {"type": "Point", "coordinates": [-79.4, 43.55]},
                "observed_on": "2026-10-04",
            },
        )
        call(
            "POST",
            "/map/weed-reports",
            "404: unknown water body",
            body={
                "water_body_id": "river:no-such-river",
                "geometry": {"type": "Point", "coordinates": a},
                "observed_on": "2026-10-04",
            },
        )
        call(
            "POST",
            "/map/weed-reports",
            "422: the date is in the future",
            body={
                "water_body_id": "river:credit-river",
                "geometry": {"type": "Point", "coordinates": a},
                "observed_on": "2999-01-01",
            },
        )
        call("GET", "/map/weed-reports", "the caller's reports, newest sighting first")
        call(
            "GET",
            "/map/weed-reports?water_body_id=river:credit-river",
            "narrowed to one water body",
        )
        call("GET", "/map/weed-reports/layer", "the map layer: GeoJSON, each labelled and dated")
        call(
            "GET",
            "/map/weed-reports/layer?bbox=-80.1,43.4,-79.5,43.9",
            "the map layer in a box",
        )
        call("GET", f"/map/weed-reports/{point['weed_report_id']}", "one report")
        call(
            "PATCH",
            f"/map/weed-reports/{point['weed_report_id']}",
            "change the date and clear the note; only what is sent changes",
            body={"observed_on": "2026-10-05", "note": None},
        )
        call(
            "GET",
            f"/map/weed-reports/{edge['weed_report_id']}",
            "another user asks for this id: 404, same as an id nobody has used",
            who=someone_else,
        )
        call(
            "GET",
            "/map/weed-reports/layer",
            "another user's layer: empty, whatever the first user has reported",
            who=someone_else,
        )
        call(
            "DELETE",
            f"/map/weed-reports/{edge['weed_report_id']}",
            "another user deletes it: 404, and it is untouched",
            who=someone_else,
        )
        call("DELETE", f"/map/weed-reports/{edge['weed_report_id']}", "delete your own report")
        call("GET", f"/map/weed-reports/{edge['weed_report_id']}", "gone: 404")

    OUT.write_text(json.dumps({"requests": entries}, indent=2) + "\n")
    print(f"wrote {len(entries)} responses to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
