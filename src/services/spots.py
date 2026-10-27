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

import importlib
import logging
import math
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from sqlite_utils.db import Database

from src.models.bathymetry import BathymetryContour
from src.models.hydrology import StreamSegment
from src.models.ingest_area import IngestArea
from src.models.jurisdiction import JurisdictionCode
from src.models.lake_outline import LakeOutline
from src.models.shoreline_feature import ShorelineFeature
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
from src.models.water_feature import ShoreStructure
from src.services import api_vocab
from src.services import spot_labels as words
from src.services.bathymetry import (
    BathymetryFeatureSet,
    LakeBathymetry,
    derive_bathymetry_features,
)
from src.services.quiet_water import QuietWaterBuild, build_quiet_water
from src.services.shoreline_features import derive_shoreline_features
from src.services.spot_repeats import distinguish_titles, merge_repeats
from src.services.stretch_anchors import DAM_MERGE_M, _coord_key
from src.services.stretches import DEFAULT_CURATION_PATH, load_curation
from src.services.water_bodies import LakeRef, WaterBodySet, derive_water_bodies, slugify
from src.storage.spots import count_spots_of_kind, get_spot, list_spots, replace_spots
from src.storage.stretch_anchors import list_anchors
from src.storage.stretches import list_stretches
from src.storage.water_bodies import get_water_body

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

# `near=` with no radius uses DEFAULT_NEAR_KM; a larger one is cut to MAX_NEAR_KM,
# and the response says so (`meta.near.radius_capped`).
DEFAULT_NEAR_KM = 50.0
MAX_NEAR_KM = 200.0

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
    """The optional `lake` and `mouth` keys of each curated stretch, by stretch id."""
    hints: dict[str, StretchHints] = {}
    for cs in load_curation(curation_path).stretches:
        lake = cs.lake or (cs.mouth.lake if cs.mouth else None)
        point = (cs.mouth.lat, cs.mouth.lon) if cs.mouth else None
        if lake or point:
            hints[cs.id] = StretchHints(lake=lake, mouth=point)
    return hints


# ── classify ──────────────────────────────────────────────────────────────────


def classify(
    anchors: list[StretchAnchor],
    stretches: list[FishingStretch],
    jurisdiction: JurisdictionCode,
    hints: dict[str, StretchHints] | None = None,
    built_at: str | None = None,
    outlines: dict[str, LakeOutline] | None = None,
    watercourses: list[StreamSegment] | None = None,
    bathymetry: dict[str, LakeBathymetry] | None = None,
    shore_structures: list[ShoreStructure] | None = None,
) -> SpotClassification:
    """Spots from anchors: merged, deduped, titled, gated and tiered. Pure.

    Returns the spots and a report counting what was dropped, what was held
    back at tier 3, and which titles had to fall back to a generic word.

    `outlines` maps a curated lake name to its resolved OHN outline,
    `watercourses` is the OHN watercourse layer and `shore_structures` the OSM
    piers, breakwaters and harbours at the lakes' shores, all supplied by the
    caller (build_spots does the network) so this function stays pure. With
    them, the lakes get their shore geometry and their shoreline features.
    `bathymetry` maps a lake name to its surveyed contours, likewise fetched by
    the caller, for the drop-offs and humps read off the bottom.
    """
    built_at = built_at or datetime.now(UTC).isoformat(timespec="seconds")
    hints = hints or {}
    outlines = outlines or {}
    bathymetry = bathymetry or {}
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
    merged = merge_repeats(list(by_id.values()), report)
    final = sorted(merged, key=lambda s: (_KIND_ORDER[s.kind], s.tier, s.spot_id))

    _attach_access(ctx, final, anchors)
    distinguish_titles(final, report)
    water_bodies, features, contours = _link_water_bodies(
        ctx, final, outlines, watercourses or [], shore_structures or [], bathymetry
    )

    for spot in final:
        report.by_kind[spot.kind] = report.by_kind.get(spot.kind, 0) + 1
        report.by_tier[str(spot.tier)] = report.by_tier.get(str(spot.tier), 0) + 1
    report.spots_total = len(final)
    return SpotClassification(
        spots=final,
        water_bodies=water_bodies.bodies,
        shoreline_features=features,
        bathymetry_contours=contours,
        report=report,
    )


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
        lead = None if river.lower() in s.name.lower() else river
        subtitle = words.subtitle_join(lead, s.name)
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
            _mouth_spot(
                ctx,
                first,
                group,
                (first.lat, first.lon),
                first.name,
                "curation" if first.source == "curated" else "ohn_topology",
            )
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
    lake = ctx.hints.get(stretch_id, StretchHints()).lake
    lake_source = "curated" if lake else None
    if lake is None and shore_name:
        # A curated mouth anchor carries the lake name the curation file gave it.
        lake, lake_source = (
            shore_name,
            "curated" if anchor and anchor.source == "curated" else "ohn",
        )
    if lake is None:
        region = ctx.stretches[stretch_id].region if stretch_id in ctx.stretches else None
        if region in LAKE_NAMES:
            lake, lake_source = region, "curated_region"
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
            "lake_name_source": lake_source,
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
        if tributary:
            attrs["tributary_name"] = tributary
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


# ── water bodies ──────────────────────────────────────────────────────────────


def _link_water_bodies(
    ctx: _Context,
    spots: list[Spot],
    outlines: dict[str, LakeOutline],
    watercourses: list[StreamSegment],
    shore_structures: list[ShoreStructure],
    bathymetry: dict[str, LakeBathymetry] | None = None,
) -> tuple[WaterBodySet, list[ShorelineFeature], list[BathymetryContour]]:
    """Derive the water bodies and hang each spot off its river, creek or lake.

    A spot is a child of the river its stretch belongs to. A river mouth is also
    on the lake it opens onto, and a junction listed under two stretches is also on
    the other one's river. The spot's own `water_id` (the stretch) is left as it is.

    The lakes' outlines (`outlines`) give the lake bodies their geometry, and the
    shoreline features are derived from those outlines next (`watercourses` names
    the inlets and outlets; `shore_structures` are the OSM piers, breakwaters and
    harbours on the shore). `bathymetry` holds each lake's surveyed contours, and
    the drop-offs and humps are read off them; the raw contours are returned so
    the build stores the evidence with the features.
    """
    lakes = [
        LakeRef(
            name=lake,
            name_source=s.structure_attrs.get("lake_name_source") or "curated",
            stretch_id=s.water_id,
            lat=s.lat,
            lon=s.lng,
        )
        for s in spots
        if s.kind == "river_mouth" and s.water_id and (lake := s.structure_attrs.get("lake"))
    ]
    outlines = {slugify(name): outline for name, outline in outlines.items() if slugify(name)}
    bodies = derive_water_bodies(
        list(ctx.stretches.values()), lakes, ctx.jurisdiction, ctx.built_at, outlines
    )

    for spot in spots:
        spot.water_body_id = bodies.river_id(spot.water_id)
        also = {bodies.river_id(sid) for sid in spot.structure_attrs.get("also_on_water_ids", [])}
        if spot.kind == "river_mouth":
            also.add(bodies.lake_id(spot.structure_attrs.get("lake")))
        spot.also_on_water_body_ids = sorted(i for i in also if i and i != spot.water_body_id)
        if spot.water_body_id:
            ctx.report.spots_linked += 1
        else:
            ctx.report.spots_without_water_body += 1

    report = ctx.report
    report.water_bodies_total = len(bodies.bodies)
    for wb in bodies.bodies:
        report.water_bodies_by_type[wb.type] = report.water_bodies_by_type.get(wb.type, 0) + 1
        report.water_body_names[wb.name_source] = report.water_body_names.get(wb.name_source, 0) + 1
        if wb.geometry is None:
            report.water_bodies_without_geometry += 1

    features: list[ShorelineFeature] = []
    contours: list[BathymetryContour] = []
    report.shore_structures_fetched = len(shore_structures)
    if outlines:
        lake_to_wb_id = {slug: bodies.lake_id(slug) for slug in outlines}
        shoreline_set = derive_shoreline_features(
            outlines,
            {slug: wb_id for slug, wb_id in lake_to_wb_id.items() if wb_id},
            ctx.jurisdiction,
            ctx.built_at,
            watercourses,
            shore_structures,
        )
        features = shoreline_set.features
        report.lakes_with_outline = shoreline_set.lakes_with_outline
        report.lakes_without_outline = shoreline_set.lakes_without_outline
        for reason, n in shoreline_set.dropped.items():
            report.shoreline_features_dropped[reason] = (
                report.shoreline_features_dropped.get(reason, 0) + n
            )

        bathymetry = {
            slugify(name): lake for name, lake in (bathymetry or {}).items() if slugify(name)
        }
        bathymetry_set: BathymetryFeatureSet = derive_bathymetry_features(
            outlines,
            {slug: wb_id for slug, wb_id in lake_to_wb_id.items() if wb_id},
            ctx.jurisdiction,
            ctx.built_at,
            bathymetry,
        )
        features = features + bathymetry_set.features
        contours = bathymetry_set.contours
        report.lakes_with_survey_contours = bathymetry_set.lakes_with_contours
        report.lakes_without_survey_contours = bathymetry_set.lakes_without_contours
        report.bathymetry_contours_stored = len(contours)
        for reason, n in bathymetry_set.dropped.items():
            report.shoreline_features_dropped[reason] = (
                report.shoreline_features_dropped.get(reason, 0) + n
            )

    report.shoreline_features_total = len(features)
    for feature in features:
        report.shoreline_features_by_kind[feature.kind] = (
            report.shoreline_features_by_kind.get(feature.kind, 0) + 1
        )
    return bodies, features, contours


# ── build ─────────────────────────────────────────────────────────────────────


def build_spots(
    db: Database,
    curation_path: Path = DEFAULT_CURATION_PATH,
    *,
    outline_resolver: Callable[[dict[str, list[tuple[float, float]]]], dict[str, LakeOutline]]
    | None = None,
    watercourse_fetcher: Callable[[float, float, float, float], list[StreamSegment]] | None = None,
    bathymetry_fetcher: Callable[[LakeOutline], LakeBathymetry] | None = None,
    structure_fetcher: Callable[
        [list[tuple[float, float, float, float]]], tuple[list[ShoreStructure], int]
    ]
    | None = None,
    quiet_water_areas: list[IngestArea] | None = None,
) -> SpotBuildReport:
    """Rebuild every spot for the curation file's jurisdiction from the stored anchors.

    Run it after `build_anchors` (the CLI does). A rebuild replaces the
    jurisdiction's spots wholesale, in one transaction: a spot that stopped
    being derived leaves the map, and the map never reads a half-written one.
    Ids come from coordinates, so rebuilding twice gives the same spots.

    The network work — matching each lake's outline from OHN Waterbody, reading
    the watercourses that name its inlets and outlets, the OSM piers, breakwaters
    and harbours on its shore, and the bathymetry that names its drop-offs and
    humps — happens here, through the cached adapters, so a second build is
    offline. Tests inject `outline_resolver`, `watercourse_fetcher`,
    `structure_fetcher` and `bathymetry_fetcher` so they never touch the network.

    Quiet water is the one kind not derived from the anchors: it wraps the
    `explore()` ranking for each ingest area of the jurisdiction
    (`quiet_water_areas` overrides which) and needs the untapped-potential parquet.
    Without it the build still succeeds, with no quiet-water spots and a
    warning, so the rest of the map never waits on a score cache.
    """
    curation = load_curation(curation_path)
    jurisdiction = curation.jurisdiction
    anchors = list_anchors(db, jurisdiction=jurisdiction)
    stretches = list_stretches(db, jurisdiction)
    failures: dict[str, int] = {}
    outlines, watercourses, shore_structures = _lake_outline_inputs(
        anchors, jurisdiction, outline_resolver, watercourse_fetcher, structure_fetcher, failures
    )
    bathymetry = _bathymetry_inputs(outlines, bathymetry_fetcher, failures)
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    result = classify(
        anchors,
        stretches,
        jurisdiction,
        load_stretch_hints(curation_path),
        built_at=built_at,
        outlines=outlines,
        watercourses=watercourses,
        shore_structures=shore_structures,
        bathymetry=bathymetry,
    )
    quiet = build_quiet_water(db, jurisdiction, built_at, quiet_water_areas)
    replace_spots(
        db,
        jurisdiction,
        result.spots + quiet.spots,
        result.water_bodies,
        result.shoreline_features,
        result.bathymetry_contours,
    )

    report = result.report
    for step, n in failures.items():
        report.upstream_failed(step, n)
    if failures:
        logger.warning(
            "%d upstream lake fetches failed and were skipped (%s): the lakes involved are "
            "missing an outline, an inlet or a depth, which says nothing about the water. "
            "Run the build again; only the failed fetches are retried.",
            sum(failures.values()),
            ", ".join(f"{step} x{n}" for step, n in sorted(failures.items())),
        )
    if not anchors:
        logger.warning(
            "no stretch anchors stored for %s: spots are empty; run build-stretches first",
            jurisdiction,
        )
    if report.spots_without_water_body:
        logger.warning(
            "%d of %d spots have no water body (their stretch is unknown to the build)",
            report.spots_without_water_body,
            report.spots_total,
        )
    if n := report.title_fallbacks.get("river_mouth_no_lake_name"):
        logger.warning(
            "%d of %d river mouths have no lake name; add `lake` to their curation entries",
            n,
            report.by_kind.get("river_mouth", 0),
        )
    if report.lakes_without_outline:
        logger.warning(
            "%d curated lakes have no OHN outline; they stay placed by their mouths",
            report.lakes_without_outline,
        )
    # After the warnings above: they talk about anchor-derived spots, and the
    # quiet-water ones have no water body by design.
    _count_quiet_water(report, quiet)
    return report


def _lake_outline_inputs(
    anchors: list[StretchAnchor],
    jurisdiction: JurisdictionCode,
    outline_resolver: Callable[[dict[str, list[tuple[float, float]]]], dict[str, LakeOutline]]
    | None = None,
    watercourse_fetcher: Callable[[float, float, float, float], list[StreamSegment]] | None = None,
    structure_fetcher: Callable[
        [list[tuple[float, float, float, float]]], tuple[list[ShoreStructure], int]
    ]
    | None = None,
    failures: dict[str, int] | None = None,
) -> tuple[dict[str, LakeOutline], list[StreamSegment], list[ShoreStructure]]:
    """Named lake mouths in the anchors → outlines plus the features read off them.

    Returns the matched outlines, the watercourses that name their inlets and
    outlets, and the OSM piers, breakwaters and harbours on their shores. Every
    call in here goes through a cache, so this stays cheap offline after the
    first build. A lake with no curated name gets no outline (an unnamed mouth
    is not enough to match a polygon). The resolver and fetchers are injected in
    tests; the defaults are the cached OHN and OSM adapters.

    One failed fetch costs that lake (or that shore cell) its piece and is counted
    into `failures` (the OSM fetcher returns its skipped-cell count with its
    structures); it never stops the build. The default resolver isolates each lake
    itself and flags it `lookup_failed`; a resolver that raises is caught here.
    """
    from shapely.geometry import shape as _shape

    failures = failures if failures is not None else {}

    mouths_by_lake: dict[str, list[tuple[float, float]]] = {}
    for a in anchors:
        if a.anchor_type == "lake_mouth" and a.name:
            lake = next((n for n in mouths_by_lake if slugify(n) == slugify(a.name)), a.name)
            mouths_by_lake.setdefault(lake, []).append((a.lat, a.lon))
    if not mouths_by_lake:
        return {}, [], []

    if outline_resolver is None:
        from src.services.lake_outlines import resolve_lake_outlines

        outline_resolver = resolve_lake_outlines
    if watercourse_fetcher is None:
        from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses_bbox

        watercourse_fetcher = fetch_watercourses_bbox
    if structure_fetcher is None:
        osm = importlib.import_module("src.ingest.global.osm")

        structure_fetcher = partial(osm.fetch_shore_structures, jurisdiction=jurisdiction)
    try:
        outlines = outline_resolver(mouths_by_lake)
    except Exception:  # noqa: BLE001 - a flaky upstream must not stop the build
        logger.warning("lake outline resolver failed; no lake outlines this run", exc_info=True)
        failures["lake_outline"] = failures.get("lake_outline", 0) + len(mouths_by_lake)
        return {}, [], []
    for outline in outlines.values():
        if outline.lookup_failed:
            step = "lake_outline_widen" if outline.matched else "lake_outline"
            failures[step] = failures.get(step, 0) + 1

    cells: list[tuple[float, float, float, float]] = []
    seen_cells: set[tuple[float, float, float, float]] = set()
    for outline in outlines.values():
        if not outline.matched or not outline.geometry:
            continue
        for cell in _shore_cells(_shape(outline.geometry).boundary):
            if cell not in seen_cells:
                seen_cells.add(cell)
                cells.append(cell)

    watercourses: list[StreamSegment] = []
    seen_ogf: set[int] = set()
    for cell in cells:
        try:
            segments = watercourse_fetcher(*cell)
        except Exception:  # noqa: BLE001 - one flaky tile must not stop the build
            logger.warning("shore watercourse fetch failed for cell %s", cell, exc_info=True)
            failures["shore_watercourses"] = failures.get("shore_watercourses", 0) + 1
            continue
        for seg in segments:
            if seg.ogf_id not in seen_ogf:
                seen_ogf.add(seg.ogf_id)
                watercourses.append(seg)

    shore_structures: list[ShoreStructure] = []
    if cells:
        try:
            shore_structures, cells_failed = structure_fetcher(cells)
            if cells_failed:
                failures["shore_structures"] = failures.get("shore_structures", 0) + cells_failed
        except Exception:  # noqa: BLE001 - a flaky OSM query must not stop the build
            logger.warning("shore structure fetch failed; no piers this run", exc_info=True)
            failures["shore_structures"] = failures.get("shore_structures", 0) + 1
    return outlines, watercourses, shore_structures


def _bathymetry_inputs(
    outlines: dict[str, LakeOutline],
    bathymetry_fetcher: Callable[[LakeOutline], LakeBathymetry] | None = None,
    failures: dict[str, int] | None = None,
) -> dict[str, LakeBathymetry]:
    """Each matched lake's surveyed contours, keyed by curated name.

    Every call goes through a cache, so this stays cheap offline after the first
    build. The fetcher is injected in tests; the default is the cached LIO
    Bathymetry adapter. A lake with no overlapping survey is left out, so the
    derivation counts it as one with no contours. A fetch that fails is counted into
    `failures` and treated the same way for this run, so one lake cannot stop the build.
    """
    failures = failures if failures is not None else {}
    matched = {name: o for name, o in outlines.items() if o.matched and o.geometry}
    if not matched:
        return {}
    if bathymetry_fetcher is None:
        from src.services.bathymetry import fetch_lake_bathymetry

        bathymetry_fetcher = fetch_lake_bathymetry
    out: dict[str, LakeBathymetry] = {}
    for name, outline in matched.items():
        try:
            lake = bathymetry_fetcher(outline)
        except Exception:  # noqa: BLE001 - one flaky survey fetch must not stop the build
            logger.warning("bathymetry fetch failed for %r", name, exc_info=True)
            failures["bathymetry"] = failures.get("bathymetry", 0) + 1
            continue
        if lake.index is not None or lake.contours:
            out[name] = lake
    return out


def _shore_cells(boundary, cell_deg: float = 0.5) -> list[tuple[float, float, float, float]]:
    """Grid cells the shoreline passes through, inset a hair so each asks for one tile.

    Inlets and outlets sit on the shore, so a big lake's open water is never fetched.
    """
    from shapely.geometry import box
    from shapely.prepared import prep

    min_lon, min_lat, max_lon, max_lat = boundary.bounds
    # Test each cell, widened by the same hair, against the raw shoreline. Buffering
    # a Great Lake's whole coastline instead builds a polygon of millions of
    # vertices and takes gigabytes; a prepared intersect keeps memory flat.
    shore = prep(boundary)
    pad = 0.001
    eps = 1e-6
    cells: list[tuple[float, float, float, float]] = []
    lat = math.floor(min_lat / cell_deg) * cell_deg
    while lat <= max_lat:
        lon = math.floor(min_lon / cell_deg) * cell_deg
        while lon <= max_lon:
            cell = (lon + eps, lat + eps, lon + cell_deg - eps, lat + cell_deg - eps)
            if shore.intersects(box(cell[0] - pad, cell[1] - pad, cell[2] + pad, cell[3] + pad)):
                cells.append(cell)
            lon += cell_deg
        lat += cell_deg
    return cells


# ── read side ─────────────────────────────────────────────────────────────────


def _count_quiet_water(report: SpotBuildReport, quiet: QuietWaterBuild) -> None:
    """Fold the quiet-water build into the report: counts, drops, and a missing cache."""
    report.quiet_water_areas = quiet.areas_checked
    report.quiet_water_candidates = quiet.candidates
    report.quiet_water_cache_missing = quiet.cache_missing
    for spot in quiet.spots:
        report.by_kind[spot.kind] = report.by_kind.get(spot.kind, 0) + 1
        report.by_tier[str(spot.tier)] = report.by_tier.get(str(spot.tier), 0) + 1
    report.spots_total += len(quiet.spots)
    for reason, n in quiet.dropped.items():
        report.drop(f"quiet_water_{reason}", n)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371.0 * c


def spots_geojson(
    db: Database,
    bbox: tuple[float, float, float, float] | None = None,
    kinds: list[str] | None = None,
    max_tier: int = 2,
    jurisdiction: str | None = None,
    species: str | None = None,
    near: tuple[float, float, float | None] | None = None,
    new_to_me: bool = False,
    user_id: int | None = None,
) -> dict:
    """Spots as a GeoJSON FeatureCollection of points, light enough for a whole map.

    `new_to_me` keeps only spots with at least one nameable recorded species the
    user (`user_id`) has not caught, and adds `properties.new_to_you` to each. It
    is per-user, computed in this call from that user's confirmed catches, and
    stored nowhere; with no `user_id` it is ignored. It runs a records lookup per
    spot, so pass a `bbox`.

    `max_tier` is cumulative: 1 is the headline spots only, 3 is everything.
    `kinds` counts in the `meta` are taken before the kind filter, so the
    filter chips can show how many each would add. `meta.notes` says, in words,
    why a kind that was asked for has nothing to show when that is a matter of
    the build rather than of the viewport (quiet water needs the explore scores).
    """
    in_view = list_spots(db, bbox=bbox, max_tier=max_tier, jurisdiction=jurisdiction)
    distances: dict[str, float] = {}
    near_meta: dict | None = None
    if near:
        nlat, nlon, requested = near
        radius = DEFAULT_NEAR_KM if requested is None else min(requested, MAX_NEAR_KM)
        for s in in_view:
            distances[s.spot_id] = _haversine_km(nlat, nlon, s.lat, s.lng)
        # The kind counts below describe what is inside the radius, so the chips
        # in near mode add up to what the radius holds.
        in_view = [s for s in in_view if distances[s.spot_id] <= radius]
        near_meta = {
            "lat": nlat,
            "lng": nlon,
            "radius_km": radius,
            "radius_capped": requested is not None and requested > MAX_NEAR_KM,
        }
    counts = {kind: 0 for kind in words.KIND_ORDER}
    for s in in_view:
        counts[s.kind] += 1
    shown = [s for s in in_view if not kinds or s.kind in kinds]
    if species:
        species_l = species.strip().lower()
        # avoid naming matches that reveal listed status - just filter by records presence
        # Only the spots about to be shown are looked up, not every spot built.
        shown = _spots_with_species(db, shown, species_l) if species_l else []
    if near:
        shown.sort(key=lambda s: (distances[s.spot_id], s.spot_id))
    else:
        shown.sort(key=lambda s: (s.tier, _KIND_ORDER[s.kind], s.spot_id))
    new_counts: dict[str, int] = {}
    new_to_me_applied = bool(new_to_me and user_id)
    if new_to_me_applied:
        from src.services.new_to_you import caught_keys
        from src.services.spot_card import new_species_at_spot

        caught_by_jurisdiction: dict[str, frozenset[str]] = {}
        kept = []
        for s in shown:
            caught = caught_by_jurisdiction.get(s.jurisdiction)
            if caught is None:
                caught = caught_by_jurisdiction[s.jurisdiction] = caught_keys(
                    db, user_id, s.jurisdiction
                )
            if n := len(new_species_at_spot(db, s, caught)):
                new_counts[s.spot_id] = n
                kept.append(s)
        shown = kept
    meta: dict = {"count": len(shown), "kind_counts": counts, "max_tier": max_tier}
    if near_meta:
        meta["near"] = near_meta
    notes = []
    if (not kinds or "quiet_water" in kinds) and count_spots_of_kind(db, "quiet_water") == 0:
        notes.append(words.QUIET_WATER_NOT_BUILT)
    if notes:
        meta["notes"] = notes
    if new_to_me_applied:
        meta["new_to_me"] = True
    return {
        "type": "FeatureCollection",
        "meta": meta,
        "features": [
            spot_feature(s, new_counts.get(s.spot_id), distances.get(s.spot_id)) for s in shown
        ],
    }


def spot_feature(
    s: Spot, new_to_you_count: int | None = None, distance_km: float | None = None
) -> dict:
    properties = {
        "spot_id": s.spot_id,
        "kind": s.kind,
        "title": s.title,
        "subtitle": words.normalise_subtitle(s.subtitle),
        "tier": s.tier,
        "jurisdiction": s.jurisdiction,
        "water_id": s.water_id,
        "water_name": s.water_name,
        "water_body_id": s.water_body_id,
        "signals": s.signals,
    }
    # Only quiet water carries caveats today; the key is absent when there are none.
    if caveats := words.caveats_for(s.kind, s.signals):
        properties["caveats"] = caveats
    if new_to_you_count is not None:
        properties["new_to_you"] = _new_to_you_property(new_to_you_count)
    # Present only for a `near` request, where the list is ordered by it.
    if distance_km is not None:
        properties["distance_km"] = round(distance_km, 1)
    return {
        "type": "Feature",
        "id": s.spot_id,
        "geometry": {"type": "Point", "coordinates": [s.lng, s.lat]},
        "properties": properties,
    }


def _new_to_you_property(count: int) -> dict:
    from src.services import spot_labels

    return {"count": count, "text": spot_labels.new_to_you_hook(count)}


def get_spot_detail(db: Database, spot_id: str, user_id: int | None = None) -> dict | None:
    """One spot in full: its facts, the other spots on the same river, and its card.

    The card is the four resolved blocks (why it's interesting, you might find,
    why fish gather here, getting there) with every string written, so the
    front end joins nothing. It is the same for every user except for the
    "new to you" hook and block, which are filled only when `user_id` is given.
    """
    from src.services.spot_card import build_spot_card

    spot = get_spot(db, spot_id)
    if spot is None:
        return None
    others = [
        {"spot_id": s.spot_id, "kind": s.kind, "title": s.title, "tier": s.tier}
        for s in list_spots(db, max_tier=3, water_id=spot.water_id)
        if s.spot_id != spot.spot_id and spot.water_id
    ]
    others.sort(key=lambda o: (o["tier"], _KIND_ORDER[o["kind"]], o["spot_id"]))
    return {
        **api_vocab.spot_for_api(spot.model_dump()),
        "subtitle": words.normalise_subtitle(spot.subtitle),
        "water_body": _water_body_ref(db, spot.water_body_id),
        "also_on_water_bodies": [
            ref for i in spot.also_on_water_body_ids if (ref := _water_body_ref(db, i))
        ],
        "more_on_this_water": others,
        "card": build_spot_card(db, spot, user_id).model_dump(),
    }


def _spots_with_species(db: Database, spots: list[Spot], species_l: str) -> list[Spot]:
    """The given spots with records of the named species within 2 km and no listed fish in the way.

    The records are the cleaned ones the spot card uses (`slices.build_records`):
    hybrids and groups are gone, a hybrid with a listed parent counts as a record
    of that parent, and the angler's own catches are not read, so another user's
    logged stops can never put a spot into this answer.

    Fails closed in three ways, because this filter must never be a way to ask
    where a protected fish lives:
      * a query that names a listed species returns nothing, whatever the records say;
      * a spot is dropped when any record its query matched is listed;
      * a matched record whose name the species file does not know is treated as
        potentially listed, so a scientific-name-only row for a listed fish cannot slip
        through (the file knows 69 species, so the filter answers for those and no more).
    """
    from src.models.context import Place
    from src.services.context import slices
    from src.services.context.species import listing_check
    from src.services.spot_card import NO_USER

    if listing_check(db, scientific_name=species_l, common_name=species_l) is True:
        return []

    # The same few fish are recorded at most spots, so each name is checked against the
    # species file once per request, not once per spot. The answer is the same call.
    listed: dict[tuple[str | None, str | None], bool | None] = {}

    def not_cleared(r) -> bool:
        key = (r.species, r.common_name)
        if key not in listed:
            listed[key] = listing_check(db, scientific_name=r.species, common_name=r.common_name)
        return listed[key] is not False

    found: list[Spot] = []
    for spot in spots:
        place = Place(query=spot.spot_id, lat=spot.lat, lng=spot.lng, radius_km=2.0)
        try:
            # No species filter on the query: a row filed under the scientific name
            # alone does not contain the common name the angler typed, and is only
            # given its common name by the cleaning that runs after the query.
            records = slices.build_records(
                db,
                place,
                species_filter=None,
                user_id=NO_USER,
                escalate=False,
                with_piscivore=False,
            ).species
        except Exception:  # noqa: BLE001 - one spot's lookup must not fail the map
            logger.warning("species filter lookup failed at %s", spot.spot_id, exc_info=True)
            continue
        matched = [r for r in records if species_l in f"{r.species} {r.common_name or ''}".lower()]
        if not matched:
            continue
        # True = listed, None = the species file does not know it: neither may pass.
        if any(not_cleared(r) for r in matched):
            continue
        found.append(spot)
    return found


def _water_body_ref(db: Database, water_body_id: str | None) -> dict | None:
    """The short form of a water body for a spot card: enough to name it and link to it."""
    wb = get_water_body(db, water_body_id) if water_body_id else None
    if wb is None:
        return None
    return {
        "water_body_id": wb.water_body_id,
        "name": wb.name,
        "display_name": wb.name or words.WATER_BODY_TYPES[wb.type]["unnamed"],
        "name_source": wb.name_source,
        "type": wb.type,
    }


def _access_layer_geojson(
    db: Database,
    attr: str,
    bbox: tuple[float, float, float, float] | None,
    jurisdiction: str | None,
) -> dict:
    """One access layer (parking, bridges) as GeoJSON, one feature per mapped element.

    The id is the kind plus the OSM element, so it is the same on every request. An
    element reached from several spots is kept once, against the nearest, whose
    water gives the fallback name when OSM named nothing ("Parking near the Credit
    River"). `meta.kind_counts` counts every kind the layer holds, zeros included.
    """
    spots = list_spots(db, bbox=None, max_tier=3, jurisdiction=jurisdiction)
    nearest: dict[str, tuple[int, AccessRef, Spot]] = {}
    for spot in spots:
        for a in getattr(spot, attr):
            if bbox is not None:
                min_lon, min_lat, max_lon, max_lat = bbox
                if not (min_lon <= a.lng <= max_lon and min_lat <= a.lat <= max_lat):
                    continue
            key = f"{a.kind}:{a.source_ref}"
            if key not in nearest or a.distance_m < nearest[key][0]:
                nearest[key] = (a.distance_m, a, spot)
    water_names: dict[str | None, str | None] = {}
    features = []
    for feature_id in sorted(nearest):
        _, a, spot = nearest[feature_id]
        if spot.water_body_id not in water_names:
            wb = get_water_body(db, spot.water_body_id) if spot.water_body_id else None
            water_names[spot.water_body_id] = wb.name if wb else None
        water = water_names[spot.water_body_id]
        name = a.name or words.access_fallback_name(a.kind, _the(water) if water else None)
        features.append(
            {
                "type": "Feature",
                "id": feature_id,
                "geometry": {"type": "Point", "coordinates": [a.lng, a.lat]},
                "properties": {
                    "feature_id": feature_id,
                    "kind": a.kind,
                    "name": name,
                    "name_source": "osm" if a.name else "fallback",
                    "jurisdiction": spot.jurisdiction,
                    "attributes": a.attributes,
                },
            }
        )
    kind_counts = {k: 0 for k in words.LAYERS[attr]["kinds"]}
    for f in features:
        kind_counts[f["properties"]["kind"]] += 1
    return {
        "type": "FeatureCollection",
        "meta": {"count": len(features), "kind_counts": kind_counts},
        "features": features,
    }


def getting_there_geojson(
    db: Database,
    bbox: tuple[float, float, float, float] | None = None,
    jurisdiction: str | None = None,
) -> dict:
    return _access_layer_geojson(db, "getting_there", bbox, jurisdiction)


def bridges_geojson(
    db: Database,
    bbox: tuple[float, float, float, float] | None = None,
    jurisdiction: str | None = None,
) -> dict:
    return _access_layer_geojson(db, "bridges", bbox, jurisdiction)
