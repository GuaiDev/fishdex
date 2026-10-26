# Handoff: FishDex — "My FishDex" Collection Screen

## Overview
"My FishDex" is the collection screen of the FishDex angling app — the payoff surface where a user browses the species they've caught. It is built around **the photograph as the interface**: each species is a full-bleed photo plate with its common name, scientific name, and catch stats. The aesthetic is a **naturalist's field journal** — a warm dusk-on-the-water dark theme, Spectral serif for names and figures, a whisper of paper grain, pigmented-moss green and weathered-brass accents. No neon, no game-Pokédex tropes.

The screen is **adaptive**: its layout is a function of what the user has actually caught, never a mode they choose.
- **Flat view (3a)** — for a narrow angler whose catches span **1–2 families**. All caught species show as one ungrouped list of tall photo plates.
- **Grouped view (3b)** — the *same screen* once a collection spans **3+ families**. Species fold into plain-language family folders with completion framing; untouched families become a lighter "explore" prompt.

The two are not separate screens the user toggles — the app infers which to render from the diversity of the collection.

## About the Design Files
The files in this bundle are **design references created in HTML** — prototypes showing the intended look and behavior, **not production code to copy directly**. The task is to **recreate these designs in the target codebase** (`GuaiDev/fishbot`) using its existing environment, patterns, component library, and data layer. Where this doc describes behavior ("tap a folder to expand"), implement it with the app's own navigation/state conventions. The HTML uses a small custom `<image-slot>` element as a photo placeholder; in the real app these map to the user's actual catch photos from the backend.

## Fidelity
**High-fidelity (hifi).** Colors, typography, spacing, radii, and layout are final and intended to be matched closely. Exact values are in [Design Tokens](#design-tokens). The one thing left open is the **real species reference data** (see [Data Requirements](#data-requirements)) — the prototype hardcodes a Credit River watershed sample.

## Screens / Views

### 1. My FishDex — Flat view (`3a`)
**Purpose:** Let a narrow-focus angler browse every species they've caught as a single scrollable stack of photographic plates, and see how far along the regional "discovery trail" they are.

**Layout:** A 390×844 mobile frame (iPhone-class). Top to bottom:
1. **Status bar** (50px) — standard iOS-style time + signal/battery.
2. **Fixed header block** (`padding: 12px 22px 16px`, 1px bottom divider `#24261d`):
   - Eyebrow "MY FISHDEX" — 11px, uppercase, letter-spacing `.18em`, color `#9fae7a`.
   - Title "Credit River watershed" — Spectral 600, 27px, `#f2ede1`.
   - Round 38px search button, top-right — `#20221a` fill, 1px `#35372c` border, magnifier glyph `#a6a08d`.
   - **Discovery trail:** a big count "9 / 24" (Spectral; "9" at 22px `#f2ede1`, "/ 24" at 15px `#54564a`) with "species in your region" pushed right (12px `#948f7e`). Below it a **progress rail**: 4px track `#26281f`, filled portion `linear-gradient(90deg,#5f6d44,#9fae7a)` to 37.5%, a 16px glowing map-pin node at the fill head (`#9fae7a` with `box-shadow: 0 0 0 4px rgba(159,174,122,.18), 0 0 14px rgba(159,174,122,.5)`), plus two small dormant dots further along. Caption row: "9 discovered" / "15 still on the trail" (11px `#6f6c5b`).
3. **Scrollable plate list** (`flex:1; overflow:auto; padding:16px 22px 30px`):
   - **Caught plates** — one per species. Each: `position:relative; border-radius:18px; height:214px; overflow:hidden; 1px #35372c border; margin-bottom:13px`. Full-bleed photo. A bottom scrim `linear-gradient(transparent 40%, rgba(11,12,8,.55) 68%, rgba(9,10,6,.9) 100%)`. Bottom-left overlay: common name (Spectral 600, 23px, `#f6f1e6`), scientific name (Spectral italic 400, 13px, `rgba(194,204,159,.8)`). Bottom-right stat: big figure (Spectral 600, 20px) + caption (10.5px `rgba(246,241,230,.6)`), e.g. "24 logged", "to 26″".
   - **Personal-Best plate** gets a top-right pill: `rgba(24,18,8,.62)` bg, `blur(4px)`, 1px `#c2a06a` border, a filled star + "PERSONAL BEST" (600 10px uppercase `#e8c98a`). Its scientific name tints brass (`rgba(232,201,138,.85)`) and its stat reads "best · 24 logged" with the PB length as the figure.
   - **New-find plate** gets a top-right pill: `rgba(20,24,12,.6)` bg, 1px `#869663` border, a `❦` glyph + "NEW FIND" (600 10px uppercase `#c2cc9f`).
   - **Divider** "STILL ON THE TRAIL" — 1px lines flanking centered 10.5px uppercase `#6f6c5b` label.
   - **Undiscovered rows** — quiet horizontal cards: `#1a1c15` bg, 1px **dashed** `#303228` border, radius 14px, 14px padding. Left: 52px hatched thumbnail (`repeating-linear-gradient(45deg,#20221a … #1a1c15)`) with a faint outline fish glyph `#4c4e40`. Middle: greyed common name (Spectral 600 16px `#8f8c7b`) + italic scientific name (`#5a5c4d`). Right: "not yet caught" (11px `#5a5c4d`).
4. **Home indicator** — 134×5 rounded bar, `rgba(236,230,216,.3)`.

**Sample content:** Smallmouth Bass (PB, 21″, 24 logged), Common Carp (47 logged, to 28″), Channel Catfish (12, to 26″), Rainbow Darter (New find, 3 logged, to 2.4″). Undiscovered: Muskellunge, Longnose Gar, Brook Trout.

### 2. My FishDex — Grouped view (`3b`)
**Purpose:** Once the collection is diverse (3+ families), organize it into plain-language family folders with per-family completion, so a completionist can see which families they've touched and which are untouched.

**Layout:** Same frame, status bar, and header pattern as 3a. Header differs only in the sub-caption: "species in your region · 5 families", and the discovery trail here omits the "discovered / on the trail" caption row.

Scroll body (`flex:1; overflow:auto; padding:16px 22px 30px`):
1. **One expanded folder — "Bass & Sunfish":**
   - Folder header row: title (Spectral 600 18px `#f2ede1`) + subtitle "Centrarchidae · 3 of 8 caught" (Spectral italic 12px `#948f7e`), with an up-chevron `#9fae7a` at right (indicates expanded/collapsible).
   - **2-column species grid** (`gap:11px`, `margin-bottom:26px`). Caught cells: 150px tall, radius 14px, full-bleed photo, bottom scrim `linear-gradient(transparent 44%, rgba(9,10,6,.9))`, `box-shadow:0 10px 24px rgba(0,0,0,.35)`. Bottom overlay: name (Spectral 600 15px `#fff`) + stat line (10.5px `rgba(246,241,230,.62)`, e.g. "×24 · 21″ PB"). PB cell adds a small top-left "Best" pill (star + 8px uppercase `#e8c98a`). Locked cell: hatched bg, faint fish glyph top-left, greyed name + "not yet caught" bottom-left.
   - Sample cells: Smallmouth Bass (Best, ×24, 21″ PB), Rock Bass (×12, 9″), Bluegill (×7, 8″), Pumpkinseed (locked).
2. **Collapsed folders** (`display:flex; flex-direction:column; gap:11px; margin-bottom:26px`): each a 96px-tall banner card, radius 16px, full-bleed representative photo with a **left-to-right** scrim `linear-gradient(90deg, rgba(9,10,6,.86) 42%, rgba(9,10,6,.15))`. Left content: family display name (Spectral 600 17px `#f6f1e6`) — with a `❦` marker if the family contains a new find — italic Latin family name (`rgba(236,230,216,.55)`), and a mini progress row: 72×3 track `rgba(236,230,216,.16)` with `#9fae7a` fill + "2 / 5" count (600 11px `#c2cc9f`). Right: forward chevron `rgba(236,230,216,.5)`.
   - Sample folders: "Perch, Walleye & Darters" (Percidae, 2/5, has ❦ new find), "Catfish & Bullheads" (Ictaluridae, 1/3), "Redhorse & Suckers" (Catostomidae, 3/6), "Carp" (Cyprinus carpio, 1/1). **Note the Carp exception below.**
3. **"NEW FAMILIES TO EXPLORE"** divider, then a **horizontal-scroll row** of prompt cards (`overflow-x:auto`): 150px wide, `#1a1c15` bg, radius 14px, a faint outline fish glyph, family display name (Spectral 600 14px `#a6a08d`), italic "Family · 0 / N" (`#5a5c4d`). Sample: Pike & Musky (Esocidae 0/2), Trout & Salmon (Salmonidae 0/4), Bowfin (Amiidae 0/1).

**Interaction:** Tapping a collapsed folder expands it into the grid form (like Bass & Sunfish) and collapses the previously open one — an accordion within the scroll. Tapping an "explore" card can open that family's undiscovered species.

## Interactions & Behavior
- **Adaptive layout selection:** compute the number of distinct families represented in the user's caught set. `< 3` families → render **flat view (3a)**. `>= 3` families → render **grouped view (3b)**. This threshold is a first guess and should be tuned against real usage.
- **Plate tap** (both views): opens that species' detail (not designed in this bundle — a natural next screen).
- **Folder accordion** (3b): one folder expanded at a time; expanding collapses the previously open one. The expanded folder renders the 2-column species grid; collapsed folders render the 96px banner.
- **Search button:** opens species/family search (not designed here).
- **Ordering:** within the flat view, plates are ordered by recency or catch count (product decision — prototype shows a curated order with the PB species first). New finds surface a "New find" pill until acknowledged.
- **Scrolling:** the header/trail block is fixed; only the plate/folder list scrolls.
- No animated transitions are specified for this screen beyond standard expand/collapse; keep motion quiet and physical, in keeping with the field-journal tone.

## State Management
State needed to render either view:
- **caughtSpecies[]** — each: `{ id, commonName, scientificName, family, familyCommonName, count, maxLengthInches, isPersonalBest, personalBestLength, isNewFind, representativePhotoUrl }`.
- **regionSpeciesPool[]** — full set of species available in the user's region (for "9 / 24" and for listing undiscovered species). Each carries `family`.
- **Derived:** `distinctFamilyCount` (drives 3a-vs-3b), per-family `{ caughtCount, totalCount }`, region `discoveredCount / totalCount`.
- **UI state:** `expandedFamilyId` (3b accordion), search query.
- **Data fetching:** the collection and the region pool come from the backend; photos are the user's own catch images (or a species fallback image).

## Data Requirements
The single most important backend dependency: **the species reference data must carry a `family` field** (both the scientific family name, e.g. `Centrarchidae`, and a plain-language display name, e.g. "Bass & Sunfish"). The grouped view is impossible without it. Confirm this exists in the `fishbot` species model; if not, it needs to be added and backfilled.

Also required per species: scientific (binomial) name, and typical/known regional membership so the region pool ("24 species in your region · 5 families") can be computed for the user's location.

**Family display-name mapping** (scientific → plain language) used in the design — extend to cover every family in the multi-province backend pool, not just this starter set:
- Centrarchidae → "Bass & Sunfish"
- Percidae → "Perch, Walleye & Darters" (darters are taxonomically honest here — they are Percidae)
- Ictaluridae → "Catfish & Bullheads"
- Catostomidae → "Redhorse & Suckers"
- Esocidae → "Pike & Musky"
- Salmonidae → "Trout & Salmon"
- Amiidae → "Bowfin"

**Documented Cyprinidae exception:** Common Carp is shown as its own folder labeled "Carp" (subtitle uses the species binomial "Cyprinus carpio" rather than the family) even though it belongs to Cyprinidae. This is intentional — most users think of carp as its own thing and the rest of Cyprinidae (shiners, chubs, dace) reads as unrelated bait-fish. Preserve this special-case in the family-grouping logic.

## Design Tokens

### Color
| Role | Hex |
|---|---|
| App background (radial) | `#24261d` → `#1b1d16` → `#141510` |
| Canvas backdrop | `#131410` |
| Card / panel fill | `#242720`, quiet `#1a1c15`, `#20221a` |
| Hairline / border | `#35372c`, `#383b2f`, divider `#24261d` / `#2a2c22` |
| Dashed (undiscovered) border | `#303228` / `#33352a` |
| Primary text | `#f2ede1` / `#f6f1e6` / `#ece6d8` |
| Muted text | `#948f7e` |
| Dim text | `#6f6c5b` / `#54564a` |
| Greyed (locked) text | `#8f8c7b` / `#7c7a6a` / `#5a5c4d` |
| Moss green (primary accent) | `#869663` (solid), `#9fae7a` (light), `#c2cc9f` (lightest) |
| Moss green (dark) | `#5f6d44` |
| Brass (personal best) | `#c2a06a`, `#e8c98a`, star fill `#d9b877` |
| On-accent text | `#191b12` |
| Hatch fill (locked) | `repeating-linear-gradient(45deg,#20221a,#20221a 6-7px,#1a1c15/#191b14 …)` |

### Typography
- **Display / names / figures:** `'Spectral', Georgia, serif` — Google Fonts, weights 400/500/600/700 + italic 400. Used at 27px (screen title), 23px (plate name), 18/17/16/15px (folder & card names), and for all numeric figures.
- **UI / body / captions:** `system-ui, -apple-system, sans-serif`.
- **Eyebrows / section labels:** system-ui, 10.5–11px, uppercase, letter-spacing `.14em`–`.18em`.

### Radii
- Phone frame `46px`; large plate `18px`; grid card `14px`; folder banner `16px`; explore card `14px`; small thumbnail `10px`; pills `14–20px`; buttons `12–14px`.

### Shadow
- Frame: `0 30px 70px rgba(0,0,0,.5)` + bezel rings.
- Grid card: `0 10px 24px rgba(0,0,0,.35)`; folder banner `0 10px 26px rgba(0,0,0,.35)`.
- PB glow node: `0 0 0 4px rgba(159,174,122,.18), 0 0 14px rgba(159,174,122,.5)`.

### Scrims
- Plate (bottom): `linear-gradient(transparent 40%, rgba(11,12,8,.55) 68%, rgba(9,10,6,.9) 100%)`.
- Grid card (bottom): `linear-gradient(transparent 44%, rgba(9,10,6,.9))`.
- Folder banner (left): `linear-gradient(90deg, rgba(9,10,6,.86) 42%, rgba(9,10,6,.15))`.

### Texture
- A subtle SVG fractal-noise grain overlay at `opacity:.05` across the frame. Optional in production but part of the intended feel.

## Assets
- **Fonts:** Spectral (Google Fonts). System UI stack otherwise.
- **Photos:** the `<image-slot>` placeholders map to real user catch photos (or a per-species fallback image). None are shipped in this bundle.
- **Icons:** all inline SVG (search, map-pin, star, chevrons, fish outline, `❦` floral-heart glyph as the "new find" mark). Reproduce with the codebase's icon system; the `❦` mark is a stylistic signature worth keeping.
- No third-party image assets are used.

## Files
- `Log a Catch v2.dc.html` — the full prototype. Both views live on one canvas: option **`3a`** (flat) and **`3b`** (grouped). (This file also contains the earlier session-logging flow — turn "2", options 1a/1b/1c — which is upstream context, not part of this collection-screen handoff.)
- `Map v2.dc.html` — the updated map canvas: option **`4a`** (My water), **`4b`** (Find water), and **`4c`** (reach detail with the map still visible).
- `screens/3a-my-fishdex-flat.png` — screenshot of the flat view.
- `screens/3b-my-fishdex-grouped.png` — screenshot of the grouped view.

To view the prototype live, open the `.dc.html` file in the design tool; the `3a`/`3b` badges on each frame are anchor links.
