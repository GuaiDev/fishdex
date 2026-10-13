"""Weed reports: the user's own sightings of where a weed edge runs.

A weed edge is reported by a person and nothing else. No data source locates
submerged or emergent weed beds (wetland maps and plant records do not say where
an edge runs), so nothing here infers one, and nothing here is read by the
context layer, the spot builder or the explorer.

Private by construction: every function takes the acting user's id and reaches
the database only through src/storage/weed_reports.py, which filters on it. There
is no count across users, and no response carries any count at all.

A report must lie on or near the water body it names, checked against that
water body's drawn shape: a river's simplified centre line, or a lake's outline.
A water body with no shape on file cannot be checked, so it cannot take a report.
"""

import math

import shapely
from shapely.geometry import Point, shape
from sqlite_utils import Database

from src.models.water_body import WaterBody
from src.models.weed_report import WeedGeometry, WeedReport, WeedReportCreate
from src.services import spot_labels
from src.services.geometry_utils import M_PER_DEG
from src.services.weed_reports import labels
from src.storage import weed_reports as store
from src.storage.water_bodies import get_water_body

SAMPLE_SPACING_M = 50


class WeedReportError(Exception):
    """A weed report that cannot be saved. `status` is the HTTP status the API answers with."""

    status = 422

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class UnknownWaterBody(WeedReportError):
    status = 404


def _display_name(wb: WaterBody) -> str:
    return wb.name or spot_labels.WATER_BODY_TYPES[wb.type]["unnamed"]


def _to_metres(geom, lat0: float):
    """Degrees to local metres around a latitude: good for the tens of km a report spans."""
    scale = [math.cos(math.radians(lat0)) * M_PER_DEG, M_PER_DEG]
    return shapely.transform(geom, lambda coords: coords * scale)


def _report_shape(geometry: WeedGeometry):
    return shape({"type": geometry.type, "coordinates": geometry.coordinates})


def check_on_water(wb: WaterBody, geometry: WeedGeometry) -> None:
    """Raise WeedReportError unless the geometry lies on or near the water body's shape."""
    if wb.geometry is None:
        raise WeedReportError(
            f"{_display_name(wb)} has no outline on file, so a weed edge cannot be "
            "checked against it. Weed edges can only be reported on water that is drawn."
        )
    report = _report_shape(geometry)
    water = shape(wb.geometry)
    is_outline = wb.geometry["type"] in ("Polygon", "MultiPolygon")
    tol = labels.NEAR_OUTLINE_M if is_outline else labels.NEAR_LINE_M
    # Project both into metres around the report so distances mean something.
    lat0 = report.centroid.y
    local_report = _to_metres(report, lat0)
    if local_report.length > labels.MAX_LINE_LENGTH_M:
        raise WeedReportError(
            f"That line is longer than {labels.MAX_LINE_LENGTH_M // 1000} km. "
            "A weed edge is along one stretch of shore or bank."
        )
    local_water = _to_metres(water, lat0)
    # Every point along the report must be near the water, not just where it was clicked.
    if local_report.geom_type == "LineString":
        local_report = shapely.segmentize(local_report, SAMPLE_SPACING_M)
    worst = max(local_water.distance(Point(c)) for c in local_report.coords)
    if worst > tol:
        where = "outline" if is_outline else "line"
        raise WeedReportError(
            f"That is {round(worst):,} m from {_display_name(wb)}. A weed edge has to be on "
            f"the water, within {tol} m of its drawn {where}."
        )


def _payload(report: WeedReport, names: dict[str, str | None]) -> dict:
    observed = report.observed_on.isoformat()
    return {
        "weed_report_id": report.id,
        "water_body_id": report.water_body_id,
        "water_body_name": names.get(report.water_body_id),
        "jurisdiction": report.jurisdiction,
        "geometry": report.geometry.model_dump(),
        "shape_label": labels.SHAPE_LABELS[report.geometry.type],
        "observed_on": observed,
        "note": report.note,
        "provenance": report.provenance,
        "provenance_label": labels.PROVENANCE_LABEL,
        "kind_label": labels.KIND_LABEL,
        "status_label": labels.STATUS_LABEL,
        "date_label": labels.date_label(observed),
        "caption": labels.caption(observed),
        "created_at": report.created_at,
        "updated_at": report.updated_at,
    }


def _names(db: Database, reports: list[WeedReport]) -> dict[str, str | None]:
    """Display name per water body id; None where the water body is no longer on file."""
    out: dict[str, str | None] = {}
    for wb_id in {r.water_body_id for r in reports}:
        wb = get_water_body(db, wb_id)
        out[wb_id] = _display_name(wb) if wb else None
    return out


def weed_report_payload(db: Database, user_id: int, report_id: int) -> dict | None:
    report = store.get_weed_report(db, user_id, report_id)
    return _payload(report, _names(db, [report])) if report else None


def create_weed_report(db: Database, user_id: int, body: WeedReportCreate) -> dict:
    wb = get_water_body(db, body.water_body_id)
    if wb is None:
        raise UnknownWaterBody(f"No water body with id {body.water_body_id!r}")
    check_on_water(wb, body.geometry)
    report_id = store.create_weed_report(
        db, user_id, wb.water_body_id, wb.jurisdiction, body.geometry, body.observed_on, body.note
    )
    return weed_report_payload(db, user_id, report_id)


def update_weed_report(db: Database, user_id: int, report_id: int, fields: dict) -> dict | None:
    """Apply the sent fields. None when the report is not this user's.

    A new geometry is checked against the report's own water body.
    """
    report = store.get_weed_report(db, user_id, report_id)
    if report is None:
        return None
    if "geometry" in fields:
        wb = get_water_body(db, report.water_body_id)
        if wb is None:
            raise UnknownWaterBody(
                f"The water body {report.water_body_id!r} is no longer on file, so the "
                "report's position cannot be checked. Delete it or leave it as it is."
            )
        check_on_water(wb, fields["geometry"])
    store.update_weed_report(db, user_id, report_id, fields)
    return weed_report_payload(db, user_id, report_id)


def list_weed_reports(
    db: Database,
    user_id: int,
    water_body_id: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict:
    reports = store.list_weed_reports(db, user_id, water_body_id, bbox)
    names = _names(db, reports)
    return {"weed_reports": [_payload(r, names) for r in reports]}


def weed_reports_geojson(
    db: Database,
    user_id: int,
    water_body_id: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict:
    """The map layer: the user's own reports as GeoJSON, each labelled and dated.

    `meta` carries the layer's words and no number of any kind.
    """
    reports = store.list_weed_reports(db, user_id, water_body_id, bbox)
    names = _names(db, reports)
    features = []
    for r in reports:
        p = _payload(r, names)
        features.append(
            {
                "type": "Feature",
                "id": p["weed_report_id"],
                "geometry": p["geometry"],
                "properties": {
                    k: p[k]
                    for k in (
                        "weed_report_id",
                        "water_body_id",
                        "water_body_name",
                        "jurisdiction",
                        "shape_label",
                        "observed_on",
                        "note",
                        "provenance",
                        "provenance_label",
                        "kind_label",
                        "status_label",
                        "date_label",
                        "caption",
                    )
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "meta": {"layer": labels.LAYER_LABEL, "note": labels.LAYER_NOTE, "visible_to": "you"},
        "features": features,
    }


__all__ = [
    "UnknownWaterBody",
    "WeedReportError",
    "check_on_water",
    "create_weed_report",
    "list_weed_reports",
    "update_weed_report",
    "weed_report_payload",
    "weed_reports_geojson",
]
