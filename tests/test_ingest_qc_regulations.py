"""Tests for Quebec regulations ingest — no live downloads, no real PDFs.

The QC adapter is structurally different from BC/AB: there is no single big
PDF to split. Each zone is its own PDF, discovered from the printable-versions
page, and the idZone query parameter drifts from the printed zone label. These
tests pin that discovery logic against a recorded page excerpt plus the pure
helpers (season-year parse, PDF extraction), monkeypatching the network.
"""

from pathlib import Path

import httpx
import pytest

from src.ingest.jurisdictions.ca_qc import regulations as reg_mod

_FIXTURE = Path(__file__).parent / "fixtures" / "qc_regulations_printable.html"


@pytest.fixture()
def page_html() -> str:
    return _FIXTURE.read_text()


# ── discovery: idZone + label pairs come from the page, never hardcoded ──────


def test_discovery_finds_every_zone_link(page_html, monkeypatch):
    monkeypatch.setattr(reg_mod, "_fetch_page", lambda: page_html)
    entries, candidates = reg_mod._discover_zone_links()
    assert len(entries) == len(candidates) == 12
    assert [lbl for _, lbl, _ in entries] == [
        "Zone 12",
        "Zone 13 east",
        "Zone 13 west",
        "Zone 14",
        "Zone 19 north",
        "Zone 19 south - part A",
        "Zone 19 south - part B",
        "Zone 20",
        "Zone 21",
        "Zone 22 north",
        "Zone 22 south",
        "Zone 29",
    ]


def test_ids_are_sequential_document_order(page_html, monkeypatch):
    monkeypatch.setattr(reg_mod, "_fetch_page", lambda: page_html)
    entries, _ = reg_mod._discover_zone_links()
    assert [zone_id for zone_id, _, _ in entries] == list(range(1, 13))


def test_idzone_is_read_from_page_not_the_label(page_html, monkeypatch):
    """The whole point: idZone 15 serves "Zone 14"; 3063 serves "Zone 19 north"."""
    monkeypatch.setattr(reg_mod, "_fetch_page", lambda: page_html)
    entries, _ = reg_mod._discover_zone_links()
    by_label = {lbl: url for _, lbl, url in entries}
    assert "idZone=15" in by_label["Zone 14"]
    assert "idZone=3063" in by_label["Zone 19 north"]
    assert "idZone=22" in by_label["Zone 20"]
    assert "idZone=13" in by_label["Zone 13 east"]
    assert "idZone=14" in by_label["Zone 13 west"]


def test_split_parent_yields_distinct_zone_ids(page_html, monkeypatch):
    monkeypatch.setattr(reg_mod, "_fetch_page", lambda: page_html)
    entries, _ = reg_mod._discover_zone_links()
    ids = {lbl: zone_id for zone_id, lbl, _ in entries}
    assert ids["Zone 13 east"] != ids["Zone 13 west"]
    assert ids["Zone 19 north"] != ids["Zone 19 south - part A"]
    assert ids["Zone 22 north"] != ids["Zone 22 south"]


def test_no_links_logs_discovery_guard(monkeypatch):
    monkeypatch.setattr(reg_mod, "_fetch_page", lambda: "<html>redesigned page</html>")
    called = {}

    def fake_guard(source, matched, candidates, matcher):
        called["source"] = source

    monkeypatch.setattr(reg_mod, "check_resource_discovery", fake_guard)
    entries, candidates = reg_mod._discover_zone_links()
    assert entries == [] and candidates == []


def test_unexpected_non_zone_anchor_label_is_skipped(monkeypatch):
    page = (
        '<a href="https://peche.faune.gouv.qc.ca/regpec/en/Exporting/'
        'ExportGrillePdf?idZone=9&amp;idSaison=-1&amp;courantes=False">'
        "Wall map of something</a>"
    )
    monkeypatch.setattr(reg_mod, "_fetch_page", lambda: page)
    entries, candidates = reg_mod._discover_zone_links()
    assert entries == []
    assert len(candidates) == 1


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_season_year_parsed_from_header():
    text = "Sport fishing in Québec\n(April 1, 2026 to March 31, 2027)\n…"
    assert reg_mod._season_year(text) == 2026


def test_season_year_fails_loudly_without_header():
    with pytest.raises(RuntimeError):
        reg_mod._season_year("no season here")


def test_download_skips_when_fresh(tmp_path, monkeypatch):
    fresh = tmp_path / "zone_1.pdf"
    fresh.write_bytes(b"%PDF-1.4 placeholder")
    monkeypatch.setattr(reg_mod, "_CACHE_DIR", tmp_path)

    def boom(*a, **k):
        raise AssertionError("should not download a fresh cache file")

    monkeypatch.setattr(httpx, "stream", boom)
    assert reg_mod._download_pdf_if_stale("https://example.invalid/z1.pdf", 1) == fresh


# ── end-to-end with the network mocked ───────────────────────────────────────


def test_fetch_regulations_builds_one_chunk_per_zone(tmp_path, monkeypatch):
    monkeypatch.setattr(reg_mod, "_fetch_page", lambda: _FIXTURE.read_text())
    monkeypatch.setattr(reg_mod, "_CACHE_DIR", tmp_path)

    def fake_extract(url, zone_id):
        return f"Sport fishing in Québec\n(April 1, 2026 to March 31, 2027)\nZone {zone_id} rules"

    monkeypatch.setattr(reg_mod, "_extract_zone_pdf", fake_extract)

    chunks = reg_mod.fetch_regulations()
    assert len(chunks) == 12
    for c in chunks:
        assert c["jurisdiction"] == "CA-QC"
        assert c["regulation_year"] == 2026
        assert c["zone_name"].startswith("Zone")
        assert c["raw_text"]
        assert c["char_count"] == len(c["raw_text"])
    assert chunks[0]["zone"] == 1
    assert chunks[-1]["zone_name"] == "Zone 29"


def test_fetch_regulations_counts_partial_failures(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(reg_mod, "_fetch_page", lambda: _FIXTURE.read_text())
    monkeypatch.setattr(reg_mod, "_CACHE_DIR", tmp_path)

    def flaky_extract(url, zone_id):
        if zone_id == 2:
            raise RuntimeError("boom")
        return "(April 1, 2026 to March 31, 2027)\nbody"

    monkeypatch.setattr(reg_mod, "_extract_zone_pdf", flaky_extract)
    chunks = reg_mod.fetch_regulations()
    assert len(chunks) == 11
    assert any(
        r.levelname == "WARNING" and "1 of 12 zones failed" in r.message for r in caplog.records
    )


def test_fetch_regulations_empty_page_returns_empty(monkeypatch):
    monkeypatch.setattr(reg_mod, "_fetch_page", lambda: "<html></html>")
    monkeypatch.setattr(reg_mod, "check_resource_discovery", lambda *a, **k: None)
    assert reg_mod.fetch_regulations() == []
