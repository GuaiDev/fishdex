"""Persistence for species reference images and their provenance.

Keyed by scientific name, the id the FishDex API already uses. The table holds
the rows `sync` derives from committed files (the selection, the originals
manifest and the species list), so dropping it costs a rerun of
`sync-species-images`, never an answer.
"""

from sqlite_utils import Database

from src.models.species_image import SpeciesImage

TABLE = "species_image"


def ensure_table(db: Database) -> None:
    if TABLE in db.table_names():
        return
    db[TABLE].create(
        {
            "species_id": str,
            "common_name": str,
            "status": str,
            "image_path": str,
            "thumb_path": str,
            "source_name": str,
            "source_url": str,
            "creator": str,
            "licence": str,
            "licence_evidence_url": str,
            "retrieved_date": str,
            "original_sha256": str,
            "processing_version": str,
            "match_confidence": str,
            "name_as_published": str,
        },
        pk="species_id",
    )


def replace_species_images(db: Database, images: list[SpeciesImage]) -> None:
    """Make the table exactly `images`: a species dropped from the list loses its row."""
    ensure_table(db)
    with db.conn:
        db[TABLE].delete_where()
        db[TABLE].insert_all([i.model_dump() for i in images])


def get_species_images(db: Database) -> dict[str, SpeciesImage]:
    """Every row, by species_id. Empty before the first sync."""
    if TABLE not in db.table_names():
        return {}
    return {r["species_id"]: SpeciesImage(**r) for r in db[TABLE].rows}
