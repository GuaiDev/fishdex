"""Fishing stretches — Level 1 of the explore map.

Build: read the curation file, fetch the OHN network for its build area,
trace every curated stretch through that network, write the result. Then
cluster the same network into candidate stretches and report the ones no
curated stretch covers — the rivers the map is still missing.

The curation file is the authority on what is published. Clustering proposes;
a person decides. Renaming, merging (several reaches in one entry), splitting
(two entries sharing a boundary point), adding and dropping a stretch are all
edits to that file — see the header of data/curation/stretches_ca_on.toml.

Read: stretches_geojson() returns the published set as a GeoJSON
FeatureCollection for the map.
"""

import logging
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from shapely.geometry import Point, box
from sqlite_utils.db import Database

from src.models.hydrology import StreamSegment
from src.models.stretch import (
    CandidateStretch,
    CuratedStretch,
    FishingStretch,
    StretchBuildIssue,
    StretchBuildReport,
    StretchCuration,
    StretchSegment,
)
from src.services.stretch_clustering import (
    StreamNetwork,
    cluster_candidates,
    stretch_shape,
    trace_reach,
)
from src.storage.stretches import list_stretches, replace_stretches

logger = logging.getLogger(__name__)

DEFAULT_CURATION_PATH = Path("data/curation/stretches_ca_on.toml")

# A traced reach this many times longer than the straight line between its ends
# has probably wandered up a tributary or around a lake. Rivers meander, so the
# bar is high; it is a warning, not a refusal.
_SUSPICIOUS_SINUOSITY = 4.0
# A candidate is "covered" once this share of its length is in curated stretches.
_COVERED_FRACTION = 0.5


def load_curation(path: Path = DEFAULT_CURATION_PATH) -> StretchCuration:
    with open(path, "rb") as f:
        return StretchCuration.model_validate(tomllib.load(f))


def load_network(curation: StretchCuration) -> list[StreamSegment]:
    """OHN watercourses for the build area. Cached 30 days by the adapter."""
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses_bbox

    return fetch_watercourses_bbox(*curation.build_bbox)


def build_stretches(
    db: Database,
    curation_path: Path = DEFAULT_CURATION_PATH,
    segments: list[StreamSegment] | None = None,
) -> StretchBuildReport:
    """Rebuild the published stretches for the curation file's jurisdiction.

    `segments` is injectable for tests; by default the network is fetched.
    """
    curation = load_curation(curation_path)
    if segments is None:
        segments = load_network(curation)
    network = StreamNetwork(segments)

    report = StretchBuildReport(
        jurisdiction=curation.jurisdiction,
        network_segments=len(network),
        curated_total=len(curation.stretches),
    )
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    claimed: dict[int, str] = {}
    published: list[FishingStretch] = []
    memberships: list[StretchSegment] = []

    for order, stretch in enumerate(curation.stretches):
        if not stretch.enabled:
            report.disabled.append(stretch.id)
            continue
        ids = _trace_stretch(network, curation, stretch, claimed, report)
        if ids is None:
            continue
        jurisdiction = curation.jurisdiction_of(stretch)
        shape = stretch_shape(network, ids)
        published.append(
            FishingStretch(
                stretch_id=stretch.id,
                name=stretch.name,
                river=stretch.river,
                region=stretch.region,
                jurisdiction=jurisdiction,
                length_km=round(network.length_m(ids) / 1000, 2),
                segment_count=len(ids),
                geometry=shape.geometry,
                bbox=shape.bbox,
                centroid_lat=shape.centroid_lat,
                centroid_lon=shape.centroid_lon,
                sort_order=order,
                notes=stretch.notes,
                built_at=built_at,
            )
        )
        for seq, ogf_id in enumerate(ids):
            claimed[ogf_id] = stretch.id
            memberships.append(
                StretchSegment(
                    ogf_id=ogf_id, stretch_id=stretch.id, jurisdiction=jurisdiction, seq=seq
                )
            )
        report.built.append(stretch.id)

    replace_stretches(db, curation.jurisdiction, published, memberships)
    report.segments_assigned = len(memberships)

    candidates = cluster_candidates(network, curation.jurisdiction)
    report.candidates_total = len(candidates)
    report.uncovered_candidates = _uncovered(network, candidates, claimed)

    for issue in report.issues:
        logger.warning("stretch %s: %s — %s", issue.stretch_id, issue.kind, issue.detail)
    if report.failed:
        logger.warning(
            "%d of %d curated stretches did not build: %s",
            len(report.failed),
            report.curated_total,
            ", ".join(report.failed),
        )
    return report


def _trace_stretch(
    network: StreamNetwork,
    curation: StretchCuration,
    stretch: CuratedStretch,
    claimed: dict[int, str],
    report: StretchBuildReport,
) -> list[int] | None:
    """Ordered segment ids for one curated stretch, or None with issues recorded."""

    def issue(kind: str, detail: str) -> None:
        report.issues.append(StretchBuildIssue(stretch_id=stretch.id, kind=kind, detail=detail))

    area = box(*curation.build_bbox)
    outside = [
        wp
        for reach in stretch.reaches
        for wp in reach.waypoints()
        if not area.contains(Point(wp.lon, wp.lat))
    ]
    if outside:
        labels = ", ".join(wp.label or f"({wp.lat}, {wp.lon})" for wp in outside)
        issue("outside_build_area", f"{labels} outside build_bbox {curation.build_bbox}")
        return None

    ids: list[int] = []
    boundaries: set[int] = set()
    for reach in stretch.reaches:
        trace = trace_reach(network, reach, stretch.max_snap_m)
        if trace.failure:
            issue(trace.failure, trace.detail)
            return None
        if trace.straight_m > 1000 and trace.length_m > _SUSPICIOUS_SINUOSITY * trace.straight_m:
            issue(
                "suspicious_path",
                f"traced {trace.length_m / 1000:.1f} km between points "
                f"{trace.straight_m / 1000:.1f} km apart — add a via point",
            )
        ids.extend(trace.ogf_ids)
        boundaries.update(trace.boundary_ids)

    unknown = [i for i in stretch.include_ogf_ids if i not in network.segments]
    if unknown:
        issue("unknown_ogf_id", f"include_ogf_ids not in the network: {unknown}")
    ids.extend(i for i in stretch.include_ogf_ids if i in network.segments)
    excluded = set(stretch.exclude_ogf_ids)
    ids = [i for i in dict.fromkeys(ids) if i not in excluded]

    # A split is two stretches sharing a boundary point, so the segment that
    # point lands on is traced by both. The earlier stretch keeps it; that is
    # the split working, not a conflict worth reporting.
    taken = [i for i in ids if i in claimed and i not in boundaries]
    if taken:
        owners = sorted({claimed[i] for i in taken})
        issue(
            "segment_conflict",
            f"{len(taken)} segment(s) already in {', '.join(owners)} — kept there; "
            "move a shared boundary point or exclude them",
        )
    ids = [i for i in ids if i not in claimed]

    if not ids:
        issue("empty_stretch", "no segments left after overrides and conflicts")
        return None
    return ids


def _uncovered(
    network: StreamNetwork, candidates: list[CandidateStretch], claimed: dict[int, str]
) -> list[CandidateStretch]:
    out = []
    for cand in candidates:
        total = network.length_m(cand.ogf_ids)
        covered = network.length_m([i for i in cand.ogf_ids if i in claimed])
        cand.covered_fraction = round(covered / total, 3) if total else 0.0
        if cand.covered_fraction < _COVERED_FRACTION:
            out.append(cand)
    return out


def candidate_toml(cand: CandidateStretch) -> str:
    """A candidate as a curation entry, ready to paste and rename."""
    name = cand.suggested_name or "Unnamed channel"
    slug = cand.candidate_id
    return (
        "[[stretch]]\n"
        f'id = "{slug}"\n'
        f'name = "{name}"\n'
        f'river = "{name}"\n'
        "[[stretch.reach]]\n"
        f"from = {{ lat = {cand.downstream.lat}, lon = {cand.downstream.lon} }}\n"
        f"to = {{ lat = {cand.upstream.lat}, lon = {cand.upstream.lon} }}\n"
    )


# ── read side ─────────────────────────────────────────────────────────────────


def stretches_geojson(db: Database, jurisdiction: str | None = None) -> dict:
    """Published stretches as a GeoJSON FeatureCollection, in curation order."""
    features = [
        {
            "type": "Feature",
            "id": s.stretch_id,
            "geometry": s.geometry,
            "properties": {
                "stretch_id": s.stretch_id,
                "name": s.name,
                "river": s.river,
                "region": s.region,
                "jurisdiction": s.jurisdiction,
                "length_km": s.length_km,
                "segment_count": s.segment_count,
                "bbox": list(s.bbox),
                "label_point": [s.centroid_lon, s.centroid_lat],
                "source": s.source,
                "built_at": s.built_at,
            },
        }
        for s in list_stretches(db, jurisdiction)
    ]
    return {"type": "FeatureCollection", "features": features}
