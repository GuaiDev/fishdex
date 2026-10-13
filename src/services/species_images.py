"""Species reference images: which species have an approved one, and its provenance.

One image per species, the same for every viewer. The rows are derived from three
committed files, so the table can always be rebuilt:

  * the species list (`data/processed/ontario_species_ranges.json`), canonicalised
    the way the FishDex API does, so a species is keyed by the id the UI uses;
  * `data/species_images/selection.csv`, where the captain's review lives;
  * `data/species_images/originals_manifest.csv`, which records each original's
    hash and the licence evidence read from its source page.

A species gets `approved` only when the review says so AND the manifest holds
its provenance. Everything else gets the single shared silhouette, marked
`placeholder` in the data.
"""

import csv
import html
import importlib
from pathlib import Path

from sqlite_utils import Database

from src.models.species_image import SpeciesImage
from src.services import species_image_pipeline as pipeline
from src.services.species_family import canonical_scientific_name
from src.storage import species_images as store

STATIC_ROOT = Path("src/api/static")
PLACEHOLDER_PATH = "species/silhouette.svg"
CREDITS_PAGE = STATIC_ROOT / "species-credits.html"


def species_catalogue() -> list[tuple[str, str]]:
    """(species_id, common name) for every species in the app, aliases folded."""
    ranges = importlib.import_module("src.ingest.jurisdictions.ca_on.species_ranges")
    out: dict[str, str] = {}
    for sr in ranges.load_species_database():
        sci = canonical_scientific_name(sr.scientific_name) if sr.scientific_name else None
        out.setdefault(sci or sr.species.lower(), sr.species)
    return list(out.items())


def _read_manifest(path: Path) -> dict[str, dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["species_id"]: row for row in csv.DictReader(fh)}


def build_images(
    catalogue: list[tuple[str, str]],
    selection: list[pipeline.Selection],
    manifest: dict[str, dict],
) -> list[SpeciesImage]:
    known = {species_id for species_id, _ in catalogue}
    reviewed = {s.species_id: s for s in selection if s.decision == "download"}
    stray = sorted(set(reviewed) - known)
    if stray:
        raise ValueError(f"selection.csv names species not in the species list: {stray}")

    images = []
    for species_id, common_name in catalogue:
        row = reviewed.get(species_id)
        original = manifest.get(species_id)
        if row and row.review == "approved":
            if not original:
                raise ValueError(f"{species_id} is approved but has no originals_manifest row")
            images.append(
                SpeciesImage(
                    species_id=species_id,
                    common_name=common_name,
                    status="approved",
                    image_path=f"species/{row.slug}-carousel.webp",
                    thumb_path=f"species/{row.slug}-tile.webp",
                    source_name=pipeline.SOURCE_NAME,
                    source_url=original["source_url"],
                    creator=original["creator"],
                    licence=original["licence"],
                    licence_evidence_url=original["licence_evidence_url"],
                    retrieved_date=original["retrieved"],
                    original_sha256=original["sha256"],
                    processing_version=pipeline.PROCESSING_VERSION,
                    match_confidence=original["match_confidence"],
                    name_as_published=original["name_as_published"],
                )
            )
        else:
            images.append(
                SpeciesImage(
                    species_id=species_id,
                    common_name=common_name,
                    status="placeholder",
                    image_path=PLACEHOLDER_PATH,
                    thumb_path=PLACEHOLDER_PATH,
                )
            )
    return images


def current_images() -> list[SpeciesImage]:
    """Rows as the committed files say they should be."""
    return build_images(
        species_catalogue(),
        pipeline.load_selection(),
        _read_manifest(pipeline.ORIGINALS_MANIFEST),
    )


def sync_species_images(db: Database) -> list[SpeciesImage]:
    """Rebuild the species_image table from the committed files."""
    images = current_images()
    store.replace_species_images(db, images)
    return images


# ── credits page ───────────────────────────────────────────────────────────

_CREDITS_CSS = """
:root{color-scheme:light dark;--bg:#f6f4ee;--fg:#1d1b17;--muted:#6a655a;--card:#fff;--line:#d9d5ca}
@media (prefers-color-scheme:dark){
:root{--bg:#14181a;--fg:#e9e6dd;--muted:#9a9588;--card:#1c2224;--line:#2d3538}}
body{font:15px/1.5 system-ui,sans-serif;margin:0;background:var(--bg);color:var(--fg)}
main{max-width:860px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 8px} p{color:var(--muted);margin:6px 0}
ul{list-style:none;margin:20px 0 0;padding:0;display:grid;gap:12px}
li{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;
  display:grid;grid-template-columns:140px 1fr;gap:14px;align-items:center}
li img{width:140px;height:auto;display:block;border-radius:8px;background:#3b6f7c}
h2{font-size:16px;margin:0} .sci{font-style:italic;color:var(--muted)}
.meta{font-size:13px;color:var(--muted);margin-top:6px;word-break:break-word}
a{color:inherit}
@media (max-width:520px){li{grid-template-columns:1fr}li img{width:100%}}
"""


def render_credits_html(images: list[SpeciesImage]) -> str:
    """The attribution page, generated from the species_image rows and nothing else."""
    approved = sorted((i for i in images if i.status == "approved"), key=lambda i: i.common_name)
    placeholders = sum(1 for i in images if i.status == "placeholder")
    esc = html.escape
    items = "".join(
        f"""
<li><img src="{esc(i.thumb_path)}" alt="{esc(i.common_name)}" loading="lazy">
<div><h2>{esc(i.common_name)}</h2><div class="sci">{esc(i.species_id)}</div>
<div class="meta">Creator: {esc(i.creator)}<br>
Source: <a href="{esc(i.source_url)}">{esc(i.source_name)}</a><br>
Licence: {esc(i.licence)}
(<a href="{esc(i.licence_evidence_url)}">evidence</a>)<br>
Retrieved {esc(i.retrieved_date)} &middot; original SHA-256
<code>{esc(i.original_sha256[:16])}&hellip;</code></div></div></li>"""
        for i in approved
    )
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FishDex image credits</title>
<style>{_CREDITS_CSS}</style>
<main>
<h1>Species image credits</h1>
<p>Species illustrations are public-domain works of the U.S. Fish and Wildlife Service
(mostly by Duane Raver), cut out and resized for FishDex. {len(approved)} species have an
illustration; the other {placeholders} show a generic silhouette.</p>
<ul>{items}
</ul>
</main>
</html>
"""


def write_credits_page(images: list[SpeciesImage], path: Path = CREDITS_PAGE) -> Path:
    path.write_text(render_credits_html(images), encoding="utf-8")
    return path
