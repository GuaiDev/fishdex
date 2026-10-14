"""Storage for per-cell species counts (see models/place_species_count.py).

A cell is replaced whole per source on every fetch, so a species that has
dropped out of a source's results does not linger from an older pull.
"""

import math

from sqlite_utils import Database

from src.models.place_species_count import PlaceSpeciesCount

TABLE = "place_species_counts"


def ensure_table(db: Database) -> None:
    if TABLE in db.table_names():
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            source          TEXT NOT NULL,
            cell_id         TEXT NOT NULL,
            south           REAL NOT NULL,
            west            REAL NOT NULL,
            north           REAL NOT NULL,
            east            REAL NOT NULL,
            jurisdiction    TEXT NOT NULL,
            scientific_name TEXT NOT NULL,
            common_name     TEXT,
            count           INTEGER NOT NULL,
            fetched_at      TEXT NOT NULL,
            PRIMARY KEY (source, cell_id, scientific_name)
        )
    """)
    db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_cell ON {TABLE}(south, west)")


def replace_cell(db: Database, source: str, cell_id: str, rows: list[PlaceSpeciesCount]) -> int:
    """Swap one source's rows for one cell. Returns how many were written."""
    ensure_table(db)
    with db.conn:
        db.execute(f"DELETE FROM {TABLE} WHERE source = ? AND cell_id = ?", [source, cell_id])
        for r in rows:
            db[TABLE].insert(r.model_dump(mode="json"))
    return len(rows)


def counts_near(db: Database, lat: float, lng: float, radius_km: float) -> list[dict]:
    """Rows from every cell whose centre lies within `radius_km` of the point."""
    if TABLE not in db.table_names():
        return []
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    rows = db.execute(
        f"SELECT source, cell_id, south, west, north, east, jurisdiction, "
        f"scientific_name, common_name, count FROM {TABLE} "
        f"WHERE (south + north) / 2 BETWEEN ? AND ? AND (west + east) / 2 BETWEEN ? AND ?",
        [lat - dlat, lat + dlat, lng - dlng, lng + dlng],
    ).fetchall()
    cols = (
        "source",
        "cell_id",
        "south",
        "west",
        "north",
        "east",
        "jurisdiction",
        "scientific_name",
        "common_name",
        "count",
    )
    out = []
    for r in rows:
        row = dict(zip(cols, r, strict=True))
        clat, clng = (row["south"] + row["north"]) / 2, (row["west"] + row["east"]) / 2
        if _km(lat, lng, clat, clng) <= radius_km:
            out.append(row)
    return out


def species_per_cell(db: Database, jurisdiction: str) -> dict[str, tuple[int, int]]:
    """`{cell_id: (distinct species, total records)}` across one jurisdiction."""
    if TABLE not in db.table_names():
        return {}
    rows = db.execute(
        f"SELECT cell_id, COUNT(DISTINCT scientific_name), SUM(count) FROM {TABLE} "
        f"WHERE jurisdiction = ? GROUP BY cell_id",
        [jurisdiction],
    ).fetchall()
    return {r[0]: (int(r[1]), int(r[2] or 0)) for r in rows}


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))
