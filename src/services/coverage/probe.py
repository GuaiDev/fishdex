"""Read what a database holds, for the check: row counts, the listing table, the spots.

Opened read-only on purpose. `get_db` runs schema migrations, and a coverage check
that rewrites the copy of the hosted database it was asked to inspect is not a check.
"""

import re
import sqlite3
from pathlib import Path

from src.models.coverage import DbProbe, ListingProbe, ProbeSpot

_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", [table]
    ).fetchone()
    return row is not None


def count_rows(conn: sqlite3.Connection, table: str) -> int | None:
    """Rows in `table`, or None when it does not exist. The name is checked, not trusted."""
    if not _TABLE_NAME.match(table) or not table_exists(conn, table):
        return None
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def probe_connection(
    conn: sqlite3.Connection,
    *,
    source: str,
    tables: list[str],
    listing_table: str,
    jurisdiction: str,
    spot_kinds: list[str] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> DbProbe:
    """Everything the check compares between two databases, from one connection.

    Also served by the API's admin probe endpoint, so the host reports about
    itself in exactly the shape a local file does.
    """
    table_rows = {t: count_rows(conn, t) for t in tables}
    return DbProbe(
        source=source,
        table_rows=table_rows,
        listing=_probe_listing(conn, listing_table, jurisdiction),
        spots=probe_spots(conn, jurisdiction, spot_kinds, bbox),
    )


def _probe_listing(conn: sqlite3.Connection, table: str, jurisdiction: str) -> ListingProbe:
    total = count_rows(conn, table)
    if total is None:
        return ListingProbe(table=table, table_present=False)
    columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    if "jurisdictions_present" in columns:
        # species_ranges keeps a comma-separated list of jurisdictions per species.
        in_jurisdiction = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE jurisdictions_present LIKE ?',
            [f"%{jurisdiction}%"],
        ).fetchone()[0]
    elif "jurisdiction" in columns:
        in_jurisdiction = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE jurisdiction = ?', [jurisdiction]
        ).fetchone()[0]
    else:
        # No way to tell which rows belong to the jurisdiction, so claim none:
        # a gate that cannot tell must not pass.
        in_jurisdiction = 0
    return ListingProbe(
        table=table,
        table_present=True,
        rows_for_jurisdiction=in_jurisdiction,
        rows_total=total,
    )


def probe_spots(
    conn: sqlite3.Connection,
    jurisdiction: str,
    kinds: list[str] | None,
    bbox: tuple[float, float, float, float] | None,
) -> list[ProbeSpot]:
    if not table_exists(conn, "spots"):
        return []
    where, params = ["jurisdiction = ?"], [jurisdiction]
    if kinds:
        where.append(f"kind IN ({', '.join('?' * len(kinds))})")
        params += kinds
    if bbox:
        where.append("lng BETWEEN ? AND ? AND lat BETWEEN ? AND ?")
        params += [bbox[0], bbox[2], bbox[1], bbox[3]]
    rows = conn.execute(
        f"SELECT spot_id, kind, lat, lng FROM spots WHERE {' AND '.join(where)}", params
    ).fetchall()
    return [ProbeSpot(spot_id=r[0], kind=r[1], lat=r[2], lon=r[3]) for r in rows]
