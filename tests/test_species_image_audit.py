"""Tests for the Commons file model and the species-image audit matching rules."""

from src.models.commons_file import TIER_AUTHOR, TIER_USGOV, CommonsFile, strip_html
from src.services import species_image_audit as audit


def _file(**kw) -> CommonsFile:
    base = dict(
        title="File:Smallmouth bass.jpg",
        page_url="https://commons.wikimedia.org/wiki/File:Smallmouth_bass.jpg",
        original_url="https://upload.wikimedia.org/x.jpg",
        width=1068,
        height=660,
        mime="image/jpeg",
        artist="Duane Raver, U.S. Fish and Wildlife Service",
        description="Smallmouth bass (Micropterus dolomieu)",
        licence_short="Public domain",
        templates=["PD-USGov-FWS"],
        categories=["Duane Raver", "Micropterus dolomieu illustrations"],
    )
    base.update(kw)
    return CommonsFile(**base)


def test_strip_html_collapses_markup_and_whitespace():
    assert strip_html('<a href="x">Duane\n  Raver</a>, <b>USFWS</b>') == "Duane Raver, USFWS"
    assert strip_html(None) == ""


def test_usgov_tag_is_the_strong_tier():
    f = _file()
    assert f.pd_template == "PD-USGov-FWS"
    assert f.licence_tier == TIER_USGOV
    assert f.resolution == "1068x660"


def test_pd_author_is_a_separate_weaker_tier():
    f = _file(templates=["PD-author"])
    assert f.pd_template is None
    assert f.licence_tier == TIER_AUTHOR
    assert f.licence_tag == "PD-author"


def test_no_tag_means_no_tier():
    assert _file(templates=[]).licence_tier is None


def test_is_raver_from_artist_or_category():
    assert _file().is_raver
    assert _file(artist="Someone", categories=["Duane Raver"]).is_raver
    assert not _file(artist="Timothy Knepp", categories=[]).is_raver


def test_untagged_file_does_not_qualify():
    assert audit._qualifies(_file())
    assert not audit._qualifies(_file(templates=[]))
    assert not audit._qualifies(_file(artist="Timothy Knepp", categories=[]))
    assert not audit._qualifies(_file(mime="image/svg+xml"))


def test_current_name_in_description_is_a_high_confidence_match():
    assert audit._classify(_file(), "Micropterus dolomieu", "Smallmouth Bass") == (
        "found",
        "high",
    )


def test_old_name_is_flagged_not_matched():
    f = _file(
        title="File:Walleye old.jpg",
        description="Walleye (Stizostedion vitreum)",
        categories=["Duane Raver"],
    )
    assert audit._classify(f, "Sander vitreus", "Walleye") == ("name-mismatch", "medium")


def test_common_name_only_is_low_confidence_mismatch():
    f = _file(
        title="File:Walleye fish.jpg",
        description="Walleye fish",
        categories=["Duane Raver"],
    )
    assert audit._classify(f, "Sander vitreus", "Walleye") == ("name-mismatch", "low")


def test_unrelated_file_is_not_a_candidate():
    assert audit._classify(_file(), "Esox lucius", "Northern Pike") is None


def test_subspecies_file_is_not_attributed_to_the_parent_species():
    f = _file(
        title="File:Tessellated.jpg",
        description="Etheostoma nigrum olmstedi",
        categories=["Duane Raver"],
    )
    assert audit._classify(f, "Etheostoma nigrum", "Johnny Darter") is None


def test_derived_variants_are_recognised():
    assert audit._is_derived(_file(title="File:Amia calva (white background).jpg"))
    assert not audit._is_derived(_file(title="File:Amia calva1.jpg"))


def test_csv_round_trip_has_every_column(tmp_path):
    import csv

    rows = [audit.AuditRow("Esox lucius", "Northern Pike", "Esox lucius")]
    out = tmp_path / "audit.csv"
    audit.write_csv(rows, out)
    with out.open() as fh:
        read = list(csv.DictReader(fh))
    assert list(read[0].keys()) == audit.CSV_COLUMNS
    assert read[0]["status"] == "not-found"


def test_commons_adapter_parses_recorded_imageinfo(monkeypatch):
    """Parse a real recorded Commons response (File:Smallmouth bass.jpg), no network."""
    import importlib
    import json
    from pathlib import Path

    commons = importlib.import_module("src.ingest.global.commons_images")
    recorded = json.loads(
        (Path(__file__).parent / "fixtures" / "commons_imageinfo_response.json").read_text()
    )
    monkeypatch.setattr(commons, "_cached_get", lambda params: recorded)

    records = commons.file_records(["File:Smallmouth bass.jpg"])
    f = records["File:Smallmouth bass.jpg"]

    assert f.pd_template == "PD-USGov-FWS"
    assert f.is_raver
    assert f.resolution == "1068x660"
    assert f.page_url == "https://commons.wikimedia.org/wiki/File:Smallmouth_bass.jpg"
    assert "?" not in f.original_url
    assert f.fws_source_url == (
        "https://digitalmedia.fws.gov/cdm/singleitem/collection/natdiglib/id/2287/rec/1"
    )


def test_commons_adapter_keeps_the_current_revision_when_paging_through_history(monkeypatch):
    """Commons pages a re-uploaded file's older revisions; they must not replace the current one."""
    import importlib

    commons = importlib.import_module("src.ingest.global.commons_images")

    def page(url, width, more):
        info = {"url": url, "width": width, "height": 400, "mime": "image/jpeg"}
        data = {"query": {"pages": [{"title": "File:A.jpg", "imageinfo": [info]}]}}
        if more:
            data["continue"] = {"iistart": "2005-10-30T17:02:16Z", "continue": "||"}
        return data

    responses = iter(
        [
            page("https://x/current.jpg?utm=1", 1098, True),
            page("https://x/archive/old.jpg", 1246, False),
        ]
    )
    monkeypatch.setattr(commons, "_cached_get", lambda params: next(responses))

    f = commons.file_records(["File:A.jpg"])["File:A.jpg"]

    assert f.original_url == "https://x/current.jpg"
    assert f.width == 1098
