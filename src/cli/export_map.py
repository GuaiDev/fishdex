"""Export map data for the Leaflet UI.

Reads untapped_potential.parquet and writes data/processed/map_data.json as a
GeoJSON FeatureCollection of segment centroids.

Only segments within 150 km of home (Oakville, ON) are included.
Lake Ontario/Erie water-body dots, Virtual Flow segments, and order-1 streams
with observation_density_25km > 500 (dense urban core only) are excluded.
15×15 grid sampling ensures regional coverage.
"""

import json
import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────

HOME_LAT = 43.4675
HOME_LNG = -79.6877
HOME_RADIUS_KM = 150.0
GRID_ROWS = 25
GRID_COLS = 25
GRID_PER_CELL = 200  # 100 top-scoring + 100 random for coverage
GRID_MIN_CELL = 20  # always keep at least this many from any populated cell

# (name, lat_min, lat_max, lng_min, lng_max, min_segments)
ANCHOR_REGIONS: list[tuple[str, float, float, float, float, int]] = [
    ("Hamilton", 43.1, 43.5, -80.1, -79.7, 100),
    ("Niagara", 42.8, 43.3, -80.0, -78.8, 200),
    ("Kawartha", 44.3, 44.8, -78.8, -78.0, 200),
    ("Burlington", 43.25, 43.55, -79.95, -79.60, 200),
]

_UNTAPPED_PATH = Path("data/processed/untapped_potential.parquet")
_OUTPUT_PATH = Path("data/processed/map_data.json")
_HTML_TEMPLATE = Path("src/map/index.html")
_HTML_OUTPUT = Path("data/processed/map_index.html")


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def grid_sample(
    df: pd.DataFrame,
    sort_col: str,
    rows: int = GRID_ROWS,
    cols: int = GRID_COLS,
    per_cell: int = GRID_PER_CELL,
    min_per_cell: int = GRID_MIN_CELL,
) -> pd.DataFrame:
    """Sample segments from each cell of a rows×cols geographic grid.

    Every populated cell contributes at least min_per_cell segments (guaranteeing
    sparse regions like Niagara always appear). Dense cells contribute up to per_cell:
    the top per_cell//2 by score plus per_cell//2 random from the remainder.
    Max output: rows × cols × per_cell segments.
    """
    lat_bins = np.linspace(df["centroid_lat"].min(), df["centroid_lat"].max(), rows + 1)
    lng_bins = np.linspace(df["centroid_lng"].min(), df["centroid_lng"].max(), cols + 1)

    sampled = []
    for i in range(rows):
        for j in range(cols):
            cell = df[
                df["centroid_lat"].between(lat_bins[i], lat_bins[i + 1])
                & df["centroid_lng"].between(lng_bins[j], lng_bins[j + 1])
            ]
            if len(cell) == 0:
                continue
            if len(cell) <= min_per_cell:
                sampled.append(cell)
                continue
            top = cell.nlargest(per_cell // 2, sort_col)
            remainder = cell[~cell.index.isin(top.index)]
            rest = (
                remainder.sample(min(per_cell // 2, len(remainder)), random_state=42)
                if len(remainder) > 0
                else pd.DataFrame()
            )
            combined = pd.concat([top, rest])
            # Guarantee minimum even when per_cell//2 alone is below min_per_cell
            if len(combined) < min_per_cell:
                combined = cell.nlargest(min_per_cell, sort_col)
            sampled.append(combined)

    if not sampled:
        return df.head(0)
    return pd.concat(sampled).drop_duplicates(subset="ogf_id")


def anchor_topup(
    exported: pd.DataFrame,
    source: pd.DataFrame,
    sort_col: str,
    anchors: list[tuple[str, float, float, float, float, int]] = ANCHOR_REGIONS,
) -> pd.DataFrame:
    """Top up named regions that fell below their minimum after grid sampling.

    Grid cell boundaries can cause a region to share a cell with a higher-scoring
    adjacent area, crowding it out. This function adds the best-scoring source
    segments from each under-represented anchor region regardless of grid cells.
    """
    extra: list[pd.DataFrame] = []
    already_exported: set = set(exported["ogf_id"])

    for name, lat_min, lat_max, lng_min, lng_max, min_segs in anchors:
        in_region = exported[
            exported["centroid_lat"].between(lat_min, lat_max)
            & exported["centroid_lng"].between(lng_min, lng_max)
        ]
        count = len(in_region)
        if count >= min_segs:
            logger.info("Anchor %s: %d segments (>= %d, ok)", name, count, min_segs)
            continue
        need = min_segs - count
        candidates = source[
            source["centroid_lat"].between(lat_min, lat_max)
            & source["centroid_lng"].between(lng_min, lng_max)
            & ~source["ogf_id"].isin(already_exported)
        ].nlargest(need, sort_col)
        logger.info(
            "Anchor %s: %d/%d — topping up with %d segments",
            name,
            count,
            min_segs,
            len(candidates),
        )
        extra.append(candidates)
        already_exported.update(candidates["ogf_id"])

    if not extra:
        return exported
    return pd.concat([exported, *extra]).drop_duplicates(subset="ogf_id")


# ── regional coverage helpers ─────────────────────────────────────────────────

_REGIONS = {
    "Niagara": {"lat": (42.8, 43.3), "lng": (-80.0, -78.8)},
    "Hamilton": {"lat": (43.1, 43.5), "lng": (-80.1, -79.7)},
    "Toronto-Barrie": {"lat": (43.8, 44.2), "lng": (-79.8, -79.2)},
    "Kawartha": {"lat": (44.0, 45.5), "lng": (-79.5, -77.5)},
}


def _region_counts(df: pd.DataFrame) -> dict[str, int]:
    counts = {}
    for name, bounds in _REGIONS.items():
        mask = df["centroid_lat"].between(*bounds["lat"]) & df["centroid_lng"].between(
            *bounds["lng"]
        )
        counts[name] = int(mask.sum())
    return counts


# ── main export ───────────────────────────────────────────────────────────────


def export_map_data(
    output_path: Path = _OUTPUT_PATH,
    html_output_path: Path = _HTML_OUTPUT,
) -> dict:
    """Build and write map_data.json. Returns summary stats."""
    logger.info("Loading untapped potential...")
    untapped = pd.read_parquet(_UNTAPPED_PATH)

    # Filter to home radius
    dists = [
        _haversine_km(HOME_LAT, HOME_LNG, row.centroid_lat, row.centroid_lng)
        for row in untapped.itertuples()
    ]
    untapped = untapped.copy()
    untapped["_dist_km"] = dists
    untapped = untapped[untapped["_dist_km"] <= HOME_RADIUS_KM].drop(columns=["_dist_km"])
    logger.info("Within %d km: %d segments", HOME_RADIUS_KM, len(untapped))

    # ── non-fishable segment filtering ────────────────────────────────────────
    # 1. Lake Ontario open-water surface only.
    # The old lat < 43.25 filter incorrectly removed the Niagara Peninsula
    # (42.8–43.25°N, -79.9 to -79.0°W). The precise lake surface sits between
    # 43.2–43.6°N and east of -79.4°W (Kingston direction), well north of the
    # peninsula land mass.
    lake_ontario = (
        (untapped["centroid_lat"] > 43.2)
        & (untapped["centroid_lat"] < 43.6)
        & (untapped["centroid_lng"] > -79.4)
        & (untapped["centroid_lng"] < -76.0)
    )
    # 2. Lake Erie open water and US territory (Ontario border is ~42.6°N at Niagara)
    lake_erie_and_us = untapped["centroid_lat"] < 42.60
    # 3. North of OHN coverage
    north_of_coverage = untapped["centroid_lat"] > 46.40
    # 4. Virtual Flow segments (hydrological connectors, not real streams)
    virtual_flow = (
        untapped["watercourse_type"] == "Virtual Flow"
        if "watercourse_type" in untapped.columns
        else pd.Series(False, index=untapped.index)
    )
    # 5. Likely-culverted urban order-1 streams (only very dense urban core)
    culverted = (
        (untapped["stream_order"] == 1) & (untapped["observation_density_25km"] > 500)
        if "observation_density_25km" in untapped.columns
        else pd.Series(False, index=untapped.index)
    )

    non_fishable = lake_ontario | lake_erie_and_us | north_of_coverage | virtual_flow | culverted
    n_removed = int(non_fishable.sum())
    logger.info(
        "Removed %d non-fishable segments "
        "(lake=%d, erie/us=%d, coverage=%d, virtual=%d, culverted=%d)",
        n_removed,
        int(lake_ontario.sum()),
        int(lake_erie_and_us.sum()),
        int(north_of_coverage.sum()),
        int(virtual_flow.sum()),
        int(culverted.sum()),
    )
    untapped = untapped[~non_fishable]

    _has = lambda c: c in untapped.columns  # noqa: E731
    sort_col = "untapped_score_balanced" if _has("untapped_score_balanced") else "untapped_score"

    # ── geographic grid sampling for regional coverage ─────────────────────────
    n_before_grid = len(untapped)
    pre_grid = untapped  # kept for anchor topup (segments not yet selected by grid)
    untapped = grid_sample(
        untapped,
        sort_col,
        rows=GRID_ROWS,
        cols=GRID_COLS,
        per_cell=GRID_PER_CELL,
        min_per_cell=GRID_MIN_CELL,
    )
    logger.info(
        "After grid sample (%dx%d, max %d/cell, min %d/cell): %d segments (from %d)",
        GRID_ROWS,
        GRID_COLS,
        GRID_PER_CELL,
        GRID_MIN_CELL,
        len(untapped),
        n_before_grid,
    )

    # ── anchor region top-up ──────────────────────────────────────────────────
    untapped = anchor_topup(untapped, pre_grid, sort_col)
    logger.info("After anchor topup: %d segments", len(untapped))

    regional = _region_counts(untapped)
    logger.info("Regional coverage — %s", ", ".join(f"{k}: {v}" for k, v in regional.items()))

    def _percentile_thresholds(col: pd.Series) -> dict:
        vals = col.sort_values(ascending=False).values
        n = len(vals)
        return {
            "p95": round(float(vals[int(n * 0.05)]), 5),
            "p80": round(float(vals[int(n * 0.20)]), 5),
            "p60": round(float(vals[int(n * 0.40)]), 5),
            "p40": round(float(vals[int(n * 0.60)]), 5),
        }

    def _col(suffix: str) -> str:
        return f"untapped_score_{suffix}" if _has(f"untapped_score_{suffix}") else "untapped_score"

    score_cols = {
        "balanced": _col("balanced"),
        "easy": _col("easy"),
        "adventure": _col("adventure"),
    }
    thresholds_by_mode = {
        mode: _percentile_thresholds(untapped[col]) for mode, col in score_cols.items()
    }
    logger.info(
        "Score thresholds (balanced) — p95: %.4f  p80: %.4f  p60: %.4f  p40: %.4f",
        *thresholds_by_mode["balanced"].values(),
    )

    # Build GeoJSON features
    geojson_features = []
    for row in untapped.itertuples():
        ogf_id = row.ogf_id

        lat = float(row.centroid_lat)
        lng = float(row.centroid_lng)

        def _score(col: str) -> float | None:
            v = getattr(row, col, None)
            return round(float(v), 4) if v is not None and v == v else None

        props = {
            "ogf_id": int(ogf_id) if ogf_id == ogf_id else None,
            "untapped_score_balanced": _score(score_cols["balanced"]),
            "untapped_score_easy": _score(score_cols["easy"]),
            "untapped_score_adventure": _score(score_cols["adventure"]),
            # No habitat_score — the scoring pipeline no longer produces one.
            # See untapped_potential module docstring.
            "access_score": round(float(row.access_score), 4),
            "stream_order": int(row.stream_order) if row.stream_order == row.stream_order else None,
            "watercourse_name": str(row.watercourse_name)
            if row.watercourse_name and str(row.watercourse_name) != "nan"
            else None,
            "nearest_named_stream": None,
            "is_confluence_segment": bool(row.is_confluence_segment),
            "connected_to_waterbody": bool(row.connected_to_waterbody),
            "observation_pressure": round(float(row.observation_pressure), 4),
            "google_maps_url": f"https://www.google.com/maps/@{lat},{lng},16z/data=!3m1!1e3",
            "swoop_url": f"https://maps.ontario.ca/swoop/#13/{lat}/{lng}",
        }

        geojson_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": props,
            }
        )

    geojson = {
        "type": "FeatureCollection",
        "features": geojson_features,
        "metadata": {
            "home_lat": HOME_LAT,
            "home_lng": HOME_LNG,
            "radius_km": HOME_RADIUS_KM,
            "segment_count": len(geojson_features),
            "score_thresholds": thresholds_by_mode,
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    geojson_json = json.dumps(geojson, separators=(",", ":"))
    output_path.write_text(geojson_json)

    json_mb = output_path.stat().st_size / 1_048_576
    logger.info("Wrote %d features to %s (%.1f MB)", len(geojson_features), output_path, json_mb)

    # Build self-contained HTML: embed GeoJSON directly
    html_out: Path | None = None
    if _HTML_TEMPLATE.exists():
        # Explicit UTF-8 both ways: the template contains non-ASCII, and the
        # platform default codec (cp1252 on Windows) cannot decode it.
        html_content = _HTML_TEMPLATE.read_text(encoding="utf-8")
        html_content = html_content.replace("%%MAP_DATA%%", geojson_json)
        html_output_path.parent.mkdir(parents=True, exist_ok=True)
        html_output_path.write_text(html_content, encoding="utf-8")
        html_mb = html_output_path.stat().st_size / 1_048_576
        html_out = html_output_path
        logger.info("Self-contained HTML at %s (%.1f MB)", html_output_path, html_mb)
    else:
        logger.warning("HTML template not found at %s", _HTML_TEMPLATE)

    return {
        "segments": len(geojson_features),
        "non_fishable_removed": n_removed,
        "grid_input": n_before_grid,
        "regional": regional,
        "json_mb": round(json_mb, 1),
        "html_mb": round(html_output_path.stat().st_size / 1_048_576, 1) if html_out else None,
        "path": str(output_path),
        "html": str(html_out) if html_out else None,
        "score_thresholds": thresholds_by_mode,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stats = export_map_data()
    print(f"\nExported {stats['segments']:,} segments -> {stats['path']} ({stats['json_mb']} MB)")
    print(f"Non-fishable removed: {stats['non_fishable_removed']:,}")
    t = stats["score_thresholds"]["balanced"]
    print(
        f"Balanced thresholds - p95: {t['p95']:.5f}  p80: {t['p80']:.5f}  "
        f"p60: {t['p60']:.5f}  p40: {t['p40']:.5f}"
    )
    if stats.get("regional"):
        print("Regional coverage:", "  ".join(f"{k}: {v}" for k, v in stats["regional"].items()))
    if stats.get("html"):
        print(f"Self-contained HTML: {stats['html']} ({stats['html_mb']} MB)")
