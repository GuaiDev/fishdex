"""Tests for the jurisdiction registry."""

from src.jurisdictions.base import Jurisdiction
from src.jurisdictions.registry import get_jurisdiction
from src.jurisdictions.unknown import UnknownJurisdiction


def test_get_ontario_is_fully_modeled():
    j = get_jurisdiction("CA-ON")
    assert isinstance(j, Jurisdiction) and not isinstance(j, UnknownJurisdiction)
    assert j.code == "CA-ON"
    assert j.name == "Ontario"
    assert j.country == "CA"
    assert j.has_detailed_data is True


def test_ontario_regulatory_context_mentions_mnrf():
    assert "MNRF" in get_jurisdiction("CA-ON").regulatory_context()


def test_michigan_stub_has_name_but_no_detailed_data():
    j = get_jurisdiction("US-MI")
    assert j.code == "US-MI"
    assert j.name == "Michigan"
    assert j.country == "US"
    assert j.has_detailed_data is False


def test_unknown_jurisdiction_falls_back_gracefully():
    j = get_jurisdiction("CA-ZZ")
    assert isinstance(j, UnknownJurisdiction)
    assert j.code == "CA-ZZ"
    assert "don't recognize" in j.regulatory_context().lower()


def test_all_priority_stub_jurisdictions_present():
    for code in ["CA-BC", "CA-QC", "US-MI", "US-NY", "US-MN", "US-WI"]:
        j = get_jurisdiction(code)
        assert j.code == code
        assert j.name
        assert j.country in ("CA", "US")
