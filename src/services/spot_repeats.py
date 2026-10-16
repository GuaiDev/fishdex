"""Repeated structures: one spot per structure, and titles that tell the rest apart.

A river with a weir every few hundred metres, or a run of unnamed creeks,
gives the classifier several spots with the same kind and the same title. A list
cannot tell them apart, and their 2 km record circles overlap so one set of
records is counted several times. Two steps, both pure and both working from
data every jurisdiction has (the spots, and the OSM access points and road
crossings already attached to them):

1. `merge_repeats` collapses spots of the same kind and the same title that sit
   within `REPEAT_MERGE_M[kind]` of each other into one. The title carries the
   water, so "same title" means the same structure kind on the same water.
2. `distinguish_titles` gives each spot that still shares its title with another
   a short place phrase: the nearest named road crossing, else the nearest named
   parking, trailhead or boat launch, else the nearest named creek that joins
   close by, else its place in order along the group ("2 of 4, west to east").

Nothing here is Ontario-specific. The words are in `spot_labels`.
"""

import math
from collections import defaultdict

from src.models.spot import AccessRef, Spot, SpotBuildReport, SpotKind
from src.services import spot_labels as words

# How close two spots of one kind and one title must be to count as the same
# structure. Measured on the lower Humber: nine weir spots, five of them within
# 340 m of each other. Junctions are tighter: two creeks 250 m apart are more
# likely two creeks than one mapped twice.
REPEAT_MERGE_M: dict[SpotKind, float] = {
    "below_dam": 400.0,
    "creek_junction": 250.0,
}
# A named creek joining within this distance is a landmark for a repeated title.
NEARBY_JUNCTION_M = 2000.0

_M_PER_DEG_LAT = 111_320.0


def _metres(a: Spot, b: Spot) -> float:
    dy = (b.lat - a.lat) * _M_PER_DEG_LAT
    dx = (b.lng - a.lng) * _M_PER_DEG_LAT * math.cos(math.radians((a.lat + b.lat) / 2))
    return math.hypot(dx, dy)


# ── merge ─────────────────────────────────────────────────────────────────────


def merge_repeats(spots: list[Spot], report: SpotBuildReport) -> list[Spot]:
    """Collapse near-coincident spots of one kind and title into the best of them.

    Greedy and order-independent: spots are taken best first (lowest tier, then
    id) and each joins the first cluster whose lead is within the kind's radius,
    else leads a new one. Joining is measured from the lead, never from the
    last member, so a line of weirs cannot chain into one spot.
    """
    groups: dict[tuple[str, str], list[Spot]] = defaultdict(list)
    keep: list[Spot] = []
    for spot in spots:
        if spot.kind in REPEAT_MERGE_M:
            groups[(spot.kind, spot.title)].append(spot)
        else:
            keep.append(spot)

    for (kind, _title), members in groups.items():
        radius = REPEAT_MERGE_M[kind]  # type: ignore[index]
        clusters: list[list[Spot]] = []
        for spot in sorted(members, key=lambda s: (s.tier, s.spot_id)):
            home = next((c for c in clusters if _metres(c[0], spot) <= radius), None)
            if home is None:
                clusters.append([spot])
            else:
                home.append(spot)
        for cluster in clusters:
            keep.append(_merged(cluster))
            report.drop("repeat_structure_merged", len(cluster) - 1)
    return keep


def _merged(cluster: list[Spot]) -> Spot:
    lead, rest = cluster[0], cluster[1:]
    if not rest:
        return lead
    attrs = dict(lead.structure_attrs)
    attrs["merged_spot_ids"] = sorted(s.spot_id for s in rest)
    also = set(attrs.get("also_on_water_ids", []))
    for s in rest:
        also |= set(s.structure_attrs.get("also_on_water_ids", []))
        if s.water_id and s.water_id != lead.water_id:
            also.add(s.water_id)
    if also:
        attrs["also_on_water_ids"] = sorted(also)
    lead.structure_attrs = attrs
    lead.sources = lead.sources + [src for s in rest for src in s.sources]
    return lead


# ── distinguish ───────────────────────────────────────────────────────────────


def distinguish_titles(spots: list[Spot], report: SpotBuildReport) -> None:
    """Give every spot that shares a kind and title a title of its own, in place.

    Run after access is attached: the road and parking names come from
    `bridges` and `getting_there`. Records how each title was told apart in
    `structure_attrs["title_distinguished_by"]` (`road`, `access`, `junction`
    or `order`) and counts them in the report.
    """
    groups: dict[tuple[str, str], list[Spot]] = defaultdict(list)
    for spot in spots:
        groups[(spot.kind, spot.title)].append(spot)
    junctions = [
        s for s in spots if s.kind == "creek_junction" and s.structure_attrs.get("tributary_name")
    ]

    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda s: s.spot_id)
        base = members[0].title
        proposals = {s.spot_id: _landmark(s, junctions) for s in members}
        titles = {
            s.spot_id: words.DISTINGUISHED_TITLES[proposals[s.spot_id][0]].format(
                title=base, place=proposals[s.spot_id][1]
            )
            if proposals[s.spot_id]
            else None
            for s in members
        }
        counts: dict[str | None, int] = defaultdict(int)
        for t in titles.values():
            counts[t] += 1
        order = _order_labels(members)
        for s in members:
            proposal = proposals[s.spot_id]
            title = titles[s.spot_id]
            if proposal and title and counts[title] == 1:
                basis = proposal[0]
            else:
                basis = "order"
                title = words.DISTINGUISHED_TITLES["order"].format(
                    title=title or base,
                    n=order[s.spot_id][0],
                    total=len(members),
                    direction=order[s.spot_id][1],
                )
            s.structure_attrs = {
                **s.structure_attrs,
                "title_distinguished_by": basis,
            }
            s.title = title
            report.distinguished(basis)


def _landmark(spot: Spot, junctions: list[Spot]) -> tuple[str, str] | None:
    """(basis, place) for the nearest landmark worth naming, or None."""
    road = _nearest_named(spot.bridges)
    if road:
        return "road", road
    access = _nearest_named(spot.getting_there)
    if access:
        return "access", access
    own = spot.structure_attrs.get("tributary_name")
    near = sorted(
        (
            (d, j)
            for j in junctions
            if j.spot_id != spot.spot_id
            and j.structure_attrs["tributary_name"] != own
            and (d := _metres(spot, j)) <= NEARBY_JUNCTION_M
        ),
        key=lambda t: (t[0], t[1].spot_id),
    )
    if near:
        return "junction", near[0][1].structure_attrs["tributary_name"]
    return None


def _nearest_named(refs: list[AccessRef]) -> str | None:
    named = [r for r in refs if r.name and r.name.strip()]
    if not named:
        return None
    return min(named, key=lambda r: (r.distance_m, r.source_ref)).name.strip()  # type: ignore[union-attr]


def _order_labels(members: list[Spot]) -> dict[str, tuple[int, str]]:
    """Each spot's place in the group, counted along the axis the group spreads on most."""
    lats = [s.lat for s in members]
    lngs = [s.lng for s in members]
    north_south = (max(lats) - min(lats)) * _M_PER_DEG_LAT > (
        max(lngs) - min(lngs)
    ) * _M_PER_DEG_LAT * math.cos(math.radians(sum(lats) / len(lats)))
    if north_south:
        ranked = sorted(members, key=lambda s: (-s.lat, s.spot_id))
        direction = words.DIRECTION_NORTH_TO_SOUTH
    else:
        ranked = sorted(members, key=lambda s: (s.lng, s.spot_id))
        direction = words.DIRECTION_WEST_TO_EAST
    return {s.spot_id: (i + 1, direction) for i, s in enumerate(ranked)}
