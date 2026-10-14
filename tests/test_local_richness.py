"""local_richness: what was realistically available at a place, local or regional."""

from src.models.place_species_count import PlaceSpeciesCount
from src.services.context.richness import local_richness
from src.storage import place_species_counts

# Recorded species per place. Each place gets enough records to be "local".
RICH = ["common carp", "channel catfish", "white sucker", "rock bass", "yellow perch",
        "creek chub", "bluegill", "pumpkinseed"]  # fmt: skip
SCI = {
    "common carp": "cyprinus carpio",
    "channel catfish": "ictalurus punctatus",
    "white sucker": "catostomus commersonii",
    "rock bass": "ambloplites rupestris",
    "yellow perch": "perca flavescens",
    "creek chub": "semotilus atromaculatus",
    "bluegill": "lepomis macrochirus",
    "pumpkinseed": "lepomis gibbosus",
}


def record(db, lat, lng, species=RICH, per_species=5, source="GBIF"):
    """Recorded species in the 0.05° cell holding (lat, lng)."""
    i, j = int(lat // 0.05), int(lng // 0.05)
    cell = f"{i}_{j}"
    rows = [
        PlaceSpeciesCount(
            source=source,
            cell_id=cell,
            south=i * 0.05,
            west=j * 0.05,
            north=(i + 1) * 0.05,
            east=(j + 1) * 0.05,
            jurisdiction="CA-ON",
            scientific_name=SCI.get(sp, sp).capitalize(),
            common_name=sp,
            count=per_species,
        )
        for sp in species
    ]
    place_species_counts.replace_cell(db, source, cell, rows)


def test_local_richness_uses_local_records_when_there_are_enough(db):
    record(db, 43.01, -80.01)
    r = local_richness(db, 43.01, -80.01)
    assert r.basis == "local" and not r.thin and r.confidence == 1.0
    assert r.richness == len(RICH)
    assert "cyprinus carpio" in r.species and r.common_names["common carp"] == "cyprinus carpio"


def test_thin_local_data_falls_back_to_the_regional_average_with_less_confidence(db):
    record(db, 43.01, -80.01)
    record(db, 44.01, -80.01, species=["common carp"], per_species=2)
    r = local_richness(db, 44.01, -80.01)
    assert r.thin and r.basis == "regional"
    assert r.richness == len(RICH)
    assert 0 < r.confidence < 1


def test_nothing_recorded_anywhere_is_basis_none(db):
    r = local_richness(db, 50.0, -85.0)
    assert r.basis == "none" and r.confidence == 0.0


def test_grid_cells_cover_a_box_exactly_once():
    import importlib

    counts = importlib.import_module("src.ingest.global.species_counts")
    cells = counts.cells_for_bbox(43.50, -79.80, 43.65, -79.55)
    assert len(cells) == 3 * 5  # 0.15° by 0.25° in 0.05° cells, no float spill-over
    assert len({c[0] for c in cells}) == len(cells)
    assert min(c[1] for c in cells) == 43.5 and max(c[3] for c in cells) == 43.65
