"""Species image pipeline: fetch approved originals, then normalise them.

Repeatable end to end. Reads `data/species_images/selection.csv` (which rows the
captain approved, and which way each fish faces in its original), downloads the
approved originals untouched into `data/species_images/originals/` with a
SHA-256 each, and writes one transparent cut-out per species on a shared
canvas:

  carousel  960 x 660   the species page's main image
  tile      500 x 344   binder tiles

both as PNG and WebP, into `src/api/static/species/`. Every fish faces left,
sits in the same central box, and is whole (never cropped). Background removal
is offline: Pillow, numpy and scipy only.

Nothing here approves an image. Approval is a human reading the contact sheet.
"""

import csv
import hashlib
import importlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

_commons = importlib.import_module("src.ingest.global.commons_images")
_audit = importlib.import_module("src.services.species_image_audit")

PROCESSING_VERSION = "1"

SELECTION_CSV = Path("data/species_images/selection.csv")
ORIGINALS_DIR = Path("data/species_images/originals")
ORIGINALS_MANIFEST = Path("data/species_images/originals_manifest.csv")
OUTPUT_DIR = Path("src/api/static/species")

# One landscape canvas for every fish (16:11, the ratio of the binder tile).
SIZES = {"carousel": (960, 660), "tile": (500, 344)}
# The fish fits inside this share of the canvas, centred. The design fades the
# image edges with a radial mask, so a fish must stay clear of them.
FISH_BOX = (0.80, 0.60)

SOURCE_NAME = "US Fish and Wildlife Service National Digital Library, via Wikimedia Commons"

MANIFEST_COLUMNS = [
    "species_id",
    "file",
    "sha256",
    "bytes",
    "source_url",
    "original_url",
    "retrieved",
    "creator",
    "licence",
    "licence_evidence_url",
    "match_confidence",
    "name_as_published",
]


@dataclass
class Selection:
    species_id: str
    common_name: str
    source_page: str
    decision: str
    reason: str
    facing: str
    review: str = ""
    """'approved' once the captain has passed the image on the contact sheet. Never set by code."""

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.species_id.lower()).strip("-")


def load_selection(path: Path = SELECTION_CSV) -> list[Selection]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [Selection(**row) for row in csv.DictReader(fh)]


def approved(rows: list[Selection]) -> list[Selection]:
    """Rows cleared for download (not the same as reviewed: see `Selection.review`)."""
    return [r for r in rows if r.decision == "download"]


# ── fetch ──────────────────────────────────────────────────────────────────


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_originals(
    rows: list[Selection],
    originals_dir: Path = ORIGINALS_DIR,
    manifest_path: Path = ORIGINALS_MANIFEST,
) -> list[dict]:
    """Download each approved original untouched and record its hash."""
    titles = ["File:" + r.source_page.split("/wiki/File:")[1].replace("_", " ") for r in rows]
    records = _commons.file_records(titles)
    manifest = []
    for row, title in zip(rows, titles, strict=True):
        record = records[title]
        ext = Path(record.original_url).suffix.lower()
        dest = originals_dir / f"{row.slug}{ext}"
        _commons.download_original(record.original_url, dest)
        verdict = _audit._classify(record, row.species_id, row.common_name)
        manifest.append(
            {
                "species_id": row.species_id,
                "file": dest.name,
                "sha256": sha256_of(dest),
                "bytes": dest.stat().st_size,
                "source_url": row.source_page,
                "original_url": record.original_url,
                "retrieved": _retrieved_date(dest),
                "creator": _creator(record),
                # The sentence the source page itself shows, with the tag that backs it.
                "licence": (
                    f"{record.licence_short}: {_commons.licence_statement(title)} "
                    f"[Commons tag {record.licence_tag}]"
                ),
                "licence_evidence_url": record.page_url,
                "match_confidence": verdict[1] if verdict else "unmatched",
                "name_as_published": _audit._published_name(record),
            }
        )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(manifest)
    return manifest


def _creator(record) -> str:
    """The creator as the source page states it; says so when the page states none."""
    if record.artist:
        return record.artist
    if "Duane Raver" in record.categories:
        return "Duane Raver (Commons category; no author stated on the file page)"
    return "U.S. Fish and Wildlife Service (no individual author stated on the file page)"


def _retrieved_date(path: Path) -> str:
    """Date the file landed on disk (the download date; stable across reruns)."""
    return date.fromtimestamp(path.stat().st_mtime).isoformat()


# ── background removal ─────────────────────────────────────────────────────

_BORDER_FRACTION = 0.02
_BG_SIGMA_FRACTION = 0.04  # smoothness of the fitted paper-colour surface
_T_BG = 16.0  # colour distance (RGB, 0-255) below which a pixel counts as paper
_EDGE_BLUR = 0.8  # px; anti-aliasing of the cut edge
_MIN_EDGE_ALPHA = 0.12


def _smooth_surface(img: np.ndarray, valid: np.ndarray, sigma: float) -> np.ndarray:
    """Per-channel blur of `img` over the `valid` pixels only (normalised convolution)."""
    weights = ndimage.gaussian_filter(valid.astype(np.float32), sigma, mode="nearest")
    out = np.empty_like(img)
    for c in range(3):
        num = ndimage.gaussian_filter(img[..., c] * valid, sigma, mode="nearest")
        out[..., c] = num / np.maximum(weights, 1e-4)
    return out


def estimate_background(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fit the paper colour as a smooth surface; return (surface, paper mask).

    The plates are painted on tinted paper that is not uniform (vignettes,
    gradients), so a single background colour leaves halos. Start from the
    image border, then repeatedly: fit a smooth surface to the pixels currently
    believed to be paper, flag every pixel close to that surface and connected
    to the border as paper, and refit.
    """
    h, w = img.shape[:2]
    b = max(2, int(min(h, w) * _BORDER_FRACTION))
    valid = np.zeros((h, w), dtype=bool)
    valid[:b, :], valid[-b:, :], valid[:, :b], valid[:, -b:] = True, True, True, True
    border_colour = np.median(img[valid], axis=0)
    # Seed: border pixels near the border's own median (drops stray marks/specks).
    valid &= np.linalg.norm(img - border_colour, axis=2) < _T_BG * 1.5

    sigma = max(h, w) * _BG_SIGMA_FRACTION
    surface = _smooth_surface(img, valid.astype(np.float32), sigma)
    paper = valid
    for _ in range(4):
        dist = np.linalg.norm(img - surface, axis=2)
        near = dist < _T_BG
        labels, _n = ndimage.label(near)
        edge_labels = np.unique(
            np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
        )
        paper = np.isin(labels, edge_labels[edge_labels > 0])
        surface = _smooth_surface(img, paper.astype(np.float32), sigma)
    return surface, paper


def cut_out(image: Image.Image) -> Image.Image:
    """Return the fish as an RGBA image: paper transparent, edges anti-aliased."""
    img = np.asarray(image.convert("RGB"), dtype=np.float32)
    _surface, paper = estimate_background(img)

    # Hard mask: everything not paper-connected-to-the-border. Enclosed
    # paper-coloured regions (a white belly, an eye highlight) stay opaque.
    solid = ~paper

    # Keep the fish and any part within a fin's reach of it; drop labels,
    # scan marks and specks.
    labels, n = ndimage.label(solid)
    if n == 0:
        raise ValueError("no foreground found")
    sizes = ndimage.sum(solid, labels, index=np.arange(1, n + 1))
    main = 1 + int(np.argmax(sizes))
    reach = max(2, int(max(img.shape[:2]) * 0.012))
    near_main = ndimage.binary_dilation(labels == main, iterations=reach)
    keep_ids = np.unique(labels[near_main & solid])
    solid = np.isin(labels, keep_ids[keep_ids > 0])

    # Pull the matte in one pixel: the outermost ring is paper blended with
    # paint, and keeping it reads as a pale outline on a dark background.
    solid = ndimage.binary_erosion(solid, iterations=1)

    # Anti-alias the edge with a slight blur of the mask, then repaint every
    # partly-transparent pixel with the colour of the nearest solid fish pixel,
    # so no trace of the paper tint can survive on the edge.
    alpha = ndimage.gaussian_filter(solid.astype(np.float32), _EDGE_BLUR)
    alpha[alpha < _MIN_EDGE_ALPHA] = 0.0
    core = alpha > 0.98
    _, (iy, ix) = ndimage.distance_transform_edt(~core, return_indices=True)
    colour = img[iy, ix]

    rgba = np.dstack([colour, alpha * 255.0]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


# ── normalise to the shared canvas ─────────────────────────────────────────


def normalise(cutout: Image.Image, facing: str, size: tuple[int, int]) -> Image.Image:
    """Crop to the fish, face it left, and centre it on the shared canvas."""
    if facing not in ("left", "right"):
        raise ValueError(f"facing must be 'left' or 'right', got {facing!r}")
    bbox = cutout.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    if bbox is None:
        raise ValueError("cut-out is empty")
    fish = cutout.crop(bbox)
    if facing == "right":
        fish = fish.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    cw, ch = size
    scale = min(cw * FISH_BOX[0] / fish.width, ch * FISH_BOX[1] / fish.height)
    new = (max(1, round(fish.width * scale)), max(1, round(fish.height * scale)))
    fish = _resize_premultiplied(fish, new)

    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(fish, ((cw - new[0]) // 2, (ch - new[1]) // 2))
    return canvas


def _resize_premultiplied(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize without dark/light fringes: filter colour weighted by alpha."""
    arr = np.asarray(img, dtype=np.float32)
    alpha = arr[..., 3:4] / 255.0
    pre = np.dstack([arr[..., :3] * alpha, arr[..., 3:4]])
    channels = [
        np.asarray(Image.fromarray(pre[..., i]).resize(size, Image.Resampling.LANCZOS))
        for i in range(4)
    ]
    out = np.dstack(channels).astype(np.float32)
    a = np.clip(out[..., 3:4], 0, 255)
    rgb = np.where(a > 0, out[..., :3] / np.maximum(a / 255.0, 1e-4), 0)
    return Image.fromarray(np.dstack([np.clip(rgb, 0, 255), a]).astype(np.uint8), "RGBA")


# ── export ─────────────────────────────────────────────────────────────────


def export(row: Selection, original: Path, out_dir: Path = OUTPUT_DIR) -> list[Path]:
    """Cut out one original and write PNG + WebP at every size."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cutout = cut_out(Image.open(original))
    written = []
    for name, size in SIZES.items():
        canvas = normalise(cutout, row.facing, size)
        png = out_dir / f"{row.slug}-{name}.png"
        webp = out_dir / f"{row.slug}-{name}.webp"
        canvas.save(png, optimize=True)
        canvas.save(webp, quality=90, method=6)
        written += [png, webp]
    return written


def original_path(row: Selection, originals_dir: Path = ORIGINALS_DIR) -> Path:
    matches = sorted(originals_dir.glob(f"{row.slug}.*"))
    if not matches:
        raise FileNotFoundError(f"no original for {row.species_id}; run the fetch step")
    return matches[0]


def process_all(rows: list[Selection]) -> list[Path]:
    missing = [r.species_id for r in rows if not r.facing]
    if missing:
        raise ValueError(f"facing not set in selection.csv for: {', '.join(missing)}")
    written: list[Path] = []
    for row in rows:
        written += export(row, original_path(row))
    return written


# ── contact sheet ──────────────────────────────────────────────────────────

_SHEET_CSS = """
body{font:14px/1.4 system-ui,sans-serif;margin:0;background:#f1efe9;color:#1d1b17}
header{padding:20px 28px;background:#fff;border-bottom:1px solid #d9d5ca}
h1{margin:0 0 6px;font-size:20px} p{margin:4px 0;max-width:880px;color:#4a463e}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));
  gap:18px;padding:22px 28px}
.card{background:#fff;border:1px solid #d9d5ca;border-radius:12px;padding:14px}
.card h2{margin:0;font-size:16px} .card .sci{font-style:italic;color:#6a655a}
.meta{font-size:12px;color:#6a655a;margin:6px 0 10px;word-break:break-all}
.views{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.v{position:relative;aspect-ratio:16/11;border-radius:10px;overflow:hidden}
.v img{width:100%;height:100%;object-fit:contain;display:block}
.v small{position:absolute;left:8px;top:6px;font-size:11px;color:#fff;
  text-shadow:0 1px 2px #000;z-index:2}
.biome{background:linear-gradient(180deg,hsl(190 34% 60%),hsl(190 42% 36%) 66%,hsl(190 48% 20%))}
.biome img{filter:drop-shadow(0 18px 22px rgba(0,0,0,.4))}
.dark img{filter:brightness(0);opacity:.5}
.check{background:#fff
  conic-gradient(#ccc 25%,#fff 0 50%,#ccc 0 75%,#fff 0) 0 0/16px 16px}
.magenta{background:#ff00ff}
.strip{display:flex;overflow-x:auto;gap:0;padding:0 28px 22px}
.strip div{flex:0 0 300px;aspect-ratio:16/11;position:relative;border-right:1px solid #0003}
.strip div::after{content:"";position:absolute;left:0;right:0;top:50%;border-top:1px dashed #fff8}
.strip img{width:100%;height:100%;object-fit:contain;display:block}
"""


def _data_uri(path: Path) -> str:
    import base64

    return "data:image/webp;base64," + base64.b64encode(path.read_bytes()).decode()


def write_contact_sheet(
    rows: list[Selection],
    manifest: list[dict],
    out_path: Path,
    out_dir: Path = OUTPUT_DIR,
    originals_dir: Path = ORIGINALS_DIR,
) -> Path:
    """One self-contained HTML page for human review of every output.

    Each fish is shown four ways: on the app's underwater background, blacked
    out the way an uncaught species renders, on a checkerboard, and on flat
    magenta. Leftover paper shows up on the last two as a box or fringe.
    """
    hashes = {m["species_id"]: m for m in manifest}
    cards, strip = [], []
    for row in rows:
        car = _data_uri(out_dir / f"{row.slug}-carousel.webp")
        tile = _data_uri(out_dir / f"{row.slug}-tile.webp")
        m = hashes[row.species_id]
        orig = Image.open(original_path(row, originals_dir))
        flipped = "flipped" if row.facing == "right" else "not flipped"
        cards.append(
            f"""<div class="card"><h2>{row.common_name}</h2>
<div class="sci">{row.species_id}</div>
<div class="meta">original {orig.width}x{orig.height} &middot; head faces {row.facing} in
the original ({flipped}) &middot; sha256 {m["sha256"][:16]}&hellip;<br>
<a href="{row.source_page}">{row.source_page}</a></div>
<div class="views">
<div class="v biome"><small>in app (carousel)</small><img src="{car}"></div>
<div class="v biome dark"><small>uncaught</small><img src="{car}"></div>
<div class="v check"><small>alpha on checkerboard</small><img src="{car}"></div>
<div class="v magenta"><small>alpha on magenta</small><img src="{tile}"></div>
</div></div>"""
        )
        strip.append(f'<div class="biome"><img src="{car}"></div>')
    html = f"""<!doctype html><meta charset="utf-8"><title>FishDex species images</title>
<style>{_SHEET_CSS}</style>
<header><h1>FishDex species images &mdash; contact sheet ({len(rows)} species)</h1>
<p>Processing version {PROCESSING_VERSION}. Carousel {SIZES["carousel"][0]}x{SIZES["carousel"][1]},
tile {SIZES["tile"][0]}x{SIZES["tile"][1]}; every fish faces left, sits in the central
{int(FISH_BOX[0] * 100)}% x {int(FISH_BOX[1] * 100)}% box and is shown whole.</p>
<p>Check: fin edges and spines, any pale fringe or leftover paper box (the magenta and
checkerboard views, and the blacked-out view, expose it), and whether neighbours line up in the
strip below. Nothing here is approved until you say so.</p></header>
<div class="strip">{"".join(strip)}</div>
<div class="grid">{"".join(cards)}</div>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
