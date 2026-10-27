# Spots API

The explore map's backend, for whoever builds the UI. A **spot** is one reason to go
to one place on the water: where a river runs out into a lake, where a creek joins a
river, the water just below a dam, an official fishing spot. The map shows spots, not
river lines; the river is context ("more spots on this river").

The spots, water-body and section endpoints are read-only GETs, and every endpoint here needs the same
`Authorization: Bearer <token>` as the other `/map/*` endpoints. The one exception to read-only is
the private [weed reports](#weed-reports) the user writes themselves. The older `/map/stretches`,
`/map/stretches/{id}/anchors` and `/map/segments` endpoints are **legacy**: they are served only
for a UI that no longer exists in this repository, are not part of the spots API, keep their
old stretch/anchor field names, and are due for removal. The spots UI
should not use them.

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
   islands, points, and the piers, breakwaters and harbours on its shore).
5. `GET /map/sections?bbox=…` instead of the pins at overview zoom, and
   `GET /map/sections/{section_id}` when a section is tapped. See [Sections](#sections).
6. `GET /map/weed-reports/layer?bbox=…` for the user's own reported weed edges, and
   `POST /map/weed-reports` when they report one on a water body. Private to them; see
   [Weed reports](#weed-reports).

## Rules the UI should keep

These come from the project's central idea: records of fish are not the same as
pressure from anglers, and a map must not blur them.

- Show `title` and `subtitle` as given. Titles are built from the river's name and never
  read "Unnamed …". Two spots of one kind never share a title: see
  [Repeated structures](#repeated-structures).
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
- At overview zoom draw [sections](#sections), not spot pins: nine near-identical pins a few
  hundred metres apart are noise, and a section is one shape with a count. Pins appear after a tap.
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
- `repeated_titles` — the templates the build uses to tell spots apart when several would share a
  title (`templates`: `road`, `access`, `junction`, `order`) and the two `directions` words. The
  titles in `/map/spots` arrive already filled in; see [Repeated structures](#repeated-structures).
- `quiet_water` — the words only quiet water needs: `titles` (the two title templates the build
  fills), `caveats` (`records`, `access_not_measured`, `access_coverage_unknown`) and `not_built`
  (what `meta.notes` says when none has been built).
- `sections` — the words the [sections](#sections) layer needs: `kinds[]` (`kind`, `label`, `one_liner`),
  `pin_types` (a word for each pin type), `count_phrases` (`one` and `many` for each thing a
  section counts, which is what `summary` is built from), `empty_summary`, `label_only_note`
  (why a water with no outline is a name only), `weed_count_note` and `not_built`.
- `layers` — the Getting there and Bridges layers: `label`, `kinds`, `default_on`, `note`.
- `access_labels` — display names for `getting_there[].kind` and `bridges[].kind`.
- `water_body_types[]` — for each water body `type`: `chip` (plural, for a filter), `label`
  (singular, for a card) and `unnamed` (the phrase to show when `name` is null).
- `name_sources` — a plain-words line for each `name_source`, for a "source" caption under a name.
- `no_outline_note` — what to say when a lake has no shape on the map.
- `shoreline_features[]` — for each shoreline feature `kind` (`inlet`, `outlet`, `island`,
  `point`, `peninsula`, `drop_off`, `hump`, `pier`, `breakwater`, `harbour`): `label` (singular, for a card), `chip` (plural,
  for a legend) and `one_liner` (what it is, in beginner words).
- `shoreline_derivations` — a plain-words phrase for each `derivation`, for the caption under a
  shoreline feature: `shoreline_outline` (a shape read off the outline itself),
  `watercourse_endpoint` (a mapped stream that ends at the shore), `bathymetry_contours`
  (a shape read off a lake's surveyed depth contours) or `osm_feature` (a structure mapped by
  OpenStreetMap volunteers).
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
      "one_liner": "A mapped stream starts at the shore here and flows away."
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
    },
    {
      "kind": "pier",
      "label": "Pier",
      "chip": "Piers",
      "one_liner": "A pier reaches out from the shore."
    },
    {
      "kind": "breakwater",
      "label": "Breakwater",
      "chip": "Breakwaters",
      "one_liner": "A breakwater stands offshore here."
    },
    {
      "kind": "harbour",
      "label": "Harbour",
      "chip": "Harbours",
      "one_liner": "A harbour is mapped here."
    }
  ],
  "shoreline_derivations": {
    "shoreline_outline": "from the shoreline outline",
    "watercourse_endpoint": "where a mapped stream meets the shore",
    "osm_feature": "mapped by OpenStreetMap volunteers"
  },
  "repeated_titles": {
    "templates": {
      "road": "{title}, near {place}",
      "access": "{title}, near {place}",
      "junction": "{title}, near where {place} joins",
      "order": "{title} ({n} of {total}, {direction})"
    },
    "directions": {
      "west_to_east": "west to east",
      "north_to_south": "north to south"
    }
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
| `species` | Scientific/common name; a spot matches when that species has records within 2 km. Listed species (status_known_listed) are never returned. The records are read around each spot in view, so pass a `bbox`: a province-wide request looks up every spot. |
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
| `structure_type` | Internal kind of structure: `lake_mouth`, `junction`, `dam`, `weir`, `fishing_spot`. For code and the bot, not for display. |
| `structure_attrs` | Facts about this structure. Junctions: `joining_upstream_km`, `river_upstream_km` (the size of the river the spot is on) and `river_flows_on` (false when that river ends at the junction). Dams: `dam_kind`, `below_dam_verified` (when `false` the title says "At the …" because the water below could not be verified), `reach_m`. River mouths: `lake` (the lake's name; null when no source gave one) and `lake_name_source` (`curated`, `curated_region` or `ohn`). The lake's name here and the name of the lake water body are always the same string. |
| `structure_attrs` (repeats) | When the build merged repeats into this spot, `merged_spot_ids` lists the ids it absorbed. When the title had to be told apart from another spot's, `title_distinguished_by` is `road`, `access`, `junction` or `order`. Junctions carry `tributary_name` when the joining creek has a name. See [Repeated structures](#repeated-structures). |
| `signals` | Separate numbers, never combined into a score: `named`, `size_class` (`small`/`medium`/`large`, junctions), `nearest_parking_m`, `nearest_launch_m`, `nearest_bridge_m`. |
| `getting_there[]` | Nearest parking, trailheads and boat launches within 1.5 km, at most two of each kind: `kind`, `name`, `lat`, `lng`, `distance_m` (straight line), `source_ref`, `attributes` (OSM tags such as `access`, `fee`, `surface`). Volunteer-mapped; say so. |
| `bridges[]` | Nearest public road crossings within 1 km, same shape. A way to reach small water, not a place to fish. |
| `sources[]` | Where the facts came from: `kind` (`ohn_topology`, `osm`, `curation`; a river mouth taken from the curation file is `curation`), `ref`, the `merged_record_ids` merged into the spot, `built_at`. Behind a "Sources" link, not on the card face. |
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
    "below_dam_verified": true
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
      "merged_record_ids": [
        "grand-cambridge-paris:tailwater:osm-way-248706708"
      ],
      "built_at": "2026-10-12T20:30:44+00:00"
    },
    {
      "kind": "osm",
      "ref": "way/248706708",
      "merged_record_ids": [
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
      "merged_record_ids": [
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
| `fields` | Comma-separated `properties` to keep, from the table below (`water_body_id` is always kept, it is the feature's id). Omit for all. An unknown name is a 422 that lists the valid ones. |
| `geometry` | `false` sends `geometry: null` on every feature and `meta.geometry: "none"`. Default `true`. Which waters are returned does not change. |
| `simplify` | Thin each shape so no point of the answer is further than this many **metres** from the stored line. `0` or omitted sends the stored shape. |
| `zoom` | The same thinning, given as a web-map zoom (0 to 22): the tolerance is what one screen pixel covers at that zoom and the water's latitude. Give `simplify` or `zoom`, not both, and neither with `geometry=false` (422). |

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

### Sending less

Almost all of this response is geometry: a matched lake outline is megabytes, and the whole
province is tens of megabytes. A client should ask for what it draws.

- **A list or a search box** (names, types, badges): `?fields=display_name,type&geometry=false`.
  Without a `bbox` no outline is read from the database; with one, only the waters that straddle
  the box's edge are read, to decide whether they are in view.
- **A map layer**: `?zoom=<the map's zoom>`, or `?simplify=<metres>` for a fixed tolerance. Thinning
  keeps polygons closed and valid and never changes which waters are returned; only their vertices.
  Ask again at the new zoom when the map zooms in, since a thinned outline is not the stored one.
- **Telling thinned from missing**: when `geometry` or `simplify`/`zoom` is used, `meta.geometry` is
  `"none"` or `"simplified"` (with `meta.simplify_m` or `meta.zoom`). A lake with no outline on file
  still has `geometry: null` and, on the detail, `geometry_source: "none"`; a `geometry=false`
  answer says nothing about outlines, so read `geometry_source` on the detail if you need it.

With none of these the response is what it always was, so existing clients are unaffected.
`/map/water-bodies/{id}/features` is not thinned: its shapes are points and small islands.

Measured on the Toronto box (a database holding the real Lake Ontario, Humber Bay and Rice
Lake outlines, 10.7 MB of geometry): the plain list is 8.9 MB; `zoom=9` is 0.10 MB;
`zoom=12` is 0.36 MB; `fields=…&geometry=false` is under 20 KB.

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
        "type": "Polygon",
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

`geometry=false` sends `geometry: null`; `simplify` (metres) or `zoom` thins the shape the same
way as on the list (see [Sending less](#sending-less)). `geometry_source` is served either way. The
same 422s apply: both options together, or either with `geometry=false`. A card that does not
draw the outline should ask for `geometry=false`: Lake Ontario's is 6.3 MB.

| Field | Meaning |
|---|---|
| `water_body_id`, `name`, `display_name`, `name_source`, `type`, `jurisdiction`, `bbox` | As in the list. |
| `geometry`, `geometry_source` | The line (`section_union`: the river's sections drawn together) or polygon (`ohn_waterbody`: the matched OHN Waterbody outline) — or `null` / `none` for a lake with no outline on file. |
| `section_ids[]` | The river sections that make up a river or creek, or that open onto a lake. The spot UI rarely needs them. |
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
  "geometry_source": "section_union",
  "bbox": [
    -79.98834,
    43.53952,
    -79.57731,
    43.81619
  ],
  "section_ids": [
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

A bay with no outline of its own (Humber Bay, Long Point Bay, Owen Sound) is never drawn as the
lake it opens off: it has no geometry and is labelled only. A lake whose outline was not matched has no geometry, and its spots are the river mouths that
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
  "section_ids": [
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
reaching out (`point`, `peninsula`), the structures OpenStreetMap maps on its shore — a
`pier`, a `breakwater`, a `harbour` — kept when they sit on the shore or within 500 m of it (at most 50 per lake, named and longer first), and —
where the lake has a digital survey — where the bottom falls away quickly (`drop_off`) or rises
into a shallower shoal (`hump`). The outline kinds are read off the lake's OHN Waterbody outline
and the OHN streams that end at its shore; the structures come from OpenStreetMap (ODbL); the
last two are read off the LIO Bathymetry depth contours. A located thing to show, never a claim
about fish. A river has no shoreline, and a lake with no contours gets no drop-off or hump, so
the honest answer is an **empty collection**, not an error; an unknown id is `404`.

Each feature's `properties`:

| Field | Meaning |
|---|---|
| `feature_id` | Stable id: `{water_body_id}:{kind}:{key}`, where the key comes from the feature's location, the stream's OGF_ID for an inlet or outlet, or the OSM element id for a pier, breakwater or harbour. |
| `water_body_id` | The lake the feature belongs to. |
| `kind` | `inlet`, `outlet`, `island`, `point`, `peninsula`, `drop_off`, `hump`, `pier`, `breakwater` or `harbour`. Words in `labels.shoreline_features`. |
| `name` | The mapped stream's name for an inlet or outlet, the mapped structure's name for a pier, breakwater or harbour, else `null`. |
| `derivation` | How it was obtained: `watercourse_endpoint` (a mapped OHN stream that ends at the shore), `shoreline_outline` (a shape read off the outline's own edge), `bathymetry_contours` (a shape read off a lake survey's depth contours) or `osm_feature` (a structure mapped in OpenStreetMap). Phrase in `labels.shoreline_derivations`. |
| `provenance` | `record` for an island (a ring inside the source polygon), a stream junction or a mapped structure; `inference` for a point, peninsula, drop-off or hump (a shape computed from the outline or the contours). |
| `note` | A full sentence for the card, already assembled from the vocabulary. Safe to show as-is. A drop-off or hump names its survey ("from a 1970 survey, transect") or says `survey date unknown`. |
| `attributes` | Survey facts behind a contour-derived feature: `contour_ogf_ids`, `survey_year`, `survey_method`, `depth_basis`, `depth_m`, and (for a drop-off) `depth_change_m` and `gap_m` or (for a hump) `area_m2`. Also the `attribution`, `licence` and `licence_url` to credit. `{}` for an outline-derived feature. |
| `source`, `source_date` | The named source (`OHN Waterbody`, `OHN Watercourse`, `LIO Bathymetry` or `OpenStreetMap`, credited ODbL) and that record's effective date (`YYYY-MM-DD`, or `null` if the source gave none — OpenStreetMap gives none). Credit these. |
| `area_m2` | An island's area, a hump's area, or the area of a structure mapped as an area; else `null`. |
| `length_m` | How far a point or peninsula sticks out from the shore, the length of a pier or breakwater drawn as a line, or the depth change across a drop-off, in metres; else `null`. |
| `geometry` | A `Point` for inlets, outlets, points, peninsulas and drop-offs; a `Polygon` for an island, a hump or a structure mapped as an area; a `LineString` for a pier or breakwater drawn as a line. |

```json
{
  "type": "FeatureCollection",
  "water_body_id": "lake:rice-lake",
  "water_body_name": "Rice Lake",
  "meta": {
    "count": 4,
    "by_kind": {
      "inlet": 1,
      "island": 1,
      "drop_off": 1,
      "pier": 1
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
    },
    {
      "type": "Feature",
      "id": "lake:rice-lake:pier:way/1036884186",
      "geometry": {
        "type": "LineString",
        "coordinates": [
          [-78.2, 44.18],
          [-78.199, 44.1803]
        ]
      },
      "properties": {
        "feature_id": "lake:rice-lake:pier:way/1036884186",
        "water_body_id": "lake:rice-lake",
        "kind": "pier",
        "name": null,
        "derivation": "osm_feature",
        "provenance": "record",
        "source": "OpenStreetMap",
        "source_date": null,
        "note": "A pier reaches out from the shore. Mapped by OpenStreetMap volunteers (OpenStreetMap, ODbL).",
        "attributes": {},
        "area_m2": null,
        "length_m": 124.7
      }
    }
  ]
}
```

## Sections

At overview zoom, drawing every spot is noise: one city view was fifty pins, nine of them
"Below the weir on the Humber River" a few hundred metres apart. So the overview draws
**sections** instead, and the pins appear only after a tap.

A **section** is one tappable piece of water:

- a **river section**: one curated run of a river or creek, like "Lower Humber — Woodbridge to
  Humber Bay". Its id is the section id already on a spot's `water_id` and a water body's
  `section_ids`;
- a **lake with an outline**, drawn as its outline. Its id is the water body id
  (`lake:rice-lake`), so a lake id and a river-section id can never be the same string.

A lake or bay with **no outline** is not a section: there is no shape to tap. It comes back in
the list's `labels` as a name at a place, and nothing opens from it.

Nothing is stored for sections. They are read off the stored river sections, water bodies, spots
and shoreline features on every call, so a rebuild changes them with no second step.

**`tests/fixtures/sections_api_responses.json`** holds the responses below, captured by
`scripts/export_sections_fixture.py` (the examples here are copied from it, trimmed where marked).
The river sections, spots and counts are from a real build. Two things in it are examples, and the
entry's `note` says which: no build has a lake outline yet, so the lake entries are from a
hand-drawn **example** lake (Rice Lake, with an island, a drop-off and a hump); and weed reports
are private, so the weed edge is an **example** added to a throwaway copy.

### How the UI uses sections

1. **Low zoom: sections, no pins.** Call `GET /map/sections?bbox=…` when the map moves and draw
   one shape per feature. Do **not** call `GET /map/spots` at this zoom. Label each shape with
   `name` and show `summary` (the counts in words) on hover or tap. Draw `labels` as plain
   text with no tap target.
2. **A tap on a section: its pins.** Call `GET /map/sections/{section_id}`, zoom to
   `section.properties.bbox`, and draw `features` as pins. Nine weirs on one river are nine pins
   here, inside one section, where they can be told apart.
3. **A tap on a pin: the card that already exists.** Branch on `properties.pin_type`:

   | `pin_type` | What it is | Opens |
   |---|---|---|
   | `spot` | a spot: below a dam, where creeks join, a river mouth, a public fishing spot | `GET /map/spots/{spot_id}`, the spot card |
   | `feature` | a lake's shoreline or depth feature: inlet, outlet, island, point, peninsula, drop-off, hump | the feature's own properties, already in the pin (same as `GET /map/water-bodies/{id}/features`) |
   | `getting_there` | parking, a trailhead or a boat launch near the section's spots | nothing: `serves` names the spot ids it is near |
   | `weed_report` | a weed edge **you** reported | `PATCH` / `DELETE /map/weed-reports/{report_id}` |

4. **A pin is not a new thing.** `id` is always the id of what the pin stands for: the spot id, the
   feature id, `kind:source_ref` for getting there, or the weed report id. A spot pin has the same
   properties as a feature in `GET /map/spots`.

`GET /map/spots` is **unchanged**. It still lists every spot, so a client that has not moved to
sections keeps working. Keep using it for the kind chips, `new_to_me` and `near`. Quiet water has no
water of its own, so it is in no section and stays on `GET /map/spots?kind=quiet_water`.

### `GET /map/sections`

Returns a GeoJSON FeatureCollection: one feature per river section and per lake with an outline,
plus `labels`. Parameters, all optional:

| Parameter | Meaning |
|---|---|
| `bbox` | `min_lon,min_lat,max_lon,max_lat`. A section is in view when its drawn shape crosses the box, not merely when its bounding box does. A label is in view when its place is. A bad box is a 422 |
| `type` | comma-separated water types: `river`, `creek`, `lake`, `pond`, `great_lake`. An unknown type is a 422 |
| `tier` | the highest spot tier counted: `1`, `2` (default) or `3`; cumulative, as on `GET /map/spots`. Anything else is a 422 |
| `jurisdiction` | ISO 3166-2 code, for example `CA-ON` |

Sorted by type (river, creek, lake, pond, great lake), then name. Each feature's properties:

| Field | Meaning |
|---|---|
| `section_id` | the id; also the feature's `id`. Pass it to `GET /map/sections/{section_id}` |
| `kind` | `river_section` or `lake`. Words for each are in `GET /map/spots/labels` under `sections.kinds` |
| `type`, `type_label` | the water's type (`river`, `creek`, `lake`, `pond`, `great_lake`) and its word |
| `name` | the section's name. Never "Unnamed …": a lake with no source name gets its type's own word |
| `water_body_id`, `water_body_name` | the river, creek or lake it belongs to |
| `region`, `length_km` | a river section's place and length; `null` for a lake |
| `bbox`, `label_point` | where to zoom, and where to put the name (inside a lake's outline, mid-run on a river) |
| `counts` | what is inside, below |
| `summary` | the counts in a sentence: `1 river mouth, 7 places where creeks join`, or "Nothing mapped here yet" |

`counts` is `{spots, features, weed_reports, total}`. `spots` and `features` are objects keyed by
kind, in display order, and a kind with none is absent (never `0`). `spots` holds
`river_mouth`, `creek_junction`, `below_dam` and `public_fishing`: a spot listed under two river
sections counts in both, and a river mouth counts on the lake it opens onto. `features` holds a
lake's `inlet`, `outlet`, `island`, `point`, `peninsula`, `drop_off` and `hump`, and is empty on a
river. `weed_reports` is **your own** weed edges only (see below). `total` is all three added up,
and is exactly the number of spot, feature and weed pins the section opens to (getting-there points
are details of the spots and are not counted).

**Payload.** A river section's line is the curated line (about a hundred points for a long run). A
lake outline is simplified to about 200 m, drawn whole with no islands cut out (islands are pins
of their own), with parts under about 0.12 km² dropped, and rounded to five decimal places. A city
view is about ten kilobytes.

```json
{
  "type": "FeatureCollection",
  "meta": {
    "count": 4,
    "label_count": 1,
    "type_counts": {
      "river": 4,
      "creek": 0,
      "lake": 1,
      "pond": 0,
      "great_lake": 0
    },
    "max_tier": 2,
    "weed_reports_note": "Weed edges you reported. Only you see them."
  },
  "features": [
    {
      "type": "Feature",
      "id": "humber-woodbridge-humber-bay",
      "geometry": {
        "type": "LineString",
        "coordinates": [
          [
            -79.58995,
            43.78818
          ],
          [
            -79.58941,
            43.78552
          ],
          [
            -79.59171,
            43.7835
          ],
          "… 103 more points"
        ]
      },
      "properties": {
        "section_id": "humber-woodbridge-humber-bay",
        "kind": "river_section",
        "type": "river",
        "type_label": "River",
        "name": "Lower Humber — Woodbridge to Humber Bay",
        "water_body_id": "river:humber-river",
        "water_body_name": "Humber River",
        "region": "Lake Ontario",
        "jurisdiction": "CA-ON",
        "length_km": 33.52,
        "bbox": [
          -79.59211,
          43.62106,
          -79.4508,
          43.78818
        ],
        "label_point": [
          -79.5391,
          43.71132
        ],
        "counts": {
          "spots": {
            "river_mouth": 1,
            "creek_junction": 7
          },
          "features": {},
          "weed_reports": 0,
          "total": 8
        },
        "summary": "1 river mouth, 7 places where creeks join"
      }
    },
    "… 3 more sections"
  ],
  "labels": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "id": "lake:humber-bay",
        "geometry": {
          "type": "Point",
          "coordinates": [
            -79.4675,
            43.63037
          ]
        },
        "properties": {
          "water_body_id": "lake:humber-bay",
          "name": "Humber Bay",
          "display_name": "Humber Bay",
          "type": "lake",
          "type_label": "Lake",
          "jurisdiction": "CA-ON",
          "bbox": [
            -79.4675,
            43.63037,
            -79.4675,
            43.63037
          ],
          "note": "No outline is on file for this water, so it is a name on the map only."
        }
      }
    ]
  }
}
```

A lake with an outline (the **example** Rice Lake), from `?type=lake`:

```json
{
  "type": "FeatureCollection",
  "meta": {
    "count": 1,
    "label_count": 0,
    "type_counts": {
      "river": 2,
      "creek": 1,
      "lake": 1,
      "pond": 0,
      "great_lake": 1
    },
    "max_tier": 2,
    "weed_reports_note": "Weed edges you reported. Only you see them."
  },
  "features": [
    {
      "type": "Feature",
      "id": "lake:rice-lake",
      "geometry": {
        "type": "Polygon",
        "coordinates": "… simplified outline"
      },
      "properties": {
        "section_id": "lake:rice-lake",
        "kind": "lake",
        "type": "lake",
        "type_label": "Lake",
        "name": "Rice Lake",
        "water_body_id": "lake:rice-lake",
        "water_body_name": "Rice Lake",
        "region": null,
        "jurisdiction": "CA-ON",
        "length_km": null,
        "bbox": [
          -78.3,
          44.0,
          -78.0,
          44.2
        ],
        "label_point": [
          -78.15,
          44.1
        ],
        "counts": {
          "spots": {
            "river_mouth": 1
          },
          "features": {
            "inlet": 2,
            "island": 1,
            "drop_off": 1,
            "hump": 1
          },
          "weed_reports": 0,
          "total": 6
        },
        "summary": "1 river mouth, 2 inlets, 1 island, 1 drop-off, 1 hump"
      }
    }
  ],
  "labels": {
    "type": "FeatureCollection",
    "features": []
  }
}
```

**Empty and unbuilt.** A box with nothing in it is a normal response with `count: 0`. A database
whose river sections have not been built returns the same shape with `meta.notes` saying so; draw
nothing and fall back to `GET /map/spots`.

**Weed counts are private.** `counts.weed_reports` and `counts.total` include the weed edges the
caller reported, and nobody else's: another login gets `0`. Because of that the response carries
`Cache-Control: private, no-store`, like the card's "new to you" line. A river's weed reports
are filed on the whole river, so each is counted in the **one** section of that river it is
nearest, never in two. That is decided across all of the river's sections, not just the ones in
the `bbox` or passing the `type` filter, so panning never moves a report between sections and the
list count always matches the section's own pins. Never show the number to anyone but the person who made the reports.

### `GET /map/sections/{section_id}`

Every point of interest in one section, as pins. A lake id contains a colon
(`lake:rice-lake`) and works in the path as it is. An unknown id is a 404, and so is a lake
with no outline: it is not a section. Parameter: `tier`, as above.

The response is a FeatureCollection of Points:

| Field | Meaning |
|---|---|
| `section` | the same feature the list carries, so a link straight to a section can draw it |
| `meta` | `count`, `by_pin_type` (`spot`, `feature`, `getting_there`, `weed_report`), `max_tier`, `weed_reports_note` |
| `features` | the pins, in this order: spots (by tier, then kind, then id), features, getting there, your weed reports |

Every pin is a Point with an `id` and a `properties.pin_type`.

**`spot`** pins carry exactly the properties of a feature in `GET /map/spots` (`spot_id`, `kind`,
`title`, `subtitle`, `signals` and the rest) plus `pin_type`. Tap one and call
`GET /map/spots/{spot_id}`. Below-dam spots, places where creeks join, river mouths and public
fishing spots are all spots. A section holds the ones on its own run, the ones listed under it as
a second section (a creek joining it) and, for a lake, the river mouths that open onto it.

```json
{
  "type": "Feature",
  "id": "creek_junction:n7951147_4366924",
  "geometry": {
    "type": "Point",
    "coordinates": [
      -79.51147,
      43.66924
    ]
  },
  "properties": {
    "pin_type": "spot",
    "spot_id": "creek_junction:n7951147_4366924",
    "kind": "creek_junction",
    "title": "Where Black Creek joins the Humber River",
    "subtitle": "Humber River · Lower Humber — Woodbridge to Humber Bay",
    "tier": 1,
    "jurisdiction": "CA-ON",
    "water_id": "humber-woodbridge-humber-bay",
    "water_name": "Lower Humber — Woodbridge to Humber Bay",
    "water_body_id": "river:humber-river",
    "signals": {
      "named": true,
      "size_class": "medium"
    }
  }
}
```

**`feature`** pins (lakes only) carry the properties of a feature in
`GET /map/water-bodies/{id}/features`: `feature_id`, `kind`, `derivation`, `provenance`, `source`,
`note`, `attributes` and the rest, plus a `label` ("Hump"). Drop-offs and humps come from a
depth survey and say so in `note`; inlets, outlets, islands, points and peninsulas are read from
the shoreline outline. An island or hump is an area: its pin sits inside it, and its outline comes
along as `properties.shape` for when you want to draw it after a tap.

```json
{
  "type": "Feature",
  "id": "lake:rice-lake:hump:1",
  "geometry": {
    "type": "Point",
    "coordinates": [
      -78.235,
      44.03
    ]
  },
  "properties": {
    "pin_type": "feature",
    "feature_id": "lake:rice-lake:hump:1",
    "water_body_id": "lake:rice-lake",
    "kind": "hump",
    "name": null,
    "derivation": "bathymetry_contours",
    "provenance": "record",
    "source": "LIO Bathymetry",
    "source_date": null,
    "note": "A shallower rise in the bottom. From a 1970 survey.",
    "attributes": {
      "survey_year": 1970
    },
    "area_m2": null,
    "length_m": null,
    "label": "Hump",
    "shape": {
      "coordinates": [
        [
          [
            -78.25,
            44.02
          ],
          [
            -78.22,
            44.02
          ],
          [
            -78.22,
            44.04
          ],
          [
            -78.25,
            44.04
          ],
          [
            -78.25,
            44.02
          ]
        ]
      ],
      "type": "Polygon"
    }
  }
}
```

**`getting_there`** pins are parking, trailheads and boat launches found near the section's
spots, each place once however many spots it serves. `properties.serves` lists the spot ids
it is near and `id` is `kind:source_ref`. They are details of those spots, so draw them small, or
only when the section is open.

**`weed_report`** pins are the caller's own weed reports in this section, still private and
still "reported, not verified": the same words (`caption`, `status_label`, `provenance_label`) as
the weed-report routes, and `id` is the weed report id. A weed line is a pin at the middle of the
line, with the line itself in `properties.shape`. Do not style one as a fact about the water or
mix it into spot text; see [Weed reports](#weed-reports). The response is `Cache-Control: private,
no-store` for the same reason as the list.

```json
{
  "type": "Feature",
  "id": 1,
  "geometry": {
    "type": "Point",
    "coordinates": [
      -79.53822,
      43.7269
    ]
  },
  "properties": {
    "pin_type": "weed_report",
    "weed_report_id": 1,
    "water_body_id": "river:humber-river",
    "water_body_name": "Humber River",
    "jurisdiction": "CA-ON",
    "shape_label": "A spot on the edge",
    "observed_on": "2026-09-20",
    "note": "Example note: weeds along the inside bend",
    "provenance": "user_reported",
    "provenance_label": "Reported by you",
    "kind_label": "Weed edge",
    "status_label": "reported, not verified",
    "date_label": "seen 2026-09-20",
    "caption": "Weed edge · reported, not verified · seen 2026-09-20"
  }
}
```

A river section (the lower Humber, with the example weed edge), trimmed:

```json
{
  "type": "FeatureCollection",
  "section": {
    "type": "Feature",
    "id": "humber-woodbridge-humber-bay",
    "geometry": {
      "type": "LineString",
      "coordinates": [
        [
          -79.58995,
          43.78818
        ],
        [
          -79.58941,
          43.78552
        ],
        [
          -79.59171,
          43.7835
        ],
        "… 103 more points"
      ]
    },
    "properties": {
      "section_id": "humber-woodbridge-humber-bay",
      "kind": "river_section",
      "type": "river",
      "type_label": "River",
      "name": "Lower Humber — Woodbridge to Humber Bay",
      "water_body_id": "river:humber-river",
      "water_body_name": "Humber River",
      "region": "Lake Ontario",
      "jurisdiction": "CA-ON",
      "length_km": 33.52,
      "bbox": [
        -79.59211,
        43.62106,
        -79.4508,
        43.78818
      ],
      "label_point": [
        -79.5391,
        43.71132
      ],
      "counts": {
        "spots": {
          "river_mouth": 1,
          "creek_junction": 7
        },
        "features": {},
        "weed_reports": 1,
        "total": 9
      },
      "summary": "1 river mouth, 7 places where creeks join, 1 weed edge you reported"
    }
  },
  "meta": {
    "count": 9,
    "by_pin_type": {
      "spot": 8,
      "feature": 0,
      "getting_there": 0,
      "weed_report": 1
    },
    "max_tier": 2,
    "weed_reports_note": "Weed edges you reported. Only you see them."
  },
  "features": [
    {
      "type": "Feature",
      "id": "river_mouth:n7946750_4363037",
      "geometry": {
        "type": "Point",
        "coordinates": [
          -79.4675,
          43.63037
        ]
      },
      "properties": {
        "pin_type": "spot",
        "spot_id": "river_mouth:n7946750_4363037",
        "kind": "river_mouth",
        "title": "Where the Humber River meets Humber Bay",
        "subtitle": "Humber River · Lower Humber — Woodbridge to Humber Bay",
        "tier": 1,
        "jurisdiction": "CA-ON",
        "water_id": "humber-woodbridge-humber-bay",
        "water_name": "Lower Humber — Woodbridge to Humber Bay",
        "water_body_id": "river:humber-river",
        "signals": {
          "named": true
        }
      }
    },
    "… 6 more spots",
    {
      "type": "Feature",
      "id": 1,
      "geometry": {
        "type": "Point",
        "coordinates": [
          -79.53822,
          43.7269
        ]
      },
      "properties": {
        "pin_type": "weed_report",
        "weed_report_id": 1,
        "water_body_id": "river:humber-river",
        "water_body_name": "Humber River",
        "jurisdiction": "CA-ON",
        "shape_label": "A spot on the edge",
        "observed_on": "2026-09-20",
        "note": "Example note: weeds along the inside bend",
        "provenance": "user_reported",
        "provenance_label": "Reported by you",
        "kind_label": "Weed edge",
        "status_label": "reported, not verified",
        "date_label": "seen 2026-09-20",
        "caption": "Weed edge · reported, not verified · seen 2026-09-20"
      }
    }
  ]
}
```

(Trimmed here; the fixture has every pin.) The same call on a lake returns its shoreline and depth
features instead. One of the **example** Rice Lake's pins:

```json
{
  "type": "Feature",
  "id": "lake:rice-lake:drop_off:1",
  "geometry": {
    "coordinates": [
      -78.1,
      44.15
    ],
    "type": "Point"
  },
  "properties": {
    "pin_type": "feature",
    "feature_id": "lake:rice-lake:drop_off:1",
    "water_body_id": "lake:rice-lake",
    "kind": "drop_off",
    "name": null,
    "derivation": "bathymetry_contours",
    "provenance": "record",
    "source": "LIO Bathymetry",
    "source_date": null,
    "note": "The bottom falls away quickly here. From a 1970 survey.",
    "attributes": {
      "survey_year": 1970
    },
    "area_m2": null,
    "length_m": null,
    "label": "Drop-off"
  }
}
```

An unknown section, or a lake with no outline:

```json
{
  "detail": "No section with id 'no-such-section'"
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

## Repeated structures

A river can have a weir every few hundred metres, or a run of small unnamed creeks. The build
used to give each one its own spot with the same title and overlapping record circles, so a list
could not tell them apart and one set of records counted several times. Two steps in the build
now deal with it. Both read only what exists for every province and state (the spots, and the
OpenStreetMap roads and parking already attached to them); nothing is specific to Ontario.

**Merging.** Spots of the same `kind` with the same `title` (the title carries the water, so this
is the same structure on the same water) that sit within a radius of each other become one spot.
The radius is `REPEAT_MERGE_M` in `src/services/spot_repeats.py`: 400 m for dams and weirs, 250 m
for creek junctions. River mouths, public fishing spots and quiet water are not merged. The best
spot (lowest `tier`, then lowest id) keeps its place and id and takes over the others' `sources`;
the ids it absorbed are in `structure_attrs.merged_spot_ids`. A spot joins a group by its distance
to the group's lead, never to the last member, so a line of weirs does not chain into one pin.
A saved id of an absorbed spot is not served after a rebuild (404): look at the merged spot instead.

**Titles.** Spots that still share a kind and title get a place phrase added, in this order, by the
first that exists. The words come from `labels.repeated_titles`.

| `structure_attrs.title_distinguished_by` | Source | Title |
|---|---|---|
| `road` | the nearest named road crossing in `bridges` | `Below the weir on the Humber River, near Steeles Avenue East` |
| `access` | the nearest named parking, trailhead or boat launch in `getting_there` | `Below the weir on the Humber River, near Old Mill Parking` |
| `junction` | the nearest other named creek that joins within `NEARBY_JUNCTION_M` (2 km) | `Where a small creek joins the Credit River, near where Black Creek joins` |
| `order` | nothing nearby has a name: the spot's place in the group, counted along the direction the group spreads most | `Where a small creek joins the Credit River (2 of 6, north to south)` |

If two spots in a group would get the same phrase (one bridge between two weirs), both fall back to
`order`. The "N of M" counts every spot that shared the original title, so a number can skip. The
direction word is plain compass order, not upstream or downstream. The subtitle is not changed.
The result is that, within one list of anchor-derived spots, a title appears once. Quiet water is
titled by the nearest water and can still repeat.

The build report prints how many spots were merged (`not kept: repeat_structure_merged`) and how
each remaining repeat was told apart (`repeated title told apart by …`).

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
      "merged_record_ids": [],
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
There is no sharing, no export, no public layer, and no count of reports anywhere a stranger
could read: not on a water body, a spot, or the layer's `meta`. The one count is on a
[section](#sections): `counts.weed_reports` is the caller's own, and `0` for anyone else. Another user's report answers `404`, exactly as an
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
  outline, the piers, breakwaters and harbours from the OpenStreetMap structures near its shore,
  and drop-offs and humps from the LIO Bathymetry depth contours of the ~3,500 surveyed
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
- **Not before ten species.** The hook only replaces the card's sentence about the water once
  the caller has confirmed at least `MIN_SPECIES_FOR_HOOK` (10) different species; before that
  nearly every recorded fish is new and the hook would read the same on every card. The
  `card.new_to_you` block and the `new_to_me` filter are not gated.
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
Overpass would serve in time, so OpenStreetMap data covers part of the 61 curated river sections: most
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
  cached OHN Waterbody outlines, the OHN streams and the OSM shore structures (piers,
  breakwaters, harbours) the lakes need — the first run fetches and caches each one, later
  runs are offline. `make build-anchors` and `make build-stretches`
  run it at the end, and print what was dropped, held back at tier 3, or given a
  generic title because the source had no name.
- `make compute-untapped` writes the score cache quiet water is built from; run it before
  `make build-spots`, and again when the ingested observations change.
- `uv run python scripts/export_spots_fixture.py [db [records-db]]` recaptures the fixture from a
  database. The optional second database lends its iNaturalist and GBIF records to a throwaway copy
  so the "recorded more often here" card can be captured; without it that entry is skipped.
- `uv run python scripts/export_sections_fixture.py path/to/fishing.db` recaptures the sections fixture
  from a copy of a built database. Sections need no build step of their own: they are read off the
  river sections, water bodies, spots and shoreline features on every call.
- `uv run python scripts/export_weed_reports_fixture.py` recaptures the weed-report fixture. It builds a
  throwaway database from the water bodies in the spots fixture and never touches the real one.
- Water bodies are rebuilt with the spots, in the same transaction, from the curated river
  sections and the lake names on river mouths; each lake's outline comes from the OHN Waterbody
  layer, matched by position and never by name alone, and the piers, breakwaters and harbours
  on its shore come from OpenStreetMap (ODbL), kept when they sit on the shore or within 500 m
  of it. The build prints how many spots were linked to a water body and how many were not,
  how many lakes got an outline, how many shore structures were fetched, and how many
  shoreline features were derived and dropped.
- User-facing words for the map live in `src/services/spot_labels.py`. The card's words
  are written by `render_spot()` in `src/services/context/render.py`, and the "why fish
  gather here" text is in `src/services/context/translate.py`. Nothing else writes any.

## `GET /map/layers/getting-there`

Returns GeoJSON point features (FeatureCollection) for parking, trailheads and boat launches. Parameters: `bbox` (optional, `min_lon,min_lat,max_lon,max_lat`) and `jurisdiction` (optional, ISO 3166-2). The bbox is applied to the access points themselves, and a point shared by several spots appears once. Labels come from the labels endpoint's `layers.getting_there` and `access_labels`. Properties: `kind`, `name`, `attributes`.

## `GET /map/layers/bridges`

Returns GeoJSON point features (FeatureCollection) for road crossings. Bridges are off by default in the UI. Parameters: `bbox` (optional) and `jurisdiction` (optional), filtered and de-duplicated as for getting-there. Labels come from `layers.bridges` and `access_labels`. Properties: `kind`, `name`, `attributes`.
