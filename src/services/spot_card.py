"""The spot card's read side: gather what is known near a spot, screen it, render it.

`GET /map/spots/{spot_id}` returns this card, so a front end never joins the
records, the labels and the access data itself. The pieces are:

  * `describe(..., caller="map_tap", radius_km=2)` for records, structure and
    access. The map-tap bundle has no history, no live call and no web
    escalation, so building a card costs nothing and cannot spend money.
  * the species-at-risk screen below, which decides which recorded species may
    not be named.
  * `special_here()`, the frequency of each species here against the rest of the
    spot's river section, which feeds the "recorded more often here" line.
  * `render_spot()`, the only place card text is produced.

The shared part of the card is the same for every user and is keyed by
`(spot_id, built_at)`; the key travels in `card.meta.cache_key`. The describe
call is made with no user at all, so the angler's own logged catches, which the
records slice would otherwise fold in, cannot reach the shared part.

The one per-user piece is "new to you" (`new_to_you.py`): when a user id is
given, their confirmed catches are read for this request, subtracted from the
nameable recorded species, and used to fill the hook and the `new_to_you` block.
They are read fresh each time and never written anywhere shared. With no user id
the card is exactly the shared one.
"""

from sqlite_utils.db import Database

from src.models.spot import Spot
from src.models.spot_card import SpotCard
from src.services.context import describe
from src.services.context.render import new_species_for_user, render_spot
from src.services.context.special import special_here
from src.services.context.species import listing_check
from src.services.new_to_you import caught_keys

CARD_RADIUS_KM = 2.0
# No such user exists (ids start at 1), so the records slice finds no personal
# catches. A card is the same for everyone.
NO_USER = 0


def screen_species(db: Database, ctx) -> tuple[frozenset[str], int]:
    """(protected, unchecked) for the species recorded in a describe() result.

    `protected` holds the lowercased `species` of every recorded species with an
    affirmative listing signal (`status_known_listed`), verified or not. It is
    never gated on `sar_alert`, which is true for all 69 species in the local
    file and would hide every fish. `unchecked` counts species the local file
    does not know at all, so a thin species file reads as thin rather than clean.
    """
    protected: set[str] = set()
    unchecked = 0
    for rec in ctx.records.species if ctx and ctx.records else []:
        listed = listing_check(db, scientific_name=rec.species, common_name=rec.common_name)
        if listed is None:
            unchecked += 1
        elif listed:
            protected.add(rec.species.strip().lower())
    return frozenset(protected), unchecked


def build_spot_card(db: Database, spot: Spot, user_id: int | None = None) -> SpotCard:
    caught = caught_keys(db, user_id, spot.jurisdiction) if user_id else None
    ctx = describe(
        db,
        lat=spot.lat,
        lng=spot.lng,
        radius_km=CARD_RADIUS_KM,
        caller="map_tap",
        user_id=NO_USER,
    )
    protected, unchecked = screen_species(db, ctx)
    # Quiet water is a reach, not a located structure on a curated river
    # section, so there is nothing to compare it with.
    special = None if spot.kind == "quiet_water" else special_here(db, spot, protected)
    return render_spot(
        spot, ctx, protected=protected, unchecked=unchecked, special=special, caught=caught
    )


def new_species_at_spot(db: Database, spot: Spot, caught: frozenset[str]) -> list:
    """The species the card would call new to this user, for the `new_to_me` filter.

    Same describe call, same listed-species screen and same ranking as the card,
    so the filter and the card agree by construction.
    """
    ctx = describe(
        db,
        lat=spot.lat,
        lng=spot.lng,
        radius_km=CARD_RADIUS_KM,
        caller="map_tap",
        user_id=NO_USER,
    )
    protected, _ = screen_species(db, ctx)
    return new_species_for_user(ctx, protected, caught)
