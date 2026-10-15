"""Capture real /map/sections responses into tests/fixtures/sections_api_responses.json.

The file is what another tool builds the sections layer against (see the "Sections"
section of docs/spots-api.md). Rerun after a build whenever a response shape changes:

    uv run python scripts/export_sections_fixture.py path/to/fishing.db

Read-only on the database you give it: it is copied to a throwaway file first, and
the copy is what the API serves. The river sections, spots and water bodies are the
build's own. Two things in the file are NOT real, and each entry's `note` says so:

- No build has a lake outline yet (the OHN Waterbody match found none), so the
  lake entries come from a tiny hand-drawn database in tests/test_sections.py: an
  EXAMPLE lake with an island, a drop-off and a hump. They show the shape.
- Weed reports are private and there is nothing real to capture, so the example
  weed edge is added to the throwaway copy. It is not a sighting.

Never touches the database you give it.
"""

import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.storage.database as database  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "sections_api_responses.json"

# Around Toronto: the box the walkthrough found noisy.
CITY_BBOX = "-79.75,43.55,-79.10,43.90"
LOWER_HUMBER = "humber-woodbridge-humber-bay"
LISTS = [
    (
        f"/map/sections?bbox={CITY_BBOX}",
        "the overview map over Toronto: one shape per river section, each with counts of "
        "what is inside it. Lakes with no outline come back as `labels`, not shapes",
    ),
    (
        "/map/sections?bbox=-79.90,43.30,-79.50,43.60&type=creek",
        "the type filter: only creeks, around Oakville. `meta.type_counts` still counts "
        "every type in view",
    ),
    ("/map/sections?bbox=0,0,1,1", "an empty view is an empty list, not an error"),
]
DETAILS = [
    (LOWER_HUMBER, "Lower Humber: every point of interest on the section, as pins"),
    ("credit-streetsville-port-credit", "Lower Credit: mouth and creek-junction pins"),
]
ERRORS = [
    ("/map/sections?type=ocean", "an unknown water type is a 422"),
    ("/map/sections?bbox=1,2,3", "a malformed bbox is a 422"),
    ("/map/sections/no-such-section", "an unknown section id is a 404"),
]


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: export_sections_fixture.py path/to/fishing.db")
    source = Path(sys.argv[1])
    if not source.exists():
        sys.exit(f"{source} does not exist")

    from fastapi.testclient import TestClient

    from src.api.main import app, get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    client = TestClient(app)
    entries: list[dict] = []

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

    real_path = database.DB_PATH
    scratch = Path(tempfile.mkdtemp())
    try:
        copy = scratch / "sections.db"
        shutil.copy(source, copy)
        database.DB_PATH = copy
        for path, note in LISTS:
            capture(path, note)
        for section_id, note in DETAILS:
            capture(f"/map/sections/{section_id}", note)

        # An EXAMPLE weed edge on the lower Humber, in the throwaway copy only.
        from src.models.weed_report import WeedGeometry
        from src.storage import weed_reports as weed_store
        from src.storage.stretches import get_stretch

        db = database.get_db(copy)
        stretch = get_stretch(db, LOWER_HUMBER)
        if stretch is not None:
            line = stretch.geometry["coordinates"]
            mid = line[len(line) // 2]
            body_id = next(
                (
                    r["water_body_id"]
                    for r in db.execute_returning_dicts(
                        "SELECT water_body_id, stretch_ids_json FROM water_bodies"
                    )
                    if LOWER_HUMBER in json.loads(r["stretch_ids_json"])
                    and r["water_body_id"].startswith(("river:", "creek:"))
                ),
                None,
            )
            if body_id is not None:
                weed_store.create_weed_report(
                    db,
                    1,
                    body_id,
                    stretch.jurisdiction,
                    WeedGeometry.model_validate({"type": "Point", "coordinates": mid}),
                    date(2026, 9, 20),
                    "Example note: weeds along the inside bend",
                )
                capture(
                    f"/map/sections?bbox={CITY_BBOX}&type=river",
                    "the same layer for an angler with one weed edge reported on the lower "
                    "Humber: `counts.weed_reports` is 1 on that section and only that angler "
                    "ever sees it. EXAMPLE weed edge added to a throwaway copy of the database",
                )
                capture(
                    f"/map/sections/{LOWER_HUMBER}",
                    "the lower Humber with that EXAMPLE weed edge as a `weed_report` pin: "
                    'private, "reported, not verified", never mixed into spot text',
                )
        for path, note in ERRORS:
            capture(path, note)
    finally:
        database.DB_PATH = real_path
        shutil.rmtree(scratch, ignore_errors=True)

    # A lake with an outline, from the hand-drawn test cast (no build has one yet).
    from src.auth.auth import generate_invite_code, redeem_invite_code
    from tests.test_sections import load

    scratch = Path(tempfile.mkdtemp())
    try:
        database.DB_PATH = scratch / "example_lake.db"
        db = database.get_db()
        load(db)
        got = redeem_invite_code(db, generate_invite_code(db), "example-you")
        app.dependency_overrides.pop(get_current_user, None)
        client = TestClient(app, headers={"Authorization": f"Bearer {got['token']}"})
        capture(
            "/map/sections?type=lake",
            "EXAMPLE lakes from a tiny hand-drawn database, not a build: Rice Lake has an "
            "outline so it is a shape; Lake Ontario has none so it is only a label",
        )
        capture(
            "/map/sections/lake:rice-lake",
            "EXAMPLE lake in full: shoreline and depth features, an island and a hump "
            "carrying their `shape`, and the river mouth that opens onto it. Hand-drawn data",
        )
    finally:
        database.DB_PATH = real_path
        shutil.rmtree(scratch, ignore_errors=True)

    OUT.write_text(json.dumps({"requests": entries}, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(entries)} responses to {OUT}")


if __name__ == "__main__":
    main()
