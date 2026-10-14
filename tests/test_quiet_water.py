"""Quiet water: explore() results wrapped as spots, with the coverage caveats.

A tiny synthetic score cache stands in for the real parquet, so the real
`explore()` runs end to end and the expected spots are known by construction.
"""

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from src.models.exclusion_zone import ExclusionZone, ExclusionZoneFile
from src.models.ingest_area import IngestArea
from src.models.spot import Spot
from src.models.spot_card import SpotCard
from src.services import quiet_water, spot_labels
from src.services import untapped_potential as untapped
from src.services.quiet_water import (
    CANDIDATES_PER_AREA,
    MIN_SEPARATION_KM,
    TIER_2_PER_AREA,
    TIER_3_PER_AREA,
    build_quiet_water,
    load_exclusions,
)
from src.services.spot_card import build_spot_card
from src.services.spots import build_spots, spots_geojson
from src.storage.spots import count_spots_of_kind, list_spots

BUILT = "2026-10-14T00:00:00+00:00"
HOME = (43.45, -79.72)  # the area centre
AREA = IngestArea(
    label="Bronte Creek Oakville",
    jurisdiction="CA-ON",
    lat=HOME[0],
    lng=HOME[1],
    radius_km=150,  # wide, so a creek strung out over tens of kilometres stays in the box
    sources=["global"],
)
KM_LNG = 1 / (111.32 * 0.7266)  # degrees of longitude per km at 43.45N


def reach(ogf, lat, lng, score, name="", measured=True, order=3, **extra):
    return {
        "ogf_id": ogf,
        "centroid_lat": lat,
        "centroid_lng": lng,
        "stream_order": order,
        "watercourse_name": name,
        "watercourse_type": "Stream",
        "observation_density_25km": 0.0,
        "is_confluence_segment": False,
        "access_is_measured": measured,
        "access_score": 0.5,
        "observation_pressure": 0.1,
        "do_median_mgl": float("nan"),
        "untapped_score_balanced": score,
        "untapped_score_easy": score,
        "untapped_score_adventure": score,
        "untapped_score": score,
        **extra,
    }


def write_scores(path, rows):
    pd.DataFrame(rows).astype({"access_is_measured": "boolean"}).to_parquet(path, index=False)


@pytest.fixture
def scores(tmp_path, monkeypatch):
    """Point the score cache at a file the test writes. Returns the writer."""
    path = tmp_path / "scores.parquet"
    monkeypatch.setattr(untapped, "_PARQUET_PATH", path)

    def write(rows):
        write_scores(path, rows)

    return write


def line_of_reaches(n, name="Black Creek", start_ogf=1000, step_km=3.0, top=0.9, **kw):
    """n named reaches strung along one creek `step_km` apart, best first."""
    return [
        reach(
            start_ogf + i,
            HOME[0],
            HOME[1] + i * step_km * KM_LNG - 0.05,
            round(top - 0.01 * i, 4),
            name=name,
            **kw,
        )
        for i in range(n)
    ]


# ── what is built ────────────────────────────────────────────────────────────


def test_a_ranked_reach_becomes_a_quiet_water_spot_with_a_plain_title(db, scores):
    scores([reach(1, 43.45, -79.72, 0.8, name="Black Creek")])
    out = build_quiet_water(db, "CA-ON", BUILT, [AREA])
    (spot,) = out.spots
    assert spot.kind == "quiet_water"
    assert spot.spot_id.startswith("quiet_water:n7972")
    assert spot.title == "A quiet part of Black Creek"
    assert spot.jurisdiction == "CA-ON"
    assert spot.structure_type == "quiet_reach"
    assert spot.structure_attrs["ogf_id"] == 1
    assert spot.sources[0].kind == "explore_ranking"
    assert Spot.model_validate(spot.model_dump()) == spot


def test_an_unnamed_reach_is_titled_after_the_named_water_beside_it(db, scores):
    scores(
        [
            reach(1, 43.45, -79.72, 0.9, name=""),
            reach(2, 43.45, -79.72 + 1.0 * KM_LNG, 0.1, name="Sheldon Creek"),
        ]
    )
    (spot, *_) = build_quiet_water(db, "CA-ON", BUILT, [AREA]).spots
    assert spot.title == "Quiet water near Sheldon Creek"
    assert spot.signals["named"] is False
    assert "unnamed" not in spot.title.lower()


def test_an_unnamed_reach_with_no_named_water_nearby_is_not_published_and_is_counted(db, scores):
    scores([reach(1, 43.45, -79.72, 0.9, name=""), reach(2, 43.55, -79.9, 0.1, name="Far Creek")])
    out = build_quiet_water(db, "CA-ON", BUILT, [AREA])
    assert [s.title for s in out.spots] == ["A quiet part of Far Creek"]
    assert out.dropped["no_name_within_reach"] == 1


def test_titles_carry_no_jargon(db, scores):
    scores(line_of_reaches(4) + [reach(99, 43.40, -79.70, 0.5, name="")])
    jargon = re.compile(
        r"\b(confluence|tailwater|anchor|stretch|ingest|topology|segment|OHN|ogf)\b", re.I
    )
    for spot in build_quiet_water(db, "CA-ON", BUILT, [AREA]).spots:
        assert not jargon.search(spot.title), spot.title


# ── top N per area, in tiers ─────────────────────────────────────────────────


def test_first_five_are_tier_two_and_the_next_ten_tier_three(db, scores):
    scores(line_of_reaches(30))
    out = build_quiet_water(db, "CA-ON", BUILT, [AREA])
    assert (TIER_2_PER_AREA, TIER_3_PER_AREA) == (5, 10)
    assert len(out.spots) == 15
    tiers = [s.tier for s in out.spots]
    assert tiers.count(2) == 5 and tiers.count(3) == 10
    # best five are tier 2: reaches 1000..1004
    assert {s.structure_attrs["ogf_id"] for s in out.spots if s.tier == 2} == set(range(1000, 1005))


def test_reaches_closer_than_the_minimum_spacing_are_one_spot(db, scores):
    """Neighbouring segments of one creek score almost the same; they are one place."""
    scores(line_of_reaches(10, step_km=0.6))
    out = build_quiet_water(db, "CA-ON", BUILT, [AREA])
    assert MIN_SEPARATION_KM == 2.0
    kept = sorted(s.lng for s in out.spots)
    assert len(out.spots) == 3  # 5.4 km of creek, at least 2 km between spots
    assert all(b - a >= 2.0 * KM_LNG * 0.999 for a, b in zip(kept, kept[1:]))
    assert out.dropped["too_close_to_a_better_reach"] == 7


def test_a_reach_in_two_overlapping_areas_keeps_the_better_tier(db, scores):
    scores(line_of_reaches(8))
    other = AREA.model_copy(update={"label": "Credit River Mississauga", "lat": HOME[0] + 0.1})
    out = build_quiet_water(db, "CA-ON", BUILT, [AREA, other])
    ids = [s.spot_id for s in out.spots]
    assert len(ids) == len(set(ids))
    assert out.dropped["duplicate_across_areas"] > 0


def test_only_the_jurisdictions_own_areas_are_read(db, scores):
    scores(line_of_reaches(3))
    bc = AREA.model_copy(update={"jurisdiction": "CA-BC"})
    out = build_quiet_water(db, "CA-ON", BUILT, [bc])
    assert out.areas_checked == 0 and out.spots == []


def test_the_candidate_list_is_much_longer_than_the_spots_kept():
    assert CANDIDATES_PER_AREA >= 10 * (TIER_2_PER_AREA + TIER_3_PER_AREA)


def test_ties_are_broken_the_same_way_every_time(db, scores):
    rows = [
        reach(10 + i, 43.45, -79.72 + i * 3.0 * KM_LNG - 0.05, 0.7, name=f"Creek {i}")
        for i in range(12)
    ]
    scores(rows)
    first = [s.spot_id for s in build_quiet_water(db, "CA-ON", BUILT, [AREA]).spots]
    scores(list(reversed(rows)))
    second = [s.spot_id for s in build_quiet_water(db, "CA-ON", BUILT, [AREA]).spots]
    assert first == second


def test_the_same_cache_gives_the_same_spot_ids_on_a_rebuild(db, scores):
    scores(line_of_reaches(6))
    a = build_quiet_water(db, "CA-ON", BUILT, [AREA]).spots
    b = build_quiet_water(db, "CA-ON", BUILT, [AREA]).spots
    assert [s.spot_id for s in a] == [s.spot_id for s in b]


# ── nothing about fish, no score ─────────────────────────────────────────────


def test_a_spot_carries_no_score_and_no_pressure_number(db, scores):
    scores([reach(1, 43.45, -79.72, 0.8, name="Black Creek")])
    (spot,) = build_quiet_water(db, "CA-ON", BUILT, [AREA]).spots
    blob = json.dumps(spot.model_dump()).lower()
    assert "score" not in blob and "pressure" not in blob
    assert set(spot.signals) == {"named", "access_is_measured"}


def test_nobody_is_asked_who_has_fished_a_reach(db, scores, monkeypatch):
    """Spots are the same for everyone; explore() runs with no user so none of it is personal."""
    seen = {}
    import src.services.context as context

    real = context.explore

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(context, "explore", spy)
    scores([reach(1, 43.45, -79.72, 0.8, name="Black Creek")])
    build_quiet_water(db, "CA-ON", BUILT, [AREA])
    assert seen["user_id"] == 0


# ── the coverage caveats ─────────────────────────────────────────────────────


def test_access_measured_true_false_and_unknown_are_three_different_caveats(db, scores):
    rows = [
        reach(1, 43.45, -79.72, 0.9, name="Measured Creek", measured=True),
        reach(2, 43.45, -79.72 + 4 * KM_LNG, 0.8, name="Outside Creek", measured=False),
        reach(3, 43.45, -79.72 + 8 * KM_LNG, 0.7, name="Stale Creek", measured=None),
    ]
    scores(rows)
    spots = {s.water_name: s for s in build_quiet_water(db, "CA-ON", BUILT, [AREA]).spots}
    assert spots["Measured Creek"].signals["access_is_measured"] is True
    assert spots["Outside Creek"].signals["access_is_measured"] is False
    assert spots["Stale Creek"].signals["access_is_measured"] is None

    def kinds(s):
        return [c["kind"] for c in spot_labels.caveats_for(s.kind, s.signals)]

    assert kinds(spots["Measured Creek"]) == ["records"]
    assert kinds(spots["Outside Creek"]) == ["records", "access"]
    outside = spot_labels.caveats_for("quiet_water", spots["Outside Creek"].signals)[1]["text"]
    stale = spot_labels.caveats_for("quiet_water", spots["Stale Creek"].signals)[1]["text"]
    assert outside.startswith("Access not measured here")
    assert outside != stale and "predate" in stale


def test_other_kinds_carry_no_caveats():
    assert spot_labels.caveats_for("river_mouth", {}) == []


def test_the_records_caveat_says_fewer_records_is_not_fewer_fish():
    text = spot_labels.QUIET_RECORDS_CAVEAT
    assert "few anglers" in text and "few observers" in text
    assert "says nothing about whether fish are there" in text


# ── First Nations waters ─────────────────────────────────────────────────────


def test_no_reach_inside_an_excluded_water_becomes_a_spot(db, scores):
    inside = reach(1, 43.10, -80.10, 0.95, name="Grand River")  # Six Nations of the Grand River
    outside = reach(2, 43.45, -79.72, 0.5, name="Black Creek")
    scores([inside, outside])
    area = AREA.model_copy(update={"lat": 43.25, "lng": -79.9, "radius_km": 50})
    out = build_quiet_water(db, "CA-ON", BUILT, [area])
    assert [s.water_name for s in out.spots] == ["Black Creek"]
    assert out.dropped["first_nations_waters"] == 1


def test_the_excluded_waters_are_dropped_before_ranking_so_they_use_up_no_slot(db, scores):
    zone = ExclusionZone(id="z", name="Z", bbox=(-80.0, 43.40, -79.0, 43.50), note="n")
    rows = line_of_reaches(8)  # all inside the zone
    rows += [reach(500, 43.55, -79.72, 0.1, name="Outside Creek")]
    scores(rows)
    out = build_quiet_water(db, "CA-ON", BUILT, [AREA], exclusions=[zone])
    assert [s.water_name for s in out.spots] == ["Outside Creek"]


def test_the_ontario_file_covers_the_waters_the_curation_file_leaves_out():
    zones = {z.id: z for z in load_exclusions("CA-ON")}
    assert set(zones) == {
        "grand_brantford_caledonia",
        "thames_delaware_thamesville",
        "st_clair_delta",
    }
    # Boundary points from stretches_ca_on.toml, and a point on Six Nations land.
    assert zones["grand_brantford_caledonia"].contains(
        43.12834, -80.2644
    )  # Brantford (Lorne Bridge)
    assert zones["grand_brantford_caledonia"].contains(43.06597, -79.938)  # Caledonia dam
    assert zones["grand_brantford_caledonia"].contains(43.07, -80.12)  # Ohsweken
    assert zones["thames_delaware_thamesville"].contains(42.89881, -81.42067)  # Delaware
    assert zones["thames_delaware_thamesville"].contains(42.54517, -81.98671)  # Thamesville
    assert zones["st_clair_delta"].contains(42.58, -82.50)  # Walpole Island
    assert not zones["grand_brantford_caledonia"].contains(43.45, -79.72)  # Oakville


def test_a_jurisdiction_with_no_file_excludes_nothing():
    assert load_exclusions("CA-BC") == []


def test_exclusion_boxes_must_be_ordered():
    with pytest.raises(ValueError):
        ExclusionZone(id="z", name="Z", bbox=(-79.0, 43.0, -80.0, 44.0), note="n")
    with pytest.raises(ValueError):
        ExclusionZone(id="Bad Id", name="Z", bbox=(-80.0, 43.0, -79.0, 44.0), note="n")
    with pytest.raises(ValueError):
        ExclusionZoneFile.model_validate({"jurisdiction": "CA-ON", "zone": [], "extra": 1})


# ── no cache ─────────────────────────────────────────────────────────────────


def test_without_the_score_cache_there_is_an_honest_empty_not_a_crash(
    db, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(untapped, "_PARQUET_PATH", tmp_path / "missing.parquet")
    with caplog.at_level("WARNING"):
        out = build_quiet_water(db, "CA-ON", BUILT, [AREA])
    assert out.spots == []
    assert out.cache_missing is True
    assert "compute-untapped" in caplog.text


def test_an_empty_cache_is_the_same_honest_empty(db, scores):
    scores([reach(1, 43.45, -79.72, 0.8, name="X")])
    pd.DataFrame(columns=["ogf_id"]).to_parquet(untapped._PARQUET_PATH)
    assert build_quiet_water(db, "CA-ON", BUILT, [AREA]).cache_missing is True


def test_an_area_with_no_ranked_reaches_says_why(db, scores):
    scores([reach(1, 50.0, -90.0, 0.8, name="Far Creek")])  # nowhere near the area
    out = build_quiet_water(db, "CA-ON", BUILT, [AREA])
    assert out.spots == [] and out.cache_missing is False
    assert any(k.startswith("area_empty_") for k in out.dropped)


# ── the build, the API and the card ──────────────────────────────────────────


@pytest.fixture
def built(db, curation, scores):
    scores(
        line_of_reaches(6) + [reach(77, 43.55, -79.72, 0.4, name="Outside Creek", measured=False)]
    )
    report = build_spots(db, curation, quiet_water_areas=[AREA])
    return db, report


def test_the_build_stores_quiet_water_beside_the_other_spots_and_reports_it(built):
    db, report = built
    assert report.quiet_water_areas == 1
    assert report.quiet_water_candidates == 7
    assert report.quiet_water_cache_missing is False
    assert report.by_kind["quiet_water"] == count_spots_of_kind(db, "quiet_water") == 7
    assert (report.by_tier["2"], report.by_tier["3"]) == (5, 2)
    assert report.spots_total == 7
    assert report.spots_without_water_body == 0  # quiet water has no water body by design


def test_a_rebuild_replaces_quiet_water_wholesale(built, scores, curation):
    db, _ = built
    scores([reach(1, 43.45, -79.72, 0.8, name="Only Creek")])
    build_spots(db, curation, quiet_water_areas=[AREA])
    assert [s.water_name for s in list_spots(db, max_tier=3)] == ["Only Creek"]


def test_the_build_without_a_cache_still_succeeds_and_says_so(db, curation, tmp_path, monkeypatch):
    monkeypatch.setattr(untapped, "_PARQUET_PATH", tmp_path / "missing.parquet")
    report = build_spots(db, curation, quiet_water_areas=[AREA])
    assert report.quiet_water_cache_missing is True
    assert "quiet_water" not in report.by_kind


@pytest.fixture
def client(built, monkeypatch):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app, get_current_user

    db, _ = built
    monkeypatch.setattr(
        database, "DB_PATH", Path(db.conn.execute("PRAGMA database_list").fetchone()[2])
    )
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_the_kind_filter_returns_quiet_water_with_its_caveats(client):
    body = client.get("/map/spots?kind=quiet_water").json()
    assert body["meta"]["count"] == 5
    assert "notes" not in body["meta"]
    for f in body["features"]:
        props = f["properties"]
        assert props["kind"] == "quiet_water"
        assert props["title"].startswith("A quiet part of")
        assert props["caveats"][0]["kind"] == "records"
        assert props["caveats"][0]["text"] == spot_labels.QUIET_RECORDS_CAVEAT


def test_the_outside_footprint_reach_says_access_was_not_measured(client):
    body = client.get("/map/spots?kind=quiet_water&tier=3").json()
    outside = [f for f in body["features"] if f["properties"]["water_name"] == "Outside Creek"]
    assert len(outside) == 1
    texts = [c["text"] for c in outside[0]["properties"]["caveats"]]
    assert texts[1].startswith("Access not measured here")


def test_quiet_water_is_listed_last_among_the_kinds_at_its_tier(client, built):
    db, _ = built
    from src.storage.spots import replace_spots

    mouth = Spot(
        spot_id="river_mouth:n7972000_4345000",
        kind="river_mouth",
        jurisdiction="CA-ON",
        lat=43.45,
        lng=-79.72,
        title="Where the Credit meets Lake Ontario",
        tier=2,
        structure_type="lake_mouth",
        built_at=BUILT,
    )
    replace_spots(db, "CA-ON", [*list_spots(db, max_tier=3), mouth])
    kinds = [f["properties"]["kind"] for f in spots_geojson(db)["features"]]
    assert kinds[0] == "river_mouth" and set(kinds[1:]) == {"quiet_water"}
    assert spot_labels.KIND_ORDER[-1] == "quiet_water"


def test_quiet_water_not_built_is_said_in_words_when_it_is_asked_for(db):
    body = spots_geojson(db, kinds=["quiet_water"])
    assert body["features"] == []
    assert body["meta"]["notes"] == [spot_labels.QUIET_WATER_NOT_BUILT]
    # Not asked for: no note.
    assert "notes" not in spots_geojson(db, kinds=["river_mouth"])["meta"]


def test_the_spot_card_for_quiet_water_has_the_caveats_and_no_made_up_fish_claim(built):
    db, _ = built
    spot = list_spots(db, max_tier=3)[0]
    card = build_spot_card(db, spot)
    assert SpotCard.model_validate(card.model_dump())
    assert [c.kind for c in card.caveats][0] == "records"
    assert card.hook.kind == "honest_empty"  # nothing recorded within 2 km, said plainly
    assert card.why_gather.what_it_is == spot_labels.KINDS["quiet_water"]["one_liner"]
    assert card.why_gather.general.provenance == "inference"
    assert card.meta.special_here == "not_applicable"
    blob = json.dumps(card.model_dump()).lower()
    for word in ("best", "hot spot", "hotspot", "popular", "productive", "prime"):
        assert word not in blob


def test_the_detail_endpoint_serves_the_quiet_water_card(client):
    spot_id = client.get("/map/spots?kind=quiet_water").json()["features"][0]["id"]
    detail = client.get(f"/map/spots/{spot_id}").json()
    assert detail["kind"] == "quiet_water"
    assert detail["card"]["caveats"][0]["text"] == spot_labels.QUIET_RECORDS_CAVEAT
    assert detail["water_body"] is None


def test_the_labels_endpoint_carries_the_quiet_water_words(client):
    body = client.get("/map/spots/labels").json()
    qw = body["quiet_water"]
    assert qw["titles"] == spot_labels.QUIET_TITLES
    assert qw["caveats"]["access_not_measured"].startswith("Access not measured here")
    assert qw["not_built"] == spot_labels.QUIET_WATER_NOT_BUILT
    assert body["kinds"][-1]["kind"] == "quiet_water" and body["kinds"][-1]["distinct"] is True


def test_every_quiet_water_string_passes_the_jargon_lint():
    jargon = re.compile(
        r"\b(confluence|tailwater|anchor|stretch|ingest|topology|segment|OHN|ogf)\b", re.I
    )
    for text in [
        *spot_labels.QUIET_TITLES.values(),
        spot_labels.QUIET_RECORDS_CAVEAT,
        spot_labels.ACCESS_NOT_MEASURED,
        spot_labels.ACCESS_COVERAGE_UNKNOWN,
        spot_labels.QUIET_WATER_NOT_BUILT,
    ]:
        assert not jargon.search(text), text
    assert set(spot_labels.QUIET_TITLES.values()) <= set(spot_labels.all_strings())


def test_the_module_documents_that_it_needs_the_cache():
    assert "parquet cache" in quiet_water.__doc__ and "honest empty" in quiet_water.__doc__
