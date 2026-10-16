"""The term registry: one entry per map/habitat concept.

Every feature type the map can show has a `term_id` here, with three wordings:

  * `plain`      what someone who has never heard the word would understand
  * `technical`  the word itself
  * `definition` one or two factual sentences saying what the thing is

The backend returns structured facts plus these term_ids and never decides
presentation. Which wording a given person sees is the presentation layer's
call (`services/presentation.py`, from their per-concept literacy) and the
component's — not the API's. Definitions say what a thing *is*, never whether
it is a good place to fish; a test enforces it.

`mapped_from` names where features of this type come from on the map, so a
label can carry its source. A term with `mapped_from=None` has no map layer
(yet): it is in the registry so literacy can be tracked against it.
`barometric_pressure` is one that never will — it is a weather concept, and
`services/weather_notes.py` reads its literacy to decide who gets the
weather-behaviour note. It is not a map layer, so it does not count toward the
map's default layer density.

This replaced `translate.GLOSSARY`, which held three of these definitions as
bare strings with no id.

The ids `confluence` and `tailwater` are kept, because literacy evidence is
stored against them, but neither word is ever shown: not as plain wording, not
as the "technical" name, not in the definition. The technical names are "Stream
junction" and "Below the dam".
"""

from typing import TypedDict


class Term(TypedDict):
    term_id: str
    plain: str
    technical: str
    definition: str
    mapped_from: str | None


_OHN = "Ontario government water map (stream lines)"
_OHN_OUTLINE = "Ontario government water map (lake outlines)"
_LIO_BATHYMETRY = "Ontario government lake depth surveys (depth contours)"
_OSM_DAMS = "OpenStreetMap volunteers (dams and weirs)"

TERMS: dict[str, Term] = {
    t["term_id"]: t
    for t in (
        Term(
            term_id="confluence",
            plain="Where two streams join",
            technical="Stream junction",
            definition="A stream junction is the place where two streams meet and become one.",
            mapped_from=_OHN,
        ),
        Term(
            term_id="river_mouth",
            plain="Where a river meets a lake",
            technical="River mouth",
            definition="A river mouth is where a river or creek flows out into a lake.",
            mapped_from=_OHN,
        ),
        Term(
            term_id="dam",
            plain="A wall holding back a river",
            technical="Dam",
            definition=(
                "A dam is a wall built across a river to hold water back. "
                "It usually forms a pond or reservoir above it."
            ),
            mapped_from=_OSM_DAMS,
        ),
        Term(
            term_id="weir",
            plain="A low wall across a river",
            technical="Weir",
            definition=(
                "A weir is a low wall built across a river that water flows over. "
                "It holds the water back a little."
            ),
            mapped_from=_OSM_DAMS,
        ),
        Term(
            term_id="tailwater",
            plain="The water just below a dam",
            technical="Below the dam",
            definition="The water below a dam is the part of a river just downstream of it.",
            mapped_from=_OSM_DAMS,
        ),
        Term(
            term_id="culvert",
            plain="A stream running through a pipe under a road",
            technical="Culvert",
            definition=(
                "A culvert is a pipe or box channel that carries a stream under a "
                "road, trail or railway."
            ),
            mapped_from="Ontario government water map and OpenStreetMap (barriers)",
        ),
        Term(
            term_id="inlet",
            plain="Where a stream flows into a lake",
            technical="Inlet",
            definition="An inlet is a place where a stream flows into a lake.",
            mapped_from=_OHN_OUTLINE,
        ),
        Term(
            term_id="outlet",
            plain="Where a stream leaves a lake",
            technical="Outlet",
            definition="An outlet is a place where a stream starts at a lake and flows away.",
            mapped_from=_OHN_OUTLINE,
        ),
        Term(
            term_id="island",
            plain="Land inside the water",
            technical="Island",
            definition="An island is land surrounded on all sides by water.",
            mapped_from=_OHN_OUTLINE,
        ),
        Term(
            term_id="point",
            plain="Land reaching out into the water",
            technical="Point",
            definition="A point is a narrow piece of shore that sticks out into a lake.",
            mapped_from=_OHN_OUTLINE,
        ),
        Term(
            term_id="peninsula",
            plain="A long arm of land reaching into the water",
            technical="Peninsula",
            definition=(
                "A peninsula is land with water on most sides, joined to the shore at one end."
            ),
            mapped_from=_OHN_OUTLINE,
        ),
        Term(
            term_id="drop_off",
            plain="Where the bottom gets deep quickly",
            technical="Drop-off",
            definition=(
                "A drop-off is a place where the bottom of a lake or river slopes "
                "steeply from shallow to deep water."
            ),
            mapped_from=_LIO_BATHYMETRY,
        ),
        Term(
            term_id="hump",
            plain="A shallow rise in the lake bottom",
            technical="Hump",
            definition=(
                "A hump is a place where the bottom of a lake rises to shallower "
                "water than the water around it."
            ),
            mapped_from=_LIO_BATHYMETRY,
        ),
        Term(
            term_id="weed_edge",
            plain="The edge of a weed bed",
            technical="Weed edge",
            definition=(
                "A weed edge is the boundary between a bed of underwater plants and open water."
            ),
            mapped_from="Your own weed reports",
        ),
        Term(
            term_id="barometric_pressure",
            plain="How heavy the air is",
            technical="Barometric pressure",
            definition=(
                "Barometric pressure is the weight of the air above a place, "
                "measured in hectopascals (hPa). It rises and falls as weather "
                "systems pass, so the direction it is moving says which way the "
                "weather is heading."
            ),
            mapped_from=None,
        ),
    )
}

TERM_ORDER: tuple[str, ...] = tuple(TERMS)

# Which concepts a feature on the map is an instance of. A spot can be more
# than one: below a dam is both the dam and its tailwater. Quiet water and
# public fishing spots are rankings or access, not habitat concepts, so they
# carry none.
SPOT_KIND_TERMS: dict[str, tuple[str, ...]] = {
    "river_mouth": ("river_mouth",),
    "creek_junction": ("confluence",),
    "below_dam": ("dam", "tailwater"),
    "public_fishing": (),
    "quiet_water": (),
}
SHORELINE_KIND_TERMS: dict[str, tuple[str, ...]] = {
    k: (k,) for k in ("inlet", "outlet", "island", "point", "peninsula", "drop_off", "hump")
}


def spot_terms(kind: str, dam_kind: str | None = None) -> tuple[str, ...]:
    """The term_ids a spot of this kind is an instance of.

    A weir is not a dam to someone learning the words, so `below_dam` with
    `dam_kind="weir"` maps to weir + tailwater.
    """
    ids = SPOT_KIND_TERMS.get(kind, ())
    if kind == "below_dam" and dam_kind == "weir":
        return ("weir", "tailwater")
    return ids


def get(term_id: str) -> Term | None:
    return TERMS.get(term_id)


# The hard words a spot card's own sentences may contain, which get a "What's a
# weir?" link on the card. Only words the jargon lint bans elsewhere: "point"
# or "island" in a sentence is plain English and needs no link.
CARD_LINKED_TERMS: tuple[str, ...] = ("weir", "confluence", "tailwater")


def terms_payload() -> list[dict]:
    """The whole registry as JSON, in a stable order: what `GET /terms` serves."""
    return [dict(TERMS[tid]) for tid in TERM_ORDER]
