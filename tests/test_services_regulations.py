"""Tests for the regulations service layer."""

import json

import pytest

from src.models.regulation import RegulationChunk
from src.services.regulations import (
    _extract_species_context,
    get_regulations_for_agent,
)
from src.storage.regulations import upsert_regulation_chunks


def _make_chunk(zone: int, text: str = "") -> RegulationChunk:
    if not text:
        text = f"ZONE {zone}\nWalleye: open May 1. Limit 4.\nBass: open last Saturday June."
    return RegulationChunk(
        zone=zone,
        jurisdiction="CA-ON",
        regulation_year=2026,
        raw_text=text,
        char_count=len(text),
        source_url="https://www.ontario.ca/files/test.pdf",
        ingested_at="2026-01-01T00:00:00",
    )


@pytest.fixture()
def populated_db(db_factory, tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    db = db_factory(db_path)
    chunks = [_make_chunk(z) for z in [5, 16, 20]]
    upsert_regulation_chunks(db, chunks)
    monkeypatch.setattr("src.services.regulations.get_db", lambda: db)
    return db


@pytest.fixture()
def empty_db(db_factory, tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    db = db_factory(db_path)
    monkeypatch.setattr("src.services.regulations.get_db", lambda: db)
    return db


# --- FMZ coordinate estimation ---


# The old _estimate_fmz tests asserted only `1 <= zone <= 20`, which is why the
# bug survived: Toronto resolved to zone 5 (Rainy River, 1,500 km away) and the
# assertion still passed. Zone resolution is now covered by
# tests/test_fmz_resolution.py against real polygons.


# --- service layer ---


def test_no_zone_no_coords_returns_error():
    result = json.loads(get_regulations_for_agent())
    assert "error" in result
    assert result["empty_reason"] == "no_location_given"
    assert "Fisheries Management Zones" in result["error"]


def test_empty_db_returns_error(empty_db):
    result = json.loads(get_regulations_for_agent(zone=5))
    assert "error" in result
    assert "make ingest" in result["error"].lower()


def test_zone_not_in_db_returns_error(populated_db):
    result = json.loads(get_regulations_for_agent(zone=3))
    assert "error" in result


def test_zone_found_returns_text(populated_db):
    result = json.loads(get_regulations_for_agent(zone=5))
    assert "text" in result
    assert result["zone"] == 5
    assert result["regulation_year"] == 2026
    assert "disclaimer" in result


def test_species_filter_narrows_text(populated_db):
    result = json.loads(get_regulations_for_agent(zone=5, species="Walleye"))
    assert "text" in result
    assert result["species_query"] == "Walleye"


def test_species_not_found_returns_note(populated_db):
    result = json.loads(get_regulations_for_agent(zone=5, species="Muskellunge"))
    assert "text" in result
    assert "not found" in result["text"].lower() or "muskellunge" in result["text"].lower()


def test_latlon_without_boundaries_withholds_regulations(populated_db):
    """Coordinates alone are not enough: without the polygon layer the zone
    cannot be established, and a guess is what caused Oakville to return
    Rainy River regulations. Refusing is the correct outcome."""
    result = json.loads(get_regulations_for_agent(lat=48.4, lng=-89.3))
    assert result["regulations_withheld"] is True
    assert result["empty_reason"] == "zone_boundaries_not_loaded"
    assert "text" not in result


# --- _extract_species_context ---


def test_extract_species_context_found():
    text = "ZONE 5\n" + "x" * 200 + "Walleye: open May 1, limit 4." + "x" * 200
    excerpt, truncated = _extract_species_context(text, "Walleye")
    assert "Walleye" in excerpt
    assert not truncated


def test_extract_species_context_not_found_returns_overview():
    text = "ZONE 5\nBass rules only."
    excerpt, _ = _extract_species_context(text, "Muskellunge")
    assert "not found" in excerpt.lower()
    assert "ZONE 5" in excerpt or "Bass" in excerpt
