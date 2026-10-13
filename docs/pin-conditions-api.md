# Pin conditions API

The conditions panel on a pin, for whoever builds the Personal-map UI. It answers one
question: what is true at this pin's coordinates right now, and how do we know?

You can build the panel without a running server: **`tests/fixtures/pin_conditions_response.json`**
is a real response, recorded from the live sources (Open-Meteo, the Water Survey of Canada
gauge network, and Ontario's PWQMN water samples) for a pin on Bronte Creek in Oakville.

## `GET /pins/{pin_id}/conditions`

Needs `Authorization: Bearer <token>`. The pin must be the caller's own: another user's pin
answers `404 {"detail": "No such pin"}`, byte-identical to a pin that does not exist, and
makes no outside call. Returns a JSON array with one row per fact, always in the same order,
always all twelve rows. A fact we cannot give is still a row: it has an `empty_reason` and
no `value`.

| key | meaning |
|---|---|
| `field` | stable key, e.g. `flow_cms` |
| `label`, `group` | display name and the group it sits in (`weather`, `water_flow`, `water_temperature`, `water_quality`) |
| `value` | a number, or a word for the trend rows; `null` when there is none |
| `unit` | `"°C"`, `"m³/s"`, … or `null` for words and pH |
| `meaning` | a plain-language note when there is one, else `null` |
| `source` | where it came from: `Open-Meteo`, `WSC station 02HB027`, `PWQMN` |
| `station` | the gauge or sampling station, by name. It can be on a different river from the pin |
| `distance_km` | how far that station is from the pin |
| `observed_at` | when it was measured (ISO-8601 UTC, or a bare date when that is all the source gives) |
| `age_s`, `age_label` | how old it is now, in seconds and in words ("12 min ago") |
| `stale` | older than the limit for its source; see below |
| `empty_reason` | why there is no value; one of the keys in `empty_reason_labels` |

Exactly one of `value` and `empty_reason` is set.

## Rules the UI should keep

- **A stale value is shown, marked, and not counted as current.** `stale: true` means the
  reading is older than its source's limit. It still has its `value`; show it with the
  `stale_label` from `/pins/labels` and its age. Do not hide it and do not grey it into
  "no data". The limits (`stale_limits_s` in `/pins/labels`) are weather 2 h, gauge 6 h,
  water quality 30 days. Phase 3's match will skip stale rows.
- **Show the source and the age on every value.** `source`, `station`, `distance_km` and
  `age_label`. A river gauge can be a few kilometres away and on another creek; the station
  name and distance are how the reader judges that.
- **Water temperature has no live source.** Anywhere. The row holds the nearest *dated*
  sample from PWQMN, almost always months or years old and therefore `stale: true`, or
  `empty_reason: "no_live_source"` when no sample is on record nearby. Do not present it
  as a current reading.
- **Water quality is one dated sample at the nearest station**, not an average. Its
  `observed_at` is the day it was taken.
- **Flow is a trend, never "normal".** `flow_trend` and `level_trend` are `rising`,
  `falling` or `steady` over the last three hours. There is no seasonal baseline behind
  this feed, so nothing here may say normal, high or low.
- **Every word comes from `GET /pins/labels`:** `condition_fields` (order, label, unit,
  group), `condition_group_labels`, `empty_reason_labels`, `trend_labels`, `stale_label`,
  `stale_note`, `stale_limits_s`. Do not hardcode them. An `empty_reason` with no label is
  a bug in the API; report it, do not invent wording.
- The panel is a set of facts about the moment. It is not a prediction and not a score:
  a stale or missing row says nothing about whether fish are there.

## Empty reasons you will see here

| `empty_reason` | when |
|---|---|
| `live_lookup_failed` | Open-Meteo or WSC could not be reached just now. Transient: offer a retry |
| `no_records_in_radius` | no active gauge within 50 km, or no water sample within 25 km |
| `field_not_populated_by_source` | the gauge reports a level but no flow, or no trend yet |
| `no_live_source` | water temperature with no dated sample on record nearby |
| `source_does_not_cover_area` | the PWQMN table is absent from this database |
| `recorded_but_not_decision_relevant` | not used on this route: the panel keeps a mid-range pH |

## Example

Three of the twelve rows, as recorded (the file has all twelve):

```json
[
  {
    "field": "air_temp_c",
    "label": "Air temperature",
    "group": "weather",
    "value": 23.7,
    "unit": "°C",
    "meaning": null,
    "source": "Open-Meteo",
    "station": null,
    "distance_km": null,
    "observed_at": "2026-10-13T16:00:00+00:00",
    "age_s": 318,
    "age_label": "5 min ago",
    "stale": false,
    "empty_reason": null
  },
  {
    "field": "flow_cms",
    "label": "River flow",
    "group": "water_flow",
    "value": 0.105,
    "unit": "m³/s",
    "meaning": null,
    "source": "WSC station 02HB027",
    "station": "FOURTEEN MILE CREEK AT OAKVILLE",
    "distance_km": 3.0,
    "observed_at": "2026-10-13T15:05:00+00:00",
    "age_s": 3618,
    "age_label": "60 min ago",
    "stale": false,
    "empty_reason": null
  },
  {
    "field": "water_temp_c",
    "label": "Water temperature",
    "group": "water_temperature",
    "value": 4.8,
    "unit": "°C",
    "meaning": "a single dated sample at the nearest monitoring station, not a live reading",
    "source": "PWQMN",
    "station": "Fourteen Mile Creek",
    "distance_km": 1.4,
    "observed_at": "2020-11-17",
    "age_s": 186336318,
    "age_label": "2157 days ago",
    "stale": true,
    "empty_reason": null
  }
]
```

## Where it comes from

`describe(..., caller="pin_panel")` in `src/services/context/`: the `pin_panel` bundle is
the conditions slice and nothing else. Stale limits are `WEATHER_STALE_S`, `GAUGE_STALE_S`
and `WATER_QUALITY_STALE_S` in `src/models/freshness.py`; labels and field order are in
`src/services/pins/labels.py`.
