"""Coverage audit: which species have a public-domain Duane Raver illustration?

Searches Wikimedia Commons for US Fish and Wildlife Service illustrations by
Duane Raver and matches them to the species list by scientific name. A file
counts as a candidate only when its Commons page carries a PD-USGov tag AND
credits Raver; anything else is excluded, never guessed at.

Match outcomes (the `status` column):
  found          a qualifying file names the species' current scientific name
  name-mismatch  a qualifying file exists but only under an old name or a
                 common name; needs a human look before it is used
  not-found      no qualifying file

Metadata only. No image bytes are fetched.
"""

import csv
import importlib
import re
from dataclasses import dataclass
from pathlib import Path

from src.models.commons_file import PD_AUTHOR_TEMPLATES, CommonsFile
from src.models.species_range import SpeciesRange

_commons = importlib.import_module("src.ingest.global.commons_images")

CSV_COLUMNS = [
    "species_id",
    "common_name",
    "scientific_name",
    "candidate_source_url",
    "licence_text",
    "native_resolution",
    "match_confidence",
    "status",
    "name_as_published",
    "creator",
    "licence_evidence_url",
    "fws_source_url",
    "commons_tag",
    "licence_tier",
    "alternates",
    "notes",
]

# Names these species were published under, or are still sometimes filed under.
# A hit on one of these is a name-mismatch for review, never an automatic match.
SYNONYMS: dict[str, list[str]] = {
    "Sander vitreus": ["Stizostedion vitreum"],
    "Sander canadensis": ["Stizostedion canadense"],
    "Etheostoma olmstedi": ["Etheostoma nigrum olmstedi"],
    "Chrosomus eos": ["Phoxinus eos"],
    "Chrosomus neogaeus": ["Phoxinus neogaeus"],
    "Ameiurus nebulosus": ["Ictalurus nebulosus"],
    "Ameiurus melas": ["Ictalurus melas"],
    "Oncorhynchus mykiss": ["Salmo gairdneri", "Salmo gairdnerii"],
    "Rhinichthys obtusus": ["Rhinichthys atratulus"],
    "Micropterus nigricans": ["Micropterus salmoides"],
    "Cyprinella spiloptera": ["Notropis spilopterus"],
    "Luxilus cornutus": ["Notropis cornutus"],
    "Catostomus commersonii": ["Catostomus commersoni"],
}

# A subspecies epithet after the species name means the file is about a
# different taxon. 'Etheostoma nigrum olmstedi' is not a Johnny darter.
_EXCLUDED_CONTAINING: dict[str, list[str]] = {
    "Etheostoma nigrum": ["nigrum olmstedi"],
}

_DERIVED_MARKERS = ("white background", "edit", "flipped", "recropped", "transformed", "cropped")
_PUBLISHED_NAME_RE = re.compile(r"\(([A-Z][a-z]+ [a-z]+(?: [a-z]+)?)\)")


@dataclass
class AuditRow:
    species_id: str
    common_name: str
    scientific_name: str
    candidate_source_url: str = ""
    licence_text: str = ""
    native_resolution: str = ""
    match_confidence: str = ""
    status: str = "not-found"
    name_as_published: str = ""
    creator: str = ""
    licence_evidence_url: str = ""
    fws_source_url: str = ""
    commons_tag: str = ""
    licence_tier: str = ""
    alternates: str = ""
    notes: str = ""


def _is_hybrid(scientific_name: str) -> bool:
    return "×" in scientific_name


def _qualifies(f: CommonsFile) -> bool:
    return (
        f.licence_tier is not None
        and f.is_raver
        and f.mime.startswith("image/")
        and not f.mime.endswith("svg+xml")
    )


def _is_derived(f: CommonsFile) -> bool:
    title = f.title.lower()
    return any(marker in title for marker in _DERIVED_MARKERS)


def _blob(f: CommonsFile) -> str:
    return " ".join([f.title, f.description, *f.categories]).lower()


def _classify(f: CommonsFile, sci: str, common: str) -> tuple[str, str] | None:
    """(status, confidence) of this file for this species, or None if unrelated."""
    blob = _blob(f)
    if any(bad in blob for bad in _EXCLUDED_CONTAINING.get(sci, [])):
        return None
    if sci.lower() in blob:
        in_metadata = sci.lower() in (f.description + " " + " ".join(f.categories)).lower()
        return "found", "high" if in_metadata else "medium"
    if any(syn.lower() in blob for syn in SYNONYMS.get(sci, [])):
        return "name-mismatch", "medium"
    if common.lower() in f.title.lower() or common.lower() in f.description.lower():
        return "name-mismatch", "low"
    return None


def _candidate_titles(sci: str, common: str) -> list[str]:
    names = [sci, *SYNONYMS.get(sci, [])]
    titles: list[str] = []
    for name in names:
        titles += _commons.category_files(f"{name} illustrations")
        titles += _commons.category_files(name)
        titles += _commons.search_files(f'"{name}" Raver')
    titles += _commons.search_files(f'"{common}" Raver')
    return titles


def _published_name(f: CommonsFile) -> str:
    m = _PUBLISHED_NAME_RE.search(f.description)
    return m.group(1) if m else ""


def audit_species(species: list[SpeciesRange]) -> list[AuditRow]:
    # The Raver category is looked up once and shared; per species only the
    # extra files from category and search hits are fetched.
    raver_titles = _commons.category_files("Duane Raver")
    raver_records = _commons.file_records(raver_titles)
    # Raver files without a government tag may still carry PD-author. That is a
    # weaker, separately-labelled tier, looked up for the Raver category only.
    untagged = [t for t, f in raver_records.items() if not f.pd_template]
    raver_records.update(_commons.file_records(untagged, PD_AUTHOR_TEMPLATES))
    rows: list[AuditRow] = []
    for sp in species:
        sci = sp.scientific_name or ""
        row = AuditRow(species_id=sci, common_name=sp.species, scientific_name=sci)
        if not sci or _is_hybrid(sci):
            row.notes = "hybrid, not a binomial; gets the shared placeholder by plan"
            rows.append(row)
            continue

        extra = [t for t in _candidate_titles(sci, sp.species) if t not in raver_records]
        records = {**raver_records, **_commons.file_records(extra)}
        matches: list[tuple[CommonsFile, str, str]] = []
        for f in records.values():
            if not _qualifies(f):
                continue
            verdict = _classify(f, sci, sp.species)
            if verdict:
                matches.append((f, *verdict))

        if not matches:
            rows.append(row)
            continue

        # Best status first, then an unedited original, then the largest file.
        rank = {"found": 0, "name-mismatch": 1}
        matches.sort(
            key=lambda m: (
                rank[m[1]],
                m[0].pd_template is None,
                _is_derived(m[0]),
                -(m[0].width * m[0].height),
            )
        )
        best, status, confidence = matches[0]
        row.status = status
        row.match_confidence = confidence
        row.candidate_source_url = best.page_url
        row.licence_evidence_url = best.page_url
        row.licence_text = (
            f"{best.licence_short}: {_commons.licence_statement(best.title)} "
            f"[Commons tag {best.licence_tag}]"
        )
        row.native_resolution = best.resolution
        row.name_as_published = _published_name(best)
        row.creator = best.artist
        row.fws_source_url = best.fws_source_url or ""
        row.commons_tag = best.licence_tag or ""
        row.licence_tier = best.licence_tier or ""
        row.alternates = "; ".join(
            f"{m[0].page_url} ({m[0].resolution}, {m[1]}, {m[0].licence_tag})" for m in matches[1:]
        )
        if status == "name-mismatch":
            row.notes = (
                f"published as '{row.name_as_published or 'common name only'}'; "
                "confirm it is the same taxon before approving"
            )
        if best.pd_template is None:
            weak = "no US-government tag on the page, only PD-author: needs a licence decision"
            row.notes = f"{row.notes}; {weak}" if row.notes else weak
        rows.append(row)
    return rows


def write_csv(rows: list[AuditRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: getattr(row, c) for c in CSV_COLUMNS})
