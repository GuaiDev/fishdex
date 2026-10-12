"""Stretch anchors — Level 2 of the explore map.

Once the map has zoomed into a stretch it shows the features worth tapping:
where tributaries join, the dam and the water just below it, where the river
meets the lake, and the places to park and to get at the water. Those are the
anchors this module builds.

Where each kind comes from:

- **Confluences and lake mouths** are read off the OHN network's topology —
  the same segments the stretch was traced through. Nothing external is
  fetched, so they exist wherever the stretch does.
- **Dams, weirs and locks, access points and road crossings** come from
  OpenStreetMap, fetched for the corridor around each stretch through the
  Overpass cache in ingest/global/osm.py. OSM is volunteer-mapped: a dam it
  lacks is simply absent here, and the build report counts what it did not
  keep so thin coverage reads as thin coverage, not as "nothing there".
- **Tailwaters** are derived: the reach of channel just downstream of a dam,
  found by following the OHN flow direction. They exist only where OHN has
  verified that direction.

These are located features, not fish predictions. A confluence is where two
channels meet; whether it holds fish, and which, is for the retrieval layer
and the angler to say. Pressure and presence stay separate (CLAUDE.md).

Read: stretch_anchors_geojson() returns one stretch's anchors and segment
lines as a GeoJSON FeatureCollection for the zoomed map.
"""

import importlib
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from shapely.geometry import LineString, Point, mapping
from shapely.ops import nearest_points, substring
from sqlite_utils.db import Database

from src.models.hydrology import StreamSegment
from src.models.stretch import FishingStretch
from src.models.stretch_anchor import (
    AnchorBuildIssue,
    AnchorBuildReport,
    StretchAnchor,
    StretchSegmentLine,
)
from src.models.water_feature import CorridorFeature
from src.services.stretch_clustering import StreamNetwork, _metres, display_name
from src.services.stretches import DEFAULT_CURATION_PATH, load_curation, load_network
from src.storage.stretch_anchors import list_anchors, list_segment_lines, replace_anchors
from src.storage.stretches import get_stretch, list_stretches, stretch_segment_ids

logger = logging.getLogger(__name__)

# A tributary joining the stretch counts as a confluence when it drains at least
# this much channel — or, if OHN names it, at least the smaller figure. Below
# that it is a ditch or a headwater rill, and a river with forty of them would
# bury the dozen joins an angler would call confluences.
MIN_TRIBUTARY_KM = 10.0
MIN_NAMED_TRIBUTARY_KM = 2.0
# Walking upstream from a joining segment, reaching the stretch's own water
# within this many segments means the "tributary" is a braid or side channel
# that left the river and came back, not a different stream.
_SIDE_CHANNEL_HOPS = 40
# A channel draining at least this much is a Great Lakes shore flow line. Measured
# on the Ontario network: Lake Erie and Ontario shore lines carry 60,000+ km, the
# Georgian Bay and Bay of Quinte lines 22,000-30,000, and the largest river that
# is not a lake or connecting channel (the Welland, at its mouth) 21,000.
LAKE_SHORE_UPSTREAM_KM = 22_000.0
# Following the channel upstream to find a name for a tributary OHN left unnamed
# at its mouth: how many segments to try.
_NAME_HOPS = 25

# OSM features are matched to the stretch's own channel, whose OHN geometry is
# generalised to ~35 m, so "on the water" has to allow for that.
DAM_SNAP_M = 120.0
ACCESS_SNAP_M = 250.0
CROSSING_SNAP_M = 40.0
# One physical dam is often mapped as a dam way, a weir node and a lock.
DAM_MERGE_M = 150.0
CROSSING_MERGE_M = 120.0
# The reach below a dam: how far to follow the flow, and where the marker sits.
TAILWATER_REACH_M = 400.0
TAILWATER_MARKER_M = 100.0
# A curated mouth owns its stretch's mouth: any mouth the topology finds this
# close to it is the same mouth, and is not published a second time.
CURATED_MOUTH_DUPLICATE_M = 300.0

_NOT_PUBLIC = {"private", "no", "customers", "delivery", "permit", "residents"}
_NOT_OUTDOOR_PARKING = {"multi-storey", "underground", "rooftop"}
_DAM_PRIORITY = {"dam": 0, "lock": 1, "weir": 2}
_KEPT_TAGS = (
    "access",
    "fee",
    "capacity",
    "operator",
    "surface",
    "highway",
    "ref",
    "bridge",
    "tunnel",
    "ele",
    "height",
)

# Stop asking Overpass after this many stretches in a row have failed.
_GIVE_UP_AFTER = 3

CorridorFetcher = Callable[[list[tuple[float, float]], str | None], list[CorridorFeature]]


def build_anchors(
    db: Database,
    curation_path: Path = DEFAULT_CURATION_PATH,
    segments: list[StreamSegment] | None = None,
    fetch_corridor: CorridorFetcher | None = None,
) -> AnchorBuildReport:
    """Rebuild every anchor for the curation file's jurisdiction.

    Reads the published stretches and their segment membership from the
    database, so it can run on its own after `build_stretches` or as the second
    half of it. Rebuilding twice from the same inputs stores the same anchors:
    ids come from the features, not from list order.

    `segments` and `fetch_corridor` are injectable for tests; by default the
    network and OSM are fetched (both through their caches). An Overpass
    failure for one stretch keeps that stretch's previously stored OSM anchors
    and says so in the report — a flaky mirror must not erase a map.
    """
    curation = load_curation(curation_path)
    jurisdiction = curation.jurisdiction
    curated_by_id = {s.id: s for s in curation.stretches}
    if segments is None:
        segments = load_network(curation)
    if fetch_corridor is None:
        # `global` is a keyword, so the adapter package cannot be imported by name.
        fetch_corridor = importlib.import_module("src.ingest.global.osm").fetch_stream_corridor

    network = StreamNetwork(segments)
    stretches = list_stretches(db, jurisdiction)
    members = {s.stretch_id: stretch_segment_ids(db, s.stretch_id) for s in stretches}
    claimed = {ogf: sid for sid, ids in members.items() for ogf in ids}
    previous_osm = {
        s.stretch_id: [a for a in list_anchors(db, s.stretch_id) if a.source == "osm"]
        for s in stretches
    }

    previous_tailwaters = [
        a for kept in previous_osm.values() for a in kept if a.anchor_type == "tailwater"
    ]

    report = AnchorBuildReport(jurisdiction=jurisdiction, stretches=len(stretches))
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    ctx = _Topology(network)
    anchors: list[StretchAnchor] = []
    lines: list[StretchSegmentLine] = []
    consecutive_failures = 0

    for stretch in stretches:
        ids = [i for i in members[stretch.stretch_id] if i in network.segments]
        if not ids:
            continue
        lines.extend(_segment_lines(network, stretch, ids))
        found = _confluences(ctx, stretch, ids, built_at, report)
        found += _lake_mouths(
            ctx, stretch, ids, built_at, report, curated=curated_by_id.get(stretch.stretch_id)
        )
        if consecutive_failures >= _GIVE_UP_AFTER:
            # Each failed fetch has already spent its retries; do not spend
            # them sixty more times against a service that is down.
            corridor = None
            failure = f"skipped: Overpass failed for {_GIVE_UP_AFTER} stretches in a row"
        else:
            try:
                corridor, failure = fetch_corridor(_polyline(stretch), jurisdiction), ""
            except Exception as exc:  # Overpass down: say so, keep what we had
                corridor, failure = None, f"{type(exc).__name__}: {exc}"
        if corridor is None:
            consecutive_failures += 1
            report.issues.append(
                AnchorBuildIssue(
                    stretch_id=stretch.stretch_id, kind="osm_unavailable", detail=failure
                )
            )
            kept = previous_osm[stretch.stretch_id]
            if kept:
                report.carried_over_stretches.append(stretch.stretch_id)
            kept_dams = {a.source_ref for a in kept if a.anchor_type == "dam"}
            found += kept + [
                a for a in previous_tailwaters if a.attributes.get("dam_source_ref") in kept_dams
            ]
        else:
            consecutive_failures = 0
            found += _osm_anchors(ctx, stretch, ids, corridor, claimed, built_at, report)
        anchors.extend(found)

    anchors = _unique(anchors)
    anchors.sort(key=lambda a: (a.stretch_id, a.anchor_type, a.anchor_id))
    replace_anchors(db, jurisdiction, anchors, lines)

    for a in anchors:
        per = report.by_stretch.setdefault(a.stretch_id, {})
        per[a.anchor_type] = per.get(a.anchor_type, 0) + 1
        report.by_type[a.anchor_type] = report.by_type.get(a.anchor_type, 0) + 1
    report.anchors_total = len(anchors)

    for issue in report.issues:
        logger.warning("stretch %s: %s — %s", issue.stretch_id, issue.kind, issue.detail)
    if report.stretches_without_osm:
        logger.warning(
            "OSM was unavailable for %d of %d stretches (%s): their dams and access points are %s",
            len(report.stretches_without_osm),
            report.stretches,
            ", ".join(report.stretches_without_osm),
            "carried over from the last build where there was one, otherwise missing",
        )
    return report


# ── topology ──────────────────────────────────────────────────────────────────


class _Topology:
    """Directed view of the OHN network: which segments flow into and out of a node."""

    def __init__(self, network: StreamNetwork):
        self.network = network
        self.incoming: dict[str, list[int]] = defaultdict(list)
        self.outgoing: dict[str, list[int]] = defaultdict(list)
        for seg in network.segments.values():
            self.outgoing[seg.start_node].append(seg.ogf_id)
            self.incoming[seg.end_node].append(seg.ogf_id)

    def upstream_km(self, ogf_id: int) -> float:
        return self.network.upstream_km.get(ogf_id, 0.0)

    def reaches_nodes_upstream(self, ogf_id: int, nodes: set[str]) -> bool:
        """Whether walking upstream from a segment gets back to any of `nodes`.

        True for a braid's side channel, which leaves the river above the
        island and rejoins below it. Breadth-first and bounded: a real
        tributary fans out into a basin and never gets back.
        """
        seen = {ogf_id}
        frontier = [ogf_id]
        for _ in range(_SIDE_CHANNEL_HOPS):
            nxt: list[int] = []
            for i in frontier:
                start = self.network.segments[i].start_node
                if start in nodes:
                    return True
                for j in self.incoming.get(start, ()):
                    if j not in seen:
                        seen.add(j)
                        nxt.append(j)
            if not nxt:
                return False
            frontier = nxt
        return False


def _member_nodes(network: StreamNetwork, ids: list[int]) -> set[str]:
    nodes: set[str] = set()
    for i in ids:
        seg = network.segments[i]
        nodes.update((seg.start_node, seg.end_node))
    return nodes


def _coord_key(lon: float, lat: float) -> str:
    return f"{round(lon * 1e5)}_{round(lat * 1e5)}".replace("-", "n")


def _node_lonlat(node: str) -> tuple[float, float]:
    lon, lat = node.split(",")
    return float(lon), float(lat)


def _confluences(
    ctx: _Topology,
    stretch: FishingStretch,
    ids: list[int],
    built_at: str,
    report: AnchorBuildReport,
) -> list[StretchAnchor]:
    """Nodes where a channel from outside the stretch flows into it.

    Includes the node where a tributary stretch ends in the river it joins:
    from that node the Grand is a channel from outside the stretch.
    """
    network = ctx.network
    member = set(ids)
    nodes = _member_nodes(network, ids)
    out: list[StretchAnchor] = []
    for node in sorted(nodes):
        stretch_in = [i for i in ctx.incoming.get(node, ()) if i in member]
        stretch_out = [i for i in ctx.outgoing.get(node, ()) if i in member]
        foreign = [i for i in ctx.incoming.get(node, ()) if i not in member]
        if not foreign:
            continue
        if any(ctx.upstream_km(i) >= LAKE_SHORE_UPSTREAM_KM for i in foreign):
            report.drop("confluence_is_lake_mouth")
            continue
        if not stretch_in:
            # The stretch starts here, so the biggest channel arriving is the
            # river it continues, not a tributary. Channels of comparable size
            # are its braided halves.
            top = max(ctx.upstream_km(i) for i in foreign)
            seam = [i for i in foreign if ctx.upstream_km(i) >= top / 2]
            report.drop("confluence_stretch_seam", len(seam))
            foreign = [i for i in foreign if i not in seam]
        real = []
        for i in foreign:
            if ctx.reaches_nodes_upstream(i, nodes):
                report.drop("confluence_side_channel")
                continue
            real.append(i)
        if not real:
            continue
        biggest = max(real, key=lambda i: (ctx.upstream_km(i), -i))
        size = ctx.upstream_km(biggest)
        name, name_ogf = _channel_name(ctx, biggest)
        if size < (MIN_NAMED_TRIBUTARY_KM if name else MIN_TRIBUTARY_KM):
            report.drop("confluence_small_tributary")
            continue
        lon, lat = _node_lonlat(node)
        attrs = {
            "joining_ogf_ids": sorted(real),
            "joining_upstream_km": round(size, 1),
            # The stretch's own channel here. At the stretch's downstream end
            # (the stretch is the tributary) nothing of it flows on.
            "stretch_upstream_km": round(
                max((ctx.upstream_km(i) for i in stretch_in), default=0.0), 1
            ),
            "stretch_flows_on": bool(stretch_out),
        }
        if name and name_ogf != biggest:
            attrs["name_from_ogf_id"] = name_ogf  # named further up the same channel
        out.append(
            StretchAnchor(
                anchor_id=f"{stretch.stretch_id}:confluence:{_coord_key(lon, lat)}",
                stretch_id=stretch.stretch_id,
                jurisdiction=stretch.jurisdiction,
                anchor_type="confluence",
                name=name,  # the joining watercourse, where OHN names it
                lat=lat,
                lon=lon,
                source="ohn_topology",
                source_ref="ogf:"
                + ",".join(str(i) for i in sorted({*real, *stretch_in, *stretch_out})),
                ogf_id=(stretch_in or stretch_out)[0],
                snap_distance_m=0.0,
                attributes=attrs,
                built_at=built_at,
            )
        )
    return out


def _channel_name(ctx: _Topology, ogf_id: int) -> tuple[str | None, int | None]:
    """OHN's name for a channel, looking up the main branch if the mouth is unnamed.

    The last segment before a confluence is often a short unnamed fragment of
    a river OHN names a few segments up. Follows the biggest inflow at each
    node, so it stays on the same stream and never wanders into another.
    """
    network = ctx.network
    current: int | None = ogf_id
    for _ in range(_NAME_HOPS):
        if current is None:
            break
        seg = network.segments[current]
        if name := display_name(seg.name):
            return name, current
        feeders = ctx.incoming.get(seg.start_node, ())
        current = max(feeders, key=lambda i: (ctx.upstream_km(i), -i)) if feeders else None
    return None, None


def _lake_mouths(
    ctx: _Topology,
    stretch: FishingStretch,
    ids: list[int],
    built_at: str,
    report: AnchorBuildReport,
    curated=None,
) -> list[StretchAnchor]:
    """The node where the stretch's water runs out into a Great Lakes shore line.

    A mouth is a downstream end of the stretch — a node some member segment
    flows into and none flows out of — where a lake-scale shore line passes
    through: one flowing in from along the shore and one flowing on. A river
    simply continuing downstream has only the second, and a tributary that
    ends in another river is a confluence, not a mouth.

    A curated mouth (`mouth = { lat, lon, lake }` in the curation file) is the
    authority for a river whose mouth the topology cannot find — an inland
    lake has no Great Lakes shore line for it to recognise. When one is
    present it is published as the stretch's mouth, named from the file, and
    any mouth the topology finds within CURATED_MOUTH_DUPLICATE_M of it is the
    same mouth, not published twice. Topology mouths take their name from the
    shore line, falling back to the file's `lake` key.
    """
    network = ctx.network
    member = set(ids)
    out: list[StretchAnchor] = []

    curated_mouth = curated.mouth if curated is not None else None
    if curated_mouth is not None:
        # The model forbids mouth.lake disagreeing with lake, so this is
        # unambiguous: the mouth's own name, else the stretch's.
        lake_name = curated_mouth.lake or curated.lake
    else:
        lake_name = curated.lake if curated is not None else None

    if curated_mouth is not None:
        m = curated_mouth
        geoms = {i: network.geoms[i] for i in ids}
        hit = _nearest_member(geoms, m.lat, m.lon)
        out.append(
            StretchAnchor(
                anchor_id=f"{stretch.stretch_id}:lake_mouth:{_coord_key(m.lon, m.lat)}",
                stretch_id=stretch.stretch_id,
                jurisdiction=stretch.jurisdiction,
                anchor_type="lake_mouth",
                name=lake_name,
                lat=round(m.lat, 6),
                lon=round(m.lon, 6),
                source="curated",
                source_ref=f"curation:{stretch.stretch_id}",
                ogf_id=None if hit is None else hit[0],
                snap_distance_m=None if hit is None else hit[1],
                built_at=built_at,
            )
        )

    for node in sorted(_member_nodes(network, ids)):
        stretch_in = [i for i in ctx.incoming.get(node, ()) if i in member]
        stretch_out = [i for i in ctx.outgoing.get(node, ()) if i in member]
        if not stretch_in or stretch_out:
            continue
        shore_in = [
            i
            for i in ctx.incoming.get(node, ())
            if i not in member and ctx.upstream_km(i) >= LAKE_SHORE_UPSTREAM_KM
        ]
        shore_out = [
            i for i in ctx.outgoing.get(node, ()) if ctx.upstream_km(i) >= LAKE_SHORE_UPSTREAM_KM
        ]
        if not (shore_in and shore_out):
            continue
        lon, lat = _node_lonlat(node)
        if (
            curated_mouth is not None
            and _metres(Point(curated_mouth.lon, curated_mouth.lat), Point(lon, lat))
            <= CURATED_MOUTH_DUPLICATE_M
        ):
            report.drop("lake_mouth_suppressed_by_curated")
            continue
        lake = next(
            (n for i in shore_in + shore_out if (n := display_name(network.segments[i].name))),
            None,
        )
        out.append(
            StretchAnchor(
                anchor_id=f"{stretch.stretch_id}:lake_mouth:{_coord_key(lon, lat)}",
                stretch_id=stretch.stretch_id,
                jurisdiction=stretch.jurisdiction,
                anchor_type="lake_mouth",
                name=lake or lake_name,
                lat=lat,
                lon=lon,
                source="ohn_topology",
                source_ref="ogf:"
                + ",".join(str(i) for i in sorted(stretch_in + shore_in + shore_out)),
                ogf_id=stretch_in[0],
                snap_distance_m=0.0,
                attributes={"shore_ogf_ids": sorted(shore_in + shore_out)},
                built_at=built_at,
            )
        )
    return out


# ── OpenStreetMap anchors ─────────────────────────────────────────────────────


def _polyline(stretch: FishingStretch) -> list[tuple[float, float]]:
    """The stretch's vertices as one (lon, lat) polyline, parts joined end to end."""
    geo = stretch.geometry
    parts = [geo["coordinates"]] if geo["type"] == "LineString" else geo["coordinates"]
    return [(c[0], c[1]) for part in parts for c in part]


def _osm_anchors(
    ctx: _Topology,
    stretch: FishingStretch,
    ids: list[int],
    corridor: list[CorridorFeature],
    claimed: dict[int, str],
    built_at: str,
    report: AnchorBuildReport,
) -> list[StretchAnchor]:
    network = ctx.network
    geoms = {i: network.geoms[i] for i in ids}
    out: list[StretchAnchor] = []

    # Snap first, merge second: a dam on a pond beside the river must not absorb
    # (and then take down with it) a weir that is on the river.
    on_channel: dict[str, tuple[int, float]] = {}
    for f in corridor:
        if f.kind not in ("dam", "weir", "lock"):
            continue
        hit = _nearest_member(geoms, f.lat, f.lng)
        if hit is None or hit[1] > DAM_SNAP_M:
            report.drop("dam_off_channel")
        else:
            on_channel[f.osm_id] = hit
    kept_dams, merged = _merge_dams([f for f in corridor if f.osm_id in on_channel])
    report.drop("dam_duplicate_mapping", merged)
    for dam, also_mapped in kept_dams:
        ogf_id, dist = on_channel[dam.osm_id]
        out.append(_dam_anchor(stretch, dam, also_mapped, ogf_id, dist, built_at))
        tail = _tailwater(ctx, dam, ogf_id, claimed, stretch, built_at, report)
        if tail is not None:
            out.append(tail)

    for f in corridor:
        if f.kind not in ("parking", "trail_head", "boat_launch", "fishing_spot"):
            continue
        if str(f.tags.get("access", "")).lower() in _NOT_PUBLIC:
            report.drop("access_not_public")
            continue
        if f.kind == "parking" and f.tags.get("parking") in _NOT_OUTDOOR_PARKING:
            report.drop("access_structured_parking")
            continue
        hit = _nearest_member(geoms, f.lat, f.lng)
        if hit is None or hit[1] > ACCESS_SNAP_M:
            report.drop("access_too_far_from_water")
            continue
        out.append(
            _access_anchor(
                stretch, f, f.kind, f"osm-{f.osm_id.replace('/', '-')}", hit, built_at, f.lat, f.lng
            )
        )

    out.extend(_road_crossings(stretch, geoms, corridor, built_at, report))
    return out


def _nearest_member(
    geoms: dict[int, LineString], lat: float, lon: float
) -> tuple[int, float] | None:
    """(ogf_id, metres) of the stretch segment closest to a point."""
    p = Point(lon, lat)
    best: tuple[float, int] | None = None
    for ogf_id, geom in geoms.items():
        d = _metres(p, geom.interpolate(geom.project(p)))
        if best is None or (d, ogf_id) < best:
            best = (d, ogf_id)
    return None if best is None else (best[1], round(best[0], 1))


def _merge_dams(
    dams: list[CorridorFeature],
) -> tuple[list[tuple[CorridorFeature, list[str]]], int]:
    """Collapse the several OSM objects of one structure into the best of them.

    Returns each kept dam with the other OSM objects it absorbed ("weir:node/2"),
    and how many were absorbed.
    """
    ranked = sorted(dams, key=lambda f: (_DAM_PRIORITY[f.kind], f.name is None, f.osm_id))
    kept: list[tuple[CorridorFeature, list[str]]] = []
    merged = 0
    for f in ranked:
        twin = next(
            (
                also
                for k, also in kept
                if _metres(Point(k.lng, k.lat), Point(f.lng, f.lat)) <= DAM_MERGE_M
            ),
            None,
        )
        if twin is None:
            kept.append((f, []))
        else:
            merged += 1
            twin.append(f"{f.kind}:{f.osm_id}")
    return kept, merged


def _tags_kept(tags: dict) -> dict:
    return {k: tags[k] for k in _KEPT_TAGS if k in tags}


def _dam_anchor(
    stretch: FishingStretch,
    dam: CorridorFeature,
    also_mapped: list[str],
    ogf_id: int,
    dist: float,
    built_at: str,
) -> StretchAnchor:
    attrs = _tags_kept(dam.tags)
    if also_mapped:
        attrs["also_mapped_as"] = sorted(also_mapped)
    return StretchAnchor(
        anchor_id=f"{stretch.stretch_id}:dam:osm-{dam.osm_id.replace('/', '-')}",
        stretch_id=stretch.stretch_id,
        jurisdiction=stretch.jurisdiction,
        anchor_type="dam",
        subtype=dam.kind,
        name=dam.name,
        lat=dam.lat,
        lon=dam.lng,
        source="osm",
        source_ref=dam.osm_id,
        ogf_id=ogf_id,
        snap_distance_m=dist,
        attributes=attrs,
        built_at=built_at,
    )


def _access_anchor(
    stretch: FishingStretch,
    f: CorridorFeature,
    kind: str,
    key: str,
    hit: tuple[int, float],
    built_at: str,
    lat: float,
    lon: float,
    extra: dict | None = None,
) -> StretchAnchor:
    return StretchAnchor(
        anchor_id=f"{stretch.stretch_id}:{kind}:{key}",
        stretch_id=stretch.stretch_id,
        jurisdiction=stretch.jurisdiction,
        anchor_type="access_point",
        subtype=kind,
        name=f.name,
        lat=lat,
        lon=lon,
        source="osm",
        source_ref=f.osm_id,
        ogf_id=hit[0],
        snap_distance_m=hit[1],
        attributes={**_tags_kept(f.tags), **(extra or {})},
        built_at=built_at,
    )


def _road_crossings(
    stretch: FishingStretch,
    geoms: dict[int, LineString],
    corridor: list[CorridorFeature],
    built_at: str,
    report: AnchorBuildReport,
) -> list[StretchAnchor]:
    """Where a public road meets the stretch's channel.

    The crossing sits on the road at its nearest approach to the water, found
    against the OHN line, which is itself generalised — hence the tolerance.
    Dual carriageways and a road mapped as several ways collapse into one
    crossing per road and place.
    """
    found: list[tuple[CorridorFeature, int, float, Point]] = []
    for road in corridor:
        if road.kind != "road" or not road.line:
            continue
        if str(road.tags.get("access", "")).lower() in _NOT_PUBLIC:
            report.drop("crossing_not_public")
            continue
        if road.tags.get("tunnel") not in (None, "culvert", "no"):
            report.drop("crossing_tunnel")
            continue
        line = LineString(road.line)
        best: tuple[float, int, Point] | None = None
        for ogf_id, geom in geoms.items():
            on_road, _on_stream = nearest_points(line, geom)
            d = _metres(on_road, _on_stream)
            if best is None or (d, ogf_id) < (best[0], best[1]):
                best = (d, ogf_id, on_road)
        if best is None or best[0] > CROSSING_SNAP_M:
            report.drop("crossing_not_on_channel")
            continue
        found.append((road, best[1], round(best[0], 1), best[2]))

    found.sort(key=lambda c: (c[0].name or c[0].tags.get("ref") or "", c[0].osm_id))
    kept: list[tuple[CorridorFeature, int, float, Point]] = []
    for c in found:
        label = c[0].name or c[0].tags.get("ref")
        twin = next(
            (
                k
                for k in kept
                if (k[0].name or k[0].tags.get("ref")) == label
                and _metres(k[3], c[3]) <= CROSSING_MERGE_M
            ),
            None,
        )
        if twin is not None:
            report.drop("crossing_duplicate_way")
            continue
        kept.append(c)

    out = []
    for road, ogf_id, dist, pt in kept:
        key = f"osm-{road.osm_id.replace('/', '-')}_{_coord_key(pt.x, pt.y)}"
        out.append(
            _access_anchor(
                stretch,
                road,
                "road_crossing",
                key,
                (ogf_id, dist),
                built_at,
                round(pt.y, 6),
                round(pt.x, 6),
            )
        )
    return out


def _tailwater(
    ctx: _Topology,
    dam: CorridorFeature,
    ogf_id: int,
    claimed: dict[int, str],
    stretch: FishingStretch,
    built_at: str,
    report: AnchorBuildReport,
) -> StretchAnchor | None:
    """The reach of channel just below a dam, located by following the flow.

    OHN digitises segments in the direction of flow, so downstream is the
    direction of increasing distance along a segment. That is only trusted
    where OHN marks the direction verified; otherwise the dam is kept and the
    tailwater is not, because a wrong direction would label the pool above the
    dam as the water below it. Weirs get one too (the Dunnville dam is mapped
    as a weir); lock gates do not, since a lock is not a drop in the river.
    """
    if dam.kind == "lock":
        return None
    network = ctx.network
    seg = network.segments[ogf_id]
    if not seg.flow_verified:
        report.issues.append(
            AnchorBuildIssue(
                stretch_id=stretch.stretch_id,
                kind="tailwater_unverified",
                detail=f"{dam.name or dam.osm_id}: OHN flow direction on segment {ogf_id} "
                "is not verified, so no tailwater was placed",
            )
        )
        return None

    geom = network.geoms[ogf_id]
    start_d = geom.project(Point(dam.lng, dam.lat))
    pieces = _walk_downstream(ctx, ogf_id, start_d, TAILWATER_REACH_M)
    coords: list[tuple[float, float]] = []
    for _, part in pieces:
        coords.extend(part if not coords else part[1:])
    if len(coords) < 2:
        return None
    reach = LineString(coords)
    reach_m = _line_m(reach)
    marker_m = min(TAILWATER_MARKER_M, reach_m)
    marker = reach.interpolate(marker_m / reach_m, normalized=True)
    marker_seg, run = pieces[-1][0], 0.0
    for piece_id, part in pieces:
        run += _line_m(LineString(part))
        if run >= marker_m:
            marker_seg = piece_id
            break
    # The water below the dam may belong to the next stretch down: the
    # tailwater is listed with the water it is in, which is where the map
    # will be looking. Water in no stretch at all stays with the dam's.
    owner = claimed.get(marker_seg, stretch.stretch_id)
    key = f"osm-{dam.osm_id.replace('/', '-')}"
    return StretchAnchor(
        anchor_id=f"{owner}:tailwater:{key}",
        stretch_id=owner,
        jurisdiction=stretch.jurisdiction,
        anchor_type="tailwater",
        name=f"Below {dam.name}" if dam.name else None,
        lat=round(marker.y, 6),
        lon=round(marker.x, 6),
        source="osm",
        source_ref=dam.osm_id,
        ogf_id=marker_seg,
        snap_distance_m=0.0,
        attributes={
            # Which structure this is below. Not an anchor id: the dam is an
            # anchor of every stretch it touches, the tailwater of just one.
            "dam_source_ref": dam.osm_id,
            "dam_kind": dam.kind,
            "reach_m": round(reach_m),
            "reach": mapping(LineString([(round(x, 5), round(y, 5)) for x, y in coords])),
        },
        built_at=built_at,
    )


def _line_m(line: LineString) -> float:
    return sum(_metres(Point(a), Point(b)) for a, b in zip(line.coords, list(line.coords)[1:]))


def _walk_downstream(
    ctx: _Topology, ogf_id: int, from_d: float, max_m: float
) -> list[tuple[int, list[tuple[float, float]]]]:
    """Up to max_m of channel below a point on a segment, as (segment id, coordinates) pieces.

    Past the end of a segment it follows the biggest channel leaving that node.
    """
    network = ctx.network
    pieces: list[tuple[int, list[tuple[float, float]]]] = []
    remaining = max_m
    current: int | None = ogf_id
    d0 = from_d
    seen: set[int] = set()
    while current is not None and remaining > 0 and current not in seen:
        seen.add(current)
        geom = network.geoms[current]
        deg_per_m = geom.length / max(_line_m(geom), 1e-6)
        d1 = min(geom.length, d0 + remaining * deg_per_m)
        part = substring(geom, d0, d1)
        pts = [(x, y) for x, y in part.coords] if part.geom_type == "LineString" else []
        if len(pts) >= 2:
            pieces.append((current, pts))
            remaining -= _line_m(part)
        if d1 < geom.length:
            break
        outs = [i for i in ctx.outgoing.get(network.segments[current].end_node, ()) if i != current]
        current = max(outs, key=lambda i: (ctx.upstream_km(i), -i)) if outs else None
        d0 = 0.0
    return pieces


def _unique(anchors: list[StretchAnchor]) -> list[StretchAnchor]:
    seen: dict[str, StretchAnchor] = {}
    for a in anchors:
        seen.setdefault(a.anchor_id, a)
    return list(seen.values())


# ── segment lines ─────────────────────────────────────────────────────────────


def _segment_lines(
    network: StreamNetwork, stretch: FishingStretch, ids: list[int]
) -> list[StretchSegmentLine]:
    out = []
    for i in ids:
        geo = mapping(network.geoms[i])
        out.append(
            StretchSegmentLine(
                ogf_id=i,
                stretch_id=stretch.stretch_id,
                jurisdiction=stretch.jurisdiction,
                name=display_name(network.segments[i].name),
                geometry={
                    "type": "LineString",
                    "coordinates": [[round(x, 5), round(y, 5)] for x, y in geo["coordinates"]],
                },
            )
        )
    return out


# ── read side ─────────────────────────────────────────────────────────────────


def stretch_anchors_geojson(db: Database, stretch_id: str) -> dict | None:
    """One stretch's anchors and segment lines as a GeoJSON FeatureCollection.

    Point features are anchors (`properties.feature == "anchor"`); LineString
    features are the stretch's segments at full detail
    (`properties.feature == "segment"`). The collection's `stretch` member
    carries the stretch itself for the map to frame. None if no such stretch.
    """
    stretch = get_stretch(db, stretch_id)
    if stretch is None:
        return None
    features = []
    for line in list_segment_lines(db, stretch_id):
        features.append(
            {
                "type": "Feature",
                "id": f"segment:{line.ogf_id}",
                "geometry": line.geometry,
                "properties": {"feature": "segment", "ogf_id": line.ogf_id, "name": line.name},
            }
        )
    for a in list_anchors(db, stretch_id):
        features.append(
            {
                "type": "Feature",
                "id": a.anchor_id,
                "geometry": {"type": "Point", "coordinates": [a.lon, a.lat]},
                "properties": {
                    "feature": "anchor",
                    "anchor_id": a.anchor_id,
                    "stretch_id": a.stretch_id,
                    "anchor_type": a.anchor_type,
                    "subtype": a.subtype,
                    "name": a.name,
                    "jurisdiction": a.jurisdiction,
                    "source": a.source,
                    "source_ref": a.source_ref,
                    "ogf_id": a.ogf_id,
                    "snap_distance_m": a.snap_distance_m,
                    "attributes": a.attributes,
                    "built_at": a.built_at,
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "stretch": {
            "stretch_id": stretch.stretch_id,
            "name": stretch.name,
            "river": stretch.river,
            "jurisdiction": stretch.jurisdiction,
            "bbox": list(stretch.bbox),
            "label_point": [stretch.centroid_lon, stretch.centroid_lat],
        },
        "features": features,
    }
