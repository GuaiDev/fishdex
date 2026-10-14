# Angler profile API

How FishDex personalises its presentation as someone logs trips, for whoever builds the
map, dex, log form and pin UI. **Groundwork:** every weight, threshold and prominence is a
named constant (in `src/services/context/profile.py` and `src/services/presentation.py`) and
none of them is final.

You can build against it without a running server: **`tests/fixtures/profile_api_responses.json`**
holds real responses for user 1, recorded after replaying their logged trips into a dev copy
of the database with GBIF and iNaturalist species counts. (`richness_basis` is emptied there
so no pulled data is committed.)

## The one rule

**A profile never filters facts.** It changes what is emphasised and how things are worded,
nothing else. Every element is always available. The backend returns structured facts plus
`term_id`s and per-element prominence values; the component decides how loudly to render.
There are no modes, no "you've shifted" moments and no beginner/expert level anywhere.

## What the profile is

Two independent signals, both recomputed once per logged session (the same recompute that
rebuilds the derived user layer):

- **Orientation**: one continuous value, `0` = specialist, `1` = lifelister, plus a
  `confidence`. It moves slowly: a decay-weighted average with a neutral prior, so a few
  trips barely move it. It is not a species count. Catches are compared against what was
  recorded at that place (`local_richness`), and intent counts over outcome: a new species
  recorded at the water you travelled to counts fully, one nothing recorded there counts
  little, and returning to the same water for the same species, or the same species in
  another season, counts toward specialist. Only confirmed catches are read.
- **Size focus** and **micro focus**: two separate soft values (the decayed share of catches
  with a size recorded, and of small-bodied species). They are not part of the spectrum.
- **Literacy per concept**: one state per `term_id` (`unseen`, `introduced`, `familiar`) with
  the evidence behind it: logged near one, saved a pin near one, opened its definition, seen
  it on the map.

Every value is stored as **value + source (`inferred` | `declared`) + weight**. A declared
value is either a starting value that fades as trips are logged (its weight halves every 10
sessions) or a settings override that holds until cleared.

## Routes

All need `Authorization: Bearer <token>`. Every route reads the user from the token, never
from the path or the body.

### `GET /profile`

The full profile: `orientation`, `size_focus`, `micro_focus` (each `{value, confidence,
inferred, declared}`), `concepts` (per term: `state`, `source`, `evidence[]`) and
`session_evidence[]` (what each logged session contributed to the orientation, with reasons).
For settings and for checking what has been inferred. Not needed to render the map or the dex.

### `GET /profile/presentation`

What the UI reads. Every number is a prominence from `0.15` (quiet, still reachable by
tapping in) to `1.0`.

| section | keys |
|---|---|
| `basis` | the profile values it was computed from, including `effective_lean` (orientation pulled toward 0.5 by low confidence) |
| `map.labels.<term_id>` | `state`, `label_mode` (`plain`: plain-language wording; `term_with_definition`: the term, tap for meaning; `term`: the term alone), `prominence` |
| `map.default_layer_density` | the share of layers drawn by default. It grows as concepts become familiar |
| `map.more_detail_toggle` | always `true`: nothing is locked out |
| `map.water_body_mix` | the lake/pond/river balance for the water-body-first view. Fixed, never personalised |
| `dex.*` | lifelist side: `family_completion`, `recorded_near_not_caught`, `lifer_markers`. Specialist side: `seasonal_timeline`, `catches_by_conditions`, `species_depth`. Middle: `journal_timeline`. Side values: `size_records`, `small_species`. One layout; elements fade |
| `log_form.defaults` | `species`, `place`, `photo`, `trip_rating`: always `1.0` |
| `log_form.details.<field>` | `prominence`, `start_expanded`, `available` (always `true`) |
| `log_form.species_order_weights` | how much to weight `your_targets`, `recorded_here_not_caught` and `recently_logged` when ordering the species picker |
| `pins.*` | `target_species_context` and `conditions_context` (specialist side), `uncaught_recorded_cue` (lifelist side) |

The wording itself comes from **`GET /terms`**: per `term_id`, `plain`, `technical`,
`definition` and `mapped_from` (where features of that type come from on the map, or `null`
when there is no map layer for it yet). A `plain` label shows `plain`; a `term` label shows
`technical`.

### `POST /profile/concept-events`

`{"term_id": "confluence", "kind": "definition_opened" | "seen_on_map", "ref": "<optional id>"}`
→ `201 {"id": …}`. Send it when someone opens a definition or a feature is drawn for them.
It is folded in at the next logged session, so wording never changes mid-visit. An unknown
term or kind gets `400`.

### `PUT /profile/declared/{key}` and `DELETE /profile/declared/{key}`

`{"value": 0..1, "weight": ≥0 (default 3), "override": false}`. Keys: `orientation`,
`size_focus`, `micro_focus` and `concept:<term_id>` (value `0` unseen, `0.5` introduced,
`1` familiar). Any other key, including any skill or expertise field, gets `400`.

- A future questionnaire writes `override: false` starting values that fade.
- A settings override writes `override: true`. It holds exactly and applies on the next read.
  `DELETE` hands the value back to behaviour (`404` if there was nothing to clear).
