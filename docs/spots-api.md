# Spots API

The explore map's backend, for whoever builds the UI. A **spot** is one reason to go
to one place on the water: where a river runs out into a lake, where a creek joins a
river, the water just below a dam, an official fishing spot. The map shows spots, not
river lines; the river is context ("more spots on this river").

Everything here is read-only, GET, and needs the same `Authorization: Bearer <token>`
as the other `/map/*` endpoints. The older `/map/stretches` and
`/map/stretches/{id}/anchors` endpoints still work and are unchanged.

You can build the whole UI without a running server: **`tests/fixtures/spots_api_responses.json`**
holds real responses from every endpoint below (the examples in this file are copied
from it). Each entry is `{request, note, status, response}`.

## The flow

1. `GET /map/spots/labels` once. Every word the UI shows comes from it. Do not hardcode
   chip names, one-liners or the "why fish gather" paragraphs.
2. `GET /map/spots?bbox=…&kind=…&tier=…` whenever the map moves or a chip changes.
3. `GET /map/spots/{spot_id}` when a spot is tapped.

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
  No spots of that kind exist yet (see "Not here yet").
- Parking, trailheads and boat launches are details of a spot (`getting_there`), not map
  pins of their own. Road crossings (`bridges`) are a layer that is **off by default**
  (`layers.bridges.default_on`).
- Spot output is personal. Do not add export, sharing, or public counts.

## `GET /map/spots/labels`

All user-facing strings, in display order (Quiet water last).

- `kinds[]` — `kind` (the id used everywhere else), `chip` (filter label), `one_liner`
  (what it is, in beginner words), `distinct` (draw apart from the others), and
  `why_fish_gather` = `{text, label, provenance}`: exactly two sentences, `label` is the
  caption to show with it, `provenance` is `"inference"`.
- `river_mouth_subline` — the line under the River mouths chip.
- `layers` — the Getting there and Bridges layers: `label`, `kinds`, `default_on`, `note`.
- `access_labels` — display names for `getting_there[].kind` and `bridges[].kind`.

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
| `kind` | One or more of `river_mouth`, `creek_junction`, `below_dam`, `public_fishing`, `quiet_water`, comma-separated. Omit for all. |
| `tier` | The highest tier to include, **cumulative**: `1` headline spots only, `2` adds the normal ones (**default**), `3` everything, including junctions of small unnamed creeks that are hidden by default. A sensible mapping is tier 1 at province zoom, 2 at regional zoom, 3 at street zoom. |
| `jurisdiction` | ISO 3166-2 code such as `CA-ON`. Omit for all. |

`meta.kind_counts` counts the spots in view for **every** kind, ignoring the `kind`
filter, so the chips can show how many each would add. `meta.count` is how many
features this response holds. Features come headline-first, then by kind, then by id —
never by any measure of how popular a place is.

Each feature's `properties`: `spot_id`, `kind`, `title`, `subtitle`, `tier`,
`jurisdiction`, `water_id`, `water_name` (the river section it sits on) and `signals`.

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
| `water_id`, `water_name` | The river section the spot is on. A junction that two sections both list is attached to one; the other is in `structure_attrs.also_on_water_ids`. |
| `structure_type` | Internal kind of structure: `lake_mouth`, `confluence`, `dam`, `weir`, `fishing_spot`. For code and the bot, not for display. |
| `structure_attrs` | Facts about this structure. Junctions: `joining_upstream_km`, `stretch_upstream_km`. Dams: `dam_kind`, `tailwater_verified` (when `false` the title says "At the …" because the water below could not be verified), `reach_m`. River mouths: `lake` (null until the stretch's curation entry names one). |
| `signals` | Separate numbers, never combined into a score: `named`, `size_class` (`small`/`medium`/`large`, junctions), `nearest_parking_m`, `nearest_launch_m`, `nearest_bridge_m`. |
| `getting_there[]` | Nearest parking, trailheads and boat launches within 1.5 km, at most two of each kind: `kind`, `name`, `lat`, `lng`, `distance_m` (straight line), `source_ref`, `attributes` (OSM tags such as `access`, `fee`, `surface`). Volunteer-mapped; say so. |
| `bridges[]` | Nearest public road crossings within 1 km, same shape. A way to reach small water, not a place to fish. |
| `sources[]` | Where the facts came from: `kind` (`ohn_topology`, `osm`, `curation`), `ref`, the `anchor_ids` merged into the spot, `built_at`. Behind a "Sources" link, not on the card face. |
| `more_on_this_water[]` | Other spots on the same river section: "More spots on this river". |

```json
{
  "spot_id": "below_dam:n8038314_4319587",
  "kind": "below_dam",
  "jurisdiction": "CA-ON",
  "lat": 43.195868,
  "lng": -80.38314,
  "water_id": "grand-cambridge-paris",
  "water_name": "Cambridge to Paris",
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
      "built_at": "2026-10-11T17:21:53+00:00"
    },
    {
      "kind": "osm",
      "ref": "way/248706708",
      "anchor_ids": [
        "grand-cambridge-paris:dam:osm-way-248706708"
      ],
      "built_at": "2026-10-11T17:21:53+00:00"
    }
  ],
  "built_at": "2026-10-11T17:21:53+00:00",
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
  "title": "Where the Credit River meets Lake Ontario",
  "subtitle": "Credit River · Lower Credit — Streetsville to Port Credit",
  "tier": 1,
  "structure_type": "lake_mouth",
  "structure_attrs": {
    "lake": "Lake Ontario",
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
      "built_at": "2026-10-11T17:21:53+00:00"
    }
  ],
  "built_at": "2026-10-11T17:21:53+00:00",
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

## What is not here yet

- **Species and "what you might find"**, the hook line, "new to you" and the card text
  itself are later phases. This API serves the spot, its structure and its access; the
  card renderer will produce the text.
- **Quiet water** spots (a ranking of reaches, not a located structure). The kind and its
  chip exist in `labels` so the filter row can be designed now; no spot has that kind.
- **Filters** other than `bbox`, `kind`, `tier` and `jurisdiction`.
- **Layer endpoints** for Getting there and Bridges as map pins. The nearest ones are
  already on each spot.

## About the example data

The fixture was captured from a build that read only the caches (Overpass was not
reachable), so OpenStreetMap data covered 7 of the 61 stretches: most spots show an
empty `getting_there`, and most river mouths have a null `lake` until the curation file
names it. A full build fills both in. Shapes do not change.

## Building and refreshing

- `make build-spots` rebuilds spots from the stored anchors, no network. `make build-anchors`
  and `make build-stretches` run it at the end, and print what was dropped, held back at
  tier 3, or given a generic title because the source had no name.
- `uv run python scripts/export_spots_fixture.py [db]` recaptures the fixture from a database.
- User-facing words live in `src/services/spot_labels.py` and nowhere else.
