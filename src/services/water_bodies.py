"""Water bodies: the named water each spot is a child of.

`derive_water_bodies()` builds them from what the repo already holds, with no
network and no database work, so the same stretches and lakes always give the
same water bodies:

- **Rivers and creeks** come from the curated stretches. Every stretch that
  carries the same curated `river` name belongs to one water body (the Grand
  River is seven stretches), and its drawn shape is those stretches' simplified
  lines. Creek or river is read from the name ("Bronte Creek" is a creek;
  "Credit River" and "Niagara River" are rivers) and `attributes.type_basis`
  says so.
- **Lakes** come from the lake names the spot classifier resolves for river
  mouths. No lake outline is on file anywhere in the repo, so a lake has no
  geometry (`geometry_source == "none"`) and is placed on the map by the mouths
  that open onto it. A mouth whose lake name nobody supplied makes no lake.

A name is only ever carried over from a source and recorded as such
(`name_source`); the module never composes one. The words the map shows for a
water body type live in services/spot_labels.py.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import shapely
from shapely.geometry import box as shapely_box
from shapely.geometry import shape
from sqlite_utils.db import Database

from src.models.jurisdiction import JurisdictionCode
from src.models.lake_outline import LakeOutline
from src.models.shoreline_feature import ShorelineFeature
from src.models.spot import Spot
from src.models.stretch import FishingStretch
from src.models.water_body import NameSource, WaterBody, WaterBodyType
from src.services import api_vocab
from src.services import spot_labels as words
from src.services.geometry_simplify import simplify_geometry
from src.storage.spots import list_spots, list_spots_also_on
from src.storage.water_bodies import get_water_body, get_water_body_geometry_text, list_water_bodies

# The Great Lakes as the map means them. Georgian Bay is the sixth by common usage but
# is part of Lake Huron; Lake St. Clair, Lake Simcoe and the Bay of Quinte are not
# on this list and read as plain lakes.
GREAT_LAKES: dict[str, str | None] = {
    "lake ontario": None,
    "lake erie": None,
    "lake huron": None,
    "lake superior": None,
    "lake michigan": None,
    "georgian bay": "Lake Huron",
}
# A bay or lake that belongs to a larger water, recorded so the link is not lost.
PART_OF: dict[str, str] = {
    "georgian bay": "Lake Huron",
    "bay of quinte": "Lake Ontario",
    "humber bay": "Lake Ontario",
    "long point bay": "Lake Erie",
    "owen sound": "Georgian Bay",
}

_CREEK_WORD = re.compile(r"\b(creek|brook|run)\b", re.IGNORECASE)
_POND_WORD = re.compile(r"\bpond\b", re.IGNORECASE)


@dataclass(frozen=True)
class LakeRef:
    """A lake name seen on a river mouth, and who gave it.

    `lat`/`lon` is the mouth, used to place a lake that has no outline.
    """

    name: str
    name_source: NameSource
    stretch_id: str
    lat: float
    lon: float


@dataclass
class WaterBodySet:
    """The derived water bodies and the lookups the spot classifier links through."""

    bodies: list[WaterBody] = field(default_factory=list)
    _by_stretch: dict[str, str] = field(default_factory=dict)
    _lakes: dict[str, str] = field(default_factory=dict)

    def river_id(self, stretch_id: str | None) -> str | None:
        """The river or creek a stretch belongs to, or None for an unknown stretch."""
        return self._by_stretch.get(stretch_id) if stretch_id else None

    def lake_id(self, name: str | None) -> str | None:
        return self._lakes.get(slugify(name)) if name else None


def slugify(name: str) -> str:
    """'St. Clair River' -> 'st-clair-river'; the way a water body id is spelled."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def river_type(name: str) -> WaterBodyType:
    """A named run of channel is a creek if its name says so and a river otherwise."""
    return "creek" if _CREEK_WORD.search(name) else "river"


def lake_type(name: str) -> tuple[WaterBodyType, str]:
    """(type, basis) for a lake name: great_lake from the list, pond from the name, else lake."""
    key = name.strip().lower()
    if key in GREAT_LAKES:
        return "great_lake", "great_lakes_list"
    if _POND_WORD.search(name):
        return "pond", "name_contains_pond"
    return "lake", "default_for_a_named_lake"


def _line_parts(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    return list(geometry["coordinates"])


def _union_bbox(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _geojson_bbox(geometry: dict) -> tuple[float, float, float, float] | None:
    """(min_lon, min_lat, max_lon, max_lat) of a GeoJSON point or polygon, or None."""
    if not geometry or not geometry.get("coordinates"):
        return None
    return tuple(shape(geometry).bounds)  # type: ignore[return-value]


def derive_water_bodies(
    stretches: list[FishingStretch],
    lakes: list[LakeRef],
    jurisdiction: JurisdictionCode,
    built_at: str | None = None,
    outlines: dict[str, LakeOutline] | None = None,
) -> WaterBodySet:
    """Rivers and creeks from the stretches, lakes from the mouth lake names.

    `outlines` maps a curated lake name to its matched OHN outline (see
    services/lake_outlines.py). A lake with an outline gets its polygon geometry
    and the source facts (OGF_IDs, OHN name, effective date, licence); a lake
    without one keeps `geometry_source == "none"` and is placed by its mouths.
    """
    built_at = built_at or datetime.now(UTC).isoformat(timespec="seconds")
    outlines = {slugify(name): o for name, o in (outlines or {}).items()}
    out = WaterBodySet()

    # Rivers and creeks: one per curated river name, in curation order.
    by_slug: dict[str, list[FishingStretch]] = {}
    for s in sorted(stretches, key=lambda s: (s.sort_order, s.stretch_id)):
        slug = slugify(s.river)
        if slug:
            by_slug.setdefault(slug, []).append(s)
    for slug, group in by_slug.items():
        names = sorted({s.river.strip() for s in group})
        name = group[0].river.strip()
        wb_type = river_type(name)
        parts = [line for s in group for line in _line_parts(s.geometry)]
        attrs: dict = {"type_basis": "name_suffix"}
        if len(names) > 1:
            attrs["also_spelled"] = [n for n in names if n != name]
        body = WaterBody(
            water_body_id=f"{wb_type}:{slug}",
            jurisdiction=jurisdiction,
            name=name,
            name_source="curated",
            type=wb_type,
            geometry={"type": "MultiLineString", "coordinates": parts},
            geometry_source="stretch_union",
            bbox=_union_bbox([s.bbox for s in group]),
            stretch_ids=[s.stretch_id for s in group],
            attributes=attrs,
            built_at=built_at,
        )
        out.bodies.append(body)
        for s in group:
            out._by_stretch[s.stretch_id] = body.water_body_id

    # Lakes: one per distinct name; the strongest name source wins, and a lake
    # keeps every stretch that opens onto it.
    by_lake: dict[str, list[LakeRef]] = {}
    for ref in sorted(lakes, key=lambda r: (r.name, r.stretch_id)):
        by_lake.setdefault(slugify(ref.name), []).append(ref)
    rank: dict[str, int] = {"curated": 0, "ohn": 1, "osm": 2, "curated_region": 3, "unknown": 4}
    for slug, refs in sorted(by_lake.items()):
        if not slug:
            continue
        lead = min(refs, key=lambda r: (rank[r.name_source], r.stretch_id))
        wb_type, basis = lake_type(lead.name)
        attrs = {"type_basis": basis}
        spellings = sorted({r.name for r in refs} - {lead.name})
        if spellings:
            attrs["also_spelled"] = spellings
        if part_of := PART_OF.get(lead.name.strip().lower()):
            attrs["part_of"] = part_of
        outline = outlines.get(slug)
        if outline is not None and outline.matched and outline.geometry:
            geometry = outline.geometry
            geometry_source = "ohn_waterbody"
            attrs["outline_source"] = "OHN Waterbody"
            attrs["outline_ogf_ids"] = outline.ogf_ids
            if outline.ohn_name:
                attrs["outline_ohn_name"] = outline.ohn_name
            if outline.effective_datetime:
                attrs["outline_effective_datetime"] = outline.effective_datetime
            attrs["outline_match_basis"] = outline.match_basis
            if outline.licence:
                attrs["outline_licence"] = outline.licence
            if outline.licence_url:
                attrs["outline_licence_url"] = outline.licence_url
            attrs["bbox_basis"] = "outline"
            bbox = _geojson_bbox(geometry)
        else:
            geometry = None
            geometry_source = "none"
            attrs["bbox_basis"] = "river_mouths"
            bbox = (
                min(r.lon for r in refs),
                min(r.lat for r in refs),
                max(r.lon for r in refs),
                max(r.lat for r in refs),
            )
        body = WaterBody(
            water_body_id=f"{wb_type}:{slug}",
            jurisdiction=jurisdiction,
            name=lead.name,
            name_source=lead.name_source,
            type=wb_type,
            geometry=geometry,
            geometry_source=geometry_source,
            bbox=bbox,
            stretch_ids=sorted({r.stretch_id for r in refs}),
            attributes=attrs,
            built_at=built_at,
        )
        out.bodies.append(body)
        out._lakes[slug] = body.water_body_id
    return out


# ── read side ─────────────────────────────────────────────────────────────────


def _display_name(wb: WaterBody) -> str:
    """The name, or the label's phrase for a water no source named. Never a made-up name."""
    return wb.name or words.WATER_BODY_TYPES[wb.type]["unnamed"]


def _spot_counts(db: Database, max_tier: int) -> dict[str, dict[str, int]]:
    """Spots per water body per kind, up to a tier: the badges on a list."""
    counts: dict[str, dict[str, int]] = {}
    for wb_id, kind, n in db.execute(
        "SELECT water_body_id, kind, COUNT(*) FROM spots "
        "WHERE water_body_id IS NOT NULL AND tier <= ? GROUP BY water_body_id, kind",
        [max_tier],
    ).fetchall():
        counts.setdefault(wb_id, {})[kind] = n
    return counts


# The properties a water-body list feature carries, in the order they are served.
LIST_FIELDS = (
    "water_body_id",
    "name",
    "display_name",
    "name_source",
    "type",
    "jurisdiction",
    "bbox",
    "spot_counts",
)


def _crosses_view(db: Database, wb: WaterBody, view) -> bool:
    """Does this water's drawn shape really cross the view box?

    The stored box is a coarse filter (a river's box can cover a whole watershed), so a
    box that only partly overlaps the view needs the shape itself. A box wholly inside
    the view cannot miss it, so that shape is never read. A water with no outline is
    placed by its box alone.
    """
    if wb.geometry_source == "none" or wb.bbox is None:
        return True
    if view.contains(shapely_box(*wb.bbox)):
        return True
    if wb.geometry is not None:
        return view.intersects(shape(wb.geometry))
    text = get_water_body_geometry_text(db, wb.water_body_id)
    return text is None or view.intersects(shapely.from_geojson(text))


def water_bodies_geojson(
    db: Database,
    bbox: tuple[float, float, float, float] | None = None,
    types: list[str] | None = None,
    jurisdiction: str | None = None,
    fields: list[str] | None = None,
    geometry: bool = True,
    simplify_m: float | None = None,
    zoom: float | None = None,
) -> dict:
    """Water bodies as a GeoJSON FeatureCollection, light enough for a whole map.

    A river or creek carries its simplified line; a lake with no outline on file
    has `geometry: null` and is found by the box of the mouths that open onto it.
    A drawn water is in view when its line crosses the box, not merely when its overall
    box does. `type_counts` in `meta` are taken before the type filter. `spot_counts` count
    the spots shown by default (tier 1 and 2).

    The weight is mostly geometry (a lake outline is megabytes), so a client can trim it:

    - `fields` keeps only those entries of `properties` (names from `LIST_FIELDS`;
      `water_body_id` is always kept, it is the feature's identity).
    - `geometry=False` serves `geometry: null` on every feature and never reads the shapes
      it does not need to decide what is in view.
    - `simplify_m` thins each shape to that many metres, or `zoom` to one screen pixel at
      that web-map zoom. Which waters are returned does not change; only their shapes.

    With none of these given the answer is exactly what it always was. When one is, `meta`
    says so (`geometry`: "none" or "simplified", and the tolerance or zoom), so a client
    can tell a thinned outline from an outline that was never on file.
    """
    wanted = [f for f in LIST_FIELDS if fields is None or f in fields or f == "water_body_id"]
    in_view = list_water_bodies(db, bbox=bbox, jurisdiction=jurisdiction, include_geometry=geometry)
    if bbox is not None:
        view = shapely_box(*bbox)
        in_view = [wb for wb in in_view if _crosses_view(db, wb, view)]
    type_counts = {t: 0 for t in words.WATER_BODY_TYPE_ORDER}
    for wb in in_view:
        type_counts[wb.type] += 1
    shown = [wb for wb in in_view if not types or wb.type in types]
    shown.sort(key=lambda wb: (words.WATER_BODY_TYPE_ORDER.index(wb.type), wb.water_body_id))
    counts = _spot_counts(db, max_tier=2) if "spot_counts" in wanted else {}

    def properties(wb: WaterBody) -> dict:
        everything = {
            "water_body_id": wb.water_body_id,
            "name": wb.name,
            "display_name": _display_name(wb),
            "name_source": wb.name_source,
            "type": wb.type,
            "jurisdiction": wb.jurisdiction,
            "bbox": list(wb.bbox) if wb.bbox else None,
            "spot_counts": counts.get(wb.water_body_id, {}),
        }
        return {k: everything[k] for k in wanted}

    meta: dict = {"count": len(shown), "type_counts": type_counts}
    if not geometry:
        meta["geometry"] = "none"
    elif simplify_m is not None or zoom is not None:
        meta["geometry"] = "simplified"
        if simplify_m is not None:
            meta["simplify_m"] = simplify_m
        else:
            meta["zoom"] = zoom
    return {
        "type": "FeatureCollection",
        "meta": meta,
        "features": [
            {
                "type": "Feature",
                "id": wb.water_body_id,
                "geometry": simplify_geometry(wb.geometry, simplify_m, zoom) if geometry else None,
                "properties": properties(wb),
            }
            for wb in shown
        ],
    }


def _spot_summary(s: Spot) -> dict:
    return {
        "spot_id": s.spot_id,
        "kind": s.kind,
        "title": s.title,
        "tier": s.tier,
        "lat": s.lat,
        "lng": s.lng,
    }


def get_water_body_detail(
    db: Database,
    water_body_id: str,
    geometry: bool = True,
    simplify_m: float | None = None,
    zoom: float | None = None,
) -> dict | None:
    """One water body in full, with the spots on it. None for an unknown id.

    `geometry=False` serves `geometry: null`; `simplify_m` or `zoom` thins the shape the
    way the list does (see `water_bodies_geojson`). Neither changes anything else.

    `spots` are its children; `also_here_spots` belong to another water body but
    are also on this one (the river mouths that open onto a lake).
    """
    wb = get_water_body(db, water_body_id)
    if wb is None:
        return None
    order = {kind: i for i, kind in enumerate(words.KIND_ORDER)}

    def ranked(spots: list[Spot]) -> list[dict]:
        spots = sorted(spots, key=lambda s: (s.tier, order[s.kind], s.spot_id))
        return [_spot_summary(s) for s in spots]

    own = list_spots(db, max_tier=3, water_body_id=water_body_id)
    also = list_spots_also_on(db, water_body_id)
    shown = api_vocab.water_body_for_api(wb.model_dump())
    shown["geometry"] = simplify_geometry(wb.geometry, simplify_m, zoom) if geometry else None
    return {
        **shown,
        "display_name": _display_name(wb),
        "spots": ranked(own),
        "also_here_spots": ranked(also),
    }


def shoreline_feature_properties(f: ShorelineFeature) -> dict:
    """What the API says about one shoreline or depth feature, wherever it is served."""
    return {
        "feature_id": f.feature_id,
        "water_body_id": f.water_body_id,
        "kind": f.kind,
        "name": f.name,
        "derivation": f.derivation,
        "provenance": f.provenance,
        "source": f.source,
        "source_date": f.source_date,
        "note": words.shoreline_feature_note(f.kind, f.derivation, f.name, f.attributes),
        "attributes": f.attributes,
        "area_m2": f.area_m2,
        # How far a point or peninsula reaches; never a drop-off's depth change.
        "length_m": None if f.kind == "drop_off" else f.length_m,
        "depth_change_m": f.attributes.get("depth_change_m") if f.kind == "drop_off" else None,
    }


def water_body_features_geojson(db: Database, water_body_id: str) -> dict | None:
    """A lake's shoreline features as a GeoJSON FeatureCollection. None for an unknown id.

    The inlets, outlets, islands, points and peninsulas derived from the water
    body's outline, plus the drop-offs and humps derived from its survey contours
    and the piers, breakwaters and harbours OpenStreetMap maps on its shore.
    `derivation` says how the shape was made: `watercourse_endpoint` (a mapped OHN
    stream at the shore), `shoreline_outline` (geometry read off the outline
    itself), `bathymetry_contours` (read off a lake survey) or `osm_feature` (a
    structure mapped in OpenStreetMap); the note sticks to that either way, and
    `attributes` carries the survey facts behind a contour feature. A river has no
    features, so an empty collection is the honest answer, not an error.
    """
    from src.storage.water_bodies import list_shoreline_features

    wb = get_water_body(db, water_body_id)
    if wb is None:
        return None
    features = list_shoreline_features(db, water_body_id)
    by_kind: dict[str, int] = {}
    out = []
    for f in features:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        out.append(
            {
                "type": "Feature",
                "id": f.feature_id,
                "geometry": f.geometry,
                "properties": shoreline_feature_properties(f),
            }
        )
    return {
        "type": "FeatureCollection",
        "water_body_id": wb.water_body_id,
        "water_body_name": wb.name,
        "meta": {"count": len(out), "by_kind": by_kind},
        "features": out,
    }
