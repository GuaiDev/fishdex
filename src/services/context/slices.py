"""Slice builders for describe().

Each builder returns a populated slice or a slice whose fields carry a
specific empty_reason. None of them ever returns a bare "no data" — the four
empty cases render differently on every surface, so they stay distinct all
the way through.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlite_utils import Database

from src.models.context import (
    AccessSlice,
    ConditionsSlice,
    ContextField,
    EmptyReason,
    HistorySlice,
    Place,
    Provenance,
    ProvenanceKind,
    RecordsSlice,
    SpeciesRecord,
    StructureSlice,
    WaterSlice,
)
from src.models.freshness import GAUGE_STALE_S, WATER_QUALITY_STALE_S, WEATHER_STALE_S
from src.services.context import translate

logger = logging.getLogger(__name__)

_KM_PER_DEGREE = 111.0

_FEATURE_MATRIX_PATH = Path("data/processed/sdm_feature_matrix.parquet")


# ── records (the only escalating slice) ───────────────────────────────────────


def build_records(
    db: Database,
    place: Place,
    species_filter: str | None = None,
    days_back: int = 3650,
    user_id: int = 1,
    escalate: bool = False,
) -> RecordsSlice:
    """Species recorded at this place, from every grounded source we hold.

    The only slice that escalates. `escalate` is off by default and switched on
    per caller bundle: a map tap must not fire a paid live search on every pan
    of the map, while a coaching question is worth one.
    """
    found: dict[str, SpeciesRecord] = {}

    for rec in _inat_records(db, place, species_filter, days_back):
        _merge(found, rec)
    for rec in _gbif_records(db, place, species_filter):
        _merge(found, rec)
    for rec in _survey_records(db, place, species_filter):
        _merge(found, rec)
    for rec in _user_catch_records(db, place, species_filter, user_id):
        _merge(found, rec)

    if not found:
        local_reason = _no_records_reason(db)
        if escalate:
            slice_ = _escalate(place, species_filter, local_reason)
        else:
            slice_ = RecordsSlice(radius_km=place.radius_km, empty_reason=local_reason)
        slice_.piscivore_activity = _piscivore_field(db, place)
        return slice_

    ordered = sorted(found.values(), key=lambda r: (-r.count, r.species))
    return RecordsSlice(
        species=ordered,
        total_count=sum(r.count for r in ordered),
        radius_km=place.radius_km,
        piscivore_activity=_piscivore_field(db, place),
    )


def _piscivore_field(db: Database, place: Place) -> ContextField:
    """Fish-eating birds as a weak presence proxy.

    Reported with the confidence word the service derives and nothing more —
    a heron count is not a fish count, and the "so what" is the only part of
    it an angler can use.
    """
    if "bird_observations" not in db.table_names():
        return ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
    try:
        from src.services.ebird import get_piscivore_activity_for_agent

        raw = json.loads(
            get_piscivore_activity_for_agent(
                lat=place.lat, lng=place.lng, radius_km=place.radius_km
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("piscivore slice failed", exc_info=True)
        return ContextField.empty(EmptyReason.LIVE_LOOKUP_FAILED)

    count = int(raw.get("combined_count") or 0)
    if not count:
        # Bird records are unusually prone to being read as absence, so the
        # reason has to say which kind of empty this is: eBird coverage tracks
        # birders, not birds.
        return ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)

    confidence = raw.get("fish_presence_confidence") or "unknown"
    sources = ", ".join(raw.get("sources") or []) or "eBird"
    return ContextField.recorded(
        f"{count} piscivore bird record(s), fish-presence signal: {confidence}",
        source=sources,
        meaning=(
            "birds that eat fish hunt where fish are — weak positive evidence, "
            "and low counts track observer effort rather than fish absence"
        ),
    )


def _escalate(
    place: Place, species_filter: str | None, local_reason: EmptyReason
) -> RecordsSlice:
    """Second rung: nothing local, so try the web before giving an honest empty.

    The local reason is kept when the search also comes up dry and the local
    gap is the more informative of the two — "we don't cover this area" tells
    the reader something a bare "the web had nothing" does not.
    """
    from src.services.context.escalation import escalate_records

    records, web_reason = escalate_records(
        place_name=place.name or place.query,
        lat=place.lat,
        lng=place.lng,
        species_filter=species_filter,
    )
    if records:
        return RecordsSlice(
            species=records,
            total_count=len(records),
            radius_km=place.radius_km,
            escalated_to_web=True,
        )

    reason = web_reason or local_reason
    if (
        reason is EmptyReason.WEB_SEARCH_EMPTY
        and local_reason is EmptyReason.SOURCE_DOES_NOT_COVER_AREA
    ):
        reason = local_reason
    return RecordsSlice(
        radius_km=place.radius_km, empty_reason=reason, escalated_to_web=True
    )


def _no_records_reason(db: Database) -> EmptyReason:
    """Distinguish 'nothing here' from 'we hold nothing for this area at all'."""
    for table in ("observations", "gbif_observations"):
        if table in db.table_names() and db[table].count > 0:
            return EmptyReason.NO_RECORDS_IN_RADIUS
    return EmptyReason.SOURCE_DOES_NOT_COVER_AREA


def _merge(found: dict[str, SpeciesRecord], rec: SpeciesRecord) -> None:
    key = rec.species.strip().lower()
    existing = found.get(key)
    if existing is None:
        found[key] = rec
        return
    existing.count += rec.count
    existing.survey_record = _latest_survey(existing.survey_record, rec.survey_record)

    # Date, source and obscured flag are one coherent triple: they all describe
    # the single most recent record we hold for this species here. Moving them
    # independently produced lines like "most recent 2025-10-29 [GBIF, 1979]" —
    # a 2025 iNaturalist sighting wearing a 1979 museum specimen's attribution,
    # because a separate rule swapped in the precise record's provenance while
    # leaving the recent record's date in place. Two dates for one record, and
    # nothing to tell the reader which is real.
    if rec.most_recent and (
        existing.most_recent is None or rec.most_recent > existing.most_recent
    ):
        existing.most_recent = rec.most_recent
        existing.provenance = rec.provenance
        existing.is_obscured = rec.is_obscured


def _latest_survey(a: Provenance | None, b: Provenance | None) -> Provenance | None:
    """The more recent of two survey provenances; a dated one beats an undated one."""
    if a is None or b is None:
        return a or b
    return b if (b.date or "") > (a.date or "") else a


def _inat_records(
    db: Database, place: Place, species_filter: str | None, days_back: int
) -> list[SpeciesRecord]:
    if "observations" not in db.table_names():
        return []
    try:
        from src.storage.observations import query_observations

        obs, _ = query_observations(
            db,
            lat=place.lat,
            lng=place.lng,
            radius_km=place.radius_km,
            days_back=days_back,
            species_filter=species_filter,
            limit=200,
        )
    except Exception:  # noqa: BLE001 - a slice failure must not kill describe()
        logger.warning("iNaturalist slice failed", exc_info=True)
        return []

    out = []
    for o in obs:
        source = getattr(o, "source", None) or "iNaturalist"
        out.append(
            SpeciesRecord(
                species=o.species,
                common_name=getattr(o, "common_name", None),
                most_recent=str(getattr(o, "observed_on", "") or "") or None,
                is_obscured=bool(getattr(o, "is_obscured", False)),
                provenance=Provenance(
                    kind=ProvenanceKind.RECORD,
                    source=source,
                    date=str(getattr(o, "observed_on", "") or "") or None,
                ),
            )
        )
    return out


def _gbif_records(
    db: Database, place: Place, species_filter: str | None
) -> list[SpeciesRecord]:
    if "gbif_observations" not in db.table_names():
        return []
    try:
        from src.storage.gbif_observations import query_gbif_observations

        obs = query_gbif_observations(
            db,
            lat=place.lat,
            lng=place.lng,
            radius_km=place.radius_km,
            species_filter=species_filter,
        )
    except Exception:  # noqa: BLE001
        logger.warning("GBIF slice failed", exc_info=True)
        return []

    out = []
    for o in obs:
        provenance = Provenance(
            kind=ProvenanceKind.RECORD,
            source=f"GBIF/{getattr(o, 'dataset_name', '') or 'occurrence'}",
            date=str(getattr(o, "observed_on", "") or "") or None,
        )
        # A sampling protocol or an event id means the row came out of a
        # structured survey, not a casual sighting.
        is_survey = bool(getattr(o, "sampling_protocol", None) or getattr(o, "event_id", None))
        out.append(
            SpeciesRecord(
                species=o.species,
                common_name=getattr(o, "common_name", None),
                most_recent=provenance.date,
                provenance=provenance,
                survey_record=provenance if is_survey else None,
            )
        )
    return out


def _survey_records(db: Database, place: Place, species_filter: str | None) -> list[SpeciesRecord]:
    """Standardised fish surveys (e.g. TRCA electrofishing) within the radius.

    Empty until a survey adapter has been run, which is the common case today.
    """
    if "fish_surveys" not in db.table_names():
        return []
    deg = place.radius_km / _KM_PER_DEGREE
    try:
        rows = list(
            db["fish_surveys"].rows_where(
                "lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
                [place.lat - deg, place.lat + deg, place.lng - deg, place.lng + deg],
                order_by="visit_date desc",
                limit=500,
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("fish survey slice failed", exc_info=True)
        return []

    out = []
    for r in rows:
        name = r.get("species_scientific_name") or r.get("species_common_name")
        common = r.get("species_common_name")
        if not name:
            continue
        if species_filter and species_filter.lower() not in f"{name} {common}".lower():
            continue
        date = r.get("visit_date") or (str(r["sample_year"]) if r.get("sample_year") else None)
        provenance = Provenance(
            kind=ProvenanceKind.RECORD,
            source=r.get("survey_program") or "fish survey",
            date=date,
        )
        out.append(
            SpeciesRecord(
                species=name,
                common_name=common,
                most_recent=date,
                provenance=provenance,
                survey_record=provenance,
            )
        )
    return out


def _user_catch_records(
    db: Database, place: Place, species_filter: str | None, user_id: int
) -> list[SpeciesRecord]:
    """The user's own confirmed catches — the most trustworthy source there is."""
    if "stops" not in db.table_names():
        return []
    deg = place.radius_km / _KM_PER_DEGREE
    rows = list(
        db.execute(
            "SELECT st.species_caught, s.date "
            "FROM stops st JOIN sessions s ON st.session_id = s.id "
            "WHERE st.user_id = ? AND st.lat BETWEEN ? AND ? AND st.lng BETWEEN ? AND ?",
            [
                user_id,
                place.lat - deg,
                place.lat + deg,
                place.lng - deg,
                place.lng + deg,
            ],
        ).fetchall()
    )

    out: list[SpeciesRecord] = []
    for species_json, date in rows:
        for sp in _load_species_list(species_json):
            if species_filter and species_filter.lower() not in sp.lower():
                continue
            out.append(
                SpeciesRecord(
                    species=sp,
                    most_recent=str(date) if date else None,
                    provenance=Provenance(
                        kind=ProvenanceKind.RECORD,
                        source="your logged catch",
                        date=str(date) if date else None,
                    ),
                )
            )
    return out


def _load_species_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(s) for s in parsed] if isinstance(parsed, list) else []


# ── water ─────────────────────────────────────────────────────────────────────


def build_water(db: Database, place: Place) -> WaterSlice:
    slice_ = WaterSlice()
    _fill_thermal(db, place, slice_)
    _fill_substrate(db, place, slice_)
    _fill_chemistry(db, place, slice_)
    _fill_benthic(db, place, slice_)
    return slice_


def _fill_thermal(db: Database, place: Place, out: WaterSlice) -> None:
    if "stream_temperature_summaries" not in db.table_names():
        out.thermal_class = ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
        return
    try:
        from src.storage.stream_temperature import query_temperature_summaries

        rows = query_temperature_summaries(
            db, lat=place.lat, lng=place.lng, radius_km=max(place.radius_km, 25.0)
        )
    except Exception:  # noqa: BLE001
        logger.warning("thermal slice failed", exc_info=True)
        rows = []

    for r in rows:
        regime = getattr(r, "thermal_regime", None)
        meaning = translate.thermal_regime(regime)
        if meaning:
            out.thermal_class = ContextField.recorded(
                regime, source=f"DFO station {getattr(r, 'station_id', '?')}", meaning=meaning
            )
            return
    out.thermal_class = ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)


def _fill_substrate(db: Database, place: Place, out: WaterSlice) -> None:
    if "geology_units" not in db.table_names():
        out.substrate = ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
        return
    try:
        from src.storage.geology import query_substrate_at_point

        unit = query_substrate_at_point(db, place.lat, place.lng)
    except Exception:  # noqa: BLE001
        logger.warning("substrate slice failed", exc_info=True)
        unit = None

    if unit is None:
        out.substrate = ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)
        return

    category = (
        getattr(unit, "substrate_class", None)
        or getattr(unit, "primary_material", None)
    )
    meaning = translate.substrate(category)
    out.substrate = (
        ContextField.recorded(category, source="Ontario surficial geology", meaning=meaning)
        if meaning
        else ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)
    )


_CHEMISTRY_SOURCES = {
    "CA-ON": "PWQMN",
    "CA-BC": "BC EnMoDS",
    "CA-QC": "MELCCFP Réseau-rivières",
}


def _chemistry_source(rows: list) -> str:
    """Name the network(s) the readings came from, by their jurisdiction code."""
    codes = sorted({getattr(r, "jurisdiction", None) or "CA-ON" for r in rows})
    names = [_CHEMISTRY_SOURCES.get(c, f"water quality ({c})") for c in codes]
    return " + ".join(names) if names else "water quality"


# What each chemistry field is read from, on the sample row.
_SAMPLE_FIELDS = ("do_mgl", "ph", "temp_c")


def _nearest_water_samples(db: Database, place: Place) -> dict[str, tuple]:
    """For each chemistry field, the nearest *dated* sample: (reading, distance_km).

    Nearest by distance, then newest — rows from one station share a distance,
    so that is the station's latest sample of the field. Fields are chosen
    independently: the nearest station that measured pH is not necessarily the
    one that measured dissolved oxygen. The sample's own date travels with it;
    an undated median across stations and years would hide that this is one
    afternoon's reading.
    """
    from src.storage.pins import distance_m
    from src.storage.water_quality import query_water_quality

    radius_km = max(place.radius_km, 25.0)
    try:
        rows = query_water_quality(db, lat=place.lat, lng=place.lng, radius_km=radius_km)
    except Exception:  # noqa: BLE001
        logger.warning("water sample lookup failed", exc_info=True)
        return {}

    best: dict[str, tuple] = {}
    for r in rows:
        if r.lat is None or r.lng is None:
            continue
        dist_km = distance_m(place.lat, place.lng, r.lat, r.lng) / 1000.0
        if dist_km > radius_km:
            continue
        for fld in _SAMPLE_FIELDS:
            if getattr(r, fld, None) is None:
                continue
            key = (dist_km, -r.sampled_at.toordinal())
            if fld not in best or key < best[fld][2]:
                best[fld] = (r, round(dist_km, 1), key)
    return {fld: (r, d) for fld, (r, d, _) in best.items()}


def _sample_field(
    sample: tuple, value: float, meaning: str | None, now: datetime | None
) -> ContextField:
    """A dated sample value as a field, sourced to its station and stale by age."""
    reading, dist_km = sample
    return ContextField.recorded(
        round(float(value), 1),
        source=_chemistry_source([reading]),
        station=reading.station_name or reading.station_id,
        distance_km=dist_km,
        observed_at=reading.sampled_at,
        stale_after_s=WATER_QUALITY_STALE_S,
        meaning=meaning,
        now=now,
    )


def _fill_chemistry(db: Database, place: Place, out: WaterSlice) -> None:
    if "water_quality_readings" not in db.table_names():
        out.dissolved_oxygen = ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
        out.ph = ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
        return
    samples = _nearest_water_samples(db, place)

    if "do_mgl" in samples:
        value = samples["do_mgl"][0].do_mgl
        out.dissolved_oxygen = _sample_field(
            samples["do_mgl"], value, translate.dissolved_oxygen(float(value)), None
        )
    else:
        out.dissolved_oxygen = ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)

    if "ph" in samples:
        value = samples["ph"][0].ph
        meaning = translate.ph(float(value))
        # A mid-range pH was measured; it just tells an angler nothing. Saying
        # "nothing recorded" would be false — the reading exists.
        out.ph = (
            _sample_field(samples["ph"], value, meaning, None)
            if meaning
            else ContextField.empty(EmptyReason.RECORDED_BUT_NOT_DECISION_RELEVANT)
        )
    else:
        out.ph = ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)


def _fill_benthic(db: Database, place: Place, out: WaterSlice) -> None:
    if "benthic_samples" not in db.table_names():
        out.benthic_health = ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
        return
    try:
        from src.storage.benthic import query_benthic

        rows = query_benthic(
            db, lat=place.lat, lng=place.lng, radius_km=max(place.radius_km, 25.0)
        )
    except Exception:  # noqa: BLE001
        logger.warning("benthic slice failed", exc_info=True)
        rows = []

    vals = [
        r.ept_proportion for r in rows if getattr(r, "ept_proportion", None) is not None
    ]
    if not vals:
        out.benthic_health = ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)
        return
    mean = sum(float(v) for v in vals) / len(vals)
    out.benthic_health = ContextField.recorded(
        round(mean, 2),
        source=f"CABIN ({len(vals)} samples)",
        meaning=translate.ept_proportion(mean),
    )


# ── structure ─────────────────────────────────────────────────────────────────


def build_structure(db: Database, place: Place) -> StructureSlice:
    out = StructureSlice()

    order = _stream_order(db, place)
    if order is not None:
        out.stream_order = ContextField.recorded(
            order, source="OHN", meaning=translate.stream_order(order)
        )
    elif place.segment_ids:
        # We resolved segments here, so the water is covered — the column is
        # simply not populated in them. Saying "nothing recorded" would blame
        # the world for a gap in our own ingest.
        out.stream_order = ContextField.empty(EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE)
    else:
        out.stream_order = ContextField.empty(
            EmptyReason.NO_RECORDS_IN_RADIUS
            if _table_has_rows(db, "stream_segments")
            else EmptyReason.SOURCE_DOES_NOT_COVER_AREA
        )

    out.is_confluence, out.waterbody_connection = _confluence_and_waterbody(db, place)

    # A barrier count of zero is only a fact where we actually hold barrier
    # data. Checking that the TABLE is non-empty is not enough: barrier ingest
    # is radius-limited, so a corpus covering only southern Ontario would let a
    # query at 52N sail through and assert "fish can move through freely" from
    # no local evidence at all — inference wearing a record tag, which is the
    # exact failure this layer exists to stop. Coverage is therefore local.
    if not _has_barrier_coverage(db, place):
        empty = ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
        out.barriers_upstream = empty
        out.barriers_downstream = empty
        return out

    up, down = _barrier_counts(db, place)
    meaning = translate.barriers(up, down)
    out.barriers_upstream = ContextField.recorded(up, source="OHN barriers", meaning=meaning)
    out.barriers_downstream = ContextField.recorded(down, source="OHN barriers")
    return out


def _table_has_rows(db: Database, table: str) -> bool:
    """True only if the table exists AND holds at least one row.

    Presence of an empty table means the schema was created, not that the data
    was ingested — treating those as the same thing manufactures false facts.
    """
    if table not in db.table_names():
        return False
    try:
        return db[table].count > 0
    except Exception:  # noqa: BLE001
        return False


# How far out we look before concluding barrier data covers this place. Wide
# enough that a genuinely barrier-free catchment still reads as covered, narrow
# enough that a distant ingest footprint does not vouch for the whole province.
_BARRIER_COVERAGE_RADIUS_KM = 100.0


def _has_barrier_coverage(db: Database, place: Place) -> bool:
    """True if any mapped barrier lies near enough to make absence meaningful."""
    if not _table_has_rows(db, "barriers"):
        return False
    deg = _BARRIER_COVERAGE_RADIUS_KM / _KM_PER_DEGREE
    for row in db["barriers"].rows:
        blat, blng = _barrier_point(row)
        if blat is None or blng is None:
            continue
        if abs(blat - place.lat) <= deg and abs(blng - place.lng) <= deg:
            return True
    return False


def _confluence_and_waterbody(
    db: Database, place: Place
) -> tuple[ContextField, ContextField]:
    """Structural flags from the Phase 3a feature matrix, when it is present."""
    if not place.segment_ids or not _FEATURE_MATRIX_PATH.exists():
        empty = ContextField.empty(
            EmptyReason.NO_RECORDS_IN_RADIUS
            if place.segment_ids
            else EmptyReason.SOURCE_DOES_NOT_COVER_AREA
        )
        return empty, empty

    try:
        import pandas as pd

        fm = pd.read_parquet(
            _FEATURE_MATRIX_PATH,
            columns=["ogf_id", "is_confluence_segment", "connected_to_waterbody"],
        )
        rows = fm[fm["ogf_id"].isin(place.segment_ids)]
    except Exception:  # noqa: BLE001
        logger.warning("structural flags unavailable", exc_info=True)
        empty = ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
        return empty, empty

    if rows.empty:
        empty = ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)
        return empty, empty

    is_conf = bool(rows["is_confluence_segment"].fillna(False).any())
    wb = bool(rows["connected_to_waterbody"].fillna(False).any())
    return (
        ContextField.recorded(
            is_conf,
            source="OHN confluence analysis",
            meaning="two streams meet in this reach" if is_conf else None,
        ),
        ContextField.recorded(
            wb,
            source="OHN connectivity",
            meaning="connects to a lake or pond" if wb else None,
        ),
    )


def _stream_order(db: Database, place: Place) -> int | None:
    if "stream_segments" not in db.table_names() or not place.segment_ids:
        return None
    rows = list(
        db["stream_segments"].rows_where(
            f"ogf_id IN ({','.join('?' * len(place.segment_ids))})",
            place.segment_ids,
        )
    )
    orders = [r["stream_order"] for r in rows if r.get("stream_order") is not None]
    return max(int(o) for o in orders) if orders else None


def _barrier_counts(db: Database, place: Place) -> tuple[int, int]:
    """Barriers near this place, split by whether they sit up- or downstream.

    Without a traversal of the connectivity graph this is a latitude proxy:
    upstream is inland/higher, downstream is toward the outlet. Good enough to
    say "fish stack below the dam"; not good enough to count fish passage.
    """
    deg = max(place.radius_km, 10.0) / _KM_PER_DEGREE
    rows = list(
        db["barriers"].rows_where(
            "nearest_segment_ogf_id IS NOT NULL", [], limit=5000
        )
    )
    up = down = 0
    for r in rows:
        blat, blng = _barrier_point(r)
        if blat is None or blng is None:
            continue
        if abs(blat - place.lat) > deg or abs(blng - place.lng) > deg:
            continue
        if blat > place.lat:
            up += 1
        else:
            down += 1
    return up, down


def _barrier_point(row: dict) -> tuple[float | None, float | None]:
    for lat_key, lng_key in (("lat", "lng"), ("centroid_lat", "centroid_lng")):
        if row.get(lat_key) is not None and row.get(lng_key) is not None:
            return float(row[lat_key]), float(row[lng_key])
    wkt = row.get("geom_wkt")
    if not wkt or "(" not in wkt:
        return None, None
    try:
        inner = wkt[wkt.index("(") + 1 : wkt.rindex(")")].strip("() ")
        parts = inner.split(",")[0].split()
        return float(parts[1]), float(parts[0])
    except (ValueError, IndexError):
        return None, None


# ── access ────────────────────────────────────────────────────────────────────


def build_access(db: Database, place: Place) -> AccessSlice:
    out = AccessSlice()
    deg = max(place.radius_km, 2.0) / _KM_PER_DEGREE

    if "access_points" not in db.table_names():
        empty = ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
        out.parking = empty
        out.trails = empty
    else:
        rows = list(
            db["access_points"].rows_where(
                "lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
                [place.lat - deg, place.lat + deg, place.lng - deg, place.lng + deg],
                limit=200,
            )
        )
        parking = [r for r in rows if "park" in str(r.get("access_type", "")).lower()]
        trails = [
            r
            for r in rows
            if any(k in str(r.get("access_type", "")).lower() for k in ("path", "trail", "track"))
        ]
        out.parking = (
            ContextField.recorded(
                len(parking), source="OpenStreetMap", meaning="somewhere to leave the car"
            )
            if parking
            else ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)
        )
        out.trails = (
            ContextField.recorded(len(trails), source="OpenStreetMap")
            if trails
            else ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)
        )

    out.crown_land = _crown_land(db, place)
    out.access_note = _access_note(out)
    return out


def _access_note(out: AccessSlice) -> ContextField:
    """Plain-language read on getting to the water, from the fields above."""
    if not out.crown_land.is_empty:
        return ContextField.inferred(
            "crown land nearby",
            meaning="public access generally permitted — verify local restrictions",
        )
    if not out.parking.is_empty:
        return ContextField.inferred(
            "parking mapped nearby", meaning="reachable by road; verify right of way"
        )
    if not out.trails.is_empty:
        return ContextField.inferred(
            "trail access only", meaning="expect a walk in; no mapped parking"
        )
    return ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)


def _crown_land(db: Database, place: Place) -> ContextField:
    # An empty table means the schema exists, not that the data was ingested:
    # "no Crown land nearby" would be a claim from no evidence at all.
    if not _table_has_rows(db, "crown_land"):
        return ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
    deg = place.radius_km / _KM_PER_DEGREE
    rows = list(
        db["crown_land"].rows_where(
            "centroid_lat BETWEEN ? AND ? AND centroid_lng BETWEEN ? AND ?",
            [place.lat - deg, place.lat + deg, place.lng - deg, place.lng + deg],
            limit=5,
        )
    )
    if rows:
        return ContextField.recorded(
            True,
            source="Ontario Crown Land",
            meaning="public access generally permitted — verify no local restriction",
        )
    return ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS)


# ── conditions (live — never cached beyond an hour) ───────────────────────────


def build_conditions(db: Database, place: Place, now: datetime | None = None) -> ConditionsSlice:
    """Conditions at a place. Every field is assigned on every path.

    Leaving a field default-constructed means no value AND no reason, which
    renders the internal "this is a bug" fallback to the reader. Each source is
    filled by its own function, so one failing never blanks another.
    """
    now = now or datetime.now(UTC)
    out = ConditionsSlice()
    _fill_weather(place, out, now)
    _fill_gauge(place, out, now)
    _fill_water_samples(db, place, out, now)
    return out


def _live_field(
    raw: dict,
    key: str,
    source: str,
    observed_at: str | None,
    limit_s: int,
    now: datetime,
    unavailable: EmptyReason,
    meaning: str | None = None,
) -> ContextField:
    value = raw.get(key)
    if value is None:
        return ContextField.empty(unavailable)
    return ContextField.recorded(
        value,
        source=source,
        observed_at=observed_at,
        stale_after_s=limit_s,
        meaning=meaning,
        now=now,
    )


def _fill_weather(place: Place, out: ConditionsSlice, now: datetime) -> None:
    try:
        from src.services.weather import get_conditions_for_agent

        raw = json.loads(get_conditions_for_agent(lat=place.lat, lng=place.lng))
        unavailable = EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE
    except Exception:  # noqa: BLE001 - live fetch must never break describe()
        logger.warning("weather conditions failed", exc_info=True)
        raw = {}
        unavailable = EmptyReason.LIVE_LOOKUP_FAILED

    seen = raw.get("time")
    trend = raw.get("pressure_trend")

    def field(key: str, meaning: str | None = None) -> ContextField:
        return _live_field(
            raw, key, "Open-Meteo", seen, WEATHER_STALE_S, now, unavailable, meaning
        )

    out.air_temp_c = field("temperature_c")
    out.wind_speed_kmh = field("wind_speed_kmh")
    out.precipitation_mm = field("precipitation_mm")
    out.pressure_hpa = field("pressure_hpa")
    out.pressure_trend = field("pressure_trend", translate.pressure_trend(trend))


def _fill_gauge(place: Place, out: ConditionsSlice, now: datetime) -> None:
    from src.services.stream_gauge import nearest_gauge

    fields = ("flow_cms", "flow_trend", "level_m", "level_trend")
    try:
        gauge = nearest_gauge(place.lat, place.lng)
    except Exception:  # noqa: BLE001 - live fetch must never break describe()
        logger.warning("gauge lookup failed", exc_info=True)
        for name in fields:
            setattr(out, name, ContextField.empty(EmptyReason.LIVE_LOOKUP_FAILED))
        return
    if gauge is None:
        for name in fields:
            setattr(out, name, ContextField.empty(EmptyReason.NO_RECORDS_IN_RADIUS))
        return

    r = gauge.reading
    where = {
        "source": f"WSC station {r.station_id}",
        "station": r.station_name or r.station_id,
        "distance_km": gauge.distance_km,
        "observed_at": r.reading_datetime,
        "stale_after_s": GAUGE_STALE_S,
        "now": now,
    }
    missing = EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE

    def reading(value, unit_round: int) -> ContextField:
        if value is None:
            return ContextField.empty(missing)
        return ContextField.recorded(round(float(value), unit_round), **where)

    def trend(raw_trend: str | None) -> ContextField:
        said = translate.flow_trend(raw_trend)
        if said is None:
            return ContextField.empty(missing)
        word, meaning = said
        return ContextField.recorded(word, meaning=meaning, **where)

    out.flow_cms = reading(r.discharge_cms, 3)
    out.flow_trend = trend(r.discharge_trend)
    out.level_m = reading(r.water_level_m, 3)
    out.level_trend = trend(r.level_trend)


def _fill_water_samples(db: Database, place: Place, out: ConditionsSlice, now: datetime) -> None:
    """Water temperature and chemistry: the nearest dated PWQMN sample, or why none.

    There is no live water-temperature feed. A grab sample is the best there
    is, and it is shown as what it is — dated, at a named station, stale once
    past WATER_QUALITY_STALE_S. Without one the field says there is no live
    source, not that the source "does not cover" the place.
    """
    if "water_quality_readings" not in db.table_names():
        samples: dict[str, tuple] = {}
        no_table = True
    else:
        samples = _nearest_water_samples(db, place)
        no_table = False

    one_sample = "a single dated sample at the nearest monitoring station, not a live reading"
    if "temp_c" in samples:
        temp = samples["temp_c"]
        out.water_temp_c = _sample_field(temp, temp[0].temp_c, one_sample, now)
    else:
        out.water_temp_c = ContextField.empty(EmptyReason.NO_LIVE_SOURCE)

    if "do_mgl" in samples:
        value = samples["do_mgl"][0].do_mgl
        out.dissolved_oxygen_mgl = _sample_field(
            samples["do_mgl"], value, translate.dissolved_oxygen(float(value)), now
        )
    else:
        out.dissolved_oxygen_mgl = ContextField.empty(
            EmptyReason.SOURCE_DOES_NOT_COVER_AREA if no_table else EmptyReason.NO_RECORDS_IN_RADIUS
        )

    if "ph" in samples:
        value = samples["ph"][0].ph
        out.ph = _sample_field(samples["ph"], value, translate.ph(float(value)), now)
    else:
        out.ph = ContextField.empty(
            EmptyReason.SOURCE_DOES_NOT_COVER_AREA if no_table else EmptyReason.NO_RECORDS_IN_RADIUS
        )


# ── history ───────────────────────────────────────────────────────────────────


def build_history(db: Database, place: Place, user_id: int = 1) -> HistorySlice:
    """The user's own record at this place — including blanks.

    Blanks are half the analytical signal and the hardest behaviour to get,
    so they are counted explicitly rather than inferred from absence.
    """
    if "stops" not in db.table_names():
        return HistorySlice(empty_reason=EmptyReason.USER_NEVER_FISHED_HERE)

    deg = place.radius_km / _KM_PER_DEGREE
    rows = list(
        db.execute(
            "SELECT st.species_caught, st.was_productive, st.technique, s.date "
            "FROM stops st JOIN sessions s ON st.session_id = s.id "
            "WHERE st.user_id = ? AND st.lat BETWEEN ? AND ? AND st.lng BETWEEN ? AND ?",
            [
                user_id,
                place.lat - deg,
                place.lat + deg,
                place.lng - deg,
                place.lng + deg,
            ],
        ).fetchall()
    )
    if not rows:
        return HistorySlice(empty_reason=EmptyReason.USER_NEVER_FISHED_HERE)

    species: set[str] = set()
    techniques: set[str] = set()
    productive = blanks = 0
    last: str | None = None

    for species_json, was_productive, technique, date in rows:
        caught = _load_species_list(species_json)
        species.update(caught)
        if technique:
            techniques.add(str(technique))
        if was_productive:
            productive += 1
        else:
            blanks += 1
        if date and (last is None or str(date) > last):
            last = str(date)

    return HistorySlice(
        visits=len(rows),
        productive_visits=productive,
        blanks=blanks,
        species_caught=sorted(species),
        last_visit=last,
        techniques_used=sorted(techniques),
    )
