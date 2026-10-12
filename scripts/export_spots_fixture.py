"""Capture real /map/spots responses into tests/fixtures/spots_api_responses.json.

The file is what another tool builds the spots UI against (see docs/spots-api.md):
real responses from the real endpoints over a real build, not hand-written JSON.
Rerun after a build whenever the response shapes change:

    uv run python scripts/export_spots_fixture.py [path/to/fishing.db]

Read-only: it serves the database through the API with a stubbed login and
changes nothing in it. Defaults to the database the app uses.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.storage.database as database  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "spots_api_responses.json"

# (path, why it is in the fixture). Detail requests are filled in from the lists.
LISTS = [
    ("/map/spots/labels", "every user-facing string; the app hardcodes none"),
    (
        "/map/spots?bbox=-80.40,43.17,-80.30,43.25",
        "default tier (headline + normal) in a box on the Grand near Paris",
    ),
    (
        "/map/spots?bbox=-79.75,43.45,-79.55,43.62&kind=river_mouth,creek_junction&tier=1",
        "kind and tier filters: headline mouths and junctions around Port Credit",
    ),
    (
        "/map/spots?bbox=-79.85,42.80,-79.55,42.95&kind=below_dam,public_fishing&tier=3",
        "dams and OSM fishing spots near Dunnville, including unnamed ones",
    ),
]
DETAIL_KINDS = ("below_dam", "creek_junction", "river_mouth", "public_fishing")
# Water bodies: the rivers, creeks and lakes the spots hang off.
WATER_BODY_LISTS = [
    (
        "/map/water-bodies?bbox=-79.75,43.40,-79.60,43.50",
        "every water body whose line crosses a box around Oakville: two creeks and the lake",
    ),
    (
        "/map/water-bodies?type=great_lake,lake",
        "type filter: lakes have geometry null (no outline on file) and a box from their mouths",
    ),
    (
        "/map/water-bodies?type=creek&bbox=-79.9,43.3,-79.5,43.6",
        "creeks only, in a box around Oakville",
    ),
]
WATER_BODY_DETAILS = [
    ("river:credit-river", "a river in full: its line, its stretches, the spots on it"),
    ("great_lake:lake-ontario", "a lake: no geometry, with the river mouths that open onto it"),
    ("creek:bronte-creek", "a creek"),
]
ERRORS = [
    ("/map/spots?kind=lake", "an unknown kind is a 422"),
    ("/map/spots/creek_junction:n0_0", "an unknown spot id is a 404"),
    ("/map/water-bodies?type=ocean", "an unknown water body type is a 422"),
    ("/map/water-bodies/river:no-such-river", "an unknown water body id is a 404"),
]


def main() -> None:
    if len(sys.argv) > 1:
        database.DB_PATH = Path(sys.argv[1])
    from fastapi.testclient import TestClient

    from src.api.main import app, get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    client = TestClient(app)

    entries = []

    def capture(path: str, note: str) -> dict:
        response = client.get(path)
        entries.append(
            {
                "request": f"GET {path}",
                "note": note,
                "status": response.status_code,
                "response": response.json(),
            }
        )
        return response.json()

    seen: dict[str, str] = {}
    for path, note in LISTS:
        body = capture(path, note)
        for feature in body.get("features", []) if path != LISTS[0][0] else []:
            seen.setdefault(feature["properties"]["kind"], feature["id"])
    for kind in DETAIL_KINDS:
        if kind in seen:
            capture(f"/map/spots/{seen[kind]}", f"one {kind} spot in full")
    for path, note in WATER_BODY_LISTS:
        capture(path, note)
    for water_body_id, note in WATER_BODY_DETAILS:
        capture(f"/map/water-bodies/{water_body_id}", note)
    for path, note in ERRORS:
        capture(path, note)

    OUT.write_text(json.dumps({"requests": entries}, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(entries)} responses to {OUT}")


if __name__ == "__main__":
    main()
