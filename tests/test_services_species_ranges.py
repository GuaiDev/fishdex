"""Tests for species range agent-facing service functions."""

import json

from src.models.species_range import SpeciesRange
from src.services.species_ranges import _STATUS_SEVERITY, _in_ontario
from src.storage.species_ranges import (
    query_sar_species,
    query_species_range,
    upsert_species_ranges,
)


def _seed_db(fast_db):
    db = fast_db
    ranges = [
        SpeciesRange(
            species="Brook Trout",
            scientific_name="Salvelinus fontinalis",
            native_to_ontario=True,
            native_to_great_lakes=True,
            general_range="Cold headwater streams across the Shield.",
            jurisdictions_present=["CA-ON", "US-MI"],
            sara_status="Not at Risk",
            ontario_status="Not at Risk",
            habitat_notes="Requires cold, clean water.",
        ),
        SpeciesRange(
            species="Greater Redhorse",
            scientific_name="Moxostoma valenciennesi",
            native_to_ontario=True,
            native_to_great_lakes=True,
            general_range="Grand River and tributaries.",
            jurisdictions_present=["CA-ON"],
            sara_status="Threatened",
            ontario_status="Threatened",
            fishing_notes="Release immediately. Report to MNRF at 1-877-TIPS-MNR.",
        ),
        SpeciesRange(
            species="Lake Sturgeon",
            scientific_name="Acipenser fulvescens",
            native_to_ontario=True,
            native_to_great_lakes=True,
            general_range="Large Ontario rivers and lakes.",
            jurisdictions_present=["CA-ON", "CA-MB"],
            sara_status="Threatened",
            ontario_status="Threatened",
            fishing_notes="Catch and release only. Handle with extreme care.",
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
            fishing_notes="Catch and release only.",
        ),
    ]
    upsert_species_ranges(db, ranges)
    return db


def _make_service(fast_db):
    """Return service functions wired to the test DB."""
    db = _seed_db(fast_db)

    def get_range(species, lat=None, lng=None):
        sr = query_species_range(db, species)
        if sr is None:
            return json.dumps({"found": False, "note": "Species not in local database."})
        sar_alert = sr.sara_status in {"Threatened", "Endangered"} or sr.ontario_status in {
            "Threatened",
            "Endangered",
        }
        is_plausible = None
        if lat is not None and lng is not None:
            in_on = _in_ontario(lat, lng)
            is_plausible = in_on and "CA-ON" in sr.jurisdictions_present
        return json.dumps(
            {
                "found": True,
                "species": sr.species,
                "sar_alert": sar_alert,
                "is_plausible_at_location": is_plausible,
                "handling_guidance": sr.fishing_notes,
                "sara_status": sr.sara_status,
                "ontario_status": sr.ontario_status,
            }
        )

    def get_sar(jurisdiction="CA-ON"):
        sar_list = query_sar_species(db, jurisdiction)
        sar_list.sort(key=lambda s: _STATUS_SEVERITY.get(s.sara_status, 99))
        return json.dumps(
            {
                "count": len(sar_list),
                "species_at_risk": [
                    {
                        "species": s.species,
                        "sara_status": s.sara_status,
                        "is_protected": s.is_protected,
                        "handling_guidance": s.handling_guidance,
                    }
                    for s in sar_list
                ],
            }
        )

    return get_range, get_sar


def test_unknown_species_returns_not_found(fast_db):
    get_range, _ = _make_service(fast_db)
    result = json.loads(get_range("platypus"))
    assert result["found"] is False
    assert "note" in result


def test_sar_species_returns_sar_alert(fast_db):
    get_range, _ = _make_service(fast_db)
    result = json.loads(get_range("Greater Redhorse"))
    assert result["found"] is True
    assert result["sar_alert"] is True
    assert result["handling_guidance"] is not None
    assert len(result["handling_guidance"]) > 0


def test_non_sar_species_no_alert(fast_db):
    get_range, _ = _make_service(fast_db)
    result = json.loads(get_range("Brook Trout"))
    assert result["found"] is True
    assert result["sar_alert"] is False


def test_plausible_in_ontario(fast_db):
    get_range, _ = _make_service(fast_db)
    # Toronto coords — within Ontario
    result = json.loads(get_range("Brook Trout", lat=43.6532, lng=-79.3832))
    assert result["is_plausible_at_location"] is True


def test_not_plausible_outside_ontario(fast_db):
    get_range, _ = _make_service(fast_db)
    # London, UK — outside Ontario
    result = json.loads(get_range("Brook Trout", lat=51.5074, lng=-0.1278))
    assert result["is_plausible_at_location"] is False


def test_plausible_none_when_no_coords(fast_db):
    get_range, _ = _make_service(fast_db)
    result = json.loads(get_range("Brook Trout"))
    assert result["is_plausible_at_location"] is None


def test_get_sar_returns_list(fast_db):
    _, get_sar = _make_service(fast_db)
    result = json.loads(get_sar("CA-ON"))
    assert result["count"] > 0
    names = [e["species"] for e in result["species_at_risk"]]
    assert "Greater Redhorse" in names


def test_get_sar_empty_jurisdiction(fast_db):
    _, get_sar = _make_service(fast_db)
    result = json.loads(get_sar("US-TX"))
    assert result["count"] == 0
