"""Catch photo storage — saves uploaded photo files under the configured data directory.

Files land under DATA_DIR/photos, alongside fishing.db, keyed by an unguessable
UUID filename. Served back out via the /photos StaticFiles mount in src/api/main.py.

Uploads are resized and re-encoded on the way in (see save_photo) rather than
stored at original resolution — these are catch-ID photos for in-app display,
not archival originals, and full-resolution phone photos (2-8MB HEIC/JPEG each)
would quickly fill a small persistent volume. Only the resized copy is kept; the
original bytes are discarded after processing.
"""

import io
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()  # lets Image.open() decode iPhone HEIC/HEIF photos

PHOTOS_DIR = Path(os.environ.get("DATA_DIR", "data")) / "photos"

MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10MB, enforced on the original upload before resizing
MAX_DIMENSION = 1600  # longest edge in px after resize
JPEG_QUALITY = 85

_CONTENT_TYPE_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}


def save_photo(upload: UploadFile, *, public_base: str = "/photos") -> dict:
    """Validate, resize, and persist an uploaded photo. Returns {"path": str, "url": str}.

    Always re-encodes to JPEG at MAX_DIMENSION/JPEG_QUALITY regardless of the
    source format (including HEIC), so every stored file is .jpg on disk.
    Raises HTTPException(400) for anything that isn't a real, reasonably-sized image.
    """
    ext = _CONTENT_TYPE_TO_EXT.get((upload.content_type or "").lower())
    if not ext:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported photo type: {upload.content_type}. Use JPEG, PNG, WEBP, or HEIC.",
        )

    data = upload.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Photo exceeds 10MB limit.")
    if not data:
        raise HTTPException(status_code=400, detail="Empty photo upload.")

    try:
        Image.open(io.BytesIO(data)).verify()
    except UnidentifiedImageError as e:
        raise HTTPException(status_code=400, detail="File is not a valid image.") from e

    # verify() leaves the Image object unusable for further ops — reopen fresh.
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image)  # respect phone camera orientation tags
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    dest = PHOTOS_DIR / filename
    image.save(dest, format="JPEG", quality=JPEG_QUALITY, optimize=True)

    return {"path": str(dest), "url": f"{public_base}/{filename}"}
