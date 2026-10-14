"""Capture real /map/spots responses into tests/fixtures/spots_api_responses.json.

The file is what another tool builds the spots UI against (see docs/spots-api.md):
real responses from the real endpoints over a real build, not hand-written JSON.
Rerun after a build whenever the response shapes change:

    uv run python scripts/export_spots_fixture.py [path/to/fishing.db [path/to/records.db]]

Read-only: it serves the database through the API with a stubbed login and
changes nothing in it. Defaults to the database the app uses.

The "recorded more often here" card needs real fish records along a river. When a
second database is given, its iNaturalist and GBIF records are copied into a
throwaway copy of the first and the spot with the strongest comparison is
captured from that; without one the entry is skipped, never invented.

Spot cards need fish records to show a species list or a protected-species care
message, and a fresh build has none. So the card examples that need records are
captured from a throwaway COPY of the database with a few illustrative records
added (see EXAMPLE_RECORDS); the copy is deleted afterwards and the entry's
`note` says the records are examples. Every other entry is the database as it is.
"""

import json
import shutil
import sys
import tempfile
from datetime import date
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
        "/map/spots?kind=below_dam,public_fishing&tier=3",
        "every dam and OSM fishing spot in the build, including unnamed ones",
    ),
    (
        "/map/spots?kind=quiet_water",
        "quiet water: reaches with few public records, each with its caveats. "
        "Needs the explore score cache; without it the list is empty and meta.notes says so",
    ),
    (
        "/map/spots?kind=quiet_water&tier=3&near=43.47,-79.69,15",
        "quiet water at every tier within 15 km of home, nearest first",
    ),
]
DETAIL_KINDS = ("below_dam", "creek_junction", "river_mouth", "public_fishing", "quiet_water")
# Water bodies: the rivers, creeks and lakes the spots hang off.
WATER_BODY_LISTS = [
    (
        "/map/water-bodies?bbox=-79.75,43.40,-79.60,43.50",
        "every water body whose line crosses a box around Oakville: two creeks and the lake",
    ),
    (
        "/map/water-bodies?type=great_lake,lake",
        "type filter: the lakes, with their geometry when an outline was matched and a box",
    ),
    (
        "/map/water-bodies?type=creek&bbox=-79.9,43.3,-79.5,43.6",
        "creeks only, in a box around Oakville",
    ),
]
LAYERS = [
    ("/map/layers/getting-there?bbox=-80.40,43.17,-80.30,43.25", "getting there features in box"),
    ("/map/layers/bridges?bbox=-80.40,43.17,-80.30,43.25", "bridges features in box"),
]
WATER_BODY_DETAILS = [
    ("river:credit-river", "a river in full: its line, its stretches, the spots on it"),
    ("great_lake:lake-ontario", "a lake: its outline, its shoreline features, the mouths onto it"),
    ("creek:bronte-creek", "a creek"),
]
# Shoreline features for a lake whose outline the build matched. Captured only
# when the build actually has one, so the fixture never invents an empty set.
FEATURE_LAKES = ("lake:rice-lake", "great_lake:lake-ontario")
# Illustrative records, added to the throwaway copy only. Not observations: they
# exist so the fixture can show a species list and the protected-species case.
EXAMPLE_FISH = [  # (scientific, common, date, survey?)
    ("Semotilus atromaculatus", "Creek Chub", "2024-06-03", False),
    ("Rhinichthys atratulus", "Blacknose Dace", "2023-08-19", False),
    ("Etheostoma caeruleum", "Rainbow Darter", "2019-07-10", True),
    ("Catostomus commersonii", "White Sucker", "2022-05-02", False),
]
EXAMPLE_LISTED = ("Clinostomus elongatus", "Redside Dace", "2022-06-11")
# Where Mullet Creek joins the Credit River: Redside Dace is a Credit-watershed
# fish, so the protected example is at least a plausible place for one. Falls
# back to any far-away junction when the build lacks it.
PROTECTED_EXAMPLE = "creek_junction:n7966741_4355566"
ERRORS = [
    ("/map/spots?kind=lake", "an unknown kind is a 422"),
    ("/map/spots/creek_junction:n0_0", "an unknown spot id is a 404"),
    ("/map/water-bodies?type=ocean", "an unknown water body type is a 422"),
    ("/map/water-bodies/river:no-such-river", "an unknown water body id is a 404"),
    ("/map/water-bodies/river:no-such-river/features", "an unknown water body id is a 404"),
]


def seed_examples_and_capture(client, capture, seen: dict[str, str]) -> None:
    """Card examples that need records, taken from a throwaway copy with examples added."""
    from src.models.gbif_observation import GBIFObservation
    from src.models.observation import Observation
    from src.models.species_range import SpeciesRange
    from src.storage.catches import insert_catch
    from src.storage.gbif_observations import upsert_gbif_observations
    from src.storage.observations import upsert_observations
    from src.storage.species_ranges import upsert_species_ranges
    from src.storage.spots import get_spot

    source = database.get_db()
    with_fish_id = seen.get("below_dam") or next(iter(seen.values()))
    here = get_spot(source, with_fish_id)
    # A different part of the province, so the two examples share no records.
    far = [
        f["id"]
        for f in client.get("/map/spots?tier=1&kind=creek_junction").json()["features"]
        if abs(f["geometry"]["coordinates"][0] - here.lng) > 0.3
        or abs(f["geometry"]["coordinates"][1] - here.lat) > 0.3
    ]
    protected_id = (
        PROTECTED_EXAMPLE
        if get_spot(source, PROTECTED_EXAMPLE)
        else far[0]
        if far
        else seen.get("creek_junction", with_fish_id)
    )
    spots = {sid: get_spot(source, sid) for sid in {with_fish_id, protected_id}}
    real_path = database.DB_PATH
    scratch = Path(tempfile.mkdtemp())
    copy = scratch / "examples.db"
    shutil.copy(real_path, copy)
    try:
        db = database.get_db(copy)
        key = 1_000_000
        for sid, spot in spots.items():
            for i, (sci, common, when, survey) in enumerate(EXAMPLE_FISH):
                key += 1
                near = spot.lat + 0.002 * (i + 1), spot.lng - 0.002 * (i + 1)
                if survey:
                    upsert_gbif_observations(
                        db,
                        [
                            GBIFObservation(
                                gbif_key=key,
                                species=sci,
                                common_name=common,
                                taxon_key=key,
                                lat=near[0],
                                lng=near[1],
                                observed_on=date.fromisoformat(when),
                                dataset_name="Example fish survey",
                                basis_of_record="HUMAN_OBSERVATION",
                                jurisdiction="CA-ON",
                                event_id="example-event-1",
                                sampling_protocol="electrofishing",
                            )
                        ],
                    )
                else:
                    upsert_observations(
                        db,
                        [
                            Observation(
                                observation_id=key,
                                species=sci,
                                common_name=common,
                                lat=near[0],
                                lng=near[1],
                                observed_on=date.fromisoformat(when),
                                quality_grade="research",
                                jurisdiction="CA-ON",
                            )
                        ],
                    )
        sci, common, when = EXAMPLE_LISTED
        spot = spots[protected_id]
        upsert_observations(
            db,
            [
                Observation(
                    observation_id=key + 1,
                    species=sci,
                    common_name=common,
                    lat=spot.lat + 0.003,
                    lng=spot.lng + 0.003,
                    observed_on=date.fromisoformat(when),
                    quality_grade="research",
                    jurisdiction="CA-ON",
                )
            ],
        )
        upsert_species_ranges(
            db,
            [
                SpeciesRange(
                    species=common,
                    scientific_name=sci,
                    native_to_ontario=True,
                    native_to_great_lakes=True,
                    general_range="Southern Ontario",
                    jurisdictions_present=["CA-ON"],
                    sara_status="Endangered",
                )
            ],
        )
        database.DB_PATH = copy
        capture(
            f"/map/spots/{with_fish_id}",
            "card with fish records, for a logged-in angler who has caught nothing yet, so "
            "the hook is `new_to_you` and `card.new_to_you` names every recorded species. "
            "EXAMPLE records added to a throwaway copy of the database to show the shape; "
            "they are not observations",
        )
        # Two confirmed catches by the same angler (user 1): those species leave the
        # per-user list, an unconfirmed guess (not inserted here) never would have.
        session_id = db["sessions"].insert({"date": "2026-06-01", "user_id": 1}).last_pk
        stop_id = (
            db["stops"]
            .insert({"session_id": session_id, "location_text": "Example", "species_caught": "[]"})
            .last_pk
        )
        for common in ("Creek Chub", "Rainbow Darter"):
            insert_catch(
                db,
                stop_id=stop_id,
                session_id=session_id,
                user_id=1,
                species=common,
                species_confirmed=True,
            )
        capture(
            f"/map/spots/{with_fish_id}",
            "the same card after the angler confirmed catching Creek Chub and Rainbow Darter: "
            "the hook counts what is left and `card.new_to_you` lists only those. A different "
            "user asking gets the previous entry, not this one. EXAMPLE records and catches in "
            "a throwaway copy of the database",
        )
        box = (
            f"{here.lng - 0.05:.4f},{here.lat - 0.05:.4f},"
            f"{here.lng + 0.05:.4f},{here.lat + 0.05:.4f}"
        )
        capture(
            f"/map/spots?bbox={box}&tier=3&new_to_me=true",
            "the New to me filter for that angler: only spots with a recorded species they have "
            "not caught, each with `properties.new_to_you`. The care-message spot is absent "
            "unless a non-listed fish is recorded there. EXAMPLE records and catches in a "
            "throwaway copy of the database",
        )
        capture(
            f"/map/spots/{protected_id}",
            "protected-species card: the hook is a care message and the listed fish is "
            "named nowhere, not even in `card.new_to_you`, which lists only the other fish "
            "this angler has not caught. An EXAMPLE listed record was added to a throwaway "
            "copy of the database; it is not an observation",
        )
    finally:
        database.DB_PATH = real_path
        shutil.rmtree(scratch, ignore_errors=True)


def capture_special_here(client, capture, records_db: Path) -> None:
    """The card with a "recorded more often here" line, from a copy holding real records.

    Copies the iNaturalist and GBIF tables of `records_db` into a throwaway copy
    of the database being served, then captures the spot whose comparison rests
    on the most records. Nothing is added by hand: the counts are the records'.
    """
    import sqlite3

    from src.services.context.special import special_here
    from src.storage.spots import list_spots

    real_path = database.DB_PATH
    scratch = Path(tempfile.mkdtemp())
    copy = scratch / "records.db"
    shutil.copy(real_path, copy)
    try:
        conn = sqlite3.connect(copy)
        conn.execute("ATTACH DATABASE ? AS src", [str(records_db)])
        for table in ("observations", "gbif_observations"):
            conn.execute(f"INSERT OR IGNORE INTO {table} SELECT * FROM src.{table}")
        conn.commit()
        conn.close()
        db = database.get_db(copy)
        best = None
        for spot in list_spots(db, max_tier=3):
            if spot.kind == "quiet_water":
                continue
            result = special_here(db, spot)
            if result.status == "found" and (
                best is None or result.here_total + result.elsewhere_total > best[0]
            ):
                best = (result.here_total + result.elsewhere_total, spot.spot_id)
        if best is None:
            print("no spot has a recorded-more-often-here comparison; entry skipped")
            return
        database.DB_PATH = copy
        capture(
            f"/map/spots/{best[1]}",
            "card with a recorded-more-often-here line. Captured from a throwaway copy of the "
            "database that also holds the real iNaturalist and GBIF records ingested in the main "
            "project database; the counts in the line are those records'",
        )
    finally:
        database.DB_PATH = real_path
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:
    if len(sys.argv) > 1:
        database.DB_PATH = Path(sys.argv[1])
    records_db = Path(sys.argv[2]) if len(sys.argv) > 2 else None
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
    if not seen:
        sys.exit("no spots in the database: run `make build-stretches` first")
    for kind in DETAIL_KINDS:
        if kind in seen:
            capture(f"/map/spots/{seen[kind]}", f"one {kind} spot in full, with its card")

    # An honest-empty card: the first spot whose hook has nothing specific to say.
    every_spot = client.get("/map/spots?tier=3").json()["features"]
    for spot_id in dict.fromkeys([f["id"] for f in every_spot]):
        card = client.get(f"/map/spots/{spot_id}").json()["card"]
        if card["hook"]["kind"] == "honest_empty":
            capture(
                f"/map/spots/{spot_id}",
                "honest-empty card: no fish records within 2 km, said plainly",
            )
            break

    seed_examples_and_capture(client, capture, seen)
    if records_db is not None:
        capture_special_here(client, capture, records_db)

    for path, note in WATER_BODY_LISTS:
        capture(path, note)
    for path, note in LAYERS:
        capture(path, note)
    # include examples with filters if they don't error
    try:
        capture(
            "/map/spots?near=43.20,-80.35&bbox=-80.40,43.17,-80.30,43.25", "near filter example"
        )
    except Exception:
        pass
    for water_body_id, note in WATER_BODY_DETAILS:
        detail = capture(f"/map/water-bodies/{water_body_id}", note)
        # Only a lake with an outline has shoreline features to capture.
        if isinstance(detail, dict) and detail.get("geometry"):
            capture(
                f"/map/water-bodies/{water_body_id}/features",
                "a lake's shoreline features: inlets, outlets, islands, points, peninsulas",
            )
    for water_body_id in FEATURE_LAKES:
        detail = client.get(f"/map/water-bodies/{water_body_id}")
        if detail.status_code != 200:
            continue
        features = client.get(f"/map/water-bodies/{water_body_id}/features")
        if features.status_code == 200 and features.json()["features"]:
            capture(
                f"/map/water-bodies/{water_body_id}/features",
                "shoreline features derived from a matched lake outline",
            )
    for path, note in ERRORS:
        capture(path, note)

    OUT.write_text(json.dumps({"requests": entries}, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(entries)} responses to {OUT}")


if __name__ == "__main__":
    main()
