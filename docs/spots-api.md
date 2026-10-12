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
4. `GET /map/water-bodies?bbox=…&type=…` to name and group the rivers, creeks and lakes the
   spots sit on, `GET /map/water-bodies/{water_body_id}` for "more spots on this river", and
   `GET /map/water-bodies/{water_body_id}/features` for a lake's shoreline (inlets, outlets,
   islands, points).

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
- A water body's `name` can be `null` (no source named it). Show `display_name`, never invent a
  name, and never print "Unnamed …". A lake with `geometry: null` has no outline on file: draw
  a label, not a shape. A lake with a polygon has one.
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
- `water_body_types[]` — for each water body `type`: `chip` (plural, for a filter), `label`
  (singular, for a card) and `unnamed` (the phrase to show when `name` is null).
- `name_sources` — a plain-words line for each `name_source`, for a "source" caption under a name.
- `no_outline_note` — what to say when a lake has no shape on the map.
- `shoreline_features[]` — for each shoreline feature `kind` (`inlet`, `outlet`, `island`,
  `point`, `peninsula`): `label` (singular, for a card), `chip` (plural, for a legend) and
  `one_liner` (what it is, in beginner words).
- `shoreline_derivations` — a plain-words phrase for each `derivation`, for the caption under a
  shoreline feature: `shoreline_outline` (a shape read off the outline itself) or
  `watercourse_endpoint` (a mapped stream that ends at the shore).

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
`jurisdiction`, `water_body_id` (the river, creek or lake it is a child of, see
`/map/water-bodies`), `water_id`, `water_name` (the river section it sits on) and `signals`.

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
enters (`inlet`) or leaves (`outlet`), an island (`island`), and a point or peninsula of land
reaching out (`point`, `peninsula`). These are read off the lake's OHN Waterbody outline and the
OHN streams that end at its shore — a located thing to show, never a claim about fish. A river
has no shoreline, so the honest answer is an **empty collection**, not an error; an unknown id
is `404`.

Each feature's `properties`:

| Field | Meaning |
|---|---|
| `feature_id` | Stable id: `{water_body_id}#{kind}:{n}`. |
| `water_body_id` | The lake the feature belongs to. |
| `kind` | `inlet`, `outlet`, `island`, `point` or `peninsula`. Words in `labels.shoreline_features`. |
| `name` | The mapped stream's name for an inlet or outlet, else `null`. |
| `derivation` | How it was obtained: `watercourse_endpoint` (a mapped OHN stream that ends at the shore) or `shoreline_outline` (a shape read off the outline's own edge). Phrase in `labels.shoreline_derivations`. |
| `provenance` | `record` for an island (a ring inside the source polygon) or a stream junction; `inference` for a point or peninsula (a shape computed from the outline). |
| `note` | A full sentence for the card, already assembled from the vocabulary. Safe to show as-is. |
| `area_m2` | An island's area, else `null`. |
| `geometry` | A `Point` for inlets, outlets, points and peninsulas; a `Polygon` for an island. |

```json
{
  "type": "FeatureCollection",
  "meta": {
    "count": 2,
    "by_kind": {
      "inlet": 1,
      "island": 1
    }
  },
  "features": [
    {
      "type": "Feature",
      "id": "great_lake:lake-ontario#inlet:1",
      "geometry": {
        "type": "Point",
        "coordinates": [
          -79.57731,
          43.54567
        ]
      },
      "properties": {
        "feature_id": "great_lake:lake-ontario#inlet:1",
        "water_body_id": "great_lake:lake-ontario",
        "kind": "inlet",
        "name": "Credit River",
        "derivation": "watercourse_endpoint",
        "provenance": "record",
        "note": "A mapped stream flows in here. Credit River is mapped here. Read from the Ontario government water map.",
        "area_m2": null
      }
    },
    {
      "type": "Feature",
      "id": "great_lake:lake-ontario#island:1",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              -79.2,
              43.6
            ],
            [
              -79.19,
              43.6
            ],
            [
              -79.19,
              43.61
            ],
            [
              -79.2,
              43.61
            ],
            [
              -79.2,
              43.6
            ]
          ]
        ]
      },
      "properties": {
        "feature_id": "great_lake:lake-ontario#island:1",
        "water_body_id": "great_lake:lake-ontario",
        "kind": "island",
        "name": null,
        "derivation": "shoreline_outline",
        "provenance": "record",
        "note": "Land inside the water. Read from the shoreline outline.",
        "area_m2": 590000.0
      }
    }
  ]
}
```

## What is not here yet

- **Species and "what you might find"**, the hook line, "new to you" and the card text
  itself are later phases. This API serves the spot, its structure and its access; the
  card renderer will produce the text.
- **Water body outlines** are built for the curated lakes: a lake's OHN Waterbody polygon is
  matched by position at build time and served as `geometry`, with the source OGF_IDs, name,
  effective date and licence on `attributes` (see `GET /map/water-bodies/{id}/features`). A lake
  with no match stays `geometry: null`.
- **Lake features** — inlets, outlets, islands, points and peninsulas — are built from a lake's
  outline and served at `GET /map/water-bodies/{id}/features`. Deeper structure (drop-offs,
  humps, weed lines) is not built: it needs bathymetry, not an outline.
- **Quiet water** spots (a ranking of reaches, not a located structure). The kind and its
  chip exist in `labels` so the filter row can be designed now; no spot has that kind.
- **Filters** other than `bbox`, `kind`, `tier` and `jurisdiction`.
- **Layer endpoints** for Getting there and Bridges as map pins. The nearest ones are
  already on each spot.

## About the example data

The fixture was captured from a build that read only the caches (Overpass was not
reachable), so OpenStreetMap data covered 7 of the 61 stretches: most spots show an
empty `getting_there`. A full build fills it in. River mouths take their lake from the
curation file, so their lakes are named. Shapes do not change.

## Building and refreshing

- `make build-spots` rebuilds spots from the stored anchors. It reads the
  cached OHN Waterbody outlines and streams the lakes need — the first run fetches
  and caches, later runs are offline. `make build-anchors` and `make build-stretches`
  run it at the end, and print what was dropped, held back at tier 3, or given a
  generic title because the source had no name.
- `uv run python scripts/export_spots_fixture.py [db]` recaptures the fixture from a database.
- Water bodies are rebuilt with the spots, in the same transaction, from the curated river
  sections and the lake names on river mouths; each lake's outline comes from the OHN Waterbody
  layer, matched by position and never by name alone. The build prints how many spots were
  linked to a water body and how many were not, how many lakes got an outline, and how many
  shoreline features were derived and dropped.
- User-facing words live in `src/services/spot_labels.py` and nowhere else.
