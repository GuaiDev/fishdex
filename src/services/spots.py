"""Spots — the unit the explore map shows.

`classify()` turns the extraction layer's stretch anchors into spots: one tap
target per reason to go, in plain words. Anchors stay as they are (they are the
input layer); this module sits on top and does no network and no database work,
so the same anchors always give the same spots.

What it decides:

- **River mouths.** Every lake-mouth anchor, plus a curated `mouth` for a
  stretch the topology could not find one on. The lake's name comes from the
  curation file's optional `lake` key.
- **Where creeks join.** One spot per physical place, however many stretches
  list it, and one definition: a channel from outside the stretch flowing in
  (the anchor extraction's definition), seen from whichever side names it.
- **Below dams.** A dam (or weir) and the tailwater under it are one spot, at
  the tailwater marker. A lock has no drop and is not a spot.
- **Public fishing spots.** OSM `leisure=fishing`, the one access feature that
  is a destination.
- Parking, trailheads, boat launches and road crossings are *not* spots. The
  nearest of each is attached to nearby spots as `getting_there` / `bridges`.

Which spots are shown, and when, is the gate and tiers of the redesign report:
a tier is a coarse rank for map density, never a quality claim, and ordering
never uses report density (presence is not pressure).

The words are in services/spot_labels.py and nowhere else.

Read side: `spots_geojson()` and `get_spot_detail()` serve the API.
"""

import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlite_utils.db import Database

from src.models.jurisdiction import JurisdictionCode
from src.models.spot import (
    AccessRef,
    Spot,
    SpotBuildReport,
    SpotClassification,
    SpotKind,
    SpotSource,
    SpotTier,
)
from src.models.stretch import FishingStretch
from src.models.stretch_anchor import StretchAnchor
from src.services import spot_labels as words
from src.services.stretch_anchors import DAM_MERGE_M, _coord_key
from src.services.stretches import DEFAULT_CURATION_PATH, load_curation
from src.storage.spots import get_spot, list_spots, replace_spots
from src.storage.stretch_anchors import list_anchors
from src.storage.stretches import list_stretches

logger = logging.getLogger(__name__)

# A joining channel draining at least this much counts as a real creek or river
# for the gate: with a name it is a tier-1 candidate, without one it still gets
# in. Measured on the 358 Ontario confluences: 178 are >= 25 km, 117 of them
# named. Below it and unnamed, a junction is held back (tier 3).
BIG_JOINING_KM = 25.0
# Words for the size of the water: under SMALL_KM "a small creek", under
# LARGE_KM "a creek", above it "a river". 77 of the 358 drain 100 km or more.
SMALL_KM = 25.0
LARGE_KM = 100.0

# How far from a spot to look for a way in, and how many of each to keep.
GETTING_THERE_RADIUS_M = 1500.0
BRIDGE_RADIUS_M = 1000.0
GETTING_THERE_PER_KIND = 2
BRIDGES_KEPT = 3

# Fallback for a stretch's lake when the curation file names none: its `region`,
# but only if it is itself one of these.
LAKE_NAMES = frozenset(
    {
        "Lake Ontario",
        "Lake Erie",
        "Lake Huron",
        "Lake St. Clair",
        "Georgian Bay",
        "Lake Simcoe",
        "Bay of Quinte",
    }
)

_GETTING_THERE_KINDS = ("parking", "trail_head", "boat_launch")
_ACCESS_TAGS = ("access", "fee", "capacity", "operator", "surface", "ref")
_M_PER_DEG_LAT = 111_320.0
_DAM_WORD = re.compile(r"\b(dam|weir|lock)\b", re.IGNORECASE)
_KIND_ORDER = {kind: i for i, kind in enumerate(words.KIND_ORDER)}


# ── curation hints ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StretchHints:
    """What the curation file says about a stretch that the topology cannot.

    `lake` names the water a river mouth opens onto; `mouth` is a (lat, lon)
    for a stretch whose mouth the network does not show.
    """

    lake: str | None = None
    mouth: tuple[float, float] | None = None


def load_stretch_hints(curation_path: Path = DEFAULT_CURATION_PATH) -> dict[str, StretchHints]:
    """The optional `lake` and `mouth` keys of each curated stretch, by stretch id.

    Read leniently: the keys are added to the curation file by a separate
    change, so a file or schema without them yields no hints, not an error.
    """
    hints: dict[str, StretchHints] = {}
    for cs in load_curation(curation_path).stretches:
        lake, mouth = _opt(cs, "lake"), _opt(cs, "mouth")
        point = None
        if mouth is not None:
            lat, lon = _opt(mouth, "lat"), _opt(mouth, "lon", "lng")
            if lat is not None and lon is not None:
                point = (float(lat), float(lon))
            lake = lake or _opt(mouth, "lake")
        if lake or point:
            hints[cs.id] = StretchHints(lake=str(lake) if lake else None, mouth=point)
    return hints


def _opt(obj, *keys):
    for key in keys:
        value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        if value is not None:
            return value
    return None


# ── classify ──────────────────────────────────────────────────────────────────


def classify(
    anchors: list[StretchAnchor],
    stretches: list[FishingStretch],
    jurisdiction: JurisdictionCode,
    hints: dict[str, StretchHints] | None = None,
    built_at: str | None = None,
) -> SpotClassification:
    """Spots from anchors: merged, deduped, titled, gated and tiered. Pure.

    Returns the spots and a report counting what was dropped, what was held
    back at tier 3, and which titles had to fall back to a generic word.
    """
    built_at = built_at or datetime.now(UTC).isoformat(timespec="seconds")
    hints = hints or {}
    report = SpotBuildReport(jurisdiction=jurisdiction, anchors_in=len(anchors))
    ctx = _Context(
        stretches={s.stretch_id: s for s in stretches},
        hints=hints,
        report=report,
        built_at=built_at,
        jurisdiction=jurisdiction,
    )

    spots = (
        _river_mouths(ctx, [a for a in anchors if a.anchor_type == "lake_mouth"])
        + _creek_junctions(ctx, [a for a in anchors if a.anchor_type == "confluence"])
        + _below_dams(ctx, [a for a in anchors if a.anchor_type in ("dam", "tailwater")])
        + _public_fishing(
            ctx,
            [a for a in anchors if a.anchor_type == "access_point" and a.subtype == "fishing_spot"],
        )
    )

    by_id: dict[str, Spot] = {}
    for spot in spots:
        if spot.spot_id in by_id:
            report.drop("duplicate_spot_id")
            continue
        by_id[spot.spot_id] = spot
    final = sorted(by_id.values(), key=lambda s: (_KIND_ORDER[s.kind], s.tier, s.spot_id))

    _attach_access(ctx, final, anchors)

    for spot in final:
        report.by_kind[spot.kind] = report.by_kind.get(spot.kind, 0) + 1
        report.by_tier[str(spot.tier)] = report.by_tier.get(str(spot.tier), 0) + 1
    report.spots_total = len(final)
    return SpotClassification(spots=final, report=report)


@dataclass
class _Context:
    stretches: dict[str, FishingStretch]
    hints: dict[str, StretchHints]
    report: SpotBuildReport
    built_at: str
    jurisdiction: JurisdictionCode

    def river(self, stretch_id: str) -> str:
        """The river's name for a title; the stretch's place names if it has none."""
        s = self.stretches.get(stretch_id)
        if s is None:
            return "this river"
        return s.river.strip() or s.name

    def water(self, stretch_id: str) -> tuple[str | None, str | None]:
        """(water_name, subtitle) for the river section a spot sits on."""
        s = self.stretches.get(stretch_id)
        if s is None:
            return None, None
        river = self.river(stretch_id)
        subtitle = s.name if river.lower() in s.name.lower() else f"{river} · {s.name}"
        return s.name, subtitle


def _the(name: str) -> str:
    """'the Credit River', but 'Bronte Creek' — the way anglers say them."""
    if re.search(r"\briver$", name, re.IGNORECASE) and not name.lower().startswith("the "):
        return f"the {name}"
    return name


def _lake_phrase(lake: str) -> str:
    """'Lake Ontario' and 'Georgian Bay' stand alone; 'the Bay of Quinte' does not."""
    return f"the {lake}" if lake.startswith("Bay of") else lake


def _size_class(km: float) -> str:
    return "small" if km < SMALL_KM else "medium" if km < LARGE_KM else "large"


def _spot(
    ctx: _Context,
    kind: SpotKind,
    lat: float,
    lon: float,
    stretch_id: str | None,
    title: str,
    tier: SpotTier,
    structure_type: str,
    structure_attrs: dict,
    signals: dict,
    sources: list[SpotSource],
) -> Spot:
    water_name, subtitle = ctx.water(stretch_id) if stretch_id else (None, None)
    return Spot(
        spot_id=f"{kind}:{_coord_key(lon, lat)}",
        kind=kind,
        jurisdiction=ctx.jurisdiction,
        lat=lat,
        lng=lon,
        water_id=stretch_id,
        water_name=water_name,
        title=title,
        subtitle=subtitle,
        tier=tier,
        structure_type=structure_type,
        structure_attrs=structure_attrs,
        signals=signals,
        sources=sources,
        built_at=ctx.built_at,
    )


def _by_place(anchors: list[StretchAnchor]) -> dict[str, list[StretchAnchor]]:
    """Anchors grouped by rounded coordinates, deterministic order inside each."""
    groups: dict[str, list[StretchAnchor]] = defaultdict(list)
    for a in sorted(anchors, key=lambda a: (a.stretch_id, a.anchor_id)):
        groups[_coord_key(a.lon, a.lat)].append(a)
    return groups


# ── river mouths ──────────────────────────────────────────────────────────────


def _river_mouths(ctx: _Context, mouths: list[StretchAnchor]) -> list[Spot]:
    out: list[Spot] = []
    for group in _by_place(mouths).values():
        first = group[0]
        ctx.report.drop("river_mouth_duplicate_location", len(group) - 1)
        out.append(
            _mouth_spot(ctx, first, group, (first.lat, first.lon), first.name, "ohn_topology")
        )

    # A curated mouth fills in for a stretch the topology found none on; it
    # never moves a mouth the topology did find.
    have = {a.stretch_id for a in mouths}
    for stretch_id, hint in sorted(ctx.hints.items()):
        if hint.mouth is None or stretch_id in have or stretch_id not in ctx.stretches:
            continue
        out.append(_mouth_spot(ctx, None, [], hint.mouth, None, "curation", stretch_id))
    return out


def _mouth_spot(
    ctx: _Context,
    anchor: StretchAnchor | None,
    group: list[StretchAnchor],
    point: tuple[float, float],
    shore_name: str | None,
    source: str,
    stretch_id: str | None = None,
) -> Spot:
    stretch_id = stretch_id or (anchor.stretch_id if anchor else None)
    assert stretch_id is not None
    lake = ctx.hints.get(stretch_id, StretchHints()).lake or shore_name
    if lake is None:
        region = ctx.stretches[stretch_id].region if stretch_id in ctx.stretches else None
        lake = region if region in LAKE_NAMES else None
    river = _the(ctx.river(stretch_id))
    if lake:
        title = words.TITLES["river_mouth"].format(river=river, lake=_lake_phrase(lake))
    else:
        title = words.TITLES["river_mouth_lake_unknown"].format(river=river)
        ctx.report.fallback("river_mouth_no_lake_name")
    return _spot(
        ctx,
        "river_mouth",
        point[0],
        point[1],
        stretch_id,
        title,
        1,
        "lake_mouth",
        {
            "lake": lake,
            **({"shore_ogf_ids": anchor.attributes.get("shore_ogf_ids", [])} if anchor else {}),
        },
        {"named": lake is not None},
        [
            SpotSource(
                kind=source,
                ref=anchor.source_ref if anchor else f"curation:{stretch_id}",
                anchor_ids=[a.anchor_id for a in group],
                built_at=ctx.built_at,
            )
        ],
    )


# ── creek junctions ───────────────────────────────────────────────────────────


def _creek_junctions(ctx: _Context, confluences: list[StretchAnchor]) -> list[Spot]:
    out: list[Spot] = []
    for group in _by_place(confluences).values():
        ctx.report.drop("confluence_duplicate_location", len(group) - 1)
        out.append(_junction_spot(ctx, group))
    return out


def _junction_spot(ctx: _Context, group: list[StretchAnchor]) -> Spot:
    """One junction from every anchor that lists it.

    The same node is a confluence of the stretch the creek joins (the creek is
    the joining channel) and of the creek's own stretch if it has one (the river
    is). Prefer the first: it names the creek by OHN and puts the river in the
    title. The second still supplies the creek's name when OHN left it blank.
    """
    main = sorted(
        group,
        key=lambda a: (
            not a.attributes.get("stretch_flows_on", True),
            a.name is None,
            -a.attributes.get("joining_upstream_km", 0.0),
            a.stretch_id,
        ),
    )[0]
    flows_on = main.attributes.get("stretch_flows_on", True)
    attrs = dict(main.attributes)
    also_on = sorted({a.stretch_id for a in group} - {main.stretch_id})
    if also_on:
        attrs["also_on_water_ids"] = also_on

    if flows_on:
        tributary = main.name
        if tributary is None:
            # OHN never named the creek; its own stretch may name it.
            for a in group:
                if not a.attributes.get("stretch_flows_on", True) and a.stretch_id in ctx.stretches:
                    tributary = ctx.river(a.stretch_id)
                    attrs["tributary_name_from_water_id"] = a.stretch_id
                    break
        km = float(main.attributes.get("joining_upstream_km", 0.0))
        river = _the(ctx.river(main.stretch_id))
        if tributary:
            title = words.TITLES["creek_junction"].format(tributary=_the(tributary), river=river)
        else:
            title = words.TITLES["creek_junction"].format(
                tributary=words.UNNAMED_TRIBUTARY[_size_class(km)], river=river
            )
            ctx.report.fallback("creek_junction_unnamed_tributary")
        named = tributary is not None
    else:
        # The stretch is the creek and ends in a bigger water.
        km = float(main.attributes.get("stretch_upstream_km", 0.0))
        larger = _the(main.name) if main.name else words.UNNAMED_LARGER_RIVER
        title = words.TITLES["creek_junction_stretch_ends"].format(
            river=_the(ctx.river(main.stretch_id)), larger=larger
        )
        if not main.name:
            ctx.report.fallback("creek_junction_unnamed_larger_river")
        named = True

    big = km >= BIG_JOINING_KM
    tier: SpotTier
    if named and big:
        tier = 1
    elif named or big:
        tier = 2
    else:
        tier = 3
        ctx.report.hold_back("confluence_unnamed_and_small")
    return _spot(
        ctx,
        "creek_junction",
        main.lat,
        main.lon,
        main.stretch_id,
        title,
        tier,
        "confluence",
        attrs,
        {"named": named, "size_class": _size_class(km)},
        _sources(ctx, group),
    )


def _sources(ctx: _Context, anchors: list[StretchAnchor]) -> list[SpotSource]:
    return [
        SpotSource(kind=a.source, ref=a.source_ref, anchor_ids=[a.anchor_id], built_at=ctx.built_at)
        for a in anchors
    ]


# ── below dams ────────────────────────────────────────────────────────────────


def _below_dams(ctx: _Context, anchors: list[StretchAnchor]) -> list[Spot]:
    """One spot per physical dam: its dam rows and the tailwater under them, merged."""
    dams = sorted((a for a in anchors if a.anchor_type == "dam"), key=lambda a: a.anchor_id)
    tails = [a for a in anchors if a.anchor_type == "tailwater"]

    groups: list[list[StretchAnchor]] = []
    for dam in dams:
        home = next(
            (
                g
                for g in groups
                if any(
                    d.source_ref == dam.source_ref
                    or _metres(d.lat, d.lon, dam.lat, dam.lon) <= DAM_MERGE_M
                    for d in g
                )
            ),
            None,
        )
        if home is None:
            groups.append([dam])
        else:
            home.append(dam)
    refs = {a.source_ref: g for g in groups for a in g}
    orphans: dict[str, list[StretchAnchor]] = defaultdict(list)
    for tail in sorted(tails, key=lambda a: a.anchor_id):
        ref = tail.attributes.get("dam_source_ref", tail.source_ref)
        if ref in refs:
            refs[ref].append(tail)
        else:
            orphans[ref].append(tail)
    groups += list(orphans.values())

    out: list[Spot] = []
    for group in groups:
        group_dams = [a for a in group if a.anchor_type == "dam"]
        group_tails = [a for a in group if a.anchor_type == "tailwater"]
        kinds = {a.subtype for a in group_dams} | {
            t.attributes.get("dam_kind") for t in group_tails
        }
        kind = next((k for k in ("dam", "weir") if k in kinds), None)
        if kind is None:
            ctx.report.drop("dam_is_lock")
            continue
        name = next((a.name for a in group_dams if a.name), None) or next(
            (re.sub(r"^Below\s+", "", t.name) for t in group_tails if t.name), None
        )
        if not group_tails and name is None:
            ctx.report.drop("dam_unverified_and_unnamed")
            continue
        out.append(_dam_spot(ctx, group_dams, group_tails, kind, name))
    return out


def _dam_label(name: str, kind: str) -> str:
    name = re.sub(r"^the\s+", "", name, flags=re.IGNORECASE)
    return f"the {name}" if _DAM_WORD.search(name) else f"the {name} {kind}"


def _dam_spot(
    ctx: _Context,
    dams: list[StretchAnchor],
    tails: list[StretchAnchor],
    kind: str,
    name: str | None,
) -> Spot:
    # The marker sits on the water below the dam, which may belong to the next
    # stretch down; the spot is listed with the water it is in.
    anchor = tails[0] if tails else dams[0]
    river = _the(ctx.river(anchor.stretch_id))
    verified = bool(tails)
    key = ("below_dam" if verified else "at_dam") + ("" if name else "_unnamed")
    title = words.TITLES[key].format(
        dam=_dam_label(name, kind) if name else "", dam_kind=kind, river=river
    )
    if name is None:
        ctx.report.fallback("below_dam_unnamed" if verified else "at_dam_unnamed")
    attrs = {"dam_kind": kind, "tailwater_verified": verified}
    for a in dams:
        for tag in ("height", "operator", "also_mapped_as"):
            if tag in a.attributes and tag not in attrs:
                attrs[tag] = a.attributes[tag]
    if tails:
        attrs["reach_m"] = tails[0].attributes.get("reach_m")
    return _spot(
        ctx,
        "below_dam",
        anchor.lat,
        anchor.lon,
        anchor.stretch_id,
        title,
        1 if name else 2,
        kind,
        attrs,
        {"named": name is not None},
        _sources(ctx, tails + dams),
    )


# ── public fishing spots ──────────────────────────────────────────────────────


def _public_fishing(ctx: _Context, anchors: list[StretchAnchor]) -> list[Spot]:
    out: list[Spot] = []
    for group in _by_place(anchors).values():
        ctx.report.drop("public_fishing_duplicate_location", len(group) - 1)
        a = group[0]
        river = _the(ctx.river(a.stretch_id))
        if a.name:
            title = words.TITLES["public_fishing"].format(name=a.name)
        else:
            title = words.TITLES["public_fishing_unnamed"].format(river=river)
            ctx.report.fallback("public_fishing_unnamed")
        out.append(
            _spot(
                ctx,
                "public_fishing",
                a.lat,
                a.lon,
                a.stretch_id,
                title,
                1,
                "fishing_spot",
                {k: v for k, v in a.attributes.items() if k in _ACCESS_TAGS},
                {"named": a.name is not None},
                [
                    SpotSource(
                        kind=a.source,
                        ref=a.source_ref,
                        anchor_ids=[g.anchor_id for g in group],
                        built_at=ctx.built_at,
                    )
                ],
            )
        )
    return out


# ── getting there ─────────────────────────────────────────────────────────────


def _metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular distance — accurate to well under 1% at this scale."""
    dy = (lat2 - lat1) * _M_PER_DEG_LAT
    dx = (lon2 - lon1) * _M_PER_DEG_LAT * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def _attach_access(ctx: _Context, spots: list[Spot], anchors: list[StretchAnchor]) -> None:
    """Hang the nearest parking, trailheads, launches and crossings on each spot.

    The same OSM element turns up once per stretch whose corridor it falls in;
    keep one of each. Nothing here becomes a spot, and no distance is a score.
    """
    seen: set[tuple[str, str, str]] = set()
    pool: list[StretchAnchor] = []
    for a in sorted(anchors, key=lambda a: (a.stretch_id, a.anchor_id)):
        if a.anchor_type != "access_point" or a.subtype in (None, "fishing_spot"):
            continue
        key = (a.subtype, a.source_ref, f"{a.lat:.4f},{a.lon:.4f}")
        if key in seen:
            continue
        seen.add(key)
        pool.append(a)
    ctx.report.access_anchors = len(pool)

    for spot in spots:
        near = sorted(
            (
                (d, a)
                for a in pool
                if (d := _metres(spot.lat, spot.lng, a.lat, a.lon)) <= GETTING_THERE_RADIUS_M
            ),
            key=lambda t: (t[0], t[1].anchor_id),
        )
        kept: list[AccessRef] = []
        for kind in _GETTING_THERE_KINDS:
            kept += [_access_ref(a, d) for d, a in near if a.subtype == kind][
                :GETTING_THERE_PER_KIND
            ]
        kept.sort(key=lambda r: (r.distance_m, r.source_ref))
        bridges = [_access_ref(a, d) for d, a in near if a.subtype == "road_crossing"]
        spot.getting_there = kept
        spot.bridges = [r for r in bridges if r.distance_m <= BRIDGE_RADIUS_M][:BRIDGES_KEPT]
        for kind, field in (("parking", "nearest_parking_m"), ("boat_launch", "nearest_launch_m")):
            first = next((r for r in kept if r.kind == kind), None)
            if first:
                spot.signals[field] = first.distance_m
        if spot.bridges:
            spot.signals["nearest_bridge_m"] = spot.bridges[0].distance_m
        if not spot.getting_there and not spot.bridges:
            ctx.report.spots_without_access += 1


def _access_ref(a: StretchAnchor, distance_m: float) -> AccessRef:
    return AccessRef(
        kind=a.subtype,  # type: ignore[arg-type]
        name=a.name,
        lat=a.lat,
        lng=a.lon,
        distance_m=round(distance_m),
        source_ref=a.source_ref,
        attributes={k: v for k, v in a.attributes.items() if k in _ACCESS_TAGS},
    )


# ── build ─────────────────────────────────────────────────────────────────────


def build_spots(db: Database, curation_path: Path = DEFAULT_CURATION_PATH) -> SpotBuildReport:
    """Rebuild every spot for the curation file's jurisdiction from the stored anchors.

    Run it after `build_anchors` (the CLI does). A rebuild replaces the
    jurisdiction's spots wholesale, in one transaction: a spot that stopped
    being derived leaves the map, and the map never reads a half-written one.
    Ids come from coordinates, so rebuilding twice gives the same spots.
    """
    curation = load_curation(curation_path)
    jurisdiction = curation.jurisdiction
    anchors = list_anchors(db, jurisdiction=jurisdiction)
    stretches = list_stretches(db, jurisdiction)
    result = classify(anchors, stretches, jurisdiction, load_stretch_hints(curation_path))
    replace_spots(db, jurisdiction, result.spots)

    report = result.report
    if not anchors:
        logger.warning(
            "no stretch anchors stored for %s: spots are empty; run build-stretches first",
            jurisdiction,
        )
    if n := report.title_fallbacks.get("river_mouth_no_lake_name"):
        logger.warning(
            "%d of %d river mouths have no lake name; add `lake` to their curation entries",
            n,
            report.by_kind.get("river_mouth", 0),
        )
    return report


# ── read side ─────────────────────────────────────────────────────────────────


def spots_geojson(
    db: Database,
    bbox: tuple[float, float, float, float] | None = None,
    kinds: list[str] | None = None,
    max_tier: int = 2,
    jurisdiction: str | None = None,
) -> dict:
    """Spots as a GeoJSON FeatureCollection of points, light enough for a whole map.

    `max_tier` is cumulative: 1 is the headline spots only, 3 is everything.
    `kinds` counts in the `meta` are taken before the kind filter, so the
    filter chips can show how many each would add.
    """
    in_view = list_spots(db, bbox=bbox, max_tier=max_tier, jurisdiction=jurisdiction)
    counts = {kind: 0 for kind in words.KIND_ORDER}
    for s in in_view:
        counts[s.kind] += 1
    shown = [s for s in in_view if not kinds or s.kind in kinds]
    shown.sort(key=lambda s: (s.tier, _KIND_ORDER[s.kind], s.spot_id))
    return {
        "type": "FeatureCollection",
        "meta": {"count": len(shown), "kind_counts": counts, "max_tier": max_tier},
        "features": [
            {
                "type": "Feature",
                "id": s.spot_id,
                "geometry": {"type": "Point", "coordinates": [s.lng, s.lat]},
                "properties": {
                    "spot_id": s.spot_id,
                    "kind": s.kind,
                    "title": s.title,
                    "subtitle": s.subtitle,
                    "tier": s.tier,
                    "jurisdiction": s.jurisdiction,
                    "water_id": s.water_id,
                    "water_name": s.water_name,
                    "signals": s.signals,
                },
            }
            for s in shown
        ],
    }


def get_spot_detail(db: Database, spot_id: str) -> dict | None:
    """One spot in full, with its sibling spots on the same river for 'more spots on this river'."""
    spot = get_spot(db, spot_id)
    if spot is None:
        return None
    others = [
        {"spot_id": s.spot_id, "kind": s.kind, "title": s.title, "tier": s.tier}
        for s in list_spots(db, max_tier=3, water_id=spot.water_id)
        if s.spot_id != spot.spot_id and spot.water_id
    ]
    others.sort(key=lambda o: (o["tier"], _KIND_ORDER[o["kind"]], o["spot_id"]))
    return {**spot.model_dump(), "more_on_this_water": others}
