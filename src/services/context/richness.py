"""local_richness(place): which fish species were realistically available there.

The angler profile must not read "lives where many species occur" as
"lifelister", so what someone caught is compared against what is recorded
around the water they fished. This module answers the second half.

Sources, unioned: per-cell aggregate counts (`place_species_counts`, from
iNaturalist research grade and GBIF), and the raw record tables when present
(`observations` research grade, `gbif_observations`, `fish_surveys`). OHN has
no species, so it does not appear here.

Where local data is thin, the regional average richness (median over the
well-recorded cells of the same jurisdiction) stands in and the result carries
a lower confidence. With nothing at all, `basis="none"` and confidence 0 —
the profile then treats lifelist evidence at that place as unknown, rather
than as absent.

Every threshold is a named constant. Groundwork: none of them is tuned.
"""

import math
from statistics import median

from sqlite_utils import Database

from src.jurisdictions.geo import jurisdiction_for_coords
from src.models.context import LocalRichness
from src.storage import place_species_counts

# Radius around a place. 4 km always contains the centre of the 0.05° grid
# cell the place sits in (half-diagonal ~3.4 km at Ontario latitudes).
LOCAL_RICHNESS_RADIUS_KM = 4.0
# Below either of these, local data is "thin" and the regional value is used.
THIN_MIN_RECORDS = 25
THIN_MIN_SPECIES = 5
# How much a regional stand-in is trusted relative to local data.
REGIONAL_CONFIDENCE = 0.4
# Obscured iNaturalist records are placed somewhere within ~22 km; inside a
# 4 km radius they say little about this water, so they are left out.
_INCLUDE_OBSCURED = False


def local_richness(
    db: Database, lat: float, lng: float, radius_km: float = LOCAL_RICHNESS_RADIUS_KM
) -> LocalRichness:
    species: set[str] = set()
    common: dict[str, str] = {}
    by_source: dict[str, int] = {}

    for row in place_species_counts.counts_near(db, lat, lng, radius_km):
        sci = row["scientific_name"].strip().lower()
        species.add(sci)
        if row.get("common_name"):
            common[row["common_name"].strip().lower()] = sci
        by_source[row["source"]] = by_source.get(row["source"], 0) + int(row["count"])

    for source, sci, com in _raw_records(db, lat, lng, radius_km):
        if not sci:
            continue
        sci = sci.strip().lower()
        species.add(sci)
        if com:
            common[com.strip().lower()] = sci
        by_source[source] = by_source.get(source, 0) + 1

    n_records = sum(by_source.values())
    thin = n_records < THIN_MIN_RECORDS or len(species) < THIN_MIN_SPECIES
    out = LocalRichness(
        lat=lat,
        lng=lng,
        radius_km=radius_km,
        species=sorted(species),
        common_names=common,
        n_records=n_records,
        thin=thin,
        by_source=by_source,
    )
    if not thin:
        out.richness, out.basis, out.confidence = float(len(species)), "local", 1.0
        return out

    regional = regional_richness(db, jurisdiction_for_coords(lat, lng))
    if regional is not None:
        out.richness, out.basis, out.confidence = regional, "regional", REGIONAL_CONFIDENCE
    return out


def regional_richness(db: Database, jurisdiction: str) -> float | None:
    """Median species count over the well-recorded cells of a jurisdiction."""
    cells = place_species_counts.species_per_cell(db, jurisdiction)
    good = [n for n, recs in cells.values() if recs >= THIN_MIN_RECORDS and n >= THIN_MIN_SPECIES]
    return float(median(good)) if good else None


def _raw_records(db: Database, lat: float, lng: float, radius_km: float):
    """(source, scientific, common) for raw records within the radius."""
    names = set(db.table_names())
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    box = [lat - dlat, lat + dlat, lng - dlng, lng + dlng]
    queries = []
    if "observations" in names:
        obscured = "" if _INCLUDE_OBSCURED else " AND COALESCE(is_obscured, 0) = 0"
        queries.append(
            (
                "iNaturalist records",
                "SELECT species, common_name, lat, lng FROM observations "
                "WHERE quality_grade = 'research' AND lat BETWEEN ? AND ? "
                f"AND lng BETWEEN ? AND ?{obscured}",
            )
        )
    if "gbif_observations" in names:
        queries.append(
            (
                "GBIF records",
                "SELECT species, common_name, lat, lng FROM gbif_observations "
                "WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
            )
        )
    if "fish_surveys" in names:
        queries.append(
            (
                "fish surveys",
                "SELECT species_scientific_name, species_common_name, lat, lng FROM fish_surveys "
                "WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
            )
        )
    for source, sql in queries:
        for sci, com, rlat, rlng in db.execute(sql, box).fetchall():
            if rlat is None or rlng is None:
                continue
            if place_species_counts._km(lat, lng, rlat, rlng) <= radius_km:
                yield source, sci, com
