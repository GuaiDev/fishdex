"""Tests for the Conservation Authority boundary adapter.

The bug these guard: _NAME_FIELD_CANDIDATES listed four field names, none of
which the LIO service publishes (it uses COMMON_NAME and LEGAL_NAME). Every one
of the 24 ingested rows silently became "Unknown CA", and nothing said so. A
boundary whose authority cannot be named cannot route a point to the authority
that surveys it, which is the only reason the layer exists.
"""

import importlib
import logging

# "global" is a Python keyword — reach the module through importlib
_ca = importlib.import_module("src.ingest.global.ca_boundaries")
_resolve_field = _ca._resolve_field
_NAME_FIELD_CANDIDATES = _ca._NAME_FIELD_CANDIDATES


def test_resolves_the_field_names_the_service_actually_publishes():
    """COMMON_NAME and LEGAL_NAME are what MapServer/11 returns."""
    props = {
        "OGF_ID": 123,
        "LEGAL_NAME": "The Upper Thames River Conservation Authority",
        "COMMON_NAME": "Upper Thames River Conservation Authority",
        "CA_ID": 26,
    }
    assert _resolve_field(props, _NAME_FIELD_CANDIDATES) == (
        "Upper Thames River Conservation Authority"
    )


def test_common_name_is_preferred_over_legal_name():
    """ "Upper Thames River" is what a person calls it; the legal name carries
    a definite article and corporate suffix."""
    props = {
        "LEGAL_NAME": "The Grand River Conservation Authority",
        "COMMON_NAME": "Grand River Conservation Authority",
    }
    assert _resolve_field(props, _NAME_FIELD_CANDIDATES).startswith("Grand")


def test_falls_back_by_structure_when_no_candidate_matches(caplog):
    """A published label can be renamed upstream without notice. Any string
    field containing NAME beats writing a placeholder."""
    props = {"OGF_ID": 1, "AUTHORITY_TITLE_NAME": "Kettle Creek Conservation Authority"}
    with caplog.at_level(logging.WARNING):
        got = _resolve_field(props, _NAME_FIELD_CANDIDATES)
    assert got == "Kettle Creek Conservation Authority"
    assert "by structure" in caplog.text, "a structural fallback must announce itself"


def test_structural_fallback_ignores_non_name_fields():
    props = {"FILE_NAME": "ca_admin_areas.shp", "LAYER_NAME": "MapServer/11"}
    assert _resolve_field(props, _NAME_FIELD_CANDIDATES) is None


def test_blank_name_is_not_accepted_as_a_name():
    """An empty string is absence, not a name."""
    props = {"COMMON_NAME": "   ", "LEGAL_NAME": ""}
    assert _resolve_field(props, _NAME_FIELD_CANDIDATES) is None


def test_unresolvable_name_returns_none_so_the_caller_can_count_it():
    assert _resolve_field({"OGF_ID": 7, "AREA_IN_HA": 1234.5}, _NAME_FIELD_CANDIDATES) is None


def test_old_candidate_names_still_work():
    """The original guesses stay in the list — a service may yet use them."""
    for field in ("OFFICIAL_CONSERVATION_AUTHORITY_NAME", "CA_NAME", "AUTHORITY_NAME", "NAME"):
        got = _resolve_field(
            {field: "Halton Region Conservation Authority"}, _NAME_FIELD_CANDIDATES
        )
        assert got == "Halton Region Conservation Authority", f"{field} stopped resolving"
