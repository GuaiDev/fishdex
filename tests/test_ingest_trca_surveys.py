"""Tests for the TRCA fish community survey adapter.

Fixture is a real slice of the published CSV (two stations, including a known
replicate pair) rather than a hand-written one — both of the bugs these guard
came from the real data's shape, and neither would have appeared in a fixture
I invented.
"""

import importlib
import logging
from pathlib import Path

from src.storage.database import get_db
from src.storage.fish_surveys import (
    survey_events_near,
    upsert_fish_surveys,
)

_trca = importlib.import_module("src.ingest.jurisdictions.ca_on.trca_surveys")

FIXTURE = Path(__file__).parent / "fixtures" / "trca_fish_community.csv"


def _records():
    return _trca.parse_survey_records(FIXTURE, source_url="https://example.invalid/fixture.csv")


# ── UTM conversion ────────────────────────────────────────────────────────────


def test_utm_converts_into_the_toronto_region():
    """TRCA publishes UTM zone 17N. A wrong zone or datum still yields a real
    number, so the only way to catch it is to check where the point lands."""
    # CC002WM as published
    got = _trca.utm_to_latlng(660295.0, 4858871.0, 17)
    assert got is not None
    lat, lng = got
    assert 43.0 < lat < 44.5, f"latitude {lat} is not the Toronto region"
    assert -80.0 < lng < -78.5, f"longitude {lng} is not the Toronto region"


def test_utm_rejects_transposed_easting_and_northing():
    """Swapping the arguments is the classic UTM mistake and produces a
    plausible float, not an error. The bounds check is the only guard."""
    assert _trca.utm_to_latlng(4858871.0, 660295.0, 17) is None


def test_utm_rejects_a_wrong_zone():
    assert _trca.utm_to_latlng(660295.0, 4858871.0, 11) is None


# ── replicate merging ─────────────────────────────────────────────────────────


def test_replicate_rows_are_summed_not_dropped(caplog):
    """The regression this guards.

    TRCA publishes one row per pass, so a station visit can hold two rows for
    the same species with different numbers — CC002WM on 2021-06-23 has brook
    stickleback at 80 fish / 40.2 g and again at 58 fish / 38 g. Keying on
    (station, visit, species) alone silently kept the last and discarded the
    other, losing real catch.
    """
    with caplog.at_level(logging.INFO):
        recs = _records()

    key = [
        r
        for r in recs
        if r.station_name == "CC002WM" and r.species_common_name == "Brook Stickleback"
    ]
    assert len(key) == 1, "replicates should collapse to one record per species per visit"
    assert key[0].total_count == 80 + 58, (
        f"replicate counts not summed: got {key[0].total_count}, expected 138"
    )
    assert "merged" in caplog.text, "a merge must be reported, not silent"


def test_one_record_per_station_visit_species():
    recs = _records()
    keys = [(r.station_name, r.visit_date, r.species_common_name) for r in recs]
    assert len(keys) == len(set(keys))


def test_none_is_not_treated_as_zero_when_merging():
    """ "not published" is not a count of nothing."""
    assert _trca._add(None, None) is None
    assert _trca._add(5, None) == 5
    assert _trca._add(None, 5) == 5
    assert _trca._add(2, 3) == 5


# ── parsing ───────────────────────────────────────────────────────────────────


def test_parses_the_published_date_format():
    """TRCA publishes M/D/YYYY, not ISO."""
    recs = _records()
    dated = [r for r in recs if r.visit_date is not None]
    assert dated, "no visit_date parsed from the fixture"
    assert all(r.visit_date.year >= 2000 for r in dated)


def test_counts_and_weights_survive():
    """Abundance is the whole reason this source matters."""
    recs = _records()
    assert any(r.total_count and r.total_count > 0 for r in recs)
    assert any(r.total_weight_g and r.total_weight_g > 0 for r in recs)


def test_unmapped_common_name_keeps_the_record():
    """A name that does not map to a scientific name is still a real count.
    Dropping the row would discard abundance to tidy a join."""
    recs = _records()
    assert all(r.species_common_name for r in recs)
    assert all(r.survey_program == "TRCA RWMP" for r in recs)
    assert all(r.sampling_protocol for r in recs)


# ── storage ───────────────────────────────────────────────────────────────────


def test_records_actually_land_in_the_table(tmp_path: Path):
    """The regression this guards.

    upsert_all looked like it worked and wrote nothing. sqlite_utils implements
    upsert as `INSERT OR IGNORE` of the primary key alone then an `UPDATE`; this
    table has four NOT NULL columns, so the PK-only insert violated them, OR
    IGNORE swallowed it, the UPDATE matched no row, and all 420 records
    vanished without an error. The ingest reported "stored 420" because it was
    echoing len(records) rather than reading anything back.
    """
    db = get_db(tmp_path / "t.db")
    recs = _records()
    written = upsert_fish_surveys(db, recs)

    assert written == len(recs), f"reported {written} written, had {len(recs)} records"
    assert db["fish_surveys"].count == len(recs), "rows did not persist"


def test_reingest_is_idempotent(tmp_path: Path):
    db = get_db(tmp_path / "t.db")
    recs = _records()
    upsert_fish_surveys(db, recs)
    first = db["fish_surveys"].count
    second = upsert_fish_surveys(db, recs)
    assert db["fish_surveys"].count == first, "re-ingest duplicated rows"
    assert second == 0, f"second pass should add no new rows, reported {second}"


def test_empty_input_writes_nothing(tmp_path: Path):
    db = get_db(tmp_path / "t.db")
    assert upsert_fish_surveys(db, []) == 0


def test_events_group_by_station_visit(tmp_path: Path):
    """A station visit with its full species list is what makes an absence
    readable: the species not in the list were not caught at known effort."""
    db = get_db(tmp_path / "t.db")
    recs = _records()
    upsert_fish_surveys(db, recs)

    located = [r for r in recs if r.lat is not None]
    assert located, "fixture produced no coordinates"
    events = survey_events_near(db, located[0].lat, located[0].lng, radius_km=50)

    assert events, "no events grouped"
    assert any(e["n_species"] > 1 for e in events), "an event should hold several species"
    for e in events:
        assert e["species"], "an event with no species list cannot evidence absence"
        assert e["n_species"] == len(e["species"])
