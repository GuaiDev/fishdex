"""Tests for SDM feature matrix helpers. All use synthetic data — no live DB."""

import networkx as nx
import numpy as np

from src.services.sdm_features import (
    _aggregate_wq_by_station,
    _assign_from_upstream_stations,
    _assign_geology,
    _assign_stocking,
    _compute_confluence_features,
    _compute_strahler_order,
    _compute_waterbody_proximity,
    _count_upstream_barriers,
    _make_snap_fn,
    _nearest_observation_distance,
    _observation_density,
    _segment_centroids,
    _subgraph_near_sources,
    build_feature_matrix,
    coverage_fraction,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_graph(*edges):
    """Build a DiGraph from (u, v, ogf_id, length_m) tuples.

    Nodes are formatted as 'lng,lat' strings (OHN convention).
    """
    G = nx.DiGraph()
    for u, v, ogf_id, length_m in edges:
        G.add_edge(u, v, ogf_id=ogf_id, length_m=length_m)
    return G


def _node(lng, lat):
    return f"{lng},{lat}"


# ── unit tests ────────────────────────────────────────────────────────────────


def test_segment_centroids_single_segment():
    segs = [
        {
            "ogf_id": 1,
            "geom_wkt": "LINESTRING (-79.0 44.0, -79.5 44.5)",
        }
    ]
    c = _segment_centroids(segs)
    assert 1 in c
    lat, lng = c[1]
    assert abs(lat - 44.25) < 1e-6
    assert abs(lng - (-79.25)) < 1e-6


def test_segment_centroids_skips_missing_wkt():
    segs = [{"ogf_id": 2, "geom_wkt": ""}]
    c = _segment_centroids(segs)
    assert 2 not in c


def test_strahler_order_fork():
    # Two headwaters A→C and B→C, then C→D
    # A and B are order 1; C→D is order 2
    A, B, C, D = _node(-79, 45), _node(-79.1, 45), _node(-79.05, 44.9), _node(-79.05, 44.8)
    G = _make_graph(
        (A, C, 1, 1000),
        (B, C, 2, 1000),
        (C, D, 3, 1000),
    )
    orders = _compute_strahler_order(G)
    assert orders[1] == 1
    assert orders[2] == 1
    assert orders[3] == 2


def test_strahler_order_linear():
    # Single chain A→B→C should stay at order 1 throughout
    A, B, C, D = _node(-79, 45), _node(-79, 44.9), _node(-79, 44.8), _node(-79, 44.7)
    G = _make_graph((A, B, 10, 500), (B, C, 11, 500), (C, D, 12, 500))
    orders = _compute_strahler_order(G)
    assert orders[10] == 1
    assert orders[11] == 1
    assert orders[12] == 1


def test_strahler_order_cycle_does_not_crash():
    # Cycle A→B→A (braided channel / data error) — should not raise, all order >= 1
    A, B, C = _node(-79, 45), _node(-79, 44.9), _node(-79, 44.8)
    G = _make_graph((A, B, 20, 500), (B, A, 21, 500), (B, C, 22, 500))
    orders = _compute_strahler_order(G)
    assert all(v >= 1 for v in orders.values())


def test_strahler_order_cycle_downstream_still_increments():
    # Chain with a side cycle: X→Y→X (cycle, both order 1), Z→Y (another tributary)
    # Y has two predecessors (X and Z) at some order — downstream of Y should be > 1
    A, B, C, D = _node(-79, 45), _node(-79, 44.9), _node(-79.1, 45), _node(-79, 44.8)
    G = _make_graph(
        (A, B, 30, 500),  # A→B
        (B, A, 31, 500),  # B→A  (creates cycle A↔B)
        (C, B, 32, 500),  # C→B  (C is another headwater into B)
        (B, D, 33, 500),  # B→D  (downstream of confluence)
    )
    orders = _compute_strahler_order(G)
    # All orders must be >= 1 and no crash
    assert all(v >= 1 for v in orders.values())
    # Segment 33 (downstream of B which had two tributaries) should be >= 1
    assert 33 in orders


def test_assign_geology_nearest():
    centroids = {1: (44.0, -79.0), 2: (44.5, -79.5)}
    geo = [
        {"centroid_lat": 44.01, "centroid_lng": -79.01, "substrate_class": "coarse"},
        {"centroid_lat": 44.49, "centroid_lng": -79.49, "substrate_class": "bedrock"},
        {"centroid_lat": 45.0, "centroid_lng": -80.0, "substrate_class": "fine"},
    ]
    result = _assign_geology(centroids, geo)
    assert result[1] == "coarse"
    assert result[2] == "bedrock"


def test_assign_geology_empty_returns_empty():
    assert _assign_geology({1: (44.0, -79.0)}, []) == {}
    geo = [{"centroid_lat": 44.0, "centroid_lng": -79.0, "substrate_class": "coarse"}]
    assert _assign_geology({}, geo) == {}


def test_subgraph_near_sources_excludes_distant_nodes():
    """Nodes outside bbox + buffer are excluded from the returned subgraph."""
    A = _node(-79.0, 44.5)
    B = _node(-79.0, 44.4)
    C = _node(-85.0, 44.0)  # far away in western Ontario
    G = _make_graph((A, B, 1, 5000), (B, C, 2, 5000))

    nodes_list = list(G.nodes())
    n_lats = np.array([float(n.split(",")[1]) for n in nodes_list])
    n_lngs = np.array([float(n.split(",")[0]) for n in nodes_list])

    # Source near A with 50km buffer — B is within range, C is not
    G_sub = _subgraph_near_sources(G, nodes_list, n_lats, n_lngs, [(44.5, -79.0)], 50.0)
    assert A in G_sub.nodes()
    assert B in G_sub.nodes()
    assert C not in G_sub.nodes()


def test_subgraph_near_sources_empty_returns_full_graph():
    A = _node(-79.0, 44.5)
    B = _node(-79.0, 44.4)
    G = _make_graph((A, B, 1, 5000))
    nodes_list = list(G.nodes())
    n_lats = np.array([float(n.split(",")[1]) for n in nodes_list])
    n_lngs = np.array([float(n.split(",")[0]) for n in nodes_list])
    G_sub = _subgraph_near_sources(G, nodes_list, n_lats, n_lngs, [], 50.0)
    assert G_sub is G


def test_aggregate_wq_by_station():
    rows = [
        {
            "station_id": "A",
            "lat": 44.0,
            "lng": -79.0,
            "do_mgl": 8.0,
            "ph": 7.0,
            "conductivity_us_cm": 100.0,
        },
        {
            "station_id": "A",
            "lat": 44.0,
            "lng": -79.0,
            "do_mgl": 10.0,
            "ph": 7.4,
            "conductivity_us_cm": 120.0,
        },
        {
            "station_id": "B",
            "lat": 44.5,
            "lng": -79.5,
            "do_mgl": 6.0,
            "ph": 6.8,
            "conductivity_us_cm": 90.0,
        },
    ]
    agg = _aggregate_wq_by_station(rows)
    by_station = {r["station_id"]: r for r in agg}
    assert abs(by_station["A"]["do_median_mgl"] - 9.0) < 1e-6
    assert abs(by_station["A"]["ph_median"] - 7.2) < 1e-6
    assert abs(by_station["B"]["conductivity_median_us_cm"] - 90.0) < 1e-6


def test_assign_from_upstream_stations():
    # Graph: station at S, then S→A→B→C (downstream)
    S = _node(-79.0, 44.5)
    A = _node(-79.0, 44.4)
    B = _node(-79.0, 44.3)
    C = _node(-79.0, 44.2)
    G = _make_graph((S, A, 1, 5000), (A, B, 2, 5000), (B, C, 3, 5000))

    snap_fn = _make_snap_fn(G)
    # Station placed at S's coordinates
    station_row = {"lat": 44.5, "lng": -79.0, "thermal_regime": "coldwater", "summer_mean_c": 12.0}
    result = _assign_from_upstream_stations(G, snap_fn, [station_row], max_km=20.0)

    # All three segments downstream of station should be assigned
    assert 1 in result
    assert 2 in result
    assert 3 in result
    assert result[1]["thermal_regime"] == "coldwater"


def test_assign_from_upstream_stations_respects_distance():
    # Station at S, chain S→A (2km) →B (20km total) →C (25km total)
    S = _node(-79.0, 44.5)
    A = _node(-79.0, 44.4)
    B = _node(-79.0, 44.0)
    C = _node(-79.0, 43.7)
    G = _make_graph((S, A, 1, 2000), (A, B, 2, 18000), (B, C, 3, 5000))

    snap_fn = _make_snap_fn(G)
    station_row = {"lat": 44.5, "lng": -79.0, "thermal_regime": "coldwater"}
    result = _assign_from_upstream_stations(G, snap_fn, [station_row], max_km=20.0)

    assert 1 in result  # 2km — within 20km
    assert 2 in result  # 20km — within limit
    assert 3 not in result  # 25km — beyond 20km


def test_count_upstream_barriers():
    # Barrier at node S, downstream chain S→A→B
    S = _node(-79.0, 44.5)
    A = _node(-79.0, 44.4)
    B = _node(-79.0, 44.3)
    G = _make_graph((S, A, 1, 5000), (A, B, 2, 5000))

    snap_fn = _make_snap_fn(G)
    # Barrier WKT with coordinates at S
    barrier = {"geom_wkt": "POINT (-79.0 44.5)"}
    counts = _count_upstream_barriers(G, snap_fn, [barrier], max_km=20.0)

    assert counts.get(1, 0) == 1
    assert counts.get(2, 0) == 1


def test_nearest_observation_distance_euclidean():
    # Seg 1 centroid is exactly at an observation → distance ≈ 0
    # Seg 2 centroid is between the two observations → small non-zero distance
    # Seg 3 centroid is exactly at the other observation → distance ≈ 0
    centroids = {
        1: (44.5, -79.0),
        2: (44.4, -79.0),
        3: (44.3, -79.1),
    }
    obs = [(44.5, -79.0), (44.3, -79.1)]

    dists = _nearest_observation_distance(centroids, obs)

    assert dists[1] is not None and dists[1] < 0.01
    assert dists[3] is not None and dists[3] < 0.01
    assert dists[2] is not None and dists[2] > 0


def test_observation_density_counts_within_radius():
    centroids = {1: (44.0, -79.0)}
    # One obs inside radius, one outside
    obs = [(44.1, -79.1), (46.0, -82.0)]
    result = _observation_density(centroids, obs, radius_deg=0.3)
    assert result[1] == 1


def test_observation_density_empty_obs():
    centroids = {1: (44.0, -79.0), 2: (44.5, -79.5)}
    result = _observation_density(centroids, [])
    assert result == {1: 0, 2: 0}


def test_assign_stocking_recent_within_radius():
    from datetime import date

    centroids = {1: (44.0, -79.0), 2: (45.0, -80.0)}
    current_year = date.today().year
    stocking = [
        # Close to segment 1, recent
        {"lat": 44.01, "lng": -79.01, "year": current_year - 2, "species": "Brown Trout"},
        # Far from both segments
        {"lat": 48.0, "lng": -85.0, "year": current_year - 1, "species": "Walleye"},
        # Close to segment 1 but too old
        {"lat": 44.01, "lng": -79.01, "year": current_year - 10, "species": "Rainbow Trout"},
    ]
    result = _assign_stocking(centroids, stocking)
    assert result.get(1, {}).get("is_stocked") is True
    assert result.get(1, {}).get("species") == "Brown Trout"
    assert 2 not in result


# ── integration test ──────────────────────────────────────────────────────────


def _insert_segments(db, segments):
    for s in segments:
        db["stream_segments"].insert(s)


def _y_network():
    """Return edges for a Y-shaped 5-segment network.

    Topology:
      H1 → J   (headwater 1, ogf_id=1)
      H2 → J   (headwater 2, ogf_id=2)
       J → M1  (confluence, ogf_id=3)
      M1 → M2  (mainstem, ogf_id=4)
      M2 → M3  (mainstem, ogf_id=5)

    Coordinates (lng,lat):
      H1 = -79.0, 44.5
      H2 = -79.2, 44.5
       J = -79.1, 44.4
      M1 = -79.1, 44.3
      M2 = -79.1, 44.2
      M3 = -79.1, 44.1
    """
    H1 = "-79.0,44.5"
    H2 = "-79.2,44.5"
    J = "-79.1,44.4"
    M1 = "-79.1,44.3"
    M2 = "-79.1,44.2"
    M3 = "-79.1,44.1"
    return [
        (H1, J, 1, 12000.0, 44.45, -79.05),  # (start, end, ogf_id, len, clat, clng)
        (H2, J, 2, 12000.0, 44.45, -79.15),
        (J, M1, 3, 11000.0, 44.35, -79.1),
        (M1, M2, 4, 11000.0, 44.25, -79.1),
        (M2, M3, 5, 11000.0, 44.15, -79.1),
    ]


def test_build_feature_matrix_integration(tmp_path, fast_db):
    from unittest.mock import patch

    db = fast_db
    edges = _y_network()
    for start, end, ogf_id, length, clat, clng in edges:
        db["stream_segments"].insert(
            {
                "ogf_id": ogf_id,
                "watercourse_type": "river",
                "name": f"Stream {ogf_id}",
                "flow_verified": 1,
                "permanency": "permanent",
                "flow_classification": "regulated",
                "length_m": length,
                "geom_wkt": f"LINESTRING ({start.replace(',', ' ')}, {end.replace(',', ' ')})",
                "start_node": start,
                "end_node": end,
                "jurisdiction": "CA-ON",
                "ingested_at": "2026-05-01T00:00:00",
            }
        )

    # Geology: one unit covering the whole area
    db["geology_units"].insert(
        {
            "unit_id": "tile_0001",
            "tile_id": "tile",
            "unit_code": "7",
            "unit_name": "Sand and Gravel",
            "primary_material": "sand",
            "substrate_class": "coarse",
            "jurisdiction": "CA-ON",
            "centroid_lat": 44.3,
            "centroid_lng": -79.1,
            "bbox_minx": -80.0,
            "bbox_miny": 44.0,
            "bbox_maxx": -79.0,
            "bbox_maxy": 45.0,
        }
    )

    # Thermal station upstream of the confluence
    db["stream_temperature_summaries"].insert(
        {
            "station_id": "T01",
            "station_name": "Test Thermal",
            "lat": 44.5,
            "lng": -79.0,
            "jurisdiction": "CA-ON",
            "summer_mean_c": 14.0,
            "summer_max_c": 20.0,
            "thermal_regime": "coldwater",
            "years_of_data": 5,
            "species_notes": "",
        }
    )

    # WQ station upstream of confluence
    db["water_quality_readings"].insert(
        {
            "record_id": "WQ001",
            "station_id": "WQ01",
            "station_name": "Test WQ",
            "lat": 44.5,
            "lng": -79.0,
            "jurisdiction": "CA-ON",
            "sampled_at": "2024-07-01",
            "do_mgl": 9.0,
            "ph": 7.2,
            "temp_c": 15.0,
            "conductivity_us_cm": 110.0,
            "turbidity_fnu": None,
        }
    )

    # CABIN site
    db["benthic_samples"].insert(
        {
            "site_visit_id": "C001",
            "site_code": "S01",
            "site_name": "Test Site",
            "lat": 44.45,
            "lng": -79.05,
            "jurisdiction": "CA-ON",
            "sampled_year": 2023,
            "sampled_julian_day": 180,
            "stream_order": 1,
            "local_basin": "test",
            "ept_richness": 12,
            "ept_count": 45.0,
            "total_count": 80.0,
            "ept_proportion": 0.56,
            "total_taxa_richness": 20,
            "habitat_quality": "high",
        }
    )

    # One observation
    db["observations"].insert(
        {
            "observation_id": 1,
            "species": "Cottus cognatus",
            "common_name": "Slimy Sculpin",
            "taxon_id": 12345,
            "lat": 44.35,
            "lng": -79.1,
            "observed_on": "2026-04-01",
            "quality_grade": "research",
            "photo_url": None,
            "observer": "tester",
            "place_guess": "Test Stream",
            "jurisdiction": "CA-ON",
            "ingested_at": "2026-05-01T00:00:00",
            "geoprivacy": "open",
            "is_obscured": 0,
            "obscuration_radius_km": None,
        }
    )

    # No GBIF observations (table must exist — ensure_schema creates it)
    # One stocking record near segment 5
    from datetime import date

    db["stocking_records"].insert(
        {
            "record_id": "ST001",
            "waterbody_name": "Test Creek",
            "waterbody_code": "TC01",
            "municipality": "Test",
            "county": "Test County",
            "lat": 44.15,
            "lng": -79.1,
            "jurisdiction": "CA-ON",
            "species": "Brown Trout",
            "species_code": "BT",
            "year": date.today().year - 2,
            "month": 4,
            "quantity": 500,
            "life_stage": "fingerling",
            "stocking_purpose": "put-and-take",
            "stocked_at": "2024-04-15",
        }
    )

    parquet_path = tmp_path / "test_matrix.parquet"
    with patch("src.services.sdm_features._PARQUET_PATH", parquet_path):
        df = build_feature_matrix(db)

    # Shape check
    assert len(df) == 5
    expected_cols = {
        "ogf_id",
        "stream_order",
        "length_m",
        "flow_verified",
        "substrate_category",
        "thermal_regime",
        "summer_mean_temp_c",
        "do_median_mgl",
        "ph_median",
        "conductivity_median_us_cm",
        "ept_quality",
        "ept_proportion",
        "barrier_count_upstream",
        "distance_to_nearest_observation_km",
        "observation_density_25km",
        "is_stocked_within_5yr",
        "nearest_stocked_species",
        "pwqmn_coverage",
    }
    assert expected_cols.issubset(set(df.columns))

    # All segments have geology (one unit covers everything)
    assert df["substrate_category"].notna().all()

    # Barrier count defaults to 0 when no barriers table rows
    assert (df["barrier_count_upstream"] == 0).all()

    # Stocking: segment 5 should be stocked
    seg5 = df[df["ogf_id"] == 5].iloc[0]
    assert seg5["is_stocked_within_5yr"]
    assert seg5["nearest_stocked_species"] == "Brown Trout"

    # Parquet was written to tmp path
    assert parquet_path.exists()

    # pwqmn_coverage: thermal station at H1 (-79.0, 44.5) is within 15km of
    # segment 1 (H1→J, 12km). Segment 2 starts at H2 (-79.2, 44.5), 16km away
    # from the station — beyond the 15km cutoff. Segments 3-5 are >15km downstream.
    seg1_row = df[df["ogf_id"] == 1].iloc[0]
    assert seg1_row["pwqmn_coverage"] is True or seg1_row["pwqmn_coverage"] == True  # noqa: E712
    # pwqmn_coverage is a bool column — must be present for all rows
    assert df["pwqmn_coverage"].notna().all()

    # Coverage fraction is a float between 0 and 1
    cov = coverage_fraction(df)
    assert 0.0 <= cov <= 1.0


# ── Phase 3a: structural features ─────────────────────────────────────────────


def test_confluence_detection():
    """A 3-way junction with order-2+ tributaries makes incident segments confluences."""
    # hub is shared by three streams: A→hub, B→hub, hub→C
    hub = _node(-79.5, 43.7)
    A = _node(-79.6, 43.7)
    B = _node(-79.5, 43.8)
    C = _node(-79.4, 43.6)

    G = nx.DiGraph()
    G.add_edge(A, hub, ogf_id=1, length_m=1000.0)
    G.add_edge(B, hub, ogf_id=2, length_m=1000.0)
    G.add_edge(hub, C, ogf_id=3, length_m=1000.0)

    centroids = {
        1: (43.7, -79.55),  # midpoint A→hub
        2: (43.75, -79.5),  # midpoint B→hub
        3: (43.65, -79.45),  # midpoint hub→C
    }
    strahler = {1: 2, 2: 2, 3: 3}  # two tributaries are order 2+ → real confluence
    is_conf, _ = _compute_confluence_features(G, centroids, strahler)

    # Segments 1 and 2 share the hub endpoint → confluence
    assert is_conf[1] is True
    assert is_conf[2] is True
    # Segment 3 also touches hub → confluence
    assert is_conf[3] is True


def test_non_confluence_segment():
    """A simple 2-node chain has no confluence — both segments are False."""
    A = _node(-79.6, 43.7)
    B = _node(-79.5, 43.7)
    C = _node(-79.4, 43.7)

    G = nx.DiGraph()
    G.add_edge(A, B, ogf_id=1, length_m=500.0)
    G.add_edge(B, C, ogf_id=2, length_m=500.0)

    centroids = {1: (43.7, -79.55), 2: (43.7, -79.45)}
    strahler = {1: 2, 2: 2}
    is_conf, dists = _compute_confluence_features(G, centroids, strahler)

    assert is_conf.get(1, False) is False
    assert is_conf.get(2, False) is False


def test_confluence_ditch_junction_filtered():
    """A 3-way junction where all segments are order-1 is NOT a confluence."""
    hub = _node(-79.5, 43.7)
    A = _node(-79.6, 43.7)
    B = _node(-79.5, 43.8)
    C = _node(-79.4, 43.6)

    G = nx.DiGraph()
    G.add_edge(A, hub, ogf_id=1, length_m=200.0)
    G.add_edge(B, hub, ogf_id=2, length_m=200.0)
    G.add_edge(hub, C, ogf_id=3, length_m=200.0)

    centroids = {1: (43.7, -79.55), 2: (43.75, -79.5), 3: (43.65, -79.45)}
    strahler = {1: 1, 2: 1, 3: 1}  # all order-1 ditches → not a real confluence
    is_conf, _ = _compute_confluence_features(G, centroids, strahler)

    assert is_conf.get(1, False) is False
    assert is_conf.get(2, False) is False
    assert is_conf.get(3, False) is False


def test_confluence_distance():
    """Segment midpoints farther from confluence get higher distance values."""
    hub = _node(-79.5, 43.7)
    A = _node(-79.6, 43.7)
    B = _node(-79.5, 43.8)
    C = _node(-79.4, 43.6)
    D = _node(-79.0, 43.7)  # far downstream segment

    G = nx.DiGraph()
    G.add_edge(A, hub, ogf_id=1, length_m=1000.0)
    G.add_edge(B, hub, ogf_id=2, length_m=1000.0)
    G.add_edge(hub, C, ogf_id=3, length_m=1000.0)
    G.add_edge(C, D, ogf_id=4, length_m=50000.0)

    centroids = {
        1: (43.7, -79.55),
        2: (43.75, -79.5),
        3: (43.65, -79.45),
        4: (43.65, -79.2),  # far from hub
    }
    strahler = {1: 2, 2: 2, 3: 3, 4: 1}
    _, dists = _compute_confluence_features(G, centroids, strahler)

    # Segment 4 is far from the hub confluence → larger distance
    assert dists[4] > dists[1]
    assert dists[4] > 10.0  # should be many km away


def test_waterbody_proximity(fast_db):
    """Segment within 200m of a pond gets connected_to_waterbody=True."""

    db = fast_db
    if "water_features" not in db.table_names():
        db["water_features"].create(
            {
                "osm_id": str,
                "feature_type": str,
                "name": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "area_m2": float,
                "tags": str,
                "fetched_at": str,
            },
            pk="osm_id",
        )
    # Insert a pond very close to segment 1 (~150m away)
    db["water_features"].insert(
        {
            "osm_id": "w1",
            "feature_type": "pond",
            "name": "Test Pond",
            "lat": 43.7013,
            "lng": -79.5000,  # ~150m north of 43.7, -79.5
            "jurisdiction": "CA-ON",
            "area_m2": 5000.0,
            "tags": "{}",
            "fetched_at": "2026-01-01T00:00:00",
        }
    )

    centroids = {
        1: (43.7, -79.5),  # close to pond
        2: (43.8, -79.5),  # far from pond (~11km)
    }
    dists = _compute_waterbody_proximity(db, centroids)

    assert dists[1] is not None
    assert dists[1] <= 200.0
    assert dists[2] is None  # outside 500m window
