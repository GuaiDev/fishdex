"""Tests for species range storage layer."""

import json

from src.models.species_range import SpeciesRange
from src.storage.species_ranges import (
    is_species_at_risk,
    query_sar_species,
    query_species_range,
    upsert_species_ranges,
    upsert_species_ranges_merged,
)


def _make_db(fast_db):
    return fast_db


def _sample_ranges() -> list[SpeciesRange]:
    return [
        SpeciesRange(
            species="Brook Trout",
            scientific_name="Salvelinus fontinalis",
            native_to_ontario=True,
            native_to_great_lakes=True,
            general_range="Cold headwater streams.",
            jurisdictions_present=["CA-ON", "US-MI"],
            sara_status="Not at Risk",
            ontario_status="Not at Risk",
        ),
        SpeciesRange(
            species="Greater Redhorse",
            scientific_name="Moxostoma valenciennesi",
            native_to_ontario=True,
            native_to_great_lakes=True,
            general_range="Grand River and tributaries.",
            jurisdictions_present=["CA-ON", "CA-QC"],
            sara_status="Threatened",
            ontario_status="Threatened",
            fishing_notes="Release immediately. Report to MNRF.",
        ),
        SpeciesRange(
            species="Smallmouth Bass",
            scientific_name="Micropterus dolomieu",
            native_to_ontario=True,
            native_to_great_lakes=True,
            general_range="Widespread in southern and central Ontario.",
            jurisdictions_present=["CA-ON", "US-MI", "US-OH"],
            sara_status="Not at Risk",
            ontario_status="Not at Risk",
        ),
        SpeciesRange(
            species="Redside Dace",
            scientific_name="Clinostomus elongatus",
            native_to_ontario=True,
            native_to_great_lakes=True,
            general_range="Cold headwater streams in southern Ontario.",
            jurisdictions_present=["CA-ON"],
            sara_status="Threatened",
            ontario_status="Endangered",
            fishing_notes="Do not target. Release immediately.",
        ),
        SpeciesRange(
            species="Spotted Gar",
            scientific_name="Lepisosteus oculatus",
            native_to_ontario=True,
            native_to_great_lakes=True,
            general_range="Lake Erie and Lake St. Clair.",
            jurisdictions_present=["CA-ON", "US-OH"],
            sara_status="Special Concern",
            ontario_status="Special Concern",
        ),
    ]


def test_upsert_and_query_exact(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    result = query_species_range(db, "Brook Trout")
    assert result is not None
    assert result.species == "Brook Trout"
    assert result.native_to_ontario is True


def test_query_case_insensitive(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    result = query_species_range(db, "brook trout")
    assert result is not None
    assert result.species == "Brook Trout"


def test_query_partial_match(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    result = query_species_range(db, "redhorse")
    assert result is not None
    assert "Redhorse" in result.species


def test_query_unknown_returns_none(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    result = query_species_range(db, "platypus")
    assert result is None


def test_upsert_idempotent(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    upsert_species_ranges(db, _sample_ranges())
    result = query_species_range(db, "Smallmouth Bass")
    assert result is not None
    count = db["species_ranges"].count
    assert count == len(_sample_ranges())


def test_jurisdictions_present_roundtrip(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    result = query_species_range(db, "Smallmouth Bass")
    assert isinstance(result.jurisdictions_present, list)
    assert "CA-ON" in result.jurisdictions_present


def test_query_sar_returns_only_at_risk(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    sar = query_sar_species(db, "CA-ON")
    names = [s.species for s in sar]
    assert "Greater Redhorse" in names
    assert "Redside Dace" in names
    assert "Spotted Gar" in names
    assert "Brook Trout" not in names
    assert "Smallmouth Bass" not in names


def test_query_sar_jurisdiction_filter(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    # Redside Dace only has CA-ON; Greater Redhorse has CA-ON and CA-QC
    sar_qc = query_sar_species(db, "CA-QC")
    names = [s.species for s in sar_qc]
    assert "Greater Redhorse" in names
    assert "Redside Dace" not in names  # not in CA-QC jurisdictions_present


def test_is_species_at_risk_true(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    assert is_species_at_risk(db, "Greater Redhorse") is True
    assert is_species_at_risk(db, "redside dace") is True


def test_is_species_at_risk_false(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    assert is_species_at_risk(db, "Smallmouth Bass") is False
    assert is_species_at_risk(db, "brook trout") is False


def test_is_species_at_risk_unknown(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    assert is_species_at_risk(db, "narwhal") is False


# --- upsert_species_ranges_merged: cross-jurisdiction merge safety ---
# Guards against the bug found while testing QC species ranges live: a blind
# upsert_all(pk="species") let a later jurisdiction's ingest silently wipe an
# earlier jurisdiction's jurisdictions_present list and status fields.


def test_merged_upsert_unions_jurisdictions_present_for_shared_species(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())  # seeds Smallmouth Bass w/ CA-ON, US-MI, US-OH

    upsert_species_ranges_merged(
        db,
        [
            {
                "species": "Smallmouth Bass",
                "scientific_name": "Micropterus dolomieu",
                "native_to_ontario": 0,
                "native_to_great_lakes": 0,
                "introduced": 0,
                "extirpated_from_ontario": 0,
                "general_range": "Quebec — centroid (47.146, -74.768)",
                "habitat_notes": None,
                "jurisdictions_present": json.dumps(["CA-QC"]),
                "sara_status": None,
                "ontario_status": None,
                "cosewic_status": None,
                "fishing_notes": None,
                "last_updated": "2026-06-01T00:00:00",
            }
        ],
    )

    result = query_species_range(db, "Smallmouth Bass")
    assert set(result.jurisdictions_present) == {"CA-ON", "US-MI", "US-OH", "CA-QC"}


def test_merged_upsert_preserves_existing_status_fields(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())  # Smallmouth Bass has ontario_status set

    upsert_species_ranges_merged(
        db,
        [
            {
                "species": "Smallmouth Bass",
                "scientific_name": "Micropterus dolomieu",
                "native_to_ontario": 0,
                "native_to_great_lakes": 0,
                "introduced": 0,
                "extirpated_from_ontario": 0,
                "general_range": "Quebec — centroid (47.146, -74.768)",
                "habitat_notes": None,
                "jurisdictions_present": json.dumps(["CA-QC"]),
                "sara_status": None,  # must not wipe the existing "Not at Risk"
                "ontario_status": None,
                "cosewic_status": None,
                "fishing_notes": None,
                "last_updated": "2026-06-01T00:00:00",
            }
        ],
    )

    row = db["species_ranges"].get("Smallmouth Bass")
    assert row["ontario_status"] == "Not at Risk"
    assert row["sara_status"] == "Not at Risk"


def test_merged_upsert_appends_general_range_instead_of_replacing(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())

    upsert_species_ranges_merged(
        db,
        [
            {
                "species": "Smallmouth Bass",
                "scientific_name": "Micropterus dolomieu",
                "native_to_ontario": 0,
                "native_to_great_lakes": 0,
                "introduced": 0,
                "extirpated_from_ontario": 0,
                "general_range": "Quebec — centroid (47.146, -74.768)",
                "habitat_notes": None,
                "jurisdictions_present": json.dumps(["CA-QC"]),
                "sara_status": None,
                "ontario_status": None,
                "cosewic_status": None,
                "fishing_notes": None,
                "last_updated": "2026-06-01T00:00:00",
            }
        ],
    )

    row = db["species_ranges"].get("Smallmouth Bass")
    assert "Widespread in southern and central Ontario." in row["general_range"]
    assert "Quebec — centroid (47.146, -74.768)" in row["general_range"]


def test_merged_upsert_inserts_fresh_row_for_new_species(fast_db):
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())

    upsert_species_ranges_merged(
        db,
        [
            {
                "species": "Striped Bass",
                "scientific_name": "Morone saxatilis",
                "native_to_ontario": 0,
                "native_to_great_lakes": 0,
                "introduced": 0,
                "extirpated_from_ontario": 0,
                "general_range": "Quebec — centroid (51.581, -57.237)",
                "habitat_notes": "Family: Moronidae",
                "jurisdictions_present": json.dumps(["CA-QC"]),
                "sara_status": None,
                "ontario_status": None,
                "cosewic_status": None,
                "fishing_notes": None,
                "last_updated": "2026-06-01T00:00:00",
            }
        ],
    )

    result = query_species_range(db, "Striped Bass")
    assert result is not None
    assert result.jurisdictions_present == ["CA-QC"]


def test_merged_upsert_does_not_regress_existing_species_count(fast_db):
    """A no-op re-ingest of the same jurisdiction must not create duplicate rows."""
    db = _make_db(fast_db)
    upsert_species_ranges(db, _sample_ranges())
    before = db["species_ranges"].count

    upsert_species_ranges_merged(
        db,
        [
            {
                "species": "Smallmouth Bass",
                "scientific_name": "Micropterus dolomieu",
                "native_to_ontario": 0,
                "native_to_great_lakes": 0,
                "introduced": 0,
                "extirpated_from_ontario": 0,
                "general_range": "Widespread in southern and central Ontario.",
                "habitat_notes": None,
                "jurisdictions_present": json.dumps(["CA-ON", "US-MI", "US-OH"]),
                "sara_status": "Not at Risk",
                "ontario_status": "Not at Risk",
                "cosewic_status": None,
                "fishing_notes": None,
                "last_updated": "2026-06-01T00:00:00",
            }
        ],
    )

    assert db["species_ranges"].count == before
