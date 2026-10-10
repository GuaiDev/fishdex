"""Stretch geometry from the OHN stream network — tracing and clustering.

Pure functions over a StreamNetwork built from StreamSegment records. Nothing
here touches the database or the network; services/stretches.py does that.

Two jobs:

- **Tracing** turns a curated reach ("Dunnville dam" to "Port Maitland mouth")
  into the OHN segments between those points. Each point snaps to the
  *largest* channel within the snap radius, not the nearest line: a boundary
  point dropped on the Grand is closer to some ditch half the time.
- **Clustering** proposes candidate stretches with no human input. Segments
  draining at least `min_upstream_km` of channel are "major"; the major
  network is cut at every confluence of two major branches and into pieces
  no longer than `max_km`. Each piece is a candidate. Candidates are checked
  against the curation file so a river the map is missing shows up by name.

Size is upstream channel length, accumulated down the flow-direction graph.
It is not Strahler order — the `stream_order` already on the feature matrix
came from a network the LIO server had silently thinned, and order needs
every headwater to be right.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import networkx as nx
from shapely import wkt
from shapely.geometry import LineString, MultiLineString, Point, mapping
from shapely.ops import linemerge
from shapely.strtree import STRtree

from src.models.hydrology import StreamSegment
from src.models.stretch import BoundaryPoint, CandidateStretch, CuratedReach

_M_PER_DEG_LAT = 111_000.0
# Snap slack: channels this much farther than the nearest still compete on size.
SNAP_PREFER_M = 150.0
# OHN carries French official names as a parenthetical: "Credit River (rivière Credit)"
_BILINGUAL_SUFFIX = re.compile(r"\s*\((rivière|riviere|ruisseau|crique|lac)\b[^)]*\)", re.I)


def display_name(name: str | None) -> str | None:
    """Strip the bilingual parenthetical from an OHN official name."""
    if not name:
        return None
    return _BILINGUAL_SUFFIX.sub("", name).strip() or None


def _metres(p: Point, q: Point) -> float:
    """Equirectangular distance — accurate to well under 1% at reach scale."""
    mean_lat = math.radians((p.y + q.y) / 2)
    dx = (q.x - p.x) * _M_PER_DEG_LAT * math.cos(mean_lat)
    dy = (q.y - p.y) * _M_PER_DEG_LAT
    return math.hypot(dx, dy)


def _node_point(node: str) -> Point:
    lon, lat = node.split(",")
    return Point(float(lon), float(lat))


@dataclass
class Snap:
    ogf_id: int
    distance_m: float


@dataclass
class ReachTrace:
    """A traced reach: ordered segment ids, or the reason there are none."""

    ogf_ids: list[int] = field(default_factory=list)
    boundary_ids: list[int] = field(default_factory=list)  # where the waypoints landed
    length_m: float = 0.0
    straight_m: float = 0.0  # summed over legs, so a via point sets the expected course
    failure: str | None = None  # an IssueKind when the trace failed
    detail: str = ""


class StreamNetwork:
    """The OHN network as a graph: nodes are segment endpoints, edges segments."""

    def __init__(self, segments: list[StreamSegment]):
        self.segments: dict[int, StreamSegment] = {s.ogf_id: s for s in segments}
        self.geoms: dict[int, LineString] = {}
        self.graph = nx.MultiGraph()
        for seg in self.segments.values():
            geom = wkt.loads(seg.geom_wkt)
            if geom.geom_type != "LineString":
                geom = LineString([_node_point(seg.start_node), _node_point(seg.end_node)])
            self.geoms[seg.ogf_id] = geom
            self.graph.add_edge(seg.start_node, seg.end_node, key=seg.ogf_id, length=seg.length_m)
        self._ids = list(self.geoms)
        self._tree = STRtree([self.geoms[i] for i in self._ids])
        self.upstream_km = _accumulate_upstream_km(self.segments.values())

    def __len__(self) -> int:
        return len(self.segments)

    # ── snapping ──────────────────────────────────────────────────────────────

    def snap(self, point: BoundaryPoint, max_m: float) -> Snap | None:
        """The largest channel close to the point, or None if nothing is within max_m.

        "Close" is within SNAP_PREFER_M of the nearest channel. Pure nearest
        lands a point dropped on the Grand on whatever ditch is a few metres
        closer; pure largest-within-max_m lands a point near a river mouth on
        the Great Lakes flow line 300 m offshore, which drains 60,000 km of
        channel. The slack prefers the river to the ditch beside it without
        reaching past the river to the lake.
        """
        p = Point(point.lon, point.lat)
        # Degrees of longitude shrink with latitude; size the search box for it.
        reach_deg = max_m / (_M_PER_DEG_LAT * max(math.cos(math.radians(point.lat)), 0.1))
        near: list[tuple[float, int]] = []
        for idx in self._tree.query(p.buffer(reach_deg)):
            ogf_id = self._ids[int(idx)]
            geom = self.geoms[ogf_id]
            d = _metres(p, geom.interpolate(geom.project(p)))
            if d <= max_m:
                near.append((d, ogf_id))
        if not near:
            return None
        cutoff = min(d for d, _ in near) + SNAP_PREFER_M
        d, ogf_id = max(
            ((d, i) for d, i in near if d <= cutoff),
            key=lambda c: (self.upstream_km.get(c[1], 0.0), -c[0], c[1]),
        )
        return Snap(ogf_id=ogf_id, distance_m=round(d, 1))

    # ── routing ───────────────────────────────────────────────────────────────

    def route(self, a: int, b: int) -> list[int] | None:
        """Segments on the shortest channel path from segment a to segment b."""
        if a == b:
            return [a]
        sa, sb = self.segments[a], self.segments[b]
        best: tuple[float, list[str]] | None = None
        for u in (sa.start_node, sa.end_node):
            for v in (sb.start_node, sb.end_node):
                try:
                    length, path = nx.single_source_dijkstra(self.graph, u, v, weight="length")
                except nx.NetworkXNoPath:
                    continue
                if best is None or length < best[0]:
                    best = (length, path)
        if best is None:
            return None
        ids = [a]
        for u, v in zip(best[1], best[1][1:]):
            # Every parallel edge between two nodes is the same reach (a braid);
            # all of them belong to the stretch.
            ids.extend(sorted(self.graph[u][v]))
        ids.append(b)
        return list(dict.fromkeys(ids))

    def length_m(self, ogf_ids: list[int]) -> float:
        return sum(self.segments[i].length_m for i in ogf_ids)


def trace_reach(network: StreamNetwork, reach: CuratedReach, max_snap_m: float) -> ReachTrace:
    """Follow the network through every waypoint of a reach."""
    snaps: list[Snap] = []
    for wp in reach.waypoints():
        if wp.ogf_id is not None:
            if wp.ogf_id not in network.segments:
                return ReachTrace(
                    failure="snap_failed",
                    detail=f"{wp.label or 'point'} is pinned to ogf_id {wp.ogf_id}, "
                    "which is not in the network",
                )
            snaps.append(Snap(ogf_id=wp.ogf_id, distance_m=0.0))
            continue
        snap = network.snap(wp, max_snap_m)
        if snap is None:
            where = f"{wp.label or 'point'} ({wp.lat:.5f}, {wp.lon:.5f})"
            return ReachTrace(
                failure="snap_failed",
                detail=f"{where} is more than {max_snap_m:.0f} m from any channel",
            )
        snaps.append(snap)

    ids: list[int] = []
    for s0, s1 in zip(snaps, snaps[1:]):
        leg = network.route(s0.ogf_id, s1.ogf_id)
        if leg is None:
            return ReachTrace(
                failure="no_path",
                detail=(
                    f"segments {s0.ogf_id} and {s1.ogf_id} are on networks that do not "
                    "connect — a point snapped to the wrong channel, or a gap in OHN"
                ),
            )
        ids.extend(leg)
    ids = list(dict.fromkeys(ids))

    wps = reach.waypoints()
    return ReachTrace(
        ogf_ids=ids,
        boundary_ids=[s.ogf_id for s in snaps],
        length_m=network.length_m(ids),
        straight_m=sum(
            _metres(Point(a.lon, a.lat), Point(b.lon, b.lat)) for a, b in zip(wps, wps[1:])
        ),
    )


# ── flow accumulation ─────────────────────────────────────────────────────────


def _accumulate_upstream_km(segments) -> dict[int, float]:
    """Channel length (km) draining through each segment, inclusive.

    OHN digitises segments in flow direction. Cycles (braids, lakes with two
    outlets, unverified directions) are condensed first so the order is a DAG.

    Mass-conserving: where flow splits, the upstream total is divided evenly
    between the branches rather than copied into each. Copying counts every
    path separately, and a lake's mesh of virtual flow lines has billions of
    paths — the first version of this reported 163 billion km of channel
    above the St. Lawrence.
    """
    flow = nx.DiGraph()
    for seg in segments:
        if flow.has_edge(seg.start_node, seg.end_node):
            flow[seg.start_node][seg.end_node]["length"] += seg.length_m
        else:
            flow.add_edge(seg.start_node, seg.end_node, length=seg.length_m)
    if flow.number_of_nodes() == 0:
        return {}

    cond = nx.condensation(flow)
    member = cond.graph["mapping"]
    edge_len: dict[tuple[int, int], float] = defaultdict(float)
    internal: dict[int, float] = defaultdict(float)
    for u, v, d in flow.edges(data=True):
        cu, cv = member[u], member[v]
        if cu == cv:
            internal[cu] += d["length"]
        else:
            edge_len[(cu, cv)] += d["length"]

    inflow: dict[int, float] = defaultdict(float)
    share: dict[int, float] = {}
    for c in nx.topological_sort(cond):
        total = inflow[c] + internal[c]
        share[c] = total / max(cond.out_degree(c), 1)
        for nxt in cond.successors(c):
            inflow[nxt] += share[c] + edge_len[(c, nxt)]

    return {
        seg.ogf_id: round((share[member[seg.start_node]] + seg.length_m) / 1000, 3)
        for seg in segments
    }


# ── clustering ────────────────────────────────────────────────────────────────


def cluster_candidates(
    network: StreamNetwork,
    jurisdiction: str,
    *,
    min_upstream_km: float = 400.0,
    max_upstream_km: float = 30_000.0,
    max_km: float = 40.0,
    min_km: float = 5.0,
) -> list[CandidateStretch]:
    """Propose stretches: major channel cut at major confluences and at max_km.

    A node where three or more major segments meet is a confluence of two
    major branches; a node with one is a source or a mouth. Chains of major
    segments between those nodes are the natural units of a river. Chains
    shorter than min_km are stubs between confluences and are dropped.

    Channel draining more than max_upstream_km is left out: that is the Great
    Lakes shore flow lines and the connecting channels, which drain whole
    basins (the St. Lawrence carries ~136,000 km; the largest Ontario river
    stretch, the Trent at Trenton, ~22,000). Unfiltered, the first dozen
    "missing rivers" were all Lake Ontario. Connecting channels worth fishing
    — the Niagara, the Detroit — are curated by hand.
    """
    major = {i for i, km in network.upstream_km.items() if min_upstream_km <= km <= max_upstream_km}
    # A simple graph whose edges carry every segment between two nodes: a
    # braid is two segments with the same ends, and as two edges it would give
    # both ends degree 3 and cut the river into candidates at every island.
    sub = nx.Graph()
    for i in sorted(major):
        seg = network.segments[i]
        u, v = seg.start_node, seg.end_node
        if sub.has_edge(u, v):
            sub[u][v]["ids"].append(i)
        else:
            sub.add_edge(u, v, ids=[i])

    candidates: list[CandidateStretch] = []
    for chain in _chains(sub):
        for piece in _split_chain(network, chain, max_km * 1000):
            length_km = network.length_m(piece) / 1000
            if length_km < min_km:
                continue
            candidates.append(_candidate(network, piece, jurisdiction, length_km))
    candidates.sort(key=lambda c: -c.upstream_km)
    return candidates


def _chains(sub: nx.Graph) -> list[list[int]]:
    """Maximal runs of edges whose interior nodes have degree 2, as segment ids."""
    used: set[frozenset[str]] = set()
    chains: list[list[int]] = []

    def walk(start: str, first: str) -> list[int]:
        chain: list[int] = []
        u, v = start, first
        while True:
            used.add(frozenset((u, v)))
            chain.extend(sub[u][v]["ids"])
            if sub.degree(v) != 2 or v == start:
                return chain
            nxt = [w for w in sub.neighbors(v) if frozenset((v, w)) not in used]
            if not nxt:
                return chain
            u, v = v, nxt[0]

    for node in sub.nodes:
        if sub.degree(node) == 2:
            continue
        for nbr in sub.neighbors(node):
            if frozenset((node, nbr)) not in used:
                chains.append(walk(node, nbr))
    # Whatever is left is a closed loop with no endpoint (a lake ring); walk it once.
    for u, v in sub.edges:
        if frozenset((u, v)) not in used:
            chains.append(walk(u, v))
    return chains


def _split_chain(network: StreamNetwork, chain: list[int], max_m: float) -> list[list[int]]:
    """Cut a chain into near-equal pieces no longer than max_m."""
    total = network.length_m(chain)
    if total <= max_m:
        return [chain]
    n = math.ceil(total / max_m)
    target = total / n
    pieces: list[list[int]] = [[]]
    run = 0.0
    for i in chain:
        if run >= target and len(pieces) < n:
            pieces.append([])
            run = 0.0
        pieces[-1].append(i)
        run += network.segments[i].length_m
    return [p for p in pieces if p]


def _candidate(
    network: StreamNetwork, piece: list[int], jurisdiction: str, length_km: float
) -> CandidateStretch:
    acc = network.upstream_km
    down = max(piece, key=lambda i: (acc.get(i, 0.0), i))
    up = min(piece, key=lambda i: (acc.get(i, 0.0), i))
    names = Counter(n for i in piece if (n := display_name(network.segments[i].name)))
    dp = _midpoint(network, down)
    upp = _midpoint(network, up)
    return CandidateStretch(
        candidate_id=f"cand-{down}",
        suggested_name=names.most_common(1)[0][0] if names else None,
        jurisdiction=jurisdiction,
        length_km=round(length_km, 2),
        upstream_km=round(acc.get(down, 0.0), 1),
        downstream=BoundaryPoint(lat=round(dp.y, 5), lon=round(dp.x, 5)),
        upstream=BoundaryPoint(lat=round(upp.y, 5), lon=round(upp.x, 5)),
        ogf_ids=sorted(piece),
    )


def _midpoint(network: StreamNetwork, ogf_id: int) -> Point:
    """Midpoint of an end segment — inside the channel, safe to snap back to."""
    return network.geoms[ogf_id].interpolate(0.5, normalized=True)


# ── geometry ──────────────────────────────────────────────────────────────────


@dataclass
class StretchShape:
    geometry: dict
    bbox: tuple[float, float, float, float]
    centroid_lat: float
    centroid_lon: float


def stretch_shape(
    network: StreamNetwork, ogf_ids: list[int], simplify_deg: float = 0.0005
) -> StretchShape:
    """Merged, simplified line geometry for drawing a stretch at low zoom.

    simplify_deg ≈ 50 m: invisible at province zoom, and it keeps a 40 km
    stretch to a few hundred vertices.
    """
    merged = linemerge([network.geoms[i] for i in ogf_ids])
    simple = merged.simplify(simplify_deg, preserve_topology=True)
    if isinstance(simple, MultiLineString) and len(simple.geoms) == 1:
        simple = simple.geoms[0]
    geo = _round_coords(mapping(simple), 5)
    min_lon, min_lat, max_lon, max_lat = merged.bounds
    # A point ON the line, for a label or a zoom target — a centroid of a
    # meandering river can land on dry land.
    longest = (
        merged if merged.geom_type == "LineString" else max(merged.geoms, key=lambda g: g.length)
    )
    mid = longest.interpolate(0.5, normalized=True)
    return StretchShape(
        geometry=geo,
        bbox=(round(min_lon, 5), round(min_lat, 5), round(max_lon, 5), round(max_lat, 5)),
        centroid_lat=round(mid.y, 5),
        centroid_lon=round(mid.x, 5),
    )


def _round_coords(geo: dict, ndigits: int) -> dict:
    def r(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], ndigits), round(c[1], ndigits)]
        return [r(x) for x in c]

    return {"type": geo["type"], "coordinates": r(list(geo["coordinates"]))}
