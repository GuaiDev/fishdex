# Pin match and Today API

The match indicator and the Today view, for whoever builds the Personal-map UI. Both answer
from the angler's own logged trips and today's conditions at the pin, and nothing else: no
model, no other angler's data, no prediction.

You can build both without a running server. **`tests/fixtures/pin_match_response.json`**,
**`pin_match_hidden_response.json`** and **`pin_today_response.json`** are real responses from
the services, on a temporary database with canned "right now" readings (10 °C, 14 km/h,
1011 hPa, a gauge at 18.3 m³/s) and the day fixed at 2026-10-14. They are not recordings of
live sources; `tests/test_pin_match_fixtures.py` fails if the code stops returning them.

Every route needs `Authorization: Bearer <token>`. The user comes from the token only. Another
user's pin answers `404 {"detail": "No such pin"}`, byte-identical to a pin that does not exist,
and makes no outside call.

## The match: `GET /pins/{pin_id}/match`

How today's conditions compare with the conditions on the trips the angler rated best at this pin.

1. The angler's **rated trips at this pin**: trips with a stop linked to the pin and a rating on
   that link. A trip with two stops here counts once. Recalled visits ("I've fished here
   before") are a separate table and are never read. Unrated trips are not counted.
2. **Fewer than `min_rated_trips`** (3): the match is `hidden`. Show the conditions panel and
   nothing else. No outside lookup is made.
3. The **best trips** are the top `best_trip_count` (3) by rating, ties at the cut-off all kept,
   and only those rated `min_best_rating` (3, "decent") or better. A pin where every trip was
   slow has no good day to compare against: status `few_good_trips`.
4. For each condition, the range across the best trips, from the conditions stored with each
   trip when it was logged. Today's reading is **inside** the range, **near** it (outside by
   less than a small margin), or **below** / **above** it.
5. A condition is **left out**, with the reason, when today's reading is stale (the same stale
   limits as the panel) or missing, when fewer than 2 best trips recorded it, when the best
   trips' flow came from a different gauge than today's (a flow from another station is another
   river), or when the best trips' water samples were taken more than 30 days from the trip.

`score` is the share of compared conditions that are inside or near. It exists to order pins;
never show it as a chance of catching fish. The match describes how today resembles the
angler's own good days. It does not say fish are there, and it does not say "normal": there is
no seasonal baseline behind the gauge feed, so flow is only ever compared with the angler's
own trips.

| key | meaning |
|---|---|
| `label` | the indicator's name. A placeholder, from `GET /pins/labels` |
| `shown` | `false` only for `hidden` |
| `status` | `hidden`, `similar`, `partly`, `different`, `not_enough_readings` (fewer than 2 conditions could be compared) or `few_good_trips` |
| `status_label` | words for the status, `null` when hidden |
| `rated_trips`, `min_rated_trips` | how many rated trips the pin has, and how many it needs |
| `best_trips`, `best_months` | how many trips set the ranges, and the months they were in |
| `compared`, `inside`, `score` | conditions compared, how many were inside or near, and `inside / compared` (`null` when nothing was compared) |
| `comparisons[]` | per condition: `field`, `label`, `unit`, `current`, `low`, `high`, `trips` (best trips that recorded it), `position`, and `text`, a ready sentence |
| `left_out[]` | per condition: `field`, `label`, `reason`, `text`, and `empty_reason` when today's reading was missing (same codes as the panel) |
| `headline` | one sentence for a list row |
| `reason` | the whole explanation as plain text: best-trip summary, one sentence per comparison, the headline, what was left out |

Example `reason`, from the fixture:

> Your 3 best trips here were in April. Air temperature: 8–12°C on those trips, 10°C now
> (inside that range). … Today looks like your best trips here: 5 of 5 conditions compared are
> in their range. Not compared: Water temperature (no current reading), …

A **hidden** match carries only its counts and a headline telling the angler how many more
rated trips would show it. Per the plan, show the conditions panel alone; the headline is there
for rows that must say something.

## Today: `GET /pins/today?sort=match|distance|last_visited|favourites&lat=&lng=`

The angler's own pins, in the order they pick. `sort` defaults to `match`. `lat` and `lng` are
where the angler is: **required for `distance`** (`400` without them), optional otherwise, where
they only fill `distance_km`. Give both or neither (`400`). Out-of-range values are `422`.

| sort | order | `reason` says |
|---|---|---|
| `match` | similar, then partly, then unlike, then pins that could not be compared, then pins with too few rated trips (by score, then name) | the match headline: "Today looks like your best trips here: 3 of 3 conditions compared are in their range.", or "No match yet: 2 more rated trips here would show one." |
| `distance` | nearest first | "4.0 km from here" |
| `last_visited` | newest logged trip first; pins never fished last | "Last fished 5 days ago (2026-10-10)", or "No logged trips here yet" |
| `favourites` | favourites first, then newest trip first | "Marked as a favourite" / "Not a favourite" |

Every row, in every sort, has a non-empty `reason`, a 1-based `rank`, and: `pin_id`, `name`,
`lat`, `lng`, `jurisdiction` (ISO 3166-2, so the same view serves any province or state),
`is_favourite`, `state`, `visits`, `last_visit`, `distance_km`.

- **Only `sort=match` carries `match`** (the full object above); other sorts return `match: null`.
  Looking up today's conditions is a live call per pin, made only for pins with enough rated
  trips to be matched, so a sort that does not need it does not make it. To show a match under
  another sort, call `GET /pins/{id}/match` for the rows on screen.
- **Recalled visits are not trips.** A pin with only recalled history is `state: "visited"` but
  has no `last_visit` and sorts with the never-fished pins under `last_visited`.
- **Shared pins** (a later phase) are not rows here and cannot move the angler's own.
- There is no species filter yet. Catches stay indexed by user and species so one can be added
  without a schema change.

## Words and numbers

Every word the match and Today say, and every tunable number, is in `src/services/pins/labels.py`
and served by `GET /pins/labels`: `match_indicator_label`, `min_rated_trips_for_match`, the
`match` object (`best_trip_count`, `min_best_rating`, `min_trips_to_compare`,
`min_compared_conditions`, `status_labels`, `position_labels`, `left_out_reasons`, `fields`) and
`today_sort_labels`. The indicator's final name and layout are still an open decision, so none
of this is final. Do not hardcode wording; the API sentences (`text`, `headline`, `reason`) are
ready to show.

Temperatures are in °C and flow in m³/s, as the sources give them. A US state's feeds will need
unit conversion in the labels module when they are added; nothing in the match is specific to
Ontario.
