"""Tests for photo upload validation and storage."""

import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image


def _upload(data: bytes, content_type: str, filename: str = "catch.jpg") -> UploadFile:
    return UploadFile(
        filename=filename, file=io.BytesIO(data), headers={"content-type": content_type}
    )


def _jpeg_bytes(size=(50, 50), color=(60, 110, 70)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _photos_dir(tmp_path, monkeypatch):
    from src.services import photo_storage

    monkeypatch.setattr(photo_storage, "PHOTOS_DIR", tmp_path / "photos")
    return tmp_path / "photos"


def test_save_valid_jpeg_writes_file_and_returns_url():
    from src.services.photo_storage import save_photo

    result = save_photo(_upload(_jpeg_bytes(), "image/jpeg"))
    assert result["url"].startswith("/photos/")
    assert result["url"].endswith(".jpg")
    from pathlib import Path

    assert Path(result["path"]).exists()


def test_save_png_is_normalized_to_jpeg():
    from src.services.photo_storage import save_photo

    buf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buf, format="PNG")
    result = save_photo(_upload(buf.getvalue(), "image/png", filename="catch.png"))
    assert result["url"].endswith(".jpg")


def test_rejects_unsupported_content_type():
    from src.services.photo_storage import save_photo

    with pytest.raises(HTTPException) as exc:
        save_photo(_upload(b"not an image", "application/pdf"))
    assert exc.value.status_code == 400


def test_rejects_corrupt_image_bytes():
    from src.services.photo_storage import save_photo

    with pytest.raises(HTTPException) as exc:
        save_photo(_upload(b"\xff\xd8\xff totally not a real jpeg", "image/jpeg"))
    assert exc.value.status_code == 400


def test_rejects_empty_upload():
    from src.services.photo_storage import save_photo

    with pytest.raises(HTTPException) as exc:
        save_photo(_upload(b"", "image/jpeg"))
    assert exc.value.status_code == 400


def test_rejects_oversized_photo():
    from src.services.photo_storage import MAX_PHOTO_BYTES, save_photo

    oversized = _jpeg_bytes() + b"\x00" * MAX_PHOTO_BYTES
    with pytest.raises(HTTPException) as exc:
        save_photo(_upload(oversized, "image/jpeg"))
    assert exc.value.status_code == 400
    assert "10MB" in exc.value.detail


@pytest.mark.parametrize("size", [(8000, 5001), (10000, 10000)])
def test_rejects_photos_over_pixel_cap(size, _photos_dir):
    from src.services.photo_storage import save_photo

    buf = io.BytesIO()
    Image.new("1", size).save(buf, format="PNG")
    with pytest.raises(HTTPException) as exc:
        save_photo(_upload(buf.getvalue(), "image/png"))
    assert exc.value.status_code == 400
    assert "40 million pixels" in exc.value.detail
    assert not _photos_dir.exists()


def test_filenames_are_unguessable_uuids_not_client_supplied():
    from src.services.photo_storage import save_photo

    result = save_photo(_upload(_jpeg_bytes(), "image/jpeg", filename="../../etc/passwd.jpg"))
    assert "etc" not in result["url"]
    assert "passwd" not in result["url"]


def test_oversized_dimensions_are_downscaled_to_max_dimension():
    from src.services.photo_storage import MAX_DIMENSION, save_photo

    result = save_photo(_upload(_jpeg_bytes(size=(4032, 3024)), "image/jpeg"))
    with Image.open(result["path"]) as saved:
        assert max(saved.size) == MAX_DIMENSION
        # aspect ratio preserved (4032:3024 == 4:3)
        assert saved.size[0] / saved.size[1] == pytest.approx(4032 / 3024, rel=0.01)


def test_small_photo_is_not_upscaled():
    from src.services.photo_storage import save_photo

    result = save_photo(_upload(_jpeg_bytes(size=(50, 50)), "image/jpeg"))
    with Image.open(result["path"]) as saved:
        assert saved.size == (50, 50)


def test_resize_substantially_reduces_file_size_for_large_photos():
    from src.services.photo_storage import save_photo

    original = _jpeg_bytes(size=(4032, 3024))
    result = save_photo(_upload(original, "image/jpeg"))
    saved_size = Path(result["path"]).stat().st_size
    assert saved_size < len(original)


def test_original_bytes_are_not_kept_on_disk():
    """The whole point of the fix: only the resized copy should exist, not the original."""
    from src.services.photo_storage import save_photo

    result = save_photo(_upload(_jpeg_bytes(size=(4032, 3024)), "image/jpeg"))
    files = list(Path(result["path"]).parent.iterdir())
    assert files == [Path(result["path"])]


def test_exif_orientation_is_applied_before_resize():
    from src.services.photo_storage import save_photo

    # A wide (landscape) image tagged as needing 90-degree rotation should be
    # saved tall (portrait) — i.e. exif_transpose actually ran.
    buf = io.BytesIO()
    img = Image.new("RGB", (200, 100), color=(10, 20, 30))
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation tag: rotate 90 CW to display correctly
    img.save(buf, format="JPEG", exif=exif)

    result = save_photo(_upload(buf.getvalue(), "image/jpeg"))
    with Image.open(result["path"]) as saved:
        assert saved.size == (100, 200)


def test_png_with_transparency_is_converted_to_rgb():
    from src.services.photo_storage import save_photo

    buf = io.BytesIO()
    Image.new("RGBA", (50, 50), (10, 20, 30, 128)).save(buf, format="PNG")
    result = save_photo(_upload(buf.getvalue(), "image/png", filename="catch.png"))
    with Image.open(result["path"]) as saved:
        assert saved.mode == "RGB"
