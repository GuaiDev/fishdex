"""Provenance for species reference images: the model, the table and the committed set.

Every database is temporary. The last group runs against the committed files,
so a bad image or a missing licence field fails the build rather than reaching
the credits page.
"""

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.models.species_image import APPROVED_REQUIRED_FIELDS, SpeciesImage
from src.services import species_image_pipeline as pipe
from src.services import species_images as service
from src.storage import species_images as store
from src.storage.database import get_db

REPO = Path(__file__).resolve().parent.parent


def _approved(**overrides) -> SpeciesImage:
    fields = dict(
        species_id="Micropterus dolomieu",
        common_name="Smallmouth Bass",
        status="approved",
        image_path="species/a-carousel.webp",
        thumb_path="species/a-tile.webp",
        source_name="US Fish and Wildlife Service, via Wikimedia Commons",
        source_url="https://commons.wikimedia.org/wiki/File:A.jpg",
        creator="Duane Raver",
        licence="Public domain: US government work",
        licence_evidence_url="https://commons.wikimedia.org/wiki/File:A.jpg",
        retrieved_date="2026-10-13",
        original_sha256="ab" * 32,
        processing_version="1",
    )
    fields.update(overrides)
    return SpeciesImage(**fields)


# ── model ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("missing", APPROVED_REQUIRED_FIELDS)
def test_approved_image_without_a_provenance_field_is_rejected(missing):
    with pytest.raises(ValidationError, match=missing):
        _approved(**{missing: "  "})


def test_placeholder_and_rejected_need_no_provenance():
    for status in ("placeholder", "rejected"):
        image = SpeciesImage(
            species_id="Esox lucius",
            common_name="Northern Pike",
            status=status,
            image_path=service.PLACEHOLDER_PATH,
            thumb_path=service.PLACEHOLDER_PATH,
        )
        assert image.status == status


def test_unknown_status_is_rejected():
    with pytest.raises(ValidationError):
        _approved(status="probably-fine")


# ── table ──────────────────────────────────────────────────────────────────


def test_table_exists_after_schema_setup_and_round_trips(tmp_path):
    db = get_db(tmp_path / "t.db")
    assert store.get_species_images(db) == {}

    store.replace_species_images(db, [_approved()])

    assert store.get_species_images(db)["Micropterus dolomieu"] == _approved()


def test_replace_drops_species_no_longer_listed(tmp_path):
    db = get_db(tmp_path / "t.db")
    store.replace_species_images(db, [_approved(), _approved(species_id="Esox lucius")])
    store.replace_species_images(db, [_approved()])

    assert list(store.get_species_images(db)) == ["Micropterus dolomieu"]


# ── building rows ──────────────────────────────────────────────────────────

_ORIGINAL = {
    "source_url": "https://commons.wikimedia.org/wiki/File:A.jpg",
    "creator": "Duane Raver",
    "licence": "Public domain: US government work",
    "licence_evidence_url": "https://commons.wikimedia.org/wiki/File:A.jpg",
    "retrieved": "2026-10-13",
    "sha256": "ab" * 32,
    "match_confidence": "high",
    "name_as_published": "",
}


def _sel(species_id="Aa bb", decision="download", review="approved"):
    return pipe.Selection(species_id, "Fish", "u", decision, "r", "left", review)


def test_only_reviewed_downloads_are_approved():
    catalogue = [("Aa bb", "Fish A"), ("Cc dd", "Fish C"), ("Ee ff", "Fish E")]
    selection = [_sel("Aa bb"), _sel("Cc dd", review=""), _sel("Ee ff", decision="excluded")]
    manifest = {"Aa bb": _ORIGINAL, "Cc dd": _ORIGINAL}

    images = {i.species_id: i for i in service.build_images(catalogue, selection, manifest)}

    assert images["Aa bb"].status == "approved"
    assert images["Aa bb"].image_path == "species/aa-bb-carousel.webp"
    # Downloaded but not yet passed on the contact sheet: still the placeholder.
    assert images["Cc dd"].status == "placeholder"
    assert images["Ee ff"].status == "placeholder"
    assert images["Ee ff"].image_path == service.PLACEHOLDER_PATH


def test_approved_species_with_no_manifest_row_is_an_error_not_a_placeholder():
    with pytest.raises(ValueError, match="no originals_manifest row"):
        service.build_images([("Aa bb", "Fish A")], [_sel()], {})


def test_selection_naming_an_unknown_species_is_an_error():
    with pytest.raises(ValueError, match="not in the species list"):
        service.build_images([("Cc dd", "Fish C")], [_sel("Aa bb")], {"Aa bb": _ORIGINAL})


# ── the committed set ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def committed(tmp_path_factory) -> dict[str, SpeciesImage]:
    db = get_db(tmp_path_factory.mktemp("db") / "committed.db")
    service.sync_species_images(db)
    return store.get_species_images(db)


def _static(path: str) -> Path:
    return REPO / service.STATIC_ROOT / path


def test_every_approved_image_has_licence_and_evidence(committed):
    approved = [i for i in committed.values() if i.status == "approved"]
    assert approved, "no approved images: the committed set is empty"
    for image in approved:
        for field in APPROVED_REQUIRED_FIELDS:
            assert getattr(image, field).strip(), f"{image.species_id}: {field} is empty"
        assert image.licence_evidence_url.startswith("https://")
        assert len(image.original_sha256) == 64


def test_every_approved_image_file_exists(committed):
    for image in committed.values():
        if image.status != "approved":
            continue
        for rel in (image.image_path, image.thumb_path):
            assert _static(rel).is_file(), f"{image.species_id}: {rel} is missing"
            assert _static(rel).with_suffix(".png").is_file(), (
                f"{image.species_id}: no PNG for {rel}"
            )


def test_every_other_species_shares_the_one_placeholder(committed):
    placeholders = [i for i in committed.values() if i.status == "placeholder"]
    assert placeholders
    assert {i.image_path for i in placeholders} == {service.PLACEHOLDER_PATH}
    assert {i.thumb_path for i in placeholders} == {service.PLACEHOLDER_PATH}
    assert _static(service.PLACEHOLDER_PATH).is_file()


def test_every_species_in_the_app_has_exactly_one_row(committed):
    # 69 in the species file; the Northern Largemouth alias folds into Largemouth.
    assert len(committed) == 68
    assert "Micropterus nigricans" not in committed
    # The two hybrids have no illustration, by plan.
    assert committed["Esox masquinongy × Esox lucius"].status == "placeholder"
    assert committed["Salvelinus fontinalis × Salvelinus namaycush"].status == "placeholder"


def test_manifest_and_selection_agree_on_which_files_exist():
    with (REPO / pipe.ORIGINALS_MANIFEST).open(newline="") as fh:
        manifest = {r["species_id"]: r for r in csv.DictReader(fh)}
    wanted = {r.species_id for r in pipe.approved(pipe.load_selection(REPO / pipe.SELECTION_CSV))}
    assert set(manifest) == wanted


def test_credits_page_is_generated_from_the_table_and_is_current(committed):
    images = list(committed.values())
    page = service.render_credits_html(images)

    for image in images:
        if image.status == "approved":
            assert image.common_name in page
            assert image.licence_evidence_url in page
    assert (REPO / service.CREDITS_PAGE).read_text() == page, (
        "credits page is stale: run `uv run python -m src.cli.main sync-species-images`"
    )
