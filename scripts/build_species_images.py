"""Build the species image set: fetch approved originals, normalise, export.

    uv run python scripts/build_species_images.py fetch
    uv run python scripts/build_species_images.py process
    uv run python scripts/build_species_images.py sheet [OUT.html]
    uv run python scripts/build_species_images.py all [OUT.html]

Run `fetch` first (it is slow: the image host throttles bursts). Every step is
repeatable; `fetch` never rewrites an original it already has.
"""

import csv
import sys
from pathlib import Path

from src.services import species_image_pipeline as pipeline


def main(argv: list[str]) -> int:
    step = argv[1] if len(argv) > 1 else "all"
    rows = pipeline.approved(pipeline.load_selection())
    if step in ("fetch", "all"):
        manifest = pipeline.fetch_originals(rows)
        print(f"fetched {len(manifest)} originals -> {pipeline.ORIGINALS_DIR}")
    if step in ("process", "all"):
        written = pipeline.process_all(rows)
        print(f"wrote {len(written)} files -> {pipeline.OUTPUT_DIR}")
    if step in ("sheet", "all"):
        with pipeline.ORIGINALS_MANIFEST.open(newline="", encoding="utf-8") as fh:
            manifest = list(csv.DictReader(fh))
        out = Path(argv[2]) if len(argv) > 2 else Path("contact-sheet.html")
        print(f"contact sheet -> {pipeline.write_contact_sheet(rows, manifest, out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
