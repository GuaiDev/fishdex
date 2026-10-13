"""Tests for the species image pipeline, using synthetic plates (no network)."""

import csv
import hashlib

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src.models.commons_file import CommonsFile
from src.services import species_image_pipeline as pipe


def _plate(width=400, height=200, head="left") -> Image.Image:
    """A tinted, vignetted 'paper' with an elliptical fish and a white eye spot.

    Drawn at 4x and shrunk so the fish edge is anti-aliased like a real scan.
    """
    k = 4
    yy, xx = np.mgrid[0 : height * k, 0 : width * k]
    tint = np.array([205.0, 225.0, 210.0])
    shade = 1.0 - 0.10 * (
        ((xx - width * k / 2) / (width * k)) ** 2 + ((yy - height * k / 2) / (height * k)) ** 2
    )
    paper = (tint[None, None, :] * shade[..., None]).astype(np.uint8)
    img = Image.fromarray(paper, "RGB")
    draw = ImageDraw.Draw(img)
    draw.ellipse((60 * k, 60 * k, 340 * k, 140 * k), fill=(70, 90, 40))
    if head == "left":
        tail = [(330, 100), (380, 70), (380, 130)]
    else:
        tail = [(70, 100), (20, 70), (20, 130)]
    draw.polygon([(x * k, y * k) for x, y in tail], fill=(150, 60, 40))
    eye_x = (100 if head == "left" else 300) * k
    draw.ellipse((eye_x - 8 * k, 92 * k, eye_x + 8 * k, 108 * k), fill=(245, 245, 235))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def test_cut_out_removes_paper_and_keeps_the_fish():
    rgba = np.asarray(pipe.cut_out(_plate()))
    alpha = rgba[..., 3]
    assert alpha[2, 2] == 0 and alpha[-3, -3] == 0  # paper corners
    assert alpha[100, 200] == 255  # body
    assert alpha[100, 100] == 255  # enclosed white eye stays opaque


def test_cut_out_has_no_paper_halo():
    rgba = np.asarray(pipe.cut_out(_plate())).astype(int)
    edge = (rgba[..., 3] > 0) & (rgba[..., 3] < 255)
    assert edge.any()
    # Partial pixels carry fish colour, not the pale paper tint (205,225,210).
    assert rgba[..., 1][edge].mean() < 150


def test_cut_out_drops_stray_marks_far_from_the_fish():
    plate = _plate()
    ImageDraw.Draw(plate).rectangle((380, 5, 392, 12), fill=(250, 250, 250))
    alpha = np.asarray(pipe.cut_out(plate))[..., 3]
    assert alpha[8, 386] == 0


def test_normalise_uses_shared_canvas_and_margins():
    cut = pipe.cut_out(_plate())
    size = pipe.SIZES["carousel"]
    out = pipe.normalise(cut, "left", size)
    assert out.size == size
    bbox = out.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    assert w <= size[0] * pipe.FISH_BOX[0] + 2 and h <= size[1] * pipe.FISH_BOX[1] + 2
    assert abs((bbox[0] + bbox[2]) / 2 - size[0] / 2) <= 2
    assert abs((bbox[1] + bbox[3]) / 2 - size[1] / 2) <= 2


def test_right_facing_fish_is_flipped_to_face_left():
    size = pipe.SIZES["tile"]

    def head_side(out):
        a = np.asarray(out)[..., 3] > 24
        cols = np.where(a.any(axis=0))[0]
        mid = (cols.min() + cols.max()) / 2
        # The tail is the thin end: the side with less mass is the tail side.
        left_mass, right_mass = a[:, : int(mid)].sum(), a[:, int(mid) :].sum()
        return "left" if left_mass > right_mass else "right"

    facing_left = pipe.normalise(pipe.cut_out(_plate(head="left")), "left", size)
    facing_right = pipe.normalise(pipe.cut_out(_plate(head="right")), "right", size)
    assert head_side(facing_left) == head_side(facing_right) == "left"


def test_invalid_facing_is_rejected():
    with pytest.raises(ValueError):
        pipe.normalise(pipe.cut_out(_plate()), "up", pipe.SIZES["tile"])


def test_slug_comes_from_scientific_name():
    row = pipe.Selection("Micropterus dolomieu", "Smallmouth Bass", "u", "download", "r", "left")
    assert row.slug == "micropterus-dolomieu"


def test_selection_loading_and_approval_filter(tmp_path):
    path = tmp_path / "selection.csv"
    path.write_text(
        "species_id,common_name,source_page,decision,reason,facing\n"
        "A b,Fish A,u1,download,ok,left\n"
        "C d,Fish C,u2,excluded,weak licence,\n"
    )
    rows = pipe.load_selection(path)
    assert [r.species_id for r in pipe.approved(rows)] == ["A b"]


def test_export_writes_png_and_webp_at_every_size(tmp_path):
    plate = tmp_path / "a-b.png"
    _plate().save(plate)
    row = pipe.Selection("A b", "Fish A", "u", "download", "ok", "left")
    written = pipe.export(row, plate, tmp_path / "out")
    names = sorted(p.name for p in written)
    assert names == [
        "a-b-carousel.png",
        "a-b-carousel.webp",
        "a-b-tile.png",
        "a-b-tile.webp",
    ]
    assert Image.open(tmp_path / "out" / "a-b-carousel.webp").size == (960, 660)
    assert Image.open(tmp_path / "out" / "a-b-tile.png").size == (500, 344)
    assert Image.open(tmp_path / "out" / "a-b-tile.png").mode == "RGBA"


def test_process_all_refuses_rows_without_facing():
    row = pipe.Selection("A b", "Fish A", "u", "download", "ok", "")
    with pytest.raises(ValueError, match="facing not set"):
        pipe.process_all([row])


def test_fetch_records_the_hash_of_the_untouched_original(tmp_path, monkeypatch):
    payload = b"original bytes"
    record = CommonsFile(
        title="File:Fish a.jpg",
        page_url="https://commons.wikimedia.org/wiki/File:Fish_a.jpg",
        original_url="https://upload.wikimedia.org/x/Fish_a.jpg",
        width=10,
        height=10,
        mime="image/jpeg",
    )
    monkeypatch.setattr(pipe._commons, "file_records", lambda titles: {titles[0]: record})
    monkeypatch.setattr(
        pipe._commons,
        "download_original",
        lambda url, dest: (
            dest.parent.mkdir(parents=True, exist_ok=True),
            dest.write_bytes(payload),
        ),
    )
    row = pipe.Selection(
        "A b",
        "Fish A",
        "https://commons.wikimedia.org/wiki/File:Fish_a.jpg",
        "download",
        "ok",
        "left",
    )
    manifest = pipe.fetch_originals([row], tmp_path / "orig", tmp_path / "m.csv")

    assert manifest[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "orig" / "a-b.jpg").read_bytes() == payload
    with (tmp_path / "m.csv").open() as fh:
        assert list(csv.DictReader(fh))[0]["file"] == "a-b.jpg"


def test_contact_sheet_embeds_every_species(tmp_path):
    plate = tmp_path / "orig" / "a-b.png"
    plate.parent.mkdir()
    _plate().save(plate)
    row = pipe.Selection("A b", "Fish A", "https://example/u", "download", "ok", "left")
    pipe.export(row, plate, tmp_path / "out")
    sheet = pipe.write_contact_sheet(
        [row],
        [{"species_id": "A b", "sha256": "ab" * 32}],
        tmp_path / "sheet.html",
        tmp_path / "out",
        tmp_path / "orig",
    )
    html = sheet.read_text()
    assert "Fish A" in html and "data:image/webp;base64," in html
