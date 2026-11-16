"""Stream connectivity and watershed analysis service.

Builds a networkx DiGraph from OHN watercourse segments stored in SQLite.
Directed edges follow OHN flow direction (start → end = upstream → downstream).
Segments without verified flow direction get bidirectional edges.

Barrier passability per species:
  Falls               → impassable for ALL species
  Rapids              → impassable for small-bodied species (darters, dace, etc.)
  Sea Lamprey Barrier → impassable for lamprey only; all others pass freely
  Rocks               → passable for ALL species
"""

import json
import logging
import math

import networkx as nx

from src.models.hydrology import ConnectivityResult, HydroBarrier, StreamSegment
from src.services.ingest_runs import record_counts
from src.storage.database import get_db

logger = logging.getLogger(__name__)

# Species whose common names suggest strong swimming ability (pass Rapids)
_STRONG_SWIMMERS = frozenset(
    {
        "salmon",
        "trout",
        "bass",
        "pike",
        "walleye",
        "muskie",
        "muskellunge",
        "carp",
        "steelhead",
        "perch",
        "catfish",
        "bullhead",
    }
)

# Species (or keywords) blocked by Sea Lamprey Barriers
_LAMPREY_KEYWORDS = frozenset({"lamprey"})

# Max snap distance when finding nearest graph node to a lat/lon query (degrees)
# ~1 km at 43°N latitude
_MAX_NODE_SNAP_DEG = 0.009


class HydrologyService:
    """Lazy-loading stream connectivity graph backed by local SQLite."""

    def __init__(self, db=None):
        self._db = db or get_db()
        self._graph: nx.DiGraph | None = None
        self._seg_index: dict[int, StreamSegment] = {}  # ogf_id → segment

    def _ensure_graph(self) -> nx.DiGraph:
        if self._graph is not None:
            return self._graph
        self._graph = self._build_graph()
        return self._graph

    def _build_graph(self) -> nx.DiGraph:
        segments = _load_segments(self._db)
        barriers = _load_barriers(self._db)

        if not segments:
            logger.warning("No stream segments in DB — run `make ingest` first")
            return nx.DiGraph()

        self._seg_index = {s.ogf_id: s for s in segments}

        # Barrier lookup: ogf_id of segment → barrier_type
        seg_barrier: dict[int, str] = {}
        for b in barriers:
            if b.nearest_segment_ogf_id is not None:
                seg_barrier[b.nearest_segment_ogf_id] = b.barrier_type

        G: nx.DiGraph = nx.DiGraph()
        for seg in segments:
            edge_data = {
                "ogf_id": seg.ogf_id,
                "name": seg.name,
                "length_m": seg.length_m,
                "barrier_type": seg_barrier.get(seg.ogf_id),
            }
            G.add_edge(seg.start_node, seg.end_node, **edge_data)
            if not seg.flow_verified:
                G.add_edge(seg.end_node, seg.start_node, **edge_data)

        logger.info(
            "Built OHN graph: %d nodes, %d edges, %d barriers assigned",
            G.number_of_nodes(),
            G.number_of_edges(),
            len(seg_barrier),
        )
        return G

    # ── public query API ──────────────────────────────────────────────────────

    def get_graph(self) -> nx.DiGraph:
        """Return the stream network DiGraph, building it if needed."""
        return self._ensure_graph()

    def upstream_of(self, lat: float, lon: float, max_km: float = 20.0) -> list[dict]:
        """BFS upstream from the nearest node. Returns edge dicts sorted by distance."""
        G = self._ensure_graph()
        node = self._nearest_node(G, lat, lon)
        if node is None:
            return []
        return self._bfs(G, node, direction="upstream", max_km=max_km)

    def downstream_of(self, lat: float, lon: float, max_km: float = 20.0) -> list[dict]:
        """BFS downstream from the nearest node. Returns edge dicts sorted by distance."""
        G = self._ensure_graph()
        node = self._nearest_node(G, lat, lon)
        if node is None:
            return []
        return self._bfs(G, node, direction="downstream", max_km=max_km)

    def reachable_from(
        self,
        lat: float,
        lon: float,
        species: str | None = None,
        max_km: float = 20.0,
    ) -> list[dict]:
        """Return all segments reachable from lat/lon, pruning barriers impassable for species.

        Traversal is bidirectional (upstream and downstream) because the question
        is whether connectivity exists at all, not directionality.
        """
        G = self._ensure_graph()
        node = self._nearest_node(G, lat, lon)
        if node is None:
            return []

        visited_nodes: set[str] = set()
        result: list[dict] = []
        queue: list[tuple[str, float]] = [(node, 0.0)]

        while queue:
            current, dist = queue.pop(0)
            if current in visited_nodes:
                continue
            visited_nodes.add(current)

            for nbr, edge_data in list(G[current].items()) + [
                (pred, G[pred][current]) for pred in G.predecessors(current)
            ]:
                bt = edge_data.get("barrier_type")
                if bt and not _can_pass(species, bt):
                    continue
                seg_km = edge_data.get("length_m", 0.0) / 1000.0
                new_dist = dist + seg_km
                if new_dist <= max_km and nbr not in visited_nodes:
                    result.append({**edge_data, "distance_km": round(new_dist, 2)})
                    queue.append((nbr, new_dist))

        return result

    def connected_tributaries(
        self,
        watercourse_name: str,
        species: str | None = None,
    ) -> list[dict]:
        """Find tributaries that join the named watercourse.

        Returns segments that share a junction node with the named stem but
        are not themselves part of the named stem. Optionally filtered by
        species passability.
        """
        G = self._ensure_graph()

        # Collect all nodes touched by named main-stem segments
        stem_nodes: set[str] = set()
        stem_ogf_ids: set[int] = set()
        for u, v, data in G.edges(data=True):
            if data.get("name") and data["name"].lower() == watercourse_name.lower():
                stem_nodes.add(u)
                stem_nodes.add(v)
                stem_ogf_ids.add(data["ogf_id"])

        if not stem_nodes:
            return []

        # Find edges that touch a stem node but are not on the stem
        tributaries: list[dict] = []
        seen: set[int] = set()
        for node in stem_nodes:
            for nbr in list(G.successors(node)) + list(G.predecessors(node)):
                edge_data = G[node][nbr] if G.has_edge(node, nbr) else G[nbr][node]
                ogf_id = edge_data["ogf_id"]
                if ogf_id in stem_ogf_ids or ogf_id in seen:
                    continue
                bt = edge_data.get("barrier_type")
                if bt and species and not _can_pass(species, bt):
                    continue
                seen.add(ogf_id)
                tributaries.append(edge_data)

        return tributaries

    def connectivity_summary(
        self,
        lat: float,
        lon: float,
        species: str,
        confirmed_observations: list[dict],
    ) -> ConnectivityResult:
        """Check whether confirmed observations are connected to lat/lon via the stream network.

        confirmed_observations: list of dicts with keys lat, lng, distance_m, place_guess.
        Returns a ConnectivityResult with a human-readable summary_sentence.
        """
        G = self._ensure_graph()
        query_node = self._nearest_node(G, lat, lon)

        if query_node is None:
            return ConnectivityResult(
                query_lat=lat,
                query_lon=lon,
                species=species,
                connected_observations=[],
                nearest_barrier=None,
                summary_sentence=(
                    "No stream network data loaded for this area. "
                    "Run `make ingest` to load OHN data before querying stream connectivity."
                ),
            )

        connected: list[dict] = []
        nearest_barrier: str | None = None
        nearest_barrier_dist_km: float = float("inf")

        for obs in confirmed_observations:
            obs_node = self._nearest_node(G, obs["lat"], obs["lng"])
            if obs_node is None:
                continue

            try:
                # Use undirected view to check connectivity regardless of flow direction
                path = nx.shortest_path(G.to_undirected(), query_node, obs_node)
            except nx.NetworkXNoPath:
                continue
            except nx.NodeNotFound:
                continue

            # Walk the path and check for barriers
            path_km = 0.0
            blocking_barrier: str | None = None
            for i in range(len(path) - 1):
                u, v = path[i], path[i + 1]
                edge = G[u][v] if G.has_edge(u, v) else G[v][u]
                path_km += edge.get("length_m", 0.0) / 1000.0
                bt = edge.get("barrier_type")
                if bt and not _can_pass(species, bt) and blocking_barrier is None:
                    blocking_barrier = bt

            place = obs.get("place_guess") or "nearby"
            connected.append(
                {
                    "lat": obs["lat"],
                    "lng": obs["lng"],
                    "place_guess": place,
                    "distance_km": round(path_km, 2),
                    "blocking_barrier": blocking_barrier,
                }
            )

            if blocking_barrier and path_km < nearest_barrier_dist_km:
                nearest_barrier = blocking_barrier
                nearest_barrier_dist_km = path_km

        summary = _build_summary(species, connected, nearest_barrier)
        return ConnectivityResult(
            query_lat=lat,
            query_lon=lon,
            species=species,
            connected_observations=connected,
            nearest_barrier=nearest_barrier,
            summary_sentence=summary,
        )

    # ── internal graph helpers ────────────────────────────────────────────────

    def _nearest_node(self, G: nx.DiGraph, lat: float, lon: float) -> str | None:
        min_dist = float("inf")
        nearest: str | None = None
        for node in G.nodes():
            try:
                nlon, nlat = map(float, node.split(","))
            except ValueError:
                continue
            dist = math.sqrt((nlon - lon) ** 2 + (nlat - lat) ** 2)
            if dist < min_dist:
                min_dist = dist
                nearest = node
        if min_dist > _MAX_NODE_SNAP_DEG:
            return None
        return nearest

    def _bfs(
        self,
        G: nx.DiGraph,
        start_node: str,
        direction: str,
        max_km: float,
    ) -> list[dict]:
        """BFS from start_node. 'upstream' follows predecessors, 'downstream' follows successors."""
        visited: set[str] = {start_node}
        queue: list[tuple[str, float]] = [(start_node, 0.0)]
        result: list[dict] = []

        while queue:
            current, dist = queue.pop(0)
            neighbors = (
                G.predecessors(current) if direction == "upstream" else G.successors(current)
            )
            for nbr in neighbors:
                if nbr in visited:
                    continue
                edge = G[nbr][current] if direction == "upstream" else G[current][nbr]
                seg_km = edge.get("length_m", 0.0) / 1000.0
                new_dist = dist + seg_km
                if new_dist <= max_km:
                    visited.add(nbr)
                    result.append({**edge, "distance_km": round(new_dist, 2)})
                    queue.append((nbr, new_dist))

        result.sort(key=lambda x: x["distance_km"])
        return result


# ── agent-facing functions ────────────────────────────────────────────────────

_service_cache: HydrologyService | None = None


def _get_service() -> HydrologyService:
    global _service_cache
    if _service_cache is None:
        _service_cache = HydrologyService()
    return _service_cache


def analyze_watershed_for_agent(
    lat: float,
    lon: float,
    species: str | None = None,
    radius_km: float = 20.0,
) -> str:
    """Return connectivity analysis for lat/lon, optionally for a specific species.

    Queries the local observations DB for confirmed sightings of species near the
    location, then checks stream connectivity from the OHN graph.
    """
    db = get_db()
    svc = _get_service()

    # Query confirmed observations from iNat + GBIF within radius
    confirmed = _nearby_observations(db, lat, lon, radius_km, species)

    if not confirmed:
        species_str = species or "any species"
        return json.dumps(
            {
                "query": {"lat": lat, "lon": lon, "species": species, "radius_km": radius_km},
                "result": "no_observations",
                "note": (
                    f"No confirmed observations of {species_str} within {radius_km}km "
                    "in the local database. Run `make ingest` to populate observation data, "
                    "or try a larger radius."
                ),
            }
        )

    if species:
        result = svc.connectivity_summary(lat, lon, species, confirmed)
        return json.dumps(
            {
                "query": {"lat": lat, "lon": lon, "species": species},
                "summary": result.summary_sentence,
                "connected_observations": result.connected_observations,
                "nearest_barrier": result.nearest_barrier,
            }
        )

    # No species specified: summarise all species found and pick the most common
    species_seen = {}
    for obs in confirmed:
        sp = obs.get("species", "Unknown")
        species_seen[sp] = species_seen.get(sp, 0) + 1

    top_species = sorted(species_seen, key=species_seen.get, reverse=True)[:3]  # type: ignore[arg-type]
    summaries = []
    for sp in top_species:
        sp_obs = [o for o in confirmed if o.get("species") == sp]
        r = svc.connectivity_summary(lat, lon, sp, sp_obs)
        summaries.append({"species": sp, "summary": r.summary_sentence})

    return json.dumps(
        {
            "query": {"lat": lat, "lon": lon, "radius_km": radius_km},
            "species_found": species_seen,
            "connectivity_summaries": summaries,
        }
    )


def find_connected_tributaries_for_agent(
    watercourse_name: str,
    species: str | None = None,
) -> str:
    """List tributaries joining the named watercourse, with species passability filter."""
    svc = _get_service()
    tribs = svc.connected_tributaries(watercourse_name, species)

    if not tribs:
        note = (
            f"No tributaries found for '{watercourse_name}'. "
            "Either the name is not in the OHN database, or no connected segments exist. "
            "Check spelling — OHN uses OFFICIAL_NAME_LABEL (e.g. 'Bronte Creek')."
        )
        return json.dumps({"watercourse": watercourse_name, "tributaries": [], "note": note})

    unique: dict[int, dict] = {}
    for t in tribs:
        ogf_id = t["ogf_id"]
        if ogf_id not in unique:
            unique[ogf_id] = {
                "name": t.get("name") or "(unnamed)",
                "length_m": t.get("length_m"),
                "barrier_type": t.get("barrier_type"),
            }

    named = [v for v in unique.values() if v["name"] != "(unnamed)"]
    unnamed_count = len(unique) - len(named)

    return json.dumps(
        {
            "watercourse": watercourse_name,
            "species_filter": species,
            "total_joining_segments": len(unique),
            "named_tributaries": named,
            "unnamed_segment_count": unnamed_count,
            "note": (
                "Segments are from the OHN Watercourse layer. Named tributaries are those "
                "with an OFFICIAL_NAME_LABEL. Barriers shown are on the joining segment itself."
            ),
        }
    )


def ingest_hydro_network(
    lat: float,
    lon: float,
    radius_km: float = 300.0,
) -> tuple[int, int]:
    """Fetch and store OHN watercourse segments and barriers. Returns (seg_count, barrier_count)."""
    from datetime import datetime

    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_barriers, fetch_watercourses

    db = get_db()

    logger.info("Fetching OHN watercourse segments (%.0fkm bbox)…", radius_km)
    segments = fetch_watercourses(lat, lon, radius_km)
    record_counts(fetched=1, empty=int(not segments))

    logger.info("Fetching OHN barrier points (%.0fkm bbox)…", radius_km)
    barriers = fetch_barriers(lat, lon, radius_km, segments=segments)
    record_counts(fetched=1, empty=int(not barriers))

    now = datetime.utcnow().isoformat()

    # Delete only CA-ON rows so other jurisdictions' data is preserved
    if "stream_segments" in db.table_names():
        db["stream_segments"].delete_where("jurisdiction = ?", ["CA-ON"])
    if "barriers" in db.table_names():
        db["barriers"].delete_where("jurisdiction = ?", ["CA-ON"])

    seg_rows = [
        {
            "ogf_id": s.ogf_id,
            "watercourse_type": s.watercourse_type,
            "name": s.name,
            "flow_verified": int(s.flow_verified),
            "permanency": s.permanency,
            "flow_classification": s.flow_classification,
            "stream_order": s.stream_order,
            "length_m": s.length_m,
            "geom_wkt": s.geom_wkt,
            "start_node": s.start_node,
            "end_node": s.end_node,
            "jurisdiction": s.jurisdiction,
            "segment_source": s.segment_source,
            "ingested_at": now,
        }
        for s in segments
    ]
    if seg_rows:
        db["stream_segments"].insert_all(seg_rows, pk="ogf_id", replace=True)

    barrier_rows = [
        {
            "ogf_id": b.ogf_id,
            "barrier_type": b.barrier_type,
            "geom_wkt": b.geom_wkt,
            "nearest_segment_ogf_id": b.nearest_segment_ogf_id,
            "snap_distance_m": b.snap_distance_m,
            "jurisdiction": b.jurisdiction,
            "ingested_at": now,
        }
        for b in barriers
    ]
    if barrier_rows:
        db["barriers"].insert_all(barrier_rows, pk="ogf_id", replace=True)

    # Invalidate the service cache so the next query rebuilds the graph
    global _service_cache
    _service_cache = None

    logger.info("OHN ingest done: %d segments, %d barriers", len(segments), len(barriers))
    record_counts(stored=len(segments) + len(barriers))
    return len(segments), len(barriers)


# ── DB helpers ────────────────────────────────────────────────────────────────


def _load_segments(db) -> list[StreamSegment]:
    if "stream_segments" not in db.table_names():
        return []
    rows = list(db["stream_segments"].rows)
    segments = []
    for row in rows:
        try:
            segments.append(
                StreamSegment(
                    ogf_id=row["ogf_id"],
                    watercourse_type=row["watercourse_type"] or "Stream",
                    name=row["name"],
                    flow_verified=bool(row["flow_verified"]),
                    permanency=row["permanency"] or "Permanent",
                    flow_classification=row["flow_classification"],
                    stream_order=row.get("stream_order"),
                    length_m=row["length_m"] or 0.0,
                    geom_wkt=row["geom_wkt"],
                    start_node=row["start_node"],
                    end_node=row["end_node"],
                    jurisdiction=row["jurisdiction"] or "CA-ON",
                    segment_source=row.get("segment_source") or "OHN",
                )
            )
        except Exception as exc:
            logger.debug("Skipping DB segment ogf_id=%s: %s", row.get("ogf_id"), exc)
    return segments


def _load_barriers(db) -> list[HydroBarrier]:
    if "barriers" not in db.table_names():
        return []
    rows = list(db["barriers"].rows)
    barriers = []
    for row in rows:
        try:
            barriers.append(
                HydroBarrier(
                    ogf_id=row["ogf_id"],
                    barrier_type=row["barrier_type"],
                    geom_wkt=row["geom_wkt"],
                    nearest_segment_ogf_id=row["nearest_segment_ogf_id"],
                    snap_distance_m=row["snap_distance_m"],
                    jurisdiction=row["jurisdiction"] or "CA-ON",
                )
            )
        except Exception as exc:
            logger.debug("Skipping DB barrier ogf_id=%s: %s", row.get("ogf_id"), exc)
    return barriers


def _nearby_observations(
    db,
    lat: float,
    lon: float,
    radius_km: float,
    species: str | None,
) -> list[dict]:
    """Query iNat + GBIF observations near a point. Returns list of dicts."""
    results: list[dict] = []

    deg = radius_km / 111.0
    lat_min, lat_max = lat - deg, lat + deg
    lon_min, lon_max = lon - deg, lon + deg

    species_clause = ""
    args: list = [lat_min, lat_max, lon_min, lon_max]
    if species:
        species_clause = " AND LOWER(species) LIKE ?"
        args.append(f"%{species.lower()}%")

    if "observations" in db.table_names():
        sql = (
            f"SELECT lat, lng, species, common_name, place_guess FROM observations "
            f"WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?{species_clause}"
        )
        for row in db.execute(sql, args).fetchall():
            results.append(
                {
                    "lat": row[0],
                    "lng": row[1],
                    "species": row[2],
                    "common_name": row[3],
                    "place_guess": row[4],
                }
            )

    if "gbif_observations" in db.table_names():
        sql = (
            f"SELECT lat, lng, species, common_name, NULL FROM gbif_observations "
            f"WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?{species_clause}"
        )
        for row in db.execute(sql, args).fetchall():
            results.append(
                {
                    "lat": row[0],
                    "lng": row[1],
                    "species": row[2],
                    "common_name": row[3],
                    "place_guess": None,
                }
            )

    return results


# ── barrier passability ───────────────────────────────────────────────────────


def _can_pass(species: str | None, barrier_type: str) -> bool:
    """Return True if species can pass the barrier."""
    if barrier_type == "Falls":
        return False
    if barrier_type == "Rocks":
        return True
    if barrier_type == "Sea Lamprey Barrier":
        if species is None:
            return True
        return not any(kw in species.lower() for kw in _LAMPREY_KEYWORDS)
    if barrier_type == "Rapids":
        if species is None:
            return True
        return any(sw in species.lower() for sw in _STRONG_SWIMMERS)
    return True  # unknown barrier type: assume passable


def _build_summary(
    species: str,
    connected: list[dict],
    nearest_barrier: str | None,
) -> str:
    if not connected:
        return (
            f"No confirmed {species} observations found on connected stream reaches "
            "in the local database. This doesn't imply absence — it may reflect low "
            "observer effort or incomplete data coverage."
        )

    passable = [c for c in connected if c.get("blocking_barrier") is None]
    blocked = [c for c in connected if c.get("blocking_barrier") is not None]

    parts: list[str] = []

    if passable:
        closest = min(passable, key=lambda x: x["distance_km"])
        place = closest["place_guess"] or "a nearby reach"
        km = closest["distance_km"]
        parts.append(
            f"{species.title()} confirmed {km:.1f}km away near {place} — "
            f"no barriers detected on the connecting reach. "
            f"Stream connectivity is intact between that record and this location."
        )

    if blocked:
        closest_blocked = min(blocked, key=lambda x: x["distance_km"])
        bt = closest_blocked["blocking_barrier"]
        place = closest_blocked["place_guess"] or "a nearby reach"
        km = closest_blocked["distance_km"]
        barrier_note = {
            "Falls": "a waterfall that is impassable for this species",
            "Rapids": "rapids that may limit upstream movement for small-bodied fish",
            "Sea Lamprey Barrier": "a sea lamprey barrier (passable for most species)",
        }.get(bt, f"a {bt} barrier")
        parts.append(
            f"{species.title()} also recorded {km:.1f}km away near {place}, "
            f"but {barrier_note} lies on the intervening reach."
        )

    return " ".join(parts)
