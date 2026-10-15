"""Sections: the overview map's unit of water, and what is inside each one.

At overview zoom the map used to draw every spot as a pin, and a city view was
fifty of them, nine of them "Below the weir on the Humber River" a few hundred
metres apart. A section replaces that: one tappable shape per piece of water,
with counts of what is in it. A tap opens the section's pins.

What counts as a section:

- **A river section** is one curated river section (a run of a river or creek
  someone drew, like "Lower Humber - Woodbridge to Humber Bay"). Its id is the
  section id the API already hands out on a spot's `water_id` and a water body's
  `section_ids`.
- **A lake** with an outline on file is one section, drawn as its outline. Its id
  is its water body id (`lake:lake-simcoe`), which no river section id can equal.
- **A lake or bay with no outline** is not a section. It has no shape to tap, so
  the list carries a label for it and nothing else.

What a section holds, as pins (`section_detail`): the spots on it (below-dam
spots, places where creeks join, river mouths, public fishing spots), a lake's
shoreline and depth features, the nearest parking, trailheads and boat launches
to those spots, and the caller's own weed reports. Each pin keeps the id of the
thing it stands for, so a tap opens the card or detail that already exists.

Not here: quiet water. It is a ranking of reaches with no water of its own, so it
belongs to no section and stays on `GET /map/spots?kind=quiet_water`.

Nothing here is stored. Sections are read off the stored river sections, water
bodies, spots and features on every call, so a rebuild changes them with no
second build step. The one per-user ingredient is the weed-report count and
pins: only the caller's own, and a call with no user sees none.
"""

from dataclasses import dataclass

from shapely.geometry import MultiPolygon, mapping, shape
from shapely.geometry import box as shapely_box
from sqlite_utils.db import Database

from src.models.spot import Spot
from src.models.stretch import FishingStretch
from src.models.water_body import WaterBody
from src.models.weed_report import WeedReport
from src.services import spot_labels as words
from src.services.geometry_utils import polygons_of
from src.services.spots import spot_feature
from src.services.water_bodies import river_type, shoreline_feature_properties
from src.services.weed_reports import labels as weed_labels
from src.storage import weed_reports as weed_store
from src.storage.spots import list_spot_memberships, list_spots_by_ids
from src.storage.stretches import count_stretches, get_stretch, list_stretches
from src.storage.water_bodies import (
    count_shoreline_features_by_kind,
    get_water_body,
    list_shoreline_features,
    list_water_bodies,
)

# A lake outline is drawn this far from its survey (degrees, ~200 m): invisible at
# overview zoom, and it keeps Lake Ontario to a few hundred points. Islands inside
# a lake are features with pins of their own, so the layer draws the lake whole.
LAKE_SIMPLIFY_DEG = 0.002
# A part of an outline smaller than this (square degrees, ~0.12 km²) is dropped,
# unless it is all the lake has.
LAKE_MIN_PART_DEG2 = 1e-5
COORD_DIGITS = 5

RIVER_TYPES = ("river", "creek")
LAKE_TYPES = ("lake", "pond", "great_lake")
KIND_RANK = {kind: i for i, kind in enumerate(words.KIND_ORDER)}
PIN_TYPE_ORDER = ("spot", "feature", "getting_there", "weed_report")
# The spot kinds a section holds; quiet water has no water of its own.
SECTION_SPOT_KINDS = ("river_mouth", "creek_junction", "below_dam", "public_fishing")


@dataclass(frozen=True)
class Section:
    """One tappable shape: a river section or a lake, with what it needs to be drawn."""

    section_id: str
    kind: str  # river_section | lake
    type: str  # the water's type: river, creek, lake, pond, great_lake
    name: str
    water_body: WaterBody | None
    stretch: FishingStretch | None
    jurisdiction: str
    geometry: dict
    bbox: tuple[float, float, float, float]
    label_point: tuple[float, float]


def _round(coords, digits: int = COORD_DIGITS):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], digits), round(coords[1], digits)]
    return [_round(c, digits) for c in coords]


def simplify_outline(geometry: dict) -> dict:
    """A lake outline for the overview: simplified, whole (no islands cut out), small parts gone."""
    parts = polygons_of(shape(geometry))
    if not parts:
        return geometry
    simple = []
    for part in sorted(parts, key=lambda p: p.area, reverse=True):
        if simple and part.area < LAKE_MIN_PART_DEG2:
            continue
        outer = part.exterior
        poly = type(part)(outer).simplify(LAKE_SIMPLIFY_DEG, preserve_topology=True)
        simple.append(poly if not poly.is_empty else type(part)(outer))
    merged = simple[0] if len(simple) == 1 else MultiPolygon(simple)
    out = mapping(merged)
    return {"type": out["type"], "coordinates": _round(list(out["coordinates"]))}


def _river_section(stretch: FishingStretch, body: WaterBody | None) -> Section:
    return Section(
        section_id=stretch.stretch_id,
        kind="river_section",
        type=body.type if body else river_type(stretch.river),
        name=stretch.name,
        water_body=body,
        stretch=stretch,
        jurisdiction=stretch.jurisdiction,
        geometry=stretch.geometry,
        bbox=stretch.bbox,
        label_point=(stretch.centroid_lon, stretch.centroid_lat),
    )


def _lake_section(body: WaterBody) -> Section:
    geometry = simplify_outline(body.geometry)
    poly = shape(geometry)
    inside = poly.representative_point()
    min_lon, min_lat, max_lon, max_lat = poly.bounds
    return Section(
        section_id=body.water_body_id,
        kind="lake",
        type=body.type,
        name=body.name or words.WATER_BODY_TYPES[body.type]["unnamed"],
        water_body=body,
        stretch=None,
        jurisdiction=body.jurisdiction,
        geometry=geometry,
        bbox=(
            round(min_lon, COORD_DIGITS),
            round(min_lat, COORD_DIGITS),
            round(max_lon, COORD_DIGITS),
            round(max_lat, COORD_DIGITS),
        ),
        label_point=(round(inside.x, COORD_DIGITS), round(inside.y, COORD_DIGITS)),
    )


def _river_bodies(bodies: list[WaterBody]) -> dict[str, WaterBody]:
    """The river or creek each curated section belongs to, by section id.

    A lake lists the sections that open onto it too, so only rivers and creeks count.
    """
    return {sid: wb for wb in bodies if wb.type in RIVER_TYPES for sid in wb.stretch_ids}


def _load(db: Database, jurisdiction: str | None) -> tuple[list[Section], list[WaterBody]]:
    """Every section, and the lakes with no outline (labels only), in map order."""
    bodies = list_water_bodies(db, jurisdiction=jurisdiction)
    river_of = _river_bodies(bodies)
    sections = [
        _river_section(s, river_of.get(s.stretch_id)) for s in list_stretches(db, jurisdiction)
    ]
    sections += [_lake_section(wb) for wb in bodies if wb.type in LAKE_TYPES and wb.geometry]
    unshaped = [wb for wb in bodies if wb.type in LAKE_TYPES and not wb.geometry]
    order = {t: i for i, t in enumerate(words.WATER_BODY_TYPE_ORDER)}
    sections.sort(key=lambda s: (order[s.type], s.name, s.section_id))
    return sections, unshaped


def _find(db: Database, section_id: str) -> Section | None:
    if ":" in section_id:
        wb = get_water_body(db, section_id)
        if wb is None or wb.type not in LAKE_TYPES or not wb.geometry:
            return None
        return _lake_section(wb)
    stretch = get_stretch(db, section_id)
    if stretch is None:
        return None
    body = _river_bodies(list_water_bodies(db, jurisdiction=stretch.jurisdiction)).get(section_id)
    return _river_section(stretch, body)


# ── what is in a section ──────────────────────────────────────────────────────


def _spot_rows(section: Section, rows: list[dict]) -> list[dict]:
    """The spot rows on a section.

    A river section holds the spots on its own section and the ones listed under it as
    a second section (a creek joining it). A lake holds the spots that are on it: the
    river mouths that open onto it.
    """
    if section.kind == "lake":
        wanted = section.section_id
        return [
            r
            for r in rows
            if r["kind"] in SECTION_SPOT_KINDS
            and (r["water_body_id"] == wanted or wanted in r["also_on_water_body_ids"])
        ]
    sid = section.section_id
    return [
        r
        for r in rows
        if r["kind"] in SECTION_SPOT_KINDS
        and (r["water_id"] == sid or sid in r["also_on_water_ids"])
    ]


def _distance_to(section: Section, geometry: dict) -> float:
    return shape(section.geometry).distance(shape(geometry))


def _weed_by_section(
    reports: list[WeedReport], sections: list[Section]
) -> dict[str, list[WeedReport]]:
    """The caller's weed reports, each placed in one section.

    A lake takes the reports filed on it. A river's reports are filed on the whole
    river, so each goes to the nearest of that river's sections: a report belongs to
    one section, never two. A report on a water with no section here is left out.
    """
    out: dict[str, list[WeedReport]] = {}
    by_body: dict[str, list[Section]] = {}
    for s in sections:
        if s.water_body is not None:
            by_body.setdefault(s.water_body.water_body_id, []).append(s)
    for r in reports:
        candidates = by_body.get(r.water_body_id, [])
        if not candidates:
            continue
        if len(candidates) == 1:
            best = candidates[0]
        else:
            geom = r.geometry.model_dump()
            best = min(candidates, key=lambda s: (_distance_to(s, geom), s.section_id))
        out.setdefault(best.section_id, []).append(r)
    return out


def _counts(spot_rows: list[dict], feature_counts: dict[str, int], weed_reports: int) -> dict:
    spots: dict[str, int] = {}
    for r in spot_rows:
        spots[r["kind"]] = spots.get(r["kind"], 0) + 1
    spots = {k: spots[k] for k in words.KIND_ORDER if k in spots}
    features = {k: feature_counts[k] for k in words.FEATURE_KIND_ORDER if k in feature_counts}
    return {
        "spots": spots,
        "features": features,
        "weed_reports": weed_reports,
        "total": sum(spots.values()) + sum(features.values()) + weed_reports,
    }


def _section_feature(section: Section, counts: dict) -> dict:
    wb = section.water_body
    stretch = section.stretch
    props = {
        "section_id": section.section_id,
        "kind": section.kind,
        "type": section.type,
        "type_label": words.WATER_BODY_TYPES[section.type]["label"],
        "name": section.name,
        "water_body_id": wb.water_body_id if wb else None,
        "water_body_name": (wb.name if wb else None) or (stretch.river if stretch else None),
        "region": stretch.region if stretch else None,
        "jurisdiction": section.jurisdiction,
        "length_km": stretch.length_km if stretch else None,
        "bbox": list(section.bbox),
        "label_point": list(section.label_point),
        "counts": counts,
        "summary": words.section_summary(
            counts["spots"], counts["features"], counts["weed_reports"]
        ),
    }
    return {
        "type": "Feature",
        "id": section.section_id,
        "geometry": section.geometry,
        "properties": props,
    }


def _label_feature(wb: WaterBody) -> dict:
    min_lon, min_lat, max_lon, max_lat = wb.bbox
    return {
        "type": "Feature",
        "id": wb.water_body_id,
        "geometry": {
            "type": "Point",
            "coordinates": [
                round((min_lon + max_lon) / 2, COORD_DIGITS),
                round((min_lat + max_lat) / 2, COORD_DIGITS),
            ],
        },
        "properties": {
            "water_body_id": wb.water_body_id,
            "name": wb.name,
            "display_name": wb.name or words.WATER_BODY_TYPES[wb.type]["unnamed"],
            "type": wb.type,
            "type_label": words.WATER_BODY_TYPES[wb.type]["label"],
            "jurisdiction": wb.jurisdiction,
            "bbox": list(wb.bbox),
            "note": words.SECTION_LABEL_ONLY_NOTE,
        },
    }


def _in_view(section: Section, view) -> bool:
    min_lon, min_lat, max_lon, max_lat = section.bbox
    if not view.intersects(shapely_box(min_lon, min_lat, max_lon, max_lat)):
        return False
    return view.intersects(shape(section.geometry))


def sections_geojson(
    db: Database,
    user_id: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    types: list[str] | None = None,
    jurisdiction: str | None = None,
    max_tier: int = 2,
) -> dict:
    """The sections in view as a GeoJSON FeatureCollection, light enough for a whole map.

    Each feature is one river section or one lake outline, with its name, `kind`,
    water `type`, and `counts` of what is inside: spots by kind, shoreline and depth
    features by kind, the caller's own weed reports, and a `total`. A kind that is
    absent has none. `summary` says the counts in a sentence.

    `labels` carries the lakes and bays with no outline: a name and a place, no shape,
    so there is nothing to tap. `max_tier` is the spot tier counted, cumulative, as on
    `GET /map/spots`. `meta.type_counts` are taken before the type filter. The weed
    counts are the caller's own and nobody else's, so the response must not be shared.
    """
    sections, unshaped = _load(db, jurisdiction)
    view = shapely_box(*bbox) if bbox is not None else None
    if view is not None:
        sections = [s for s in sections if _in_view(s, view)]
        min_lon, min_lat, max_lon, max_lat = bbox
        unshaped = [
            wb
            for wb in unshaped
            if wb.bbox
            and min_lon <= (wb.bbox[0] + wb.bbox[2]) / 2 <= max_lon
            and min_lat <= (wb.bbox[1] + wb.bbox[3]) / 2 <= max_lat
        ]
    type_counts = {t: 0 for t in words.WATER_BODY_TYPE_ORDER}
    for s in sections:
        type_counts[s.type] += 1
    for wb in unshaped:
        type_counts[wb.type] += 1
    if types:
        sections = [s for s in sections if s.type in types]
        unshaped = [wb for wb in unshaped if wb.type in types]
    unshaped = [wb for wb in unshaped if wb.bbox]

    memberships = list_spot_memberships(db, max_tier, jurisdiction)
    feature_counts = count_shoreline_features_by_kind(db, jurisdiction)
    weed = (
        _weed_by_section(weed_store.list_weed_reports(db, user_id), sections)
        if user_id is not None
        else {}
    )
    features = []
    for s in sections:
        counts = _counts(
            _spot_rows(s, memberships),
            feature_counts.get(s.section_id, {}) if s.kind == "lake" else {},
            len(weed.get(s.section_id, [])),
        )
        features.append(_section_feature(s, counts))
    meta: dict = {
        "count": len(features),
        "label_count": len(unshaped),
        "type_counts": type_counts,
        "max_tier": max_tier,
        "weed_reports_note": words.SECTION_WEED_COUNT_NOTE,
    }
    if count_stretches(db, jurisdiction) == 0:
        meta["notes"] = [words.SECTIONS_NOT_BUILT]
    return {
        "type": "FeatureCollection",
        "meta": meta,
        "features": features,
        "labels": {
            "type": "FeatureCollection",
            "features": [_label_feature(wb) for wb in unshaped],
        },
    }


# ── a section's pins ──────────────────────────────────────────────────────────


def _on_section_line(geometry: dict) -> list[float]:
    """A point ON a weed edge, for its pin: the middle of a line, the point itself."""
    if geometry["type"] == "Point":
        return geometry["coordinates"]
    line = shape(geometry)
    mid = line.interpolate(0.5, normalized=True)
    return [round(mid.x, 6), round(mid.y, 6)]


def _spot_pin(spot: Spot) -> dict:
    pin = spot_feature(spot)
    pin["properties"] = {"pin_type": "spot", **pin["properties"]}
    return pin


def _feature_pin(f) -> dict:
    geometry = f.geometry
    props = {"pin_type": "feature", **shoreline_feature_properties(f)}
    props["label"] = words.FEATURE_KINDS[f.kind]["label"]
    if geometry["type"] == "Point":
        point = geometry
    else:
        # An island or hump is an area: the pin sits inside it, and the area comes along.
        inside = shape(geometry).representative_point()
        point = {"type": "Point", "coordinates": [round(inside.x, 6), round(inside.y, 6)]}
        props["shape"] = geometry
    return {"type": "Feature", "id": f.feature_id, "geometry": point, "properties": props}


def _getting_there_pins(spots: list[Spot]) -> list[dict]:
    """Parking, trailheads and boat launches near the section's spots, each place once."""
    seen: dict[tuple[str, float, float], dict] = {}
    for spot in spots:
        for a in spot.getting_there:
            key = (a.kind, a.lng, a.lat)
            if key not in seen:
                seen[key] = {
                    "type": "Feature",
                    "id": f"{a.kind}:{a.source_ref}",
                    "geometry": {"type": "Point", "coordinates": [a.lng, a.lat]},
                    "properties": {
                        "pin_type": "getting_there",
                        "kind": a.kind,
                        "label": words.ACCESS_LABELS[a.kind],
                        "name": a.name,
                        "attributes": a.attributes,
                        "source_ref": a.source_ref,
                        "serves": [],
                    },
                }
            seen[key]["properties"]["serves"].append(spot.spot_id)
    order = {k: i for i, k in enumerate(words.ACCESS_LABELS)}
    return sorted(seen.values(), key=lambda p: (order[p["properties"]["kind"]], p["id"]))


def _weed_pin(report: WeedReport, water_body_name: str | None) -> dict:
    geometry = report.geometry.model_dump()
    observed = report.observed_on.isoformat()
    props = {
        "pin_type": "weed_report",
        "weed_report_id": report.id,
        "water_body_id": report.water_body_id,
        "water_body_name": water_body_name,
        "jurisdiction": report.jurisdiction,
        "shape_label": weed_labels.SHAPE_LABELS[geometry["type"]],
        "observed_on": observed,
        "note": report.note,
        "provenance": report.provenance,
        "provenance_label": weed_labels.PROVENANCE_LABEL,
        "kind_label": weed_labels.KIND_LABEL,
        "status_label": weed_labels.STATUS_LABEL,
        "date_label": weed_labels.date_label(observed),
        "caption": weed_labels.caption(observed),
    }
    if geometry["type"] != "Point":
        props["shape"] = geometry
    return {
        "type": "Feature",
        "id": report.id,
        "geometry": {"type": "Point", "coordinates": _on_section_line(geometry)},
        "properties": props,
    }


def _weed_for(db: Database, section: Section, user_id: int | None) -> list[WeedReport]:
    """The caller's weed reports in this section; none for a call with no user."""
    if user_id is None:
        return []
    siblings = [section]
    body = section.water_body
    if section.kind == "river_section" and body is not None:
        # A river's reports are filed on the river, so placing one needs every section of it.
        siblings = [
            _river_section(s, body)
            for s in list_stretches(db, section.jurisdiction)
            if s.stretch_id in body.stretch_ids
        ]
    reports = weed_store.list_weed_reports(db, user_id)
    return _weed_by_section(reports, siblings).get(section.section_id, [])


def section_detail(
    db: Database, section_id: str, user_id: int | None = None, max_tier: int = 2
) -> dict | None:
    """One section and every point of interest in it, as pins. None for an unknown id.

    The result is a GeoJSON FeatureCollection of Points. `section` is the same feature
    the list carries, so a link straight to a section can draw it. Each pin has a
    `pin_type`: `spot` (the same properties as `GET /map/spots`; open
    `GET /map/spots/{spot_id}`), `feature` (the same properties as a lake's features;
    an island or hump also carries its `shape`), `getting_there` (`serves` names the
    spots it is near) or `weed_report` (the caller's own, "reported, not verified";
    a weed line also carries its `shape`). Only a weed report is per-user.
    """
    section = _find(db, section_id)
    if section is None:
        return None
    jurisdiction = section.jurisdiction
    memberships = _spot_rows(section, list_spot_memberships(db, max_tier, jurisdiction))
    spots = list_spots_by_ids(db, [r["spot_id"] for r in memberships])
    spots.sort(key=lambda s: (s.tier, KIND_RANK[s.kind], s.spot_id))

    features = []
    if section.kind == "lake":
        order = {k: i for i, k in enumerate(words.FEATURE_KIND_ORDER)}
        features = sorted(
            list_shoreline_features(db, section.section_id),
            key=lambda f: (order[f.kind], f.feature_id),
        )

    weed = _weed_for(db, section, user_id)

    counts = _counts(
        memberships,
        _feature_kind_counts(features),
        len(weed),
    )
    pins = [_spot_pin(s) for s in spots]
    pins += [_feature_pin(f) for f in features]
    pins += _getting_there_pins(spots)
    wb_name = section.water_body.name if section.water_body else None
    pins += [_weed_pin(r, wb_name) for r in weed]
    by_pin_type = {t: 0 for t in PIN_TYPE_ORDER}
    for pin in pins:
        by_pin_type[pin["properties"]["pin_type"]] += 1
    return {
        "type": "FeatureCollection",
        "section": _section_feature(section, counts),
        "meta": {
            "count": len(pins),
            "by_pin_type": by_pin_type,
            "max_tier": max_tier,
            "weed_reports_note": words.SECTION_WEED_COUNT_NOTE,
        },
        "features": pins,
    }


def _feature_kind_counts(features) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in features:
        out[f.kind] = out.get(f.kind, 0) + 1
    return out


__all__ = ["Section", "section_detail", "sections_geojson", "simplify_outline"]
