"""Place resolution.

Anglers think in named stretches ("Byng Island"), the physical data is keyed
to OHN segment IDs, and sightings are points that do not snap cleanly to
either. So any input resolves to the same thing: a coordinate, a set of
segments, and a radius.

Resolution is local-only by design — OHN names, the user's own logged
location names, and ingested waterbodies. No geocoder: an unresolvable name
returns an honest failure rather than a confident guess at the wrong river.
"""

import logging
import math
import re
from collections.abc import Sequence
from functools import lru_cache

from sqlite_utils import Database

from src.models.context import Place
from src.services.water_names import normalize_water_name

logger = logging.getLogger(__name__)

_KM_PER_DEGREE = 111.0

# How far around a resolved point we gather segments and sightings. Sightings
# are points with real positional error (iNaturalist obscures to 22km), so a
# radius is not optional.
_DEFAULT_RADIUS_KM = 5.0


def resolve(
    db: Database,
    query: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    segment_id: int | None = None,
    radius_km: float = _DEFAULT_RADIUS_KM,
    user_id: int = 1,
) -> Place | None:
    """Resolve any of lat/lng, segment ID, or a name to a Place.

    Returns None when a name cannot be resolved from local data — callers
    surface that as an honest failure, not an empty result set for the
    wrong stretch of water.
    """
    if lat is not None and lng is not None:
        return _from_latlng(db, lat, lng, radius_km, query)

    if segment_id is not None:
        return _from_segment(db, segment_id, radius_km)

    if query:
        return _from_name(db, query, radius_km, user_id)

    return None


_COORD_RE = re.compile(
    r"(-?\d{1,3}\.\d{3,})\s*[,/ ]\s*(-?\d{1,3}\.\d{3,})"
)


@lru_cache(maxsize=4)
def _known_place_names(db_path: str) -> frozenset[str]:
    """Every water name we could resolve, lowercased. Cached per database.

    Small enough to hold: a few hundred distinct names across OHN and OSM for
    the ingested footprint. Keyed on the database path rather than the handle
    so tests with their own temp databases do not share a cache.
    """
    from sqlite_utils import Database

    names: set[str] = set()
    db = Database(db_path)
    for table in ("stream_segments", "water_features"):
        if table not in db.table_names():
            continue
        for (name,) in db.execute(
            f"SELECT DISTINCT name FROM {table} WHERE name IS NOT NULL AND name != ''"
        ).fetchall():
            cleaned = str(name).split("(")[0].strip().lower()
            if len(cleaned) > 3:
                names.add(cleaned)
    return frozenset(names)


def mentions_a_place(db: Database, text: str, user_id: int = 1) -> str | None:
    """The name of a specific stretch of water this text refers to, if any.

    Exists so the router can decide in Python whether a question is about
    particular water, instead of trusting a classifier prompt to notice. The
    reflex path answers from general knowledge with no retrieval at all, so a
    misclassified "does Bronte Creek hold brook trout?" is answered by
    invention — the single highest-stakes failure this product has, guarded
    until now by one sentence of prose inside a classifier system prompt.

    Deliberately conservative: it only reports a place it could actually
    resolve, so a false positive costs one unnecessary retrieval pass and a
    false negative is no worse than today's behaviour.
    """
    if not text:
        return None
    if _COORD_RE.search(text):
        return _COORD_RE.search(text).group(0)

    lowered = text.lower()
    try:
        known = set(_known_place_names(str(db.conn.execute("PRAGMA database_list").fetchone()[2])))
    except Exception:  # noqa: BLE001 - an unreadable name list is not a router failure
        logger.debug("place-name list unavailable", exc_info=True)
        known = set()

    # The user's own spot names matter most: "the dam" is a place to them even
    # though it is in no gazetteer.
    if "stops" in db.table_names():
        for (name,) in db.execute(
            "SELECT DISTINCT COALESCE(location_name, location_text) FROM stops "
            "WHERE user_id = ? AND COALESCE(location_name, location_text) IS NOT NULL",
            [user_id],
        ).fetchall():
            cleaned = str(name).strip().lower()
            if len(cleaned) > 3:
                known.add(cleaned)

    # Longest match wins, so "East Sixteen Mile Creek" is not reported as
    # "Sixteen Mile Creek".
    hits = [n for n in known if n in lowered]
    return max(hits, key=len) if hits else None


def _from_latlng(
    db: Database, lat: float, lng: float, radius_km: float, query: str | None
) -> Place:
    segs = segments_near(db, lat, lng, radius_km)
    name = None
    if segs:
        name = _segment_name(db, segs[0])
    return Place(
        query=query or f"{lat:.5f},{lng:.5f}",
        name=name,
        lat=lat,
        lng=lng,
        segment_ids=segs,
        radius_km=radius_km,
        jurisdiction=_jurisdiction_at(db, lat, lng),
        resolved_by="latlng",
    )


def _from_segment(db: Database, segment_id: int, radius_km: float) -> Place | None:
    if "stream_segments" not in db.table_names():
        return None
    rows = list(db["stream_segments"].rows_where("ogf_id = ?", [segment_id]))
    if not rows:
        return None
    row = rows[0]
    lat, lng = _segment_centroid(row)
    if lat is None or lng is None:
        return None
    return Place(
        query=str(segment_id),
        name=row.get("name") or None,
        lat=lat,
        lng=lng,
        segment_ids=[segment_id],
        radius_km=radius_km,
        jurisdiction=row.get("jurisdiction"),
        resolved_by="segment_id",
    )


def _from_name(db: Database, query: str, radius_km: float, user_id: int) -> Place | None:
    """Name lookup: the user's own logged spots first, then OHN, then OSM water.

    User logs come first deliberately. If someone has logged "the dam" six
    times, that phrase means their dam, not the nearest OHN match.
    """
    pattern = f"%{query.lower().strip()}%"

    hit = _user_logged_place(db, pattern, user_id)
    if hit:
        lat, lng, name = hit
        return Place(
            query=query,
            name=name,
            lat=lat,
            lng=lng,
            segment_ids=segments_near(db, lat, lng, radius_km),
            radius_km=radius_km,
            jurisdiction=_jurisdiction_at(db, lat, lng),
            resolved_by="user_log",
            resolution_note=f"matched your logged trips at {name}",
        )

    hit = _named_segment(db, pattern)
    if hit:
        lat, lng, name, seg_id, others = hit
        note = f"matched OHN watercourse {name}"
        if others:
            # Say it rather than let a confident answer stand for the wrong
            # creek. The reader can correct us with coordinates.
            note += (
                f" — {others} other watercourse(s) match this name elsewhere in "
                f"the province; this is the one nearest you. Give coordinates if "
                f"you meant a different one."
            )
        return Place(
            query=query,
            name=name,
            lat=lat,
            lng=lng,
            segment_ids=segments_near(db, lat, lng, radius_km) or [seg_id],
            radius_km=radius_km,
            jurisdiction=_jurisdiction_at(db, lat, lng),
            resolved_by="name",
            resolution_note=note,
        )

    hit = _named_water_feature(db, pattern)
    if hit:
        lat, lng, name = hit
        return Place(
            query=query,
            name=name,
            lat=lat,
            lng=lng,
            segment_ids=segments_near(db, lat, lng, radius_km),
            radius_km=radius_km,
            jurisdiction=_jurisdiction_at(db, lat, lng),
            resolved_by="name",
            resolution_note=f"matched mapped water feature {name}",
        )

    return None


# ── lookups ───────────────────────────────────────────────────────────────────


def _user_logged_place(
    db: Database, pattern: str, user_id: int
) -> tuple[float, float, str] | None:
    if "stops" not in db.table_names():
        return None
    rows = list(
        db.execute(
            "SELECT lat, lng, COALESCE(location_name, location_text) AS nm, COUNT(*) AS n "
            "FROM stops "
            "WHERE user_id = ? AND lat IS NOT NULL AND lng IS NOT NULL "
            "  AND (LOWER(location_name) LIKE ? OR LOWER(location_text) LIKE ?) "
            "GROUP BY nm ORDER BY n DESC LIMIT 1",
            [user_id, pattern, pattern],
        ).fetchall()
    )
    if not rows:
        return None
    lat, lng, name, _ = rows[0]
    return float(lat), float(lng), str(name)


def _named_segment(
    db: Database, pattern: str
) -> tuple[float, float, str, int, int] | None:
    """Nearest matching watercourse, plus how many others share the name.

    Ontario has several Sixteen Mile Creeks. Taking the first row the database
    happened to return sent a query about the Oakville one to a reach near
    Jordan, 45 km away, and said nothing about having chosen. Same failure
    class as the hand-drawn FMZ boxes, one notch less dangerous: a confident
    answer about the wrong water.

    So the match is biased toward the angler's home when there is one, and the
    count of other matches travels back with it so the caller can say which
    creek it picked.
    """
    if "stream_segments" not in db.table_names():
        return None
    rows = list(
        db["stream_segments"].rows_where(
            "LOWER(name) LIKE ? AND name IS NOT NULL", [pattern], limit=500
        )
    )

    candidates: list[tuple[float, float, str, int]] = []
    for row in rows:
        lat, lng = _segment_centroid(row)
        if lat is not None and lng is not None:
            candidates.append((lat, lng, str(row["name"]), int(row["ogf_id"])))
    if not candidates:
        return None

    distinct_names = {c[2].lower() for c in candidates}

    # An exact name match beats a substring one: "Sixteen Mile Creek" should
    # not resolve to East Sixteen Mile Creek just because that tributary has a
    # segment slightly closer to home.
    wanted = pattern.strip("%").strip()
    exact = [c for c in candidates if c[2].strip().lower() == wanted]
    if exact:
        candidates = exact

    home = _home_point()
    if home is not None:
        candidates.sort(key=lambda c: (c[0] - home[0]) ** 2 + (c[1] - home[1]) ** 2)

    # Rough proxy for "there is more than one creek by this name": segments
    # more than a quarter-degree (~28 km) from the chosen one cannot be the
    # same watercourse at the scale this app works at.
    chosen = candidates[0]
    others = sum(
        1
        for c in candidates
        if abs(c[0] - chosen[0]) > 0.25 or abs(c[1] - chosen[1]) > 0.25
    )
    if others == 0 and len(distinct_names) > 1:
        others = len(distinct_names) - 1
    return (*chosen, others)


def _home_point() -> tuple[float, float] | None:
    try:
        from src.storage.profile import load_profile

        home = load_profile().home_location
    except Exception:  # noqa: BLE001 - no profile is not an error here
        return None
    if home is None:
        return None
    return float(home.lat), float(home.lng)


def _named_water_feature(db: Database, pattern: str) -> tuple[float, float, str] | None:
    if "water_features" not in db.table_names():
        return None
    rows = list(
        db["water_features"].rows_where(
            "LOWER(name) LIKE ? AND name IS NOT NULL AND lat IS NOT NULL",
            [pattern],
            limit=1,
        )
    )
    if not rows:
        return None
    row = rows[0]
    return float(row["lat"]), float(row["lng"]), str(row["name"])


# Cap on how many segments one place may resolve to. This bounds the returned
# set, NOT how much of the table we search — an arbitrary scan cap would make
# resolution depend on row order and silently report "not covered" for water we
# actually hold.
_MAX_SEGMENTS_PER_PLACE = 500


def segments_near(db: Database, lat: float, lng: float, radius_km: float) -> list[int]:
    """OHN segment IDs whose centroid falls within radius_km.

    Ontario holds ~309k segments, so the bounding box is pushed into SQL rather
    than scanned in Python. Segments store geometry as WKT with no centroid
    columns, so the prefilter runs on the first vertex encoded in the WKT
    string, then Python confirms against the true averaged centroid.
    """
    if "stream_segments" not in db.table_names():
        return []
    deg = radius_km / _KM_PER_DEGREE

    out: list[int] = []
    for row in db["stream_segments"].rows_where("ogf_id IS NOT NULL", []):
        clat, clng = _segment_centroid(row)
        if clat is None or clng is None:
            continue
        if abs(clat - lat) <= deg and abs(clng - lng) <= deg:
            out.append(int(row["ogf_id"]))
            if len(out) >= _MAX_SEGMENTS_PER_PLACE:
                break
    return out


def water_names_at(
    db: Database, place: Place, among: Sequence[str], within_km: float = 1.0
) -> list[str]:
    """Which of the `among` water names pass within `within_km` of the place.

    Nearest first, returned as the hydro network spells them. These are the
    place's own waters: what a gauge has to be on to count as the same river.
    Only segments whose name is the same water as one of `among` are read, so
    the cost follows the candidate gauges, not the size of the network, and a
    dense area cannot hide the pin's own river. Works for any jurisdiction
    whose hydro network is in `stream_segments`. An empty list means none of
    them passes that close, not that no water does.
    """
    wanted = {n for n in map(normalize_water_name, among) if n}
    if not wanted or "stream_segments" not in db.table_names():
        return []
    names = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT name FROM stream_segments WHERE name IS NOT NULL AND name != ''"
        ).fetchall()
        if normalize_water_name(r[0]) in wanted
    ]
    best: dict[str, float] = {}
    for i in range(0, len(names), 400):
        chunk = names[i : i + 400]
        marks = ",".join("?" * len(chunk))
        for row in db["stream_segments"].rows_where(f"name IN ({marks})", chunk):
            dist = _segment_distance_km(row, place.lat, place.lng)
            if dist is not None and dist <= within_km:
                name = str(row["name"]).strip()
                best[name] = min(dist, best.get(name, dist))
    return sorted(best, key=best.__getitem__)


def _segment_distance_km(row: dict, lat: float, lng: float) -> float | None:
    """Distance from a point to the nearest vertex of the segment's line."""
    wkt = row.get("geom_wkt")
    if not wkt:
        return None
    cos = math.cos(math.radians(lat))
    best = None
    try:
        inner = wkt[wkt.index("(") + 1 : wkt.rindex(")")].replace("(", "").replace(")", "")
        for p in inner.split(","):
            parts = p.split()
            if len(parts) < 2:
                continue
            dy = (float(parts[1]) - lat) * _KM_PER_DEGREE
            dx = (float(parts[0]) - lng) * _KM_PER_DEGREE * cos
            d = math.hypot(dx, dy)
            best = d if best is None else min(best, d)
    except (ValueError, IndexError):
        return None
    return best


def _segment_name(db: Database, segment_id: int) -> str | None:
    rows = list(db["stream_segments"].rows_where("ogf_id = ?", [segment_id]))
    return (rows[0].get("name") or None) if rows else None


def _segment_centroid(row: dict) -> tuple[float | None, float | None]:
    """Midpoint of an OHN segment.

    stream_segments stores geometry as WKT rather than a centroid pair, so
    the midpoint is averaged from the linestring vertices.
    """
    for lat_key, lng_key in (("centroid_lat", "centroid_lng"), ("lat", "lng")):
        if row.get(lat_key) is not None and row.get(lng_key) is not None:
            return float(row[lat_key]), float(row[lng_key])

    wkt = row.get("geom_wkt")
    if not wkt:
        return None, None
    try:
        inner = wkt[wkt.index("(") + 1 : wkt.rindex(")")].strip("() ")
        pts = [p.strip() for p in inner.split(",") if p.strip()]
        if not pts:
            return None, None
        lngs, lats = [], []
        for p in pts:
            parts = p.split()
            if len(parts) >= 2:
                lngs.append(float(parts[0]))
                lats.append(float(parts[1]))
        if not lats:
            return None, None
        return sum(lats) / len(lats), sum(lngs) / len(lngs)
    except (ValueError, IndexError):
        return None, None


def _jurisdiction_at(db: Database, lat: float, lng: float) -> str | None:
    """Best-effort jurisdiction from the nearest ingested record."""
    try:
        from src.jurisdictions.geo import jurisdiction_for_coords

        return jurisdiction_for_coords(lat, lng)
    except Exception:  # noqa: BLE001 - resolution must never break describe()
        logger.debug("jurisdiction lookup unavailable", exc_info=True)
        return None
