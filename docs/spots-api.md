# Spots API

The explore map's backend, for whoever builds the UI. A **spot** is one reason to go
to one place on the water: where a river runs out into a lake, where a creek joins a
river, the water just below a dam, an official fishing spot. The map shows spots, not
river lines; the river is context ("more spots on this river").

The spots and water-body endpoints are read-only GETs, and every endpoint here needs the same
`Authorization: Bearer <token>` as the other `/map/*` endpoints. The one exception to read-only is
the private [weed reports](#weed-reports) the user writes themselves. The older `/map/stretches` and
`/map/stretches/{id}/anchors` endpoints still work and are unchanged.

You can build the whole UI without a running server: **`tests/fixtures/spots_api_responses.json`**
holds real responses from every endpoint below (the examples in this file are copied
from it). Each entry is `{request, note, status, response}`.
The fixture also holds the `near` example, both layer endpoints, the quiet-water list and card,
and a card with a "recorded more often here" line.

## The flow

1. `GET /map/spots/labels` once. Every word the UI shows comes from it. Do not hardcode
   chip names, one-liners or the "why fish gather" paragraphs.
2. `GET /map/spots?bbox=…&kind=…&tier=…` whenever the map moves or a chip changes.
3. `GET /map/spots/{spot_id}` when a spot is tapped. It returns the spot and its
   **`card`**: the four blocks a tap reveals, already written. Render the card; do not
   fetch records, labels or access data yourself.
4. `GET /map/water-bodies?bbox=…&type=…` to name and group the rivers, creeks and lakes the
   spots sit on, `GET /map/water-bodies/{water_body_id}` for "more spots on this river", and
   `GET /map/water-bodies/{water_body_id}/features` for a lake's shoreline (inlets, outlets,
   islands, points).
5. `GET /map/weed-reports/layer?bbox=…` for the user's own reported weed edges, and
   `POST /map/weed-reports` when they report one on a water body. Private to them; see
   [Weed reports](#weed-reports).

## Rules the UI should keep

These come from the project's central idea: records of fish are not the same as
pressure from anglers, and a map must not blur them.

- Show `title` and `subtitle` as given. Titles are built from the river's name and never
  read "Unnamed …".
- The "why fish gather here" paragraph is **general reasoning about a kind of water, not a
  record about the spot**. Show it with its `label` ("General reasoning, not a record about
  this spot"), never as a fact about the spot.
- Never show `tier`. It decides what appears at which zoom and is not a quality claim.
  There is no rating, no score, no "popular", and none should be invented.
- Quiet water (`distinct: true`) looks different from the other kinds and is listed last.
  It is a ranking of reaches, not a located structure, and it means only that few public
  records exist there: few anglers, or just few observers. Show each spot's `caveats` with it.
  Never word it as a claim about fish, "undiscovered", "untouched" or "good" (see
  [Quiet water](#quiet-water)).
- Parking, trailheads and boat launches are details of a spot (`getting_there`), not map
  pins of their own. Road crossings (`bridges`) are a layer that is **off by default**
  (`layers.bridges.default_on`).
- A water body's `name` can be `null` (no source named it). Show `display_name`, never invent a
  name, and never print "Unnamed …". A lake with `geometry: null` has no outline on file: draw
  a label, not a shape. A lake with a polygon has one.
- Spot output is personal. Do not add export, sharing, or public counts. Weed reports are
  stricter still: visible only to the user who made them, labelled "reported, not verified" on
  every read, and never mixed into spot or card text.
- On a card, show the text as given. Every sentence on it, including block titles, the
  "not a catch guarantee" line and the care message, comes from the API. A species is
  "recorded", never "here", "good" or "hot", and nothing on the card says how popular a
  place is.
- Records and reasoning never share a line. `provenance: "record"` things carry a
  `source`; the one `provenance: "inference"` thing is the general explanation, shown
  with its `label`.
- The card is the same for every user and the shared part can be cached by `meta.cache_key`
  (`{spot_id}@{built_at}`), with one exception: **"new to you" is per-user**. `card.hook`
  (when its `kind` is `new_to_you`) and `card.new_to_you` are computed for the caller from
  their own confirmed catches on every request. Never cache the response across users, and
  never show the count, the names or the filter to anyone but the person they belong to: it
  is not a leaderboard, a public count or something to share. The endpoints send
  `Cache-Control: private, no-store` for this reason.

## `GET /map/spots/labels`

All user-facing strings, in display order (Quiet water last).

- `kinds[]` — `kind` (the id used everywhere else), `chip` (filter label), `one_liner`
  (what it is, in beginner words), `distinct` (draw apart from the others), and
  `why_fish_gather` = `{text, label, provenance}`: exactly two sentences, `label` is the
  caption to show with it, `provenance` is `"inference"`.
- `river_mouth_subline` — the line under the River mouths chip.
- `new_to_you` — the words for the per-user hook: `title` (the block title), `hook_one` and
  `hook_many` (the hook text; `hook_many` has an `{n}` to fill, but `card.hook.text` and
  `properties.new_to_you.text` already arrive filled in), `tap_prompt` ("Tap to see which")
  and `note` (whose catches it is based on).
- `filters.new_to_me` — `chip` and `one_liner` for the "New to me" toggle.
- `quiet_water` — the words only quiet water needs: `titles` (the two title templates the build
  fills), `caveats` (`records`, `access_not_measured`, `access_coverage_unknown`) and `not_built`
  (what `meta.notes` says when none has been built).
- `layers` — the Getting there and Bridges layers: `label`, `kinds`, `default_on`, `note`.
- `access_labels` — display names for `getting_there[].kind` and `bridges[].kind`.
- `water_body_types[]` — for each water body `type`: `chip` (plural, for a filter), `label`
  (singular, for a card) and `unnamed` (the phrase to show when `name` is null).
- `name_sources` — a plain-words line for each `name_source`, for a "source" caption under a name.
- `no_outline_note` — what to say when a lake has no shape on the map.
- `shoreline_features[]` — for each shoreline feature `kind` (`inlet`, `outlet`, `island`,
  `point`, `peninsula`, `drop_off`, `hump`): `label` (singular, for a card), `chip` (plural,
  for a legend) and `one_liner` (what it is, in beginner words).
- `shoreline_derivations` — a plain-words phrase for each `derivation`, for the caption under a
  shoreline feature: `shoreline_outline` (a shape read off the outline itself),
  `watercourse_endpoint` (a mapped stream that ends at the shore) or `bathymetry_contours`
  (a shape read off a lake's surveyed depth contours).
- `bathymetry_data_note` — a data note for contour-derived features (a drop-off or a hump):
  depth is at the survey's own water level, not today's, and the source states no unit. For a
  data-notes panel, never a card line.

```json
{
  "kinds": [
    {
      "kind": "river_mouth",
      "chip": "River mouths",
      "one_liner": "Where a river runs out into a lake.",
      "distinct": false,
      "why_fish_gather": {
        "text": "Fish that live in the lake pass through here to feed or to run upstream. The river also carries food out into open water.",
        "label": "General reasoning, not a record about this spot",
        "provenance": "inference"
      }
    },
    {
      "kind": "creek_junction",
      "chip": "Where creeks join",
      "one_liner": "Two streams meeting. Different temperature, speed and food mix at the join.",
      "distinct": false,
      "why_fish_gather": {
        "text": "Two flows mixing makes a seam between fast and slow water, which fish use for shelter with food drifting past. A creek that runs cooler than the river can also give fish a place to ease out of warm water.",
        "label": "General reasoning, not a record about this spot",
        "provenance": "inference"
      }
    },
    {
      "kind": "below_dam",
      "chip": "Below dams",
      "one_liner": "The water just downstream of a dam or weir.",
      "distinct": false,
      "why_fish_gather": {
        "text": "A dam or weir blocks fish from moving further upstream, so they gather in the water just below it. That water is churned up and rich in oxygen.",
        "label": "General reasoning, not a record about this spot",
        "provenance": "inference"
      }
    },
    {
      "kind": "public_fishing",
      "chip": "Public fishing spots",
      "one_liner": "Places that volunteer mappers have marked as set aside for fishing.",
      "distinct": false,
      "why_fish_gather": {
        "text": "Someone has mapped this as a place to fish, usually because the water is easy and allowed to reach. That is about access, so it says nothing by itself about what is swimming there.",
        "label": "General reasoning, not a record about this spot",
        "provenance": "inference"
      }
    },
    {
      "kind": "quiet_water",
      "chip": "Quiet water",
      "one_liner": "Reaches with few public observation records, which may mean few anglers or just few observers.",
      "distinct": true,
      "why_fish_gather": {
        "text": "These reaches have few public observation records. That can mean fewer anglers, few observers or hard access, and it never tells you whether fish are there.",
        "label": "General reasoning, not a record about this spot",
        "provenance": "inference"
      }
    }
  ],
  "river_mouth_subline": "Where a river meets a lake",
  "water_body_types": [
    {
      "type": "river",
      "chip": "Rivers",
      "label": "River",
      "unnamed": "A river with no name on file"
    },
    {
      "type": "creek",
      "chip": "Creeks",
      "label": "Creek",
      "unnamed": "A creek with no name on file"
    },
    {
      "type": "lake",
      "chip": "Lakes",
      "label": "Lake",
      "unnamed": "A lake with no name on file"
    },
    {
      "type": "pond",
      "chip": "Ponds",
      "label": "Pond",
      "unnamed": "A pond with no name on file"
    },
    {
      "type": "great_lake",
      "chip": "Great Lakes",
      "label": "Great Lake",
      "unnamed": "A Great Lake with no name on file"
    }
  ],
  "name_sources": {
    "curated": "Named in this app's own list of fishing waters",
    "curated_region": "Taken from the area the river runs through",
    "ohn": "Ontario government water map",
    "osm": "OpenStreetMap volunteers",
    "unknown": "No name on file"
  },
  "no_outline_note": "No outline is on file for this water, so it has no shape on the map yet.",
  "shoreline_features": [
    {
      "kind": "inlet",
      "label": "Inlet",
      "chip": "Inlets",
      "one_liner": "A mapped stream flows in here."
    },
    {
      "kind": "outlet",
      "label": "Outlet",
      "chip": "Outlets",
      "one_liner": "The lake drains out here."
    },
    {
      "kind": "island",
      "label": "Island",
      "chip": "Islands",
      "one_liner": "Land inside the water."
    },
    {
      "kind": "point",
      "label": "Point",
      "chip": "Points",
      "one_liner": "A point of land reaches out."
    },
    {
      "kind": "peninsula",
      "label": "Peninsula",
      "chip": "Peninsulas",
      "one_liner": "A long point of land reaches out."
    }
  ],
  "shoreline_derivations": {
    "shoreline_outline": "from the shoreline outline",
    "watercourse_endpoint": "where a mapped stream meets the shore"
  },
  "quiet_water": {
    "titles": {
      "named": "A quiet part of {name}",
      "near": "Quiet water near {name}"
    },
    "caveats": {
      "records": "Few public records here can mean few anglers, or just few observers. It says nothing about whether fish are there.",
      "access_not_measured": "Access not measured here: our road and parking map does not reach this far, so there is no access figure for this water.",
      "access_coverage_unknown": "We cannot say whether access was measured here: the stored access figures predate the record of where roads and parking were mapped."
    },
    "not_built": "No quiet water is built yet. It needs the explore scores for this area, which have not been worked out."
  },
  "layers": {
    "getting_there": {
      "label": "Getting there",
      "kinds": [
        "parking",
        "trail_head",
        "boat_launch"
      ],
      "default_on": true,
      "note": "Mapped by OpenStreetMap volunteers; not checked on the ground."
    },
    "bridges": {
      "label": "Bridges",
      "kinds": [
        "road_crossing"
      ],
      "default_on": false,
      "note": "Public road crossings. A way to reach small water, not a place to fish."
    }
  },
  "access_labels": {
    "parking": "Parking",
    "trail_head": "Trailhead",
    "boat_launch": "Boat launch",
    "road_crossing": "Road crossing"
  }
}
```

## `GET /map/spots`

GeoJSON `FeatureCollection` of points, light enough to load for a whole viewport.

| Query | Meaning |
|---|---|
| `bbox` | `min_lon,min_lat,max_lon,max_lat`. Omit for everywhere. |
| `kind` | One or more of `river_mouth`, `creek_junction`, `below_dam`, `public_fishing`, `quiet_water`, comma-separated. Omit for all. `quiet_water` is built from the explore score cache; see [Quiet water](#quiet-water). |
| `tier` | The highest tier to include, **cumulative**: `1` headline spots only, `2` adds the normal ones (**default**), `3` everything, including junctions of small unnamed creeks that are hidden by default. A sensible mapping is tier 1 at province zoom, 2 at regional zoom, 3 at street zoom. |
| `jurisdiction` | ISO 3166-2 code such as `CA-ON`. Omit for all. |
| `species` | Scientific/common name; a spot matches when that species has records within 2 km. Listed species (status_known_listed) are never returned. |
| `near` | `lat,lon` or `lat,lon,radius_km`; results sorted by distance, nearest first. Radius defaults to 50 km and is capped at 200 km. |
| `new_to_me` | `true` keeps only spots with at least one species recorded within 2 km that **the caller** has not caught (see [New to you](#new-to-you)). Per-user, computed for this request, never cached; the response is `Cache-Control: private, no-store`. Combines with every other filter. It reads the records around each spot, so a `bbox` is required: without one the request is rejected with 422 (`new_to_me needs a bbox`). Default `false`. |

`meta.kind_counts` counts the spots in view for **every** kind, ignoring the `kind`
filter, so the chips can show how many each would add. `meta.count` is how many
features this response holds. Features come headline-first, then by kind, then by id —
never by any measure of how popular a place is.

Each feature's `properties`: `spot_id`, `kind`, `title`, `subtitle`, `tier`,
`jurisdiction`, `water_body_id` (the river, creek or lake it is a child of, see
`/map/water-bodies`), `water_id`, `water_name` (the river section it sits on) and `signals`.
With `new_to_me=true` each feature also has `new_to_you` = `{count, text}` (`text` is the
ready-made "2 species recorded here you haven't caught yet"), and `meta.new_to_me` is `true`.
Quiet-water features add `caveats`, `[{kind, text}]`, already written: `records` always, plus
`access` unless the access figure was measured. The key is absent on every other kind. They have
`water_body_id: null` and `water_id: null` on purpose: a quiet reach belongs to no curated river.

`meta.notes` is present only when a kind that was asked for has nothing to show because of the
build, not the viewport. Today that is quiet water before its first build: the text says so
(`labels.quiet_water.not_built`). Features still come back, as an empty list.

```json
{
  "type": "FeatureCollection",
  "meta": {
    "count": 2,
    "kind_counts": {
      "river_mouth": 0,
      "creek_junction": 1,
      "below_dam": 1,
      "public_fishing": 0,
      "quiet_water": 0
    },
    "max_tier": 2
  },
  "features": [
    {
      "type": "Feature",
      "id": "creek_junction:n8038226_4319286",
      "geometry": {
        "type": "Point",
        "coordinates": [
          -80.38226,
          43.19286
        ]
      },
      "properties": {
        "spot_id": "creek_junction:n8038226_4319286",
        "kind": "creek_junction",
        "title": "Where the Nith River joins the Grand River",
        "subtitle": "Grand River · Cambridge to Paris",
        "tier": 1,
        "jurisdiction": "CA-ON",
        "water_id": "grand-cambridge-paris",
        "water_name": "Cambridge to Paris",
        "water_body_id": "river:grand-river",
        "signals": {
          "named": true,
          "nearest_bridge_m": 187,
          "nearest_parking_m": 138,
          "size_class": "large"
        }
      }
    },
    {
      "type": "Feature",
      "id": "below_dam:n8038314_4319587",
      "geometry": {
        "type": "Point",
        "coordinates": [
          -80.38314,
          43.195868
        ]
      },
      "properties": {
        "spot_id": "below_dam:n8038314_4319587",
        "kind": "below_dam",
        "title": "Below the Paris Dam",
        "subtitle": "Grand River · Cambridge to Paris",
        "tier": 1,
        "jurisdiction": "CA-ON",
        "water_id": "grand-cambridge-paris",
        "water_name": "Cambridge to Paris",
        "water_body_id": "river:grand-river",
        "signals": {
          "named": true,
          "nearest_bridge_m": 171,
          "nearest_parking_m": 150
        }
      }
    }
  ]
}
```

Bad input is a `422` with a `detail` message: `bbox` that is not four ordered numbers,
a `kind` that is not one of the five, a `tier` outside 1–3.

```json
{
  "detail": "unknown kind 'lake'; one of ['below_dam', 'creek_junction', 'public_fishing', 'quiet_water', 'river_mouth']"
}
```

## `GET /map/spots/{spot_id}`

One spot in full. Ids look like `below_dam:n8038314_4319587`: the kind and the spot's own
coordinates, so they stay the same across rebuilds, and across the river section being
renamed, and contain no river or section id. Saving a spot means saving its id.

| Field | Meaning |
|---|---|
| `spot_id`, `kind`, `jurisdiction`, `lat`, `lng` | Identity and place. `jurisdiction` is ISO 3166-2 (`CA-ON`). |
| `title`, `subtitle`, `tier` | What to show. `tier` is for the map only. |
| `water_body_id` | The river or creek the spot is a child of, e.g. `river:credit-river`. Null only when the build could not place the spot's river section (the build reports a count). Every spot of a river mouth, junction, dam or fishing spot is a child of its **river**; the lake is context. |
| `also_on_water_body_ids[]` | Other water bodies it is also on: the lake a river mouth opens onto (`great_lake:lake-ontario`), or the other river a junction is listed under. |
| `water_id`, `water_name` | The river section the spot is on. A junction that two sections both list is attached to one; the other is in `structure_attrs.also_on_water_ids`. |
| `structure_type` | Internal kind of structure: `lake_mouth`, `confluence`, `dam`, `weir`, `fishing_spot`. For code and the bot, not for display. |
| `structure_attrs` | Facts about this structure. Junctions: `joining_upstream_km`, `stretch_upstream_km`. Dams: `dam_kind`, `tailwater_verified` (when `false` the title says "At the …" because the water below could not be verified), `reach_m`. River mouths: `lake` (the lake's name; null when no source gave one) and `lake_name_source` (`curated`, `curated_region` or `ohn`). The lake's name here and the name of the lake water body are always the same string. |
| `signals` | Separate numbers, never combined into a score: `named`, `size_class` (`small`/`medium`/`large`, junctions), `nearest_parking_m`, `nearest_launch_m`, `nearest_bridge_m`. |
| `getting_there[]` | Nearest parking, trailheads and boat launches within 1.5 km, at most two of each kind: `kind`, `name`, `lat`, `lng`, `distance_m` (straight line), `source_ref`, `attributes` (OSM tags such as `access`, `fee`, `surface`). Volunteer-mapped; say so. |
| `bridges[]` | Nearest public road crossings within 1 km, same shape. A way to reach small water, not a place to fish. |
| `sources[]` | Where the facts came from: `kind` (`ohn_topology`, `osm`, `curation`; a river mouth taken from the curation file is `curation`), `ref`, the `anchor_ids` merged into the spot, `built_at`. Behind a "Sources" link, not on the card face. |
| `water_body` | `{water_body_id, name, display_name, name_source, type}` for the river or creek the spot is a child of, or null. Enough for a breadcrumb; `GET /map/water-bodies/{id}` for the rest. |
| `also_on_water_bodies[]` | The same short form for each id in `also_on_water_body_ids`. |
| `more_on_this_water[]` | Other spots on the same river **section**: kept as it was. For "more spots on this river" across all its sections, use the water body's `spots`. |
| `card` | The four resolved blocks a tap reveals. See "The spot card" below. |

```json
{
  "spot_id": "below_dam:n8038314_4319587",
  "kind": "below_dam",
  "jurisdiction": "CA-ON",
  "lat": 43.195868,
  "lng": -80.38314,
  "water_id": "grand-cambridge-paris",
  "water_name": "Cambridge to Paris",
  "water_body_id": "river:grand-river",
  "also_on_water_body_ids": [],
  "title": "Below the Paris Dam",
  "subtitle": "Grand River · Cambridge to Paris",
  "tier": 1,
  "structure_type": "dam",
  "structure_attrs": {
    "dam_kind": "dam",
    "reach_m": 410,
    "tailwater_verified": true
  },
  "signals": {
    "named": true,
    "nearest_bridge_m": 171,
    "nearest_parking_m": 150
  },
  "getting_there": [
    {
      "kind": "parking",
      "name": null,
      "lat": 43.196501,
      "lng": -80.3815019,
      "distance_m": 150,
      "source_ref": "way/533807418",
      "attributes": {}
    },
    {
      "kind": "parking",
      "name": null,
      "lat": 43.1936867,
      "lng": -80.3843379,
      "distance_m": 262,
      "source_ref": "way/475398031",
      "attributes": {
        "access": "yes",
        "fee": "no",
        "surface": "asphalt"
      }
    }
  ],
  "bridges": [
    {
      "kind": "road_crossing",
      "name": "William Street",
      "lat": 43.194342,
      "lng": -80.383334,
      "distance_m": 171,
      "source_ref": "way/40790441",
      "attributes": {
        "ref": "CR 50"
      }
    }
  ],
  "sources": [
    {
      "kind": "osm",
      "ref": "way/248706708",
      "anchor_ids": [
        "grand-cambridge-paris:tailwater:osm-way-248706708"
      ],
      "built_at": "2026-10-12T20:30:44+00:00"
    },
    {
      "kind": "osm",
      "ref": "way/248706708",
      "anchor_ids": [
        "grand-cambridge-paris:dam:osm-way-248706708"
      ],
      "built_at": "2026-10-12T20:30:44+00:00"
    }
  ],
  "built_at": "2026-10-12T20:30:44+00:00",
  "water_body": {
    "water_body_id": "river:grand-river",
    "name": "Grand River",
    "display_name": "Grand River",
    "name_source": "curated",
    "type": "river"
  },
  "also_on_water_bodies": [],
  "more_on_this_water": [
    {
      "spot_id": "creek_junction:n8038226_4319286",
      "kind": "creek_junction",
      "title": "Where the Nith River joins the Grand River",
      "tier": 1
    },
    {
      "spot_id": "creek_junction:n8031055_4330884",
      "kind": "creek_junction",
      "title": "Where Alder Creek joins the Grand River",
      "tier": 2
    },
    {
      "spot_id": "creek_junction:n8031731_4334263",
      "kind": "creek_junction",
      "title": "Where Moffatt's Creek joins the Grand River",
      "tier": 2
    }
  ]
}
```

(The `card` field is left out of the two examples in this section; it has its own section
below, and the real thing is in the fixture.)

A river mouth, whose lake name is read from the curation file when it has one:

```json
{
  "spot_id": "river_mouth:n7957731_4354567",
  "kind": "river_mouth",
  "jurisdiction": "CA-ON",
  "lat": 43.54567,
  "lng": -79.57731,
  "water_id": "credit-streetsville-port-credit",
  "water_name": "Lower Credit — Streetsville to Port Credit",
  "water_body_id": "river:credit-river",
  "also_on_water_body_ids": [
    "great_lake:lake-ontario"
  ],
  "title": "Where the Credit River meets Lake Ontario",
  "subtitle": "Credit River · Lower Credit — Streetsville to Port Credit",
  "tier": 1,
  "structure_type": "lake_mouth",
  "structure_attrs": {
    "lake": "Lake Ontario",
    "lake_name_source": "curated",
    "shore_ogf_ids": [
      214624516,
      214624565
    ]
  },
  "signals": {
    "named": true
  },
  "getting_there": [],
  "bridges": [],
  "sources": [
    {
      "kind": "ohn_topology",
      "ref": "ogf:214624516,214624565,214624585",
      "anchor_ids": [
        "credit-streetsville-port-credit:lake_mouth:n7957731_4354567"
      ],
      "built_at": "2026-10-12T20:30:44+00:00"
    }
  ],
  "built_at": "2026-10-12T20:30:44+00:00",
  "water_body": {
    "water_body_id": "river:credit-river",
    "name": "Credit River",
    "display_name": "Credit River",
    "name_source": "curated",
    "type": "river"
  },
  "also_on_water_bodies": [
    {
      "water_body_id": "great_lake:lake-ontario",
      "name": "Lake Ontario",
      "display_name": "Lake Ontario",
      "name_source": "curated",
      "type": "great_lake"
    }
  ],
  "more_on_this_water": [
    {
      "spot_id": "creek_junction:n7966741_4355566",
      "kind": "creek_junction",
      "title": "Where Mullet Creek joins the Credit River",
      "tier": 1
    },
    {
      "spot_id": "creek_junction:n7959213_4355165",
      "kind": "creek_junction",
      "title": "Where Mary Fix Creek joins the Credit River",
      "tier": 2
    },
    {
      "spot_id": "creek_junction:n7965757_4354286",
      "kind": "creek_junction",
      "title": "Where Sawmill Creek joins the Credit River",
      "tier": 2
    },
    {
      "spot_id": "creek_junction:n7969383_4357433",
      "kind": "creek_junction",
      "title": "Where Carolyn Creek joins the Credit River",
      "tier": 2
    }
  ]
}
```

Unknown id is a `404`:

```json
{
  "detail": "No spot with id 'creek_junction:n0_0'"
}
```

## `GET /map/water-bodies`

The rivers, creeks and lakes that spots sit on, as a GeoJSON `FeatureCollection`. A spot is a
child of one **water body** (its `water_body_id`); the water body is the context for "more
spots on this river" and the thing to name in a breadcrumb. Use it to group spots, draw the
river quietly behind them, and let someone browse by water.

| Query | Meaning |
|---|---|
| `bbox` | `min_lon,min_lat,max_lon,max_lat`. A river or creek is in view when its **line** crosses the box (not merely its overall box, which can cover a whole watershed). A lake is in view when the box of the river mouths that open onto it overlaps. Omit for everywhere. |
| `type` | One or more of `river`, `creek`, `lake`, `pond`, `great_lake`, comma-separated. Omit for all. |
| `jurisdiction` | ISO 3166-2 code such as `CA-ON`. Omit for all. |

`meta.type_counts` counts every type in view, ignoring the `type` filter, so a filter row can
show what each would add. Features come in type order (rivers, creeks, lakes, ponds, Great
Lakes), then by id.

Each feature's `properties`:

| Field | Meaning |
|---|---|
| `water_body_id` | `{type}:{slug of the name}`, e.g. `river:credit-river`. Built from the water's own name, so it is stable across rebuilds and does not change if a river section is renamed or split. |
| `name` | The name a source gave, or **`null`**. A name nobody supplied is never made up. |
| `display_name` | `name`, or when `name` is null a plain phrase from `labels.water_body_types[].unnamed` ("A lake with no name on file"). Safe to show as-is. |
| `name_source` | Where `name` came from: `curated` (written in this app's own list of fishing waters), `curated_region` (the area a river runs through, used as the lake only when it is itself a known lake), `ohn`, `osm`, or `unknown` (exactly when `name` is null). Words for each are in `labels.name_sources`. |
| `type` | `river`, `creek`, `lake`, `pond` or `great_lake`. A river or creek is read from its name ("Bronte Creek"). The Great Lakes list is Ontario, Erie, Huron, Superior, Michigan and Georgian Bay; bays (Bay of Quinte, Humber Bay) are `lake` with a `part_of` note in the full record. |
| `jurisdiction` | ISO 3166-2 code. |
| `bbox` | `[min_lon, min_lat, max_lon, max_lat]`, or null. |
| `spot_counts` | Spots on this water by kind, for the default view (tier 1 and 2). A badge, not a ranking. |

`geometry` is a simplified `MultiLineString` for a river or creek. A **lake carries a
`Polygon`** when its OHN Waterbody outline was matched at build time, or `geometry: null` when
none was on file (then draw it as a label at the river mouths that open onto it — their spots
list it in `also_on_water_body_ids` — and show `labels.no_outline_note` if you need to explain
why). Never fill in a made-up outline.

```json
{
  "type": "FeatureCollection",
  "meta": {
    "count": 3,
    "type_counts": {
      "river": 0,
      "creek": 2,
      "lake": 0,
      "pond": 0,
      "great_lake": 1
    }
  },
  "features": [
    {
      "type": "Feature",
      "id": "creek:bronte-creek",
      "geometry": {
        "coordinates": [
          [
            [
              -79.85031,
              43.42883
            ],
            [
              -79.84866,
              43.42726
            ],
            [
              -79.84584,
              43.42793
            ]
          ]
        ],
        "type": "MultiLineString"
      },
      "properties": {
        "water_body_id": "creek:bronte-creek",
        "name": "Bronte Creek",
        "display_name": "Bronte Creek",
        "name_source": "curated",
        "type": "creek",
        "jurisdiction": "CA-ON",
        "bbox": [
          -79.85031,
          43.37634,
          -79.6854,
          43.42883
        ],
        "spot_counts": {
          "creek_junction": 2,
          "river_mouth": 1
        }
      }
    },
    {
      "type": "Feature",
      "id": "creek:sixteen-mile-creek",
      "geometry": {
        "coordinates": [
          [
            [
              -79.87115,
              43.50984
            ],
            [
              -79.86395,
              43.5051
            ],
            [
              -79.86216,
              43.50452
            ]
          ]
        ],
        "type": "MultiLineString"
      },
      "properties": {
        "water_body_id": "creek:sixteen-mile-creek",
        "name": "Sixteen Mile Creek",
        "display_name": "Sixteen Mile Creek",
        "name_source": "curated",
        "type": "creek",
        "jurisdiction": "CA-ON",
        "bbox": [
          -79.87115,
          43.43105,
          -79.64903,
          43.50984
        ],
        "spot_counts": {
          "creek_junction": 3,
          "river_mouth": 1
        }
      }
    },
    {
      "type": "Feature",
      "id": "great_lake:lake-ontario",
      "geometry": null,
      "properties": {
        "water_body_id": "great_lake:lake-ontario",
        "name": "Lake Ontario",
        "display_name": "Lake Ontario",
        "name_source": "curated",
        "type": "great_lake",
        "jurisdiction": "CA-ON",
        "bbox": [
          -79.6854,
          43.19108,
          -78.29204,
          43.93725
        ],
        "spot_counts": {}
      }
    }
  ]
}
```

(Line coordinates are shortened here; the fixture has the full simplified lines.)

Bad input is a `422` with a `detail` message: a `bbox` that is not four ordered numbers, or a
`type` that is not one of the five.

```json
{
  "detail": "unknown type 'ocean'; one of ['creek', 'great_lake', 'lake', 'pond', 'river']"
}
```

## `GET /map/water-bodies/{water_body_id}`

One water body in full: everything above plus its shape, the curated river sections it is
made of, and every spot on it.

| Field | Meaning |
|---|---|
| `water_body_id`, `name`, `display_name`, `name_source`, `type`, `jurisdiction`, `bbox` | As in the list. |
| `geometry`, `geometry_source` | The line (`stretch_union`: the river's sections drawn together) or polygon (`ohn_waterbody`: the matched OHN Waterbody outline) — or `null` / `none` for a lake with no outline on file. |
| `stretch_ids[]` | The river sections that make up a river or creek, or that open onto a lake. Their detail is at `/map/stretches`; the spot UI rarely needs them. |
| `attributes` | How the row was made. `type_basis` says why it has this type (`name_suffix`, `great_lakes_list`, `default_for_a_named_lake`, `name_contains_pond`); `part_of` names the larger lake a bay belongs to; `also_spelled` lists other spellings that were folded into this one; `bbox_basis` is `"river_mouths"` (a lake's box comes from the mouths that open onto it) or `"outline"` (from the matched polygon). When an outline was matched: `outline_source`, `outline_ogf_ids[]`, `outline_ohn_name`, `outline_effective_datetime`, `outline_match_basis` and the `outline_licence` / `outline_licence_url` to credit. For code and attribution, not for display. |
| `spots[]` | The spots that are children of this water body, headline-first, all tiers: `spot_id`, `kind`, `title`, `tier`, `lat`, `lng`. This is "more spots on this river". |
| `also_here_spots[]` | Spots that belong to another water body but are also on this one, same shape: the river mouths that open onto a lake. |
| `built_at` | When the row was built. |

```json
{
  "water_body_id": "river:credit-river",
  "jurisdiction": "CA-ON",
  "name": "Credit River",
  "name_source": "curated",
  "type": "river",
  "geometry": {
    "coordinates": [
      [
        [
          -79.98834,
          43.80977
        ],
        [
          -79.97764,
          43.80647
        ],
        [
          -79.97519,
          43.80644
        ]
      ],
      [
        [
          -79.96461,
          43.81345
        ],
        [
          -79.96423,
          43.8139
        ]
      ]
    ],
    "type": "MultiLineString"
  },
  "geometry_source": "stretch_union",
  "bbox": [
    -79.98834,
    43.53952,
    -79.57731,
    43.81619
  ],
  "stretch_ids": [
    "credit-forks-glen-williams",
    "credit-glen-williams-streetsville",
    "credit-streetsville-port-credit"
  ],
  "attributes": {
    "type_basis": "name_suffix"
  },
  "built_at": "2026-10-12T20:30:44+00:00",
  "display_name": "Credit River",
  "spots": [
    {
      "spot_id": "river_mouth:n7957731_4354567",
      "kind": "river_mouth",
      "title": "Where the Credit River meets Lake Ontario",
      "tier": 1,
      "lat": 43.54567,
      "lng": -79.57731
    },
    {
      "spot_id": "creek_junction:n7966741_4355566",
      "kind": "creek_junction",
      "title": "Where Mullet Creek joins the Credit River",
      "tier": 1,
      "lat": 43.55566,
      "lng": -79.66741
    },
    {
      "spot_id": "creek_junction:n7971768_4360952",
      "kind": "creek_junction",
      "title": "Where Fletchers Creek joins the Credit River",
      "tier": 1,
      "lat": 43.60952,
      "lng": -79.71768
    },
    {
      "spot_id": "creek_junction:n7986378_4364837",
      "kind": "creek_junction",
      "title": "Where Black Creek joins the Credit River",
      "tier": 1,
      "lat": 43.64837,
      "lng": -79.86378
    }
  ],
  "also_here_spots": []
}
```

A lake whose outline was not matched has no geometry, and its spots are the river mouths that
open onto it:

```json
{
  "water_body_id": "great_lake:lake-ontario",
  "jurisdiction": "CA-ON",
  "name": "Lake Ontario",
  "name_source": "curated",
  "type": "great_lake",
  "geometry": null,
  "geometry_source": "none",
  "bbox": [
    -79.6854,
    43.19108,
    -78.29204,
    43.93725
  ],
  "stretch_ids": [
    "bowmanville-soper",
    "bronte-lowville-bronte",
    "credit-streetsville-port-credit",
    "duffins-pickering-ajax",
    "ganaraska-garden-hill-port-hope",
    "niagara-lower-queenston-notl",
    "rouge-lower",
    "sixteen-mile-milton-oakville",
    "twenty-mile-balls-falls-jordan",
    "wilmot-newcastle"
  ],
  "attributes": {
    "bbox_basis": "river_mouths",
    "type_basis": "great_lakes_list"
  },
  "built_at": "2026-10-12T20:30:44+00:00",
  "display_name": "Lake Ontario",
  "spots": [],
  "also_here_spots": [
    {
      "spot_id": "river_mouth:n7829204_4393725",
      "kind": "river_mouth",
      "title": "Where the Ganaraska River meets Lake Ontario",
      "tier": 1,
      "lat": 43.93725,
      "lng": -78.29204
    },
    {
      "spot_id": "river_mouth:n7859506_4389124",
      "kind": "river_mouth",
      "title": "Where Wilmot Creek meets Lake Ontario",
      "tier": 1,
      "lat": 43.89124,
      "lng": -78.59506
    },
    {
      "spot_id": "river_mouth:n7865768_4387791",
      "kind": "river_mouth",
      "title": "Where Bowmanville Creek meets Lake Ontario",
      "tier": 1,
      "lat": 43.87791,
      "lng": -78.65768
    }
  ]
}
```

(`spots` is shortened here to three entries.)

Unknown id is a `404`:

```json
{
  "detail": "No water body with id 'river:no-such-river'"
}
```

## `GET /map/water-bodies/{water_body_id}/features`

The shoreline features of one lake, as a GeoJSON `FeatureCollection`: where a mapped stream
enters (`inlet`) or leaves (`outlet`), an island (`island`), a point or peninsula of land
reaching out (`point`, `peninsula`), and — where the lake has a digital survey — where the
bottom falls away quickly (`drop_off`) or rises into a shallower shoal (`hump`). The first five
are read off the lake's OHN Waterbody outline and the OHN streams that end at its shore; the
last two are read off the LIO Bathymetry depth contours. A located thing to show, never a claim
about fish. A river has no shoreline, and a lake with no contours gets no drop-off or hump, so
the honest answer is an **empty collection**, not an error; an unknown id is `404`.

Each feature's `properties`:

| Field | Meaning |
|---|---|
| `feature_id` | Stable id: `{water_body_id}:{kind}:{key}`, where the key comes from the feature's location (or the stream's OGF_ID for an inlet or outlet). |
| `water_body_id` | The lake the feature belongs to. |
| `kind` | `inlet`, `outlet`, `island`, `point`, `peninsula`, `drop_off` or `hump`. Words in `labels.shoreline_features`. |
| `name` | The mapped stream's name for an inlet or outlet, else `null`. |
| `derivation` | How it was obtained: `watercourse_endpoint` (a mapped OHN stream that ends at the shore), `shoreline_outline` (a shape read off the outline's own edge), or `bathymetry_contours` (a shape read off a lake survey's depth contours). Phrase in `labels.shoreline_derivations`. |
| `provenance` | `record` for an island (a ring inside the source polygon) or a stream junction; `inference` for a point, peninsula, drop-off or hump (a shape computed from the outline or the contours). |
| `note` | A full sentence for the card, already assembled from the vocabulary. Safe to show as-is. A drop-off or hump names its survey ("from a 1970 survey, transect") or says `survey date unknown`. |
| `attributes` | Survey facts behind a contour-derived feature: `contour_ogf_ids`, `survey_year`, `survey_method`, `depth_basis`, `depth_m`, and (for a drop-off) `depth_change_m` and `gap_m` or (for a hump) `area_m2`. Also the `attribution`, `licence` and `licence_url` to credit. `{}` for an outline-derived feature. |
| `source`, `source_date` | The named source (`OHN Waterbody`, `OHN Watercourse` or `LIO Bathymetry`) and that record's effective date (`YYYY-MM-DD`, or `null` if the source gave none). Credit these. |
| `area_m2` | An island's area or a hump's area, else `null`. |
| `length_m` | How far a point or peninsula sticks out from the shore, or the depth change across a drop-off, in metres; else `null`. |
| `geometry` | A `Point` for inlets, outlets, points, peninsulas and drop-offs; a `Polygon` for an island or hump. |

```json
{
  "type": "FeatureCollection",
  "water_body_id": "lake:rice-lake",
  "water_body_name": "Rice Lake",
  "meta": {
    "count": 3,
    "by_kind": {
      "inlet": 1,
      "island": 1,
      "drop_off": 1
    }
  },
  "features": [
    {
      "type": "Feature",
      "id": "lake:rice-lake:inlet:1",
      "geometry": {
        "type": "Point",
        "coordinates": [
          -78.32,
          44.2
        ]
      },
      "properties": {
        "feature_id": "lake:rice-lake:inlet:1",
        "water_body_id": "lake:rice-lake",
        "kind": "inlet",
        "name": "Otonabee River",
        "derivation": "watercourse_endpoint",
        "provenance": "record",
        "source": "OHN Watercourse",
        "source_date": "2024-01-01",
        "note": "A mapped stream flows in here. Otonabee River is mapped here. Read from the Ontario government water map.",
        "attributes": {},
        "area_m2": null,
        "length_m": null
      }
    },
    {
      "type": "Feature",
      "id": "lake:rice-lake:island:-78.195_44.205",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              -78.2,
              44.2
            ],
            [
              -78.19,
              44.2
            ],
            [
              -78.19,
              44.21
            ],
            [
              -78.2,
              44.21
            ],
            [
              -78.2,
              44.2
            ]
          ]
        ]
      },
      "properties": {
        "feature_id": "lake:rice-lake:island:-78.195_44.205",
        "water_body_id": "lake:rice-lake",
        "kind": "island",
        "name": null,
        "derivation": "shoreline_outline",
        "provenance": "record",
        "source": "OHN Waterbody",
        "source_date": "2021-04-08",
        "note": "Land inside the water. Read from the shoreline outline.",
        "attributes": {},
        "area_m2": 590000.0,
        "length_m": null
      }
    },
    {
      "type": "Feature",
      "id": "lake:rice-lake:drop_off:-78.145_44.19",
      "geometry": {
        "type": "Point",
        "coordinates": [
          -78.145,
          44.19
        ]
      },
      "properties": {
        "feature_id": "lake:rice-lake:drop_off:-78.145_44.19",
        "water_body_id": "lake:rice-lake",
        "kind": "drop_off",
        "name": null,
        "derivation": "bathymetry_contours",
        "provenance": "inference",
        "source": "LIO Bathymetry",
        "source_date": "1970-07-10",
        "note": "The bottom falls away quickly here. From a 1970 survey, Transect.",
        "attributes": {
          "contour_ogf_ids": [118431234, 118431235],
          "survey_year": 1970,
          "survey_method": "Transect",
          "depth_basis": "at the survey's own water level; metres inferred from the source",
          "depth_m": -4.0,
          "depth_change_m": 4.0,
          "gap_m": 35.2,
          "attribution": "Contains information licensed under the Open Government Licence – Ontario. Bathymetry, Ontario Ministry of Natural Resources (Land Information Ontario).",
          "licence": "Open Government Licence – Ontario",
          "licence_url": "https://www.ontario.ca/page/open-government-licence-ontario"
        },
        "area_m2": null,
        "length_m": 4.0
      }
    }
  ]
}
```

## The spot card

`card` on `GET /map/spots/{spot_id}` is one read model with four fixed blocks. Blocks 1
and 2 are for the first tap (`default_open: true`); 3 and 4 expand (`default_open: false`).
Every user-facing string is in it, including block titles.

| Block | Field | What it holds |
|---|---|---|
| 1. Why it's interesting | `card.hook` | `title`, `kind`, `text`: at most two lines. The first rule with data wins: `protected_species` (a care message that replaces everything else), then `structure` (one plain sentence about the water, only where a record supports it), then `honest_empty` ("No fish records within 2 km, so this is a place to find out."). `kind_description` is used when fish are recorded but nothing above has anything specific to say; it is the kind's one-liner. `new_to_you` is the first hook for a logged-in user who has something new (see [New to you](#new-to-you)); it sits after `protected_species`, so a care message still replaces it, and when it has nothing the chain falls through to `structure` as before. `distinctive` follows `new_to_you`: a species recorded more often here than along the rest of the river (see [Special here](#special-here)). |
| 2. You might find | `card.might_find` | `species[]` (top 5), `more_text`, `caveat` ("Recorded nearby, not a catch guarantee."), `empty_text` (why there is no list), `note` (why some recorded species are left out), `radius_km`. Each species has `name`, `scientific_name`, `source`, `date`, `evidence`, `cite` (`"iNaturalist, 2024-06-03"`, ready to show in brackets) and `note` (`"Older record"` for a record a decade old or more). Standardised surveys (`evidence: "survey"`) come first, then photos and sightings, each most recent first. `special_here` is `null` unless a species stands out; see [Special here](#special-here). |
| 3. Why fish gather here | `card.why_gather` | `what_it_is` (the kind in plain words), `general` = `{text, label, provenance: "inference"}`: the kind's two sentences, shown with the label "General reasoning, not a record about this spot". Then `facts_title` and `facts[]`: facts about this spot, each `{text, provenance: "record", source}`. They are listed apart from the reasoning on purpose. |
| 4. Getting there | `card.getting_there` | `items[]`: the nearest parking, trailhead, boat launch and public road crossing, each `{kind, label, text, distance_m, details[], source}` (`text` is "Parking about 150 m away"; `details` are mapped tags such as "mapped as free"). `empty_text` when there are none. `crown_land` = `{state, text, source}` where `state` is `mapped`, `none_found` or `not_checked`. `unverified_title` and `unverified[]`: what has not been checked on the ground. |

Also on the card: `new_to_you` (see below; `null` when there is nothing new); `caveats[]`, `[{kind, text}]` limits on what the card means (`records` and `access`; only quiet water has any today, so it is `[]` for the other kinds); and `terms[]`, entries from the term registry (`{term, term_id, text}`,
`src/services/terms.py`) for the hard words that appear in this card's own text (for a
weir, "weir"), to show behind a "What's a weir?" link; and `meta`, counts for a Sources view that are not shown on the card face:
`species_shown`, `species_withheld_protected` (a number, never names),
`species_withheld_imprecise`, `species_status_unchecked`, `special_here` (why there is, or is not,
a "recorded more often here" line: `found`, `none_stands_out`, `too_few_records_here`,
`too_few_records_along_river`, `no_river_section`, or `not_applicable` for quiet water), `cache_key`.

`GET /map/spots/labels` also carries `terms[]`, the whole term registry (`term_id`,
`plain`, `technical`, `definition`, `mapped_from`); each kind and shoreline feature
carries the `term_ids` it is an instance of. `GET /terms` serves the same registry.

**Protected species.** If any species recorded within 2 km has a listing signal (Special
Concern, Threatened, Endangered or Extirpated from any source we hold, verified or not),
the hook becomes the care message and that species is not named anywhere on the card:
not in "You might find", not in a count, not in the facts. The gate reads
`status_known_listed`, never `sar_alert`, which is true for every fish in the local
species file. A species the file does not know at all is still shown, and counted in
`meta.species_status_unchecked`. Records whose location is deliberately fuzzed (about
22 km) are not "within 2 km", so they are left out and counted in
`meta.species_withheld_imprecise`.

**What a card is built from.** The spot, and `describe(map_tap)` within 2 km: no model
call, no live lookup, no web search. The response has `Cache-Control: private,
no-store`, because the "new to you" hook depends on who asks.

### Special here

`card.might_find.special_here` and the `distinctive` hook say that a species is **recorded more
often here than along the rest of the river**. It is a statement about records, and records follow
the people who look as well as the fish, so the wording is "recorded more often", never "lives
here", and the block's own `caveat` says why. It compares *frequency*, not count: a fish with
thirty records at a busy bridge is not special if it is just as large a share of the records
along the whole river.

For each species, its share of the records within 2 km of the spot is compared with its share of
the records along the rest of the spot's river section (the curated river section the spot is on,
`water_id`: anything within 2 km of its line, minus the spot's own 2 km). A species is named
only when **all** of these hold, each a named constant in `src/services/context/special.py`:

| Rule | Constant | Value |
|---|---|---|
| Records within 2 km of the spot | `MIN_RECORDS_HERE` | at least 10 |
| Records along the rest of the river section | `MIN_RECORDS_ELSEWHERE` | at least 30 |
| Records of the species here | `MIN_SPECIES_RECORDS_HERE` | at least 3 |
| Its share here against its share elsewhere | `MIN_SHARE_RATIO` | at least 2 times |
| Its share here minus its share elsewhere | `MIN_SHARE_GAP` | at least 0.05 (five points) |
| Species named | `MAX_SPECIES` | at most 3, largest gap first |

**When either side is short, there is no line at all**, not a weak one: `special_here` is `null`,
the hook falls through to the next rule, and `meta.special_here` says which side was short
(`too_few_records_here` or `too_few_records_along_river`). `none_stands_out` means there were
enough records on both sides and no species stood out. `no_river_section` means the spot is not on
a curated river section or the section's line is missing.

- **Surveys outrank photos.** The shares are made from standardised survey records when both
  sides have enough of them (`evidence: "survey"`), and from photo and sighting records otherwise
  (`evidence: "sighting"`). The two are never mixed in one share. `basis_text` says which.
- **One record per fish per visit.** For sightings, one per person per day per species; for
  surveys, one per survey. One person logging fifty of a fish at their favourite bridge would
  otherwise make that fish "special" there, and in the first run on real records one did.
- Records with a deliberately fuzzed location (about 22 km) are on neither side. The user's own
  catches are not read: the card is the same for every user.
- A species with a listing signal is never named, and when one is recorded within 2 km the
  care message replaces the hook and `special_here` is `null`.
- Nothing about months or seasons.

Each line is `{name, scientific_name, text, here_records, here_total, elsewhere_records,
elsewhere_total}`; `text` is already written ("19 of 108 records here (18%), against 3% along the
rest of the Otonabee River"). A card with a line, captured from the real iNaturalist and GBIF
records in the main database (the hook, then the block):

```json
{
  "title": "Why it's interesting",
  "kind": "distinctive",
  "text": "Yellow Perch and 2 more are recorded more often here than along the rest of the Otonabee River."
}
```

```json
{
  "title": "Recorded more often here",
  "label": "Recorded more often here than along the rest of the Otonabee River",
  "species": [
    {
      "name": "Yellow Perch",
      "scientific_name": "Perca flavescens",
      "text": "19 of 108 records here (18%), against 3% along the rest of the Otonabee River",
      "here_records": 19,
      "here_total": 108,
      "elsewhere_records": 2,
      "elsewhere_total": 68
    },
    {
      "name": "White Sucker",
      "scientific_name": "Catostomus commersonii",
      "text": "7 of 108 records here (6%), against 1% along the rest of the Otonabee River",
      "here_records": 7,
      "here_total": 108,
      "elsewhere_records": 1,
      "elsewhere_total": 68
    },
    {
      "name": "Bluegill",
      "scientific_name": "Lepomis macrochirus",
      "text": "7 of 108 records here (6%), against 1% along the rest of the Otonabee River",
      "here_records": 7,
      "here_total": 108,
      "elsewhere_records": 1,
      "elsewhere_total": 68
    }
  ],
  "evidence": "sighting",
  "basis_text": "Counted from photo and sighting records only, one for each fish per person per day.",
  "caveat": "This compares how often each fish was recorded. Records follow where people look as well as where fish are, so it does not mean the fish are found only here.",
  "radius_km": 2.0
}
```

The card payload for a spot whose structure gives nothing specific to say and where nothing is
recorded (an honest-empty card):

```json
{
  "spot_id": "creek_junction:n7742945_4424383",
  "hook": {
    "title": "Why it's interesting",
    "kind": "honest_empty",
    "text": "No fish records within 2 km, so this is a place to find out."
  },
  "might_find": {
    "title": "You might find",
    "species": [],
    "more_text": null,
    "caveat": "Recorded nearby, not a catch guarantee.",
    "empty_text": "We hold no fish records for this area yet.",
    "note": null,
    "radius_km": 2.0,
    "default_open": true
  },
  "why_gather": {
    "title": "Why fish gather here",
    "what_it_is": "Two streams meeting. Different temperature, speed and food mix at the join.",
    "general": {
      "text": "Two flows mixing makes a seam between fast and slow water, which fish use for shelter with food drifting past. A creek that runs cooler than the river can also give fish a place to ease out of warm water.",
      "label": "General reasoning, not a record about this spot",
      "provenance": "inference"
    },
    "facts_title": "What our records say about this spot",
    "facts": [],
    "default_open": false
  },
  "getting_there": {
    "title": "Getting there",
    "items": [],
    "empty_text": "No parking, trailhead or boat launch found within 1.5 km in our map data.",
    "crown_land": {
      "state": "not_checked",
      "text": "We have no Crown land data for this area.",
      "source": null
    },
    "unverified_title": "Not checked on the ground",
    "unverified": [
      "Parking, trails, launches and road crossings come from OpenStreetMap, which volunteers map. A missing one may just be unmapped.",
      "Fishing rules and permission to cross private land are not checked here."
    ],
    "default_open": false
  },
  "terms": [],
  "meta": {
    "records_radius_km": 2.0,
    "species_shown": 0,
    "species_withheld_protected": 0,
    "species_withheld_imprecise": 0,
    "species_status_unchecked": 0,
    "cache_key": "creek_junction:n7742945_4424383@2026-10-12T21:43:44+00:00"
  }
}
```

A protected-species card. The listed fish is named nowhere in the response, and the other
fish recorded nearby are still listed:

```json
{
  "spot_id": "creek_junction:n7966741_4355566",
  "hook": {
    "title": "Why it's interesting",
    "kind": "protected_species",
    "text": "Protected fish have been recorded near here. Handle any fish with care and release it."
  },
  "might_find": {
    "title": "You might find",
    "species": [
      {
        "name": "Rainbow Darter",
        "scientific_name": "Etheostoma caeruleum",
        "source": "GBIF/Example fish survey",
        "date": "2019-07-10",
        "evidence": "survey",
        "cite": "GBIF/Example fish survey, 2019-07-10",
        "note": null
      },
      {
        "name": "Creek Chub",
        "scientific_name": "Semotilus atromaculatus",
        "source": "iNaturalist",
        "date": "2024-06-03",
        "evidence": "sighting",
        "cite": "iNaturalist, 2024-06-03",
        "note": null
      },
      {
        "name": "Blacknose Dace",
        "scientific_name": "Rhinichthys atratulus",
        "source": "iNaturalist",
        "date": "2023-08-19",
        "evidence": "sighting",
        "cite": "iNaturalist, 2023-08-19",
        "note": null
      },
      {
        "name": "White Sucker",
        "scientific_name": "Catostomus commersonii",
        "source": "iNaturalist",
        "date": "2022-05-02",
        "evidence": "sighting",
        "cite": "iNaturalist, 2022-05-02",
        "note": null
      }
    ],
    "more_text": null,
    "caveat": "Recorded nearby, not a catch guarantee.",
    "empty_text": null,
    "note": "Protected species recorded nearby are not listed.",
    "radius_km": 2.0,
    "default_open": true
  },
  "why_gather": {
    "title": "Why fish gather here",
    "what_it_is": "Two streams meeting. Different temperature, speed and food mix at the join.",
    "general": {
      "text": "Two flows mixing makes a seam between fast and slow water, which fish use for shelter with food drifting past. A creek that runs cooler than the river can also give fish a place to ease out of warm water.",
      "label": "General reasoning, not a record about this spot",
      "provenance": "inference"
    },
    "facts_title": "What our records say about this spot",
    "facts": [
      {
        "text": "The smaller stream has about 46 km of channel upstream of this point; the larger has about 1238 km.",
        "provenance": "record",
        "source": "Ontario Hydro Network"
      },
      {
        "text": "4 fish records within 2 km, covering 4 species.",
        "provenance": "record",
        "source": "GBIF, iNaturalist"
      }
    ],
    "default_open": false
  },
  "getting_there": {
    "title": "Getting there",
    "items": [],
    "empty_text": "No parking, trailhead or boat launch found within 1.5 km in our map data.",
    "crown_land": {
      "state": "not_checked",
      "text": "We have no Crown land data for this area.",
      "source": null
    },
    "unverified_title": "Not checked on the ground",
    "unverified": [
      "Parking, trails, launches and road crossings come from OpenStreetMap, which volunteers map. A missing one may just be unmapped.",
      "Fishing rules and permission to cross private land are not checked here."
    ],
    "default_open": false
  },
  "terms": [],
  "meta": {
    "records_radius_km": 2.0,
    "species_shown": 4,
    "species_withheld_protected": 1,
    "species_withheld_imprecise": 0,
    "species_status_unchecked": 4,
    "cache_key": "creek_junction:n7966741_4355566@2026-10-12T21:43:44+00:00"
  }
}
```

## Quiet water

`kind=quiet_water` spots are **reaches with few public observation records**. That can mean few
anglers, or just few observers, or hard access. It is not a claim about fish: fewer records never
implies absence or presence, and nothing in the title, the card or the caveats says otherwise.
It is the one kind that is a ranking rather than a located structure, and it is the one kind
that depends on a cache, so read this section before building the chip.

**Where the spots come from.** `make build-spots` wraps the `explore()` ranking (the untapped-water
scorer) for each labelled ingest area of the jurisdiction (`data/ingest_areas.json`; outside
them every reach has few records, because nothing was collected there, so "quiet" would only mean
"not ingested"). Per area the best 5 reaches are tier 2 and the next 10 tier 3, spaced at least 2 km
apart so one creek is not fifteen spots. Spots are the same for every user: `explore()` runs with
no user, so reaches an angler has fished are not removed here.

**It needs the score cache.** `data/processed/untapped_potential.parquet`, written by
`make compute-untapped`. Without it there are no quiet-water spots: the build still finishes, prints
`quiet water: no score cache, none built` and logs a warning, `GET /map/spots?kind=quiet_water`
returns an empty `features` list with `meta.notes` carrying the words for it, and nothing crashes.
Existing spots are untouched. A stale cache gives stale spots, not wrong ids: ids come from the
reach's coordinates.

**Titles.** "A quiet part of {name}" for a named reach; "Quiet water near {name}" for a reach with no
name of its own, titled after the nearest named water within 3 km. A reach with neither is not
published (and is counted in the build report). `labels.quiet_water.titles` holds the templates.

**Coverage caveats.** Every quiet-water feature and card carries `caveats`:

| `kind` | When | Text |
|---|---|---|
| `records` | always | "Few public records here can mean few anglers, or just few observers. It says nothing about whether fish are there." |
| `access` | `signals.access_is_measured` is `false` | "Access not measured here: our road and parking map does not reach this far, so there is no access figure for this water." |
| `access` | `signals.access_is_measured` is `null` | "We cannot say whether access was measured here: the stored access figures predate the record of where roads and parking were mapped." |

`access_is_measured` is tri-state because there are three facts with three remedies. `true`: the
road and parking map covers the reach, no caveat. `false`: the reach is outside the OpenStreetMap
footprint (about 55 km of home), so the access term is a placeholder; the reach is still ranked
on what is real out there, and the caveat says so. `null`: the cached scores predate coverage
tracking, a pipeline gap that `make compute-access` settles. The UI shows the text as given.

**First Nations waters are left out.** No boundary layer exists, so reaches inside the boxes of
`data/curation/first_nations_waters_ca_on.toml` (the Grand between Brantford and Caledonia, the
Thames between Delaware and Thamesville, the St. Clair delta) are dropped before ranking. The boxes
are generous on purpose; see the file.

**What a quiet-water spot has.** `signals` is `{named, access_is_measured}` and nothing else: no
score, no pressure number (those would be a leaderboard). `structure_type` is `quiet_reach`,
`structure_attrs` has the OHN `ogf_id` of the reach, its `stream_order` and the ingest `area`, and
`sources[0].kind` is `explore_ranking`. `water_id`, `water_body_id` and `subtitle` are `null`. The
card is the usual four blocks: the hook is the kind's one-liner or the honest-empty line, "Why fish
gather here" is the kind's general reasoning (labelled inference), and there is no "recorded
more often here" line (`meta.special_here` is `not_applicable`).

**Ordering.** Quiet water is last in `kinds` and last within its tier in every list, as the
`distinct` flag asks. It has tiers 2 and 3 only, so `tier=1` (province zoom) never shows it.

One feature from the list, then the detail of a spot (card omitted here; see the fixture):

```json
{
  "type": "FeatureCollection",
  "meta": {
    "count": 1,
    "kind_counts": {
      "river_mouth": 31,
      "creek_junction": 230,
      "below_dam": 9,
      "public_fishing": 0,
      "quiet_water": 10
    },
    "max_tier": 2
  },
  "features": [
    {
      "type": "Feature",
      "id": "quiet_water:n7933778_4317874",
      "geometry": {
        "type": "Point",
        "coordinates": [
          -79.33778303982308,
          43.17874158743132
        ]
      },
      "properties": {
        "spot_id": "quiet_water:n7933778_4317874",
        "kind": "quiet_water",
        "title": "A quiet part of Eighteen Mile Creek",
        "subtitle": null,
        "tier": 2,
        "jurisdiction": "CA-ON",
        "water_id": null,
        "water_name": "Eighteen Mile Creek",
        "water_body_id": null,
        "signals": {
          "access_is_measured": false,
          "named": true
        },
        "caveats": [
          {
            "kind": "records",
            "text": "Few public records here can mean few anglers, or just few observers. It says nothing about whether fish are there."
          },
          {
            "kind": "access",
            "text": "Access not measured here: our road and parking map does not reach this far, so there is no access figure for this water."
          }
        ]
      }
    }
  ]
}
```

```json
{
  "spot_id": "quiet_water:n7933778_4317874",
  "kind": "quiet_water",
  "jurisdiction": "CA-ON",
  "lat": 43.17874158743132,
  "lng": -79.33778303982308,
  "water_id": null,
  "water_name": "Eighteen Mile Creek",
  "water_body_id": null,
  "also_on_water_body_ids": [],
  "title": "A quiet part of Eighteen Mile Creek",
  "subtitle": null,
  "tier": 2,
  "structure_type": "quiet_reach",
  "structure_attrs": {
    "area": "Grand River Dunnville",
    "named": true,
    "ogf_id": 127939961,
    "stream_order": 3
  },
  "signals": {
    "access_is_measured": false,
    "named": true
  },
  "getting_there": [],
  "bridges": [],
  "sources": [
    {
      "kind": "explore_ranking",
      "ref": "ogf:127939961",
      "anchor_ids": [],
      "built_at": "2026-10-14T04:26:26+00:00"
    }
  ],
  "built_at": "2026-10-14T04:26:26+00:00",
  "water_body": null,
  "also_on_water_bodies": [],
  "more_on_this_water": []
}
```

## Weed reports

A **weed report** is one person's own sighting of where a weed edge runs on a water body:
a point, or a short line along the edge, and the date they saw it. Weed edges are
**user-reported only**. No data source locates submerged or emergent weed beds (wetland maps
and plant records do not say where an edge runs), so nothing is derived, guessed or filled
in: every weed edge on the map is one a person wrote down, and each says so.

**They are private.** A report is visible only to the user who made it, like a personal pin.
There is no sharing, no export, no public layer, and no count of reports anywhere: not on a
water body, a spot, or the layer's `meta`. Another user's report answers `404`, exactly as an
id nobody has used does. The reporter is always the login on the request; the body cannot name
one.

These routes need the same `Authorization: Bearer <token>` as the rest of `/map/*`. Unlike
the endpoints above they write (`POST`, `PATCH`, `DELETE`).

**`tests/fixtures/weed_reports_api_responses.json`** holds real responses from every route
below, captured with real logins by `scripts/export_weed_reports_fixture.py` (the examples
here are copied from it). Because reports are private there is no real data to capture:
the water bodies are the real Credit River and Bronte Creek lines from the spots fixture, and
the reports are **examples** laid on those lines, with example dates and notes. They are not
sightings.

### What the UI keeps

- Show `caption` (or `kind_label`, `status_label` and `date_label` separately) as given. Every
  report says **"reported, not verified"** and when it was seen. Never drop that wording, and
  never style a weed edge as a fact about the water. The words all come from `GET /map/weed-reports/labels`.
- A weed edge is a person's sighting, not a record and not a model output. Do not show it in
  the same list as `provenance: "record"` things, and do not use it as a reason a spot is good.
- Do not add sharing, export, or "N people reported" anywhere. None exists to read.
- A report can only be placed on a water body that has a drawn shape (`geometry` not `null`), and
  within a tolerance of it (below). Draw the water body, let the person tap or trace on it, and
  send the `water_body_id` of the one they tapped.

### `GET /map/weed-reports/labels`

Every word and tolerance the UI needs: `kind_label` ("Weed edge"), `status_label`
("reported, not verified"), `provenance_label`, the `layer` name and note, `shape_labels` per geometry
type, and the checking tolerances (`near_line_m`: 150 for a river or creek line, `near_outline_m`: 100
for a lake outline, `max_line_length_m`: 10000).

```json
{
  "kind_label": "Weed edge",
  "status_label": "reported, not verified",
  "provenance_label": "Reported by you",
  "layer": {
    "label": "Your weed edges",
    "note": "Weed edges you reported. Only you can see them. They are your sightings, not checked against anything else.",
    "default_on": true
  },
  "shape_labels": {
    "Point": "A spot on the edge",
    "LineString": "A line along the edge"
  },
  "near_line_m": 150,
  "near_outline_m": 100,
  "max_line_length_m": 10000
}
```

### `POST /map/weed-reports`

Create a report. Body:

| Field | Meaning |
|---|---|
| `water_body_id` | The water body the edge is on (from `/map/water-bodies`). |
| `geometry` | A GeoJSON `Point` (`[lng, lat]`) or `LineString` (2 to 500 positions, at most 10 km long). |
| `observed_on` | The day it was seen, `YYYY-MM-DD`. Not in the future. |
| `note` | Optional, up to 2000 characters. |

Any other field (`user_id`, `provenance`, `visibility`, …) is `422`. Returns `201` and the report.

The position is checked against the water body's drawn shape: **every part of the point or line**
must be within `near_line_m` of a river's or creek's line, or inside (or within `near_outline_m` of) a
lake's outline. A water body with no outline on file cannot take a report.

Request body and `201` response:

```json
{
  "water_body_id": "river:credit-river",
  "geometry": {
    "type": "Point",
    "coordinates": [
      -79.96423,
      43.8139
    ]
  },
  "observed_on": "2026-10-04",
  "note": "Example: weeds thin out below the riffle."
}
```

```json
{
  "weed_report_id": 1,
  "water_body_id": "river:credit-river",
  "water_body_name": "Credit River",
  "jurisdiction": "CA-ON",
  "geometry": {
    "type": "Point",
    "coordinates": [
      -79.96423,
      43.8139
    ]
  },
  "shape_label": "A spot on the edge",
  "observed_on": "2026-10-04",
  "note": "Example: weeds thin out below the riffle.",
  "provenance": "user_reported",
  "provenance_label": "Reported by you",
  "kind_label": "Weed edge",
  "status_label": "reported, not verified",
  "date_label": "seen 2026-10-04",
  "caption": "Weed edge · reported, not verified · seen 2026-10-04",
  "created_at": "2026-10-13 16:05:09",
  "updated_at": null
}
```

A line works the same way; `geometry` is a `LineString` and `shape_label` reads "A line along the edge".

Errors (`detail` is always shown to the person as given):

- `404` unknown water body:
```json
{
  "detail": "No water body with id 'river:no-such-river'"
}
```

- `422` not on or near the water body:
```json
{
  "detail": "That is 6,180 m from Credit River. A weed edge has to be on the water, within 150 m of its drawn line."
}
```

- `422` the water body has no outline on file:
```json
{
  "detail": "Lake Ontario has no outline on file, so a weed edge cannot be checked against it. Weed edges can only be reported on water that is drawn."
}
```

- `422` a malformed body (a future date, a bad position, an extra field). FastAPI's list form:
```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": [
        "body",
        "observed_on"
      ],
      "msg": "Value error, observed_on cannot be in the future"
    }
  ]
}
```

### `GET /map/weed-reports`

The caller's own reports, newest sighting first, as `{"weed_reports": [...]}`. Optional
`water_body_id` and `bbox` (`min_lon,min_lat,max_lon,max_lat`, a report is in view when its own box
overlaps it). An empty list is `{"weed_reports": []}`. No count field. One report (`GET /map/weed-reports/{id}`)
is the same object as one item. The first of the two reports on the Credit River:

```json
{
  "weed_reports": [
    {
      "weed_report_id": 1,
      "water_body_id": "river:credit-river",
      "water_body_name": "Credit River",
      "jurisdiction": "CA-ON",
      "geometry": {
        "type": "Point",
        "coordinates": [
          -79.96423,
          43.8139
        ]
      },
      "shape_label": "A spot on the edge",
      "observed_on": "2026-10-04",
      "note": "Example: weeds thin out below the riffle.",
      "provenance": "user_reported",
      "provenance_label": "Reported by you",
      "kind_label": "Weed edge",
      "status_label": "reported, not verified",
      "date_label": "seen 2026-10-04",
      "caption": "Weed edge · reported, not verified · seen 2026-10-04",
      "created_at": "2026-10-13 16:05:09",
      "updated_at": null
    }
  ]
}
```

### `GET /map/weed-reports/layer`

The map layer: the same reports as a GeoJSON `FeatureCollection`, with `water_body_id` and `bbox`
narrowing it. Only the caller's own reports are ever in it. Every feature is labelled and dated; `meta`
holds the layer's name and note and has no number in it. The `id` of a feature is its `weed_report_id`.
(Two of the three example features shown.)

```json
{
  "type": "FeatureCollection",
  "meta": {
    "layer": "Your weed edges",
    "note": "Weed edges you reported. Only you can see them. They are your sightings, not checked against anything else.",
    "visible_to": "you"
  },
  "features": [
    {
      "type": "Feature",
      "id": 1,
      "geometry": {
        "type": "Point",
        "coordinates": [
          -79.96423,
          43.8139
        ]
      },
      "properties": {
        "weed_report_id": 1,
        "water_body_id": "river:credit-river",
        "water_body_name": "Credit River",
        "jurisdiction": "CA-ON",
        "shape_label": "A spot on the edge",
        "observed_on": "2026-10-04",
        "note": "Example: weeds thin out below the riffle.",
        "provenance": "user_reported",
        "provenance_label": "Reported by you",
        "kind_label": "Weed edge",
        "status_label": "reported, not verified",
        "date_label": "seen 2026-10-04",
        "caption": "Weed edge · reported, not verified · seen 2026-10-04"
      }
    },
    {
      "type": "Feature",
      "id": 2,
      "geometry": {
        "type": "LineString",
        "coordinates": [
          [
            -79.96423,
            43.8139
          ],
          [
            -79.96307,
            43.81495
          ]
        ]
      },
      "properties": {
        "weed_report_id": 2,
        "water_body_id": "river:credit-river",
        "water_body_name": "Credit River",
        "jurisdiction": "CA-ON",
        "shape_label": "A line along the edge",
        "observed_on": "2026-09-21",
        "note": null,
        "provenance": "user_reported",
        "provenance_label": "Reported by you",
        "kind_label": "Weed edge",
        "status_label": "reported, not verified",
        "date_label": "seen 2026-09-21",
        "caption": "Weed edge · reported, not verified · seen 2026-09-21"
      }
    }
  ]
}
```

### `PATCH /map/weed-reports/{report_id}`

Change `geometry`, `observed_on` or `note`. Only the fields sent change; `"note": null` clears the note;
`geometry` and `observed_on` cannot be cleared. A new `geometry` is checked against the report's water body
again (`422` as above). The water body cannot change: delete the report and make a new one. `404` if the report
is not the caller's.

```json
{
  "observed_on": "2026-10-05",
  "note": null
}
```

```json
{
  "weed_report_id": 1,
  "water_body_id": "river:credit-river",
  "water_body_name": "Credit River",
  "jurisdiction": "CA-ON",
  "geometry": {
    "type": "Point",
    "coordinates": [
      -79.96423,
      43.8139
    ]
  },
  "shape_label": "A spot on the edge",
  "observed_on": "2026-10-05",
  "note": null,
  "provenance": "user_reported",
  "provenance_label": "Reported by you",
  "kind_label": "Weed edge",
  "status_label": "reported, not verified",
  "date_label": "seen 2026-10-05",
  "caption": "Weed edge · reported, not verified · seen 2026-10-05",
  "created_at": "2026-10-13 16:05:09",
  "updated_at": "2026-10-13T16:05:09"
}
```

### `DELETE /map/weed-reports/{report_id}`

Delete the caller's report. `404` if it is not theirs.

```json
{
  "deleted": 2
}
```

### Another user's report

Asking for, editing or deleting a report that belongs to someone else is a `404`, the same as an
id nobody has used. Their layer and list are simply empty of it.

```json
{
  "detail": "No such weed report"
}
```


## What is not here yet

- **Seasons and gear.** The card makes no claim about when or how to fish.
- **Water body outlines** are built for the curated lakes: a lake's OHN Waterbody polygon is
  matched by position at build time and served as `geometry`, with the source OGF_IDs, name,
  effective date and licence on `attributes` (see `GET /map/water-bodies/{id}/features`). A lake
  with no match stays `geometry: null`.
- **Lake features** — inlets, outlets, islands, points and peninsulas — are built from a lake's
  outline, and drop-offs and humps from the LIO Bathymetry depth contours of the ~3,500 surveyed
  Ontario lakes, all served at `GET /map/water-bodies/{id}/features`. A lake with no survey gets
  no drop-off or hump. The only weed edges anywhere are the private ones a user reports themselves
  (see [Weed reports](#weed-reports)); none is derived, and none appears on a spot, a card or a
  water body.
- **Quiet water outside the ingest areas.** It is built only for the areas the weekly ingest
  covers; elsewhere "few records" would mean "nothing collected".
- **Month or season claims** on "recorded more often here". Not made: too few dated records.

### New to you

A per-user hook and block, filled only for the logged-in caller:

    new_species = species recorded within 2 km − the caller's confirmed catches
                  (and never a species with a listing signal)

- **Confirmed catches only.** A species the angler has logged but not confirmed (a text or
  photo-ID guess) is not caught, so it still counts as new. This is the same rule the
  FishDex screen uses.
- **Never a listed species.** The same screen that keeps a protected fish out of "You might
  find" keeps it out of the count and the names. A spot whose only recorded fish are listed
  has nothing new, and its hook stays the care message.
- **Computed per request, for the caller alone.** One angler's catches never change another
  angler's card, and nothing about it is stored on a spot or in a cache. Do not cache the
  response across users.
- **No user, or nothing new:** `card.new_to_you` is `null` and the hook is whatever the
  chain gives next (`structure`, `honest_empty`, `kind_description`).
- **Shown as a prompt, not a score:** "3 species recorded here you haven't caught yet" in the
  hook; the names behind a tap on `card.new_to_you.species[]`. No ranking, no total across
  spots, nothing to share.

`card.new_to_you` = `title`, `count`, `tap_prompt`, `species[]` (every new species, each
shaped like a `might_find` species with its own `cite`, surveys first), `note` and
`default_open` (`false`: collapsed behind the tap).

A card for an angler who has confirmed catching Creek Chub and Rainbow Darter, at a spot with
four recorded species (example records; see the fixture):

```json
{
  "hook": {
    "title": "Why it's interesting",
    "kind": "new_to_you",
    "text": "2 species recorded here you haven't caught yet"
  },
  "new_to_you": {
    "title": "New to you",
    "count": 2,
    "tap_prompt": "Tap to see which",
    "species": [
      {
        "name": "Blacknose Dace",
        "scientific_name": "Rhinichthys atratulus",
        "source": "iNaturalist",
        "date": "2023-08-19",
        "evidence": "sighting",
        "cite": "iNaturalist, 2023-08-19",
        "note": null
      },
      {
        "name": "White Sucker",
        "scientific_name": "Catostomus commersonii",
        "source": "iNaturalist",
        "date": "2022-05-02",
        "evidence": "sighting",
        "cite": "iNaturalist, 2022-05-02",
        "note": null
      }
    ],
    "note": "Based on the catches you have confirmed. Only you see this.",
    "default_open": false
  }
}
```

`GET /map/spots?bbox=…&new_to_me=true` for the same angler returns the spots that still have
something new, each with its count (`meta.new_to_me` is `true`):

```json
{
  "type": "Feature",
  "id": "creek_junction:n8038226_4319286",
  "geometry": { "type": "Point", "coordinates": [-80.38226, 43.19286] },
  "properties": {
    "spot_id": "creek_junction:n8038226_4319286",
    "kind": "creek_junction",
    "title": "Where the Nith River joins the Grand River",
    "tier": 1,
    "jurisdiction": "CA-ON",
    "water_body_id": "river:grand-river",
    "new_to_you": {
      "count": 2,
      "text": "2 species recorded here you haven't caught yet"
    }
  }
}
```

(Trimmed here; the fixture has the full feature and the full response.)

## About the example data

The fixture was captured from a build that read the caches and fetched only what
Overpass would serve in time, so OpenStreetMap data covers part of the 61 stretches: most
spots show an empty `getting_there` (so their cards say nothing was found nearby) and the
build has no public fishing spots. River mouths take their lake from the curation file, so
their lakes are named. A full build fills the rest in. Shapes do not change.

No fish records have been ingested into the database the fixture was taken from, so the
spot cards in it are honest-empty or show the structure hook. The exception to "no records" is
the card with a "recorded more often here" line: its note says it was captured from a throwaway
copy that also holds the real iNaturalist and GBIF records of the main project database, and the
counts in it are those records'. Quiet water was built from the real score cache. Two entries are the
exception, and their `note` says so: the card with fish records and the protected-species
card were captured from a throwaway copy of the database with **example** records added
(four common fish, one of them a survey record, and one Redside Dace marked Endangered).
Those two example cards, the "after two catches" card and the `new_to_me` list were
captured as a logged-in angler (user 1) with example catches added to the same throwaway
copy. They exist to show the shape and are not observations. A real record near a real spot
produces the same payload.

## Building and refreshing

- `make build-spots` rebuilds spots from the stored anchors. It reads the
  cached OHN Waterbody outlines and streams the lakes need — the first run fetches
  and caches, later runs are offline. `make build-anchors` and `make build-stretches`
  run it at the end, and print what was dropped, held back at tier 3, or given a
  generic title because the source had no name.
- `make compute-untapped` writes the score cache quiet water is built from; run it before
  `make build-spots`, and again when the ingested observations change.
- `uv run python scripts/export_spots_fixture.py [db [records-db]]` recaptures the fixture from a
  database. The optional second database lends its iNaturalist and GBIF records to a throwaway copy
  so the "recorded more often here" card can be captured; without it that entry is skipped.
- `uv run python scripts/export_weed_reports_fixture.py` recaptures the weed-report fixture. It builds a
  throwaway database from the water bodies in the spots fixture and never touches the real one.
- Water bodies are rebuilt with the spots, in the same transaction, from the curated river
  sections and the lake names on river mouths; each lake's outline comes from the OHN Waterbody
  layer, matched by position and never by name alone. The build prints how many spots were
  linked to a water body and how many were not, how many lakes got an outline, and how many
  shoreline features were derived and dropped.
- User-facing words for the map live in `src/services/spot_labels.py`. The card's words
  are written by `render_spot()` in `src/services/context/render.py`, and the "why fish
  gather here" text is in `src/services/context/translate.py`. Nothing else writes any.

## `GET /map/layers/getting-there`

Returns GeoJSON point features (FeatureCollection) for parking, trailheads and boat launches. Parameters: `bbox` (optional, `min_lon,min_lat,max_lon,max_lat`) and `jurisdiction` (optional, ISO 3166-2). The bbox is applied to the access points themselves, and a point shared by several spots appears once. Labels come from the labels endpoint's `layers.getting_there` and `access_labels`. Properties: `kind`, `name`, `attributes`.

## `GET /map/layers/bridges`

Returns GeoJSON point features (FeatureCollection) for road crossings. Bridges are off by default in the UI. Parameters: `bbox` (optional) and `jurisdiction` (optional), filtered and de-duplicated as for getting-there. Labels come from `layers.bridges` and `access_labels`. Properties: `kind`, `name`, `attributes`.
