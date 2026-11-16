"""Tests for IngestArea and AreaIngestResult models."""

import pytest
from pydantic import ValidationError

from src.models.ingest_area import AreaIngestResult, IngestArea


def _area(**kwargs) -> IngestArea:
    defaults = {
        "label": "Bronte Creek Oakville",
        "jurisdiction": "CA-ON",
        "lat": 43.45,
        "lng": -79.72,
        "radius_km": 25,
        "sources": ["global"],
    }
    defaults.update(kwargs)
    return IngestArea(**defaults)


def test_area_valid():
    a = _area()
    assert a.sources == ["global"]
    assert a.radius_for("global") == 25


def test_source_radius_override_applies_to_that_source_only():
    a = _area(sources=["global", "tidal"], source_radius_km={"tidal": 100})
    assert a.radius_for("tidal") == 100
    assert a.radius_for("global") == 25


@pytest.mark.parametrize(
    "bad",
    [
        {"jurisdiction": "Ontario"},
        {"lat": 91},
        {"lng": -181},
        {"radius_km": 0},
        {"sources": []},
        {"label": ""},
    ],
)
def test_area_rejects_bad_values(bad):
    with pytest.raises(ValidationError):
        _area(**bad)


def test_result_ok_reflects_failures():
    r = AreaIngestResult(label="x", source="global", stored={"GBIF": 3})
    assert r.ok
    r.failed["iNaturalist"] = "TimeoutError: slow"
    assert not r.ok
