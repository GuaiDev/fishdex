"""Tests for the Conservation Authority portal harvester.

No network: every probe is mocked. The harvester's job is to keep three
outcomes apart — unchecked, checked-and-empty, and checked-and-found — because
collapsing them is how a data gap becomes invisible.
"""

import importlib
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

_h = importlib.import_module("src.ingest.jurisdictions.ca_on.ca_harvest")


def _registry(tmp_path: Path, authorities: list[dict]) -> Path:
    p = tmp_path / "ca_portals.json"
    p.write_text(json.dumps({"schema_version": 1, "authorities": authorities}), encoding="utf-8")
    return p


def _json_response(payload: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.headers = {"content-type": "application/json;charset=utf-8"}
    m.json.return_value = payload
    return m


def _dead_response() -> MagicMock:
    m = MagicMock()
    m.status_code = 404
    m.headers = {"content-type": "text/html"}
    return m


# ── the three outcomes stay distinct ─────────────────────────────────────────


def test_no_website_is_unchecked_not_empty(tmp_path: Path):
    """An authority nobody has found a URL for is NOT an authority that
    publishes nothing. Different fact, different remedy."""
    reg = _registry(tmp_path, [{"name": "Crowe Valley Conservation", "website": None}])
    with patch("httpx.get") as get:
        results = _h.harvest(reg)
    assert get.call_count == 0, "an authority with no URL must not be probed"
    assert results[0].status == "needs_url"


def test_site_with_no_catalogue_is_distinguished_from_unchecked(tmp_path: Path):
    reg = _registry(tmp_path, [{"name": "Grand River", "website": "https://grandriver.ca"}])
    with patch("httpx.get", return_value=_dead_response()), patch.object(_h, "_POLITE_DELAY", 0):
        results = _h.harvest(reg)
    assert results[0].status == "no_catalogue"
    assert results[0].status != "needs_url"


def test_ckan_catalogue_is_found_and_fish_datasets_filtered(tmp_path: Path):
    reg = _registry(tmp_path, [{"name": "Example CA", "website": "https://example.invalid"}])
    payload = {
        "result": [
            "rwmp-fish-community-data",
            "aquatic-habitat-survey",
            "trail-parking-locations",
            "budget-2024",
        ]
    }
    with (
        patch("httpx.get", return_value=_json_response(payload)),
        patch.object(_h, "_POLITE_DELAY", 0),
    ):
        results = _h.harvest(reg)
    r = results[0]
    assert r.status == "catalogue_found"
    assert r.total_datasets == 4
    assert set(r.fish_datasets) == {"rwmp-fish-community-data", "aquatic-habitat-survey"}


def test_an_adapted_authority_is_not_probed_again(tmp_path: Path):
    """Already adapted means the question is settled; re-probing it every night
    is wasted requests against someone else's server."""
    reg = _registry(
        tmp_path,
        [{"name": "TRCA", "website": "https://trca.ca", "adapter": "src/.../trca_surveys.py"}],
    )
    with patch("httpx.get") as get:
        results = _h.harvest(reg)
    assert get.call_count == 0
    assert results[0].status == "adapted"
    assert results[0].adapter


# ── the discovery guard ──────────────────────────────────────────────────────


def test_catalogue_with_no_fish_match_goes_through_the_discovery_guard(tmp_path: Path, caplog):
    """A catalogue sitting right there whose datasets match nothing is a fact
    about our keyword filter as much as about the source. check_resource_discovery
    exists to make that loud rather than reporting a cheerful zero."""
    reg = _registry(tmp_path, [{"name": "Example CA", "website": "https://example.invalid"}])
    payload = {"result": ["budget-2024", "trail-closures", "board-minutes"]}
    with (
        patch("httpx.get", return_value=_json_response(payload)),
        patch.object(_h, "_POLITE_DELAY", 0),
    ):
        with caplog.at_level(logging.ERROR):
            results = _h.harvest(reg)
    assert results[0].status == "catalogue_found"
    assert results[0].fish_datasets == []
    assert "matched 0 of 3" in caplog.text


# ── host candidates ──────────────────────────────────────────────────────────


def test_probes_the_data_subdomain_where_ckan_usually_lives():
    hosts = _h._candidate_hosts("https://trca.ca")
    assert "https://trca.ca" in hosts
    assert "https://data.trca.ca" in hosts


def test_does_not_double_prefix_an_already_data_host():
    hosts = _h._candidate_hosts("https://data.trca.ca")
    assert hosts == ["https://data.trca.ca"]


# ── report ───────────────────────────────────────────────────────────────────


def test_report_separates_actionable_from_everything_else(tmp_path: Path):
    """The actionable list is the only output that should cost attention: an
    authority publishing fish data with no adapter yet."""
    results = [
        _h.AuthorityResult(
            name="Has fish",
            status="catalogue_found",
            fish_datasets=["rwmp-fish-community-data"],
            total_datasets=9,
        ),
        _h.AuthorityResult(
            name="Catalogue but no fish",
            status="catalogue_found",
            fish_datasets=[],
            total_datasets=4,
        ),
        _h.AuthorityResult(name="Unchecked", status="needs_url"),
        _h.AuthorityResult(name="Done", status="adapted", adapter="x.py"),
    ]
    path = _h.write_report(results, tmp_path / "report.json")
    doc = json.loads(path.read_text(encoding="utf-8"))

    assert [a["name"] for a in doc["actionable"]] == ["Has fish"]
    assert doc["summary"] == {"catalogue_found": 2, "needs_url": 1, "adapted": 1}
    assert len(doc["authorities"]) == 4


def test_report_warns_when_something_is_newly_adaptable(tmp_path: Path, caplog):
    results = [
        _h.AuthorityResult(
            name="Upper Thames",
            status="catalogue_found",
            fish_datasets=["fish-community-2024"],
            total_datasets=12,
        )
    ]
    with caplog.at_level(logging.WARNING):
        _h.write_report(results, tmp_path / "r.json")
    assert "no adapter yet" in caplog.text
    assert "Upper Thames" in caplog.text


def test_report_says_unchecked_authorities_are_unchecked(tmp_path: Path, caplog):
    results = [_h.AuthorityResult(name="Crowe Valley", status="needs_url")]
    with caplog.at_level(logging.INFO):
        _h.write_report(results, tmp_path / "r.json")
    assert "not known to publish nothing" in caplog.text
