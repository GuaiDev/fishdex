---
name: FishBot
description: Naturalist's field journal — Spectral serif + moss/brass palette, now the primary system across Chat, Log, Map, Trips, and FishDex. Section 2-6's Bark/Moss palette is legacy, retained only for Login pending migration.
colors:
  base-bark: "#17140F"
  surface-loam: "#211C15"
  border-twig: "#392F22"
  text-bone: "#EAE3D3"
  text-ash: "#9A9280"
  moss: "#7C8F69"
  moss-fill: "#3D4A32"
  moss-fill-dim: "#2A331F"
  sage-tint-bg: "#262A1C"
  wet-stone: "#6B7268"
  gold: "#B8923D"
  gold-tint-bg: "#2E260F"
  rust: "#C1673D"
  rust-bg: "#2A1810"
  rust-border: "#4A2C1A"
  heat-high: "#e74c3c"
  heat-med: "#e67e22"
  heat-mid: "#f1c40f"
  heat-low: "#2ecc71"
  heat-min: "#3498db"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "normal"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "0.02em"
  mono:
    fontFamily: "monospace"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "2px"
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "12px"
  xxl: "14px"
  sheet: "16px"
  pill: "20px"
  full: "50%"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.moss-fill}"
    textColor: "{colors.text-bone}"
    rounded: "{rounded.pill}"
    padding: "14px"
  button-primary-disabled:
    backgroundColor: "{colors.border-twig}"
    textColor: "{colors.text-ash}"
    rounded: "{rounded.pill}"
    padding: "14px"
  button-primary-loading:
    backgroundColor: "{colors.moss-fill-dim}"
    textColor: "{colors.text-bone}"
    rounded: "{rounded.pill}"
    padding: "14px"
  chip-tag:
    backgroundColor: "{colors.sage-tint-bg}"
    textColor: "{colors.moss}"
    rounded: "{rounded.sm}"
    padding: "3px 8px"
  input-field:
    backgroundColor: "{colors.surface-loam}"
    textColor: "{colors.text-bone}"
    rounded: "{rounded.md}"
    padding: "12px 14px"
  card-surface:
    backgroundColor: "{colors.surface-loam}"
    textColor: "{colors.text-bone}"
    rounded: "{rounded.lg}"
    padding: "12px 14px"
  catch-photo-card:
    backgroundColor: "{colors.surface-loam}"
    textColor: "{colors.text-bone}"
    rounded: "{rounded.xl}"
  personal-best-badge:
    backgroundColor: "{colors.gold-tint-bg}"
    textColor: "{colors.gold}"
    rounded: "{rounded.pill}"
    padding: "4px 12px"
fishdexColors:
  bg-grad-1: "#24261d"
  bg-grad-2: "#1b1d16"
  bg-grad-3: "#141510"
  canvas: "#131410"
  card-fill: "#242720"
  card-fill-quiet: "#1a1c15"
  card-fill-dark: "#20221a"
  hairline: "#35372c"
  hairline-2: "#383b2f"
  divider: "#24261d"
  dashed-border: "#303228"
  text-primary: "#f2ede1"
  text-primary-2: "#f6f1e6"
  text-primary-3: "#ece6d8"
  text-muted: "#948f7e"
  text-dim: "#6f6c5b"
  text-dim-2: "#54564a"
  text-locked: "#8f8c7b"
  text-locked-2: "#7c7a6a"
  text-locked-3: "#5a5c4d"
  moss: "#869663"
  moss-light: "#9fae7a"
  moss-lightest: "#c2cc9f"
  moss-dark: "#5f6d44"
  brass: "#c2a06a"
  brass-light: "#e8c98a"
  brass-star: "#d9b877"
  on-accent: "#191b12"
fishdexTypography:
  display:
    fontFamily: "'Spectral', Georgia, serif"
    weights: [400, 500, 600, 700]
    italic: true
  ui:
    fontFamily: "system-ui, -apple-system, sans-serif"
fishdexRounded:
  phone-frame: "46px"
  plate: "18px"
  grid-card: "14px"
  folder-banner: "16px"
  explore-card: "14px"
  small-thumbnail: "10px"
---

# Design System: FishBot

## Status (read this first)

**The naturalist/moss-brass palette in Section 7 is now the primary, app-wide
system.** As of this revision it's live on Chat, Log (LogTrip), Map, Trips,
and FishDex — Spectral serif for names/headers/figures, system-ui for
body/UI text, the `--fx-*` custom properties in `web/src/fishdex-tokens.css`,
the warm dark radial-gradient background, FishDex's card radii throughout,
and a shared `.fx-grain` paper-noise overlay (`GrainOverlay.jsx`).

**Sections 2–6 below describe the prior Bark/Moss system** (`web/src/tokens.css`,
`--color-*`). It is not deleted or wrong — it's now legacy, still the live,
correct system for the one screen not yet migrated: **Login**. Read Section 7
first for anything touching Chat/Log/Map/Trips/FishDex; read Sections 2–6 for
anything touching Login. See "App-Wide Migration" under Section 7 for exactly
what changed, what stayed, and what's still open.

One exception spans both systems: **Rust remains the single alarm/error
color everywhere**, including on the migrated screens. Section 7's token set
(`fishdex-tokens.css`) never defined its own alarm color, and inventing a
second one purely for the new screens would violate The One Alarm Rule
below — so error states on Chat/Log/Map/Trips still use `--color-rust` /
`--color-rust-bg` / `--color-rust-border` from the legacy tokens, which stay
loaded app-wide for exactly this reason (Trips' anomaly-flag plate badge
included). `InstrumentDial.jsx` is shared by Map and Trips — it already
rendered with `--fx-*` tokens before Trips' own migration, so Trips' dial
was already reskinned as a side effect; Trips' migration now also floats it
directly over the trip photo per the Overlay Chrome Rule (translucent Bark
scrim + blur), scaled down to fit a plate corner.

## 1. Overview

**Creative North Star: "The Naturalist's Field Journal"**

The North Star holds, but this revision changes what it means structurally. The reference is MOSS, a nature-walk discovery app: full-bleed real photography IS the interface — the UI sits as a thin, confident layer on top of the photo, not the other way around. The previous description of this system as "dark cards with a small photo accent inside" is retired; that's a dashboard pattern, and this is a journal pattern. A journal doesn't put your photo in a little box with a caption underneath — the photo takes the page, and you write directly on it.

Concretely: species and catch photography goes near-full-bleed, with the species name, personal-best flag, and count sitting directly on the image over a gradient scrim — not beside it or below it in a separate text zone. Completion states (finishing a session) get a real moment: a checkmark, a stat row, a filmstrip of what was actually caught, and a clear "Added to your FishDex" flag when a session produced a new species. Collection progress ("3 of 5 discoveries") gets a trail — segmented and storied — instead of a flat thin bar. Instrument data (temperature, pressure, water clarity) gets a circular dial/gauge treatment instead of small text chips, borrowing the confident, scientific-instrument feel of a compass or hiking altimeter. Chrome — nav, headers, buttons — stays thin, minimal, and out of the photo's way in every case; the photo supplies the adventure, the chrome supplies the function.

Two things this explicitly is not: it is not the illustrated/flat-icon fish style (reviewed and rejected — real photography only, never illustration, for any species or catch); and it is not a literal hiking/outdoor-brand identity (no boot icons, no leaf icons, no expedition-brand typography or taglines like "go further"). FishBot stays precise and quiet, not expedition-branded — adventurous *and* scientific, never gamified or cutesy. Think "curious and a little reverent about nature," not "conquer the trail." The prior anti-references still hold too: no pure black background, no Inter, no purple/generic SaaS gradients, no cards nested in cards.

**Key Characteristics:**
- Full-bleed real photography IS the interface on species/catch/session screens — not a thumbnail inside a card
- Species name, PB flag, and count sit directly on the photo via a bottom gradient scrim, never in a separate text zone beside or below it
- Session completion is a real moment: checkmark, stat row, catch-photo filmstrip, "Added to your FishDex" flags
- Collection/discovery progress uses a segmented trail motif, not a flat thin bar (the map's continuous score meter is the one exception — see Components)
- Instrument data (temp, pressure, clarity) reads as a circular dial/gauge, not a text chip
- Chrome stays thin, minimal, and translucent when it floats over photography; solid only on non-photo screens (chat, forms, lists)
- Real photography only — never illustrated/flat-icon fish art, never literal hiking-brand tropes

## 2. Colors

Palette is unchanged from the prior revision — warm neutrals, a desaturated moss/sage/wet-stone accent family, gold held in reserve, one earthy alarm tone. What changes in this revision is where and how these colors get used: more of the screen is now photography, so flat color fields (card backgrounds, chip fills) cover proportionally less of any given view, and gradient scrims (Bark fading to transparent) do more of the work that solid Loam fills used to do.

### Primary
- **Moss** (`#7C8F69`): Text, icons, links, active indicators, and dial-arc fills for "favorable" instrument readings.
- **Moss Fill** (`#3D4A32`): Filled surfaces (primary button backgrounds) holding Bone text.
- **Moss Fill Dim** (`#2A331F`): The "processing" state of a filled button.
- **Sage Tint** (`#262A1C`): Background behind Moss text on chips/tags.

### Secondary
- **Wet Stone** (`#6B7268`): Secondary/inactive elements, and dial-arc fills for "neutral" instrument readings — present but not the point.

### Tertiary (rare)
- **Gold** (`#B8923D`): Personal-best moments and new species added to the FishDex — the one warm, precious color in the system. See The Earned Gold Rule.
- **Gold Tint** (`#2E260F`): Background behind a gold callout or badge.

### Neutral
- **Bark** (`#17140F`): App background — warm, not pure black.
- **Loam** (`#211C15`): Raised surfaces on non-photo screens (chat, forms, lists). Not used as a photo-card frame anymore in the near-full-bleed pattern — see Components.
- **Twig** (`#392F22`): Hairline borders, dividers, disabled fills, progress tracks.
- **Bone** (`#EAE3D3`): Primary text — on dark surfaces and directly on photo scrims alike.
- **Ash** (`#9A9280`): Secondary/meta text, and dial-arc fills for "unfavorable" instrument readings alongside Rust for genuinely poor conditions.

### Semantic (single alarm)
- **Rust** (`#C1673D` text / `#2A1810` background / `#4A2C1A` border): The one alarm color — errors, warnings, and poor-condition instrument readings alike.

### Data (heat scale — now unused by the map)
- **Heat High → Heat Min**: The explore map no longer shows a segment score (it draws stretches and anchors in Brass/Moss/Rust/Bone from `tokens.js`), so nothing in `web/src` reads the heat scale; the `tokens.css` variables and frontmatter entries remain until removed deliberately.

### Named Rules
**The Photograph IS the Interface Rule.** On any species, catch, or session screen, the photo is not an element on the page — it is the page. UI sits on top of it as a thin translucent layer (see The Overlay Chrome Rule), never as a frame around a smaller photo inside a card.

**The Earned Gold Rule.** Gold marks an actual event: a personal-best catch, a new species added to the FishDex, a milestone. Never a generic highlight or default active state.

**The One Alarm Rule.** Rust is the only alarm color, for UI errors and for a poor instrument reading alike. Severity is carried by copy and dial position, not a second hue.

## 3. Typography

**Body Font:** -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif — the system stack, deliberately not Inter, unchanged in this revision.
**Label/Mono Font:** monospace (system default), used once: the invite-code field.

**Character:** Unchanged — a single honest system sans at every size. On photo-first screens, type sits directly on the image (over a scrim), so it carries slightly more weight there by necessity: prefer 600 over 400 for anything overlaid on a photo, even where the equivalent element on a solid surface would be regular weight.

### Hierarchy
- **Title** (600, 18–24px, 1.3 line-height): Screen headings, the Login wordmark, and species names overlaid on a photo card.
- **Body** (400, 14px, 1.5 line-height): Chat messages, textarea content, form values.
- **Label** (500, 10–12px, 1.3 line-height, occasional uppercase + 0.02em tracking): Form labels, timestamps, nav labels, dial readouts, stat-row captions.
- **Mono** (400, 16px, 2px tracking): The invite-code input only.

### Named Rules
**The Quiet Type Rule.** Nothing exceeds 24px, and 24px appears exactly once. Hierarchy comes from weight and color, never a dramatic size jump — this includes text overlaid on photography, which gets weight, not size, to stay legible.

## 4. Elevation

Flat by default, unchanged: no `box-shadow` anywhere. What's new is a second chrome treatment specifically for photo-first screens. On non-photo screens (chat, forms, lists), raised surfaces are still solid Loam + a 1px Twig edge. On photo-first screens (a session-complete summary, a species detail hero, any screen where a nav bar or header floats directly over full-bleed photography), chrome instead uses a translucent dark scrim with `backdrop-filter: blur(...)` — solid Loam would fight the photo underneath it; a blurred scrim lets the image continue to read through.

Grain texture (from the prior revision) still applies only to non-photo background surfaces — it has no role on top of real photography, which already supplies all the material/texture the screen needs.

### Named Rules
**The Flat-by-Default Rule.** No shadows. Loam + Twig for solid chrome; a blurred scrim for chrome over photography.

**The Overlay Chrome Rule.** Any nav, header, or button that sits directly on top of full-bleed photography uses a translucent Bark scrim (`rgba(23,20,15,~0.5–0.7)`) with backdrop blur, never a solid fill — the photo must stay visible through it.

**The Sparing Grain Rule.** Grain is a non-photo background material only, on at most one or two large surfaces per screen.

## 5. Components

### Catch / Species Photo Card (primary browsing pattern — rewritten)
- **Two distinct full-bleed treatments, not one** (clarified after checking moss_reference and hiking_app_concept directly — the two references actually show different jobs, which the original spec conflated into a single description):
  - **Inset browsing card** (this component): a rounded-corner card with visible outer padding/margin against the page background — near-full-bleed *within the card*, not edge-to-edge with the screen. This is what moss_reference's home-screen hero card and hiking_app_concept's "Aiguille du Midi" card both do. Aspect ratio is closer to landscape/square in moss_reference, taller (~4:5) in hiking_app_concept — either reads correctly; don't force a single ratio, let the source photo's natural crop lead within a portrait-to-square range.
  - **Full-screen backdrop** (see Session Complete below): true edge-to-edge photography with no padding and no corner radius, filling the entire screen, with chrome floating on top. Reserved for in-session and completion screens, not the browsing card.
- **Structure:** One large photo per scroll unit — not a grid of small square thumbnails. Minimal outer padding so the card reads as intentional and near-full-bleed, not a small thumbnail in a frame.
- **Text placement:** Species name (Title weight), personal-best flag, and count sit directly on the photo inside a bottom gradient scrim (Bark fading to transparent, covering roughly the bottom 35–40% of the image) — never in a text zone beside or below the photo.
- **Border:** A single 1px Twig edge at the card's outer radius (`{rounded.xl}`) — the only framing device; no inner card, no nested surface.
- **Personal-best / new-species variant:** A small Gold badge (`personal-best-badge` token) in the photo's top corner — the primary place The Earned Gold Rule surfaces in product.
- Species chips (`chip-tag`) still exist, but only for dense inline lists with no photo available (chat mentions, a compact fallback list) — never as the default browsing pattern.

### Compact List Row (new component — was missing from the prior revision)
Found repeatedly in moss_reference (the "Near you" section, the "Next discovery" bottom-sheet card, the "New species" row on the completion screen) but absent from the prior spec, which only offered two options — the full-bleed hero card or the no-photo chip-tag. This is the real middle ground: secondary/compact browsing that still carries a photo.
- **Structure:** A horizontal row — small square or rounded-square photo thumbnail (roughly 40–56px) on the leading edge, title + subtitle text beside it, optional trailing chevron or badge.
- **Use:** Secondary/nearby lists where a full-bleed hero card would be too heavy for the content's weight — a "Near you" style list, a compact discovery/waypoint callout, a recap row that needs a photo but isn't the primary browsing surface.
- **Distinct from `chip-tag`:** chip-tag has no photo and is for dense inline mentions; this row always carries a thumbnail image.
- **Distinct from the Photo Card:** this is not a downgrade of the hero card at small size — it's a different job (secondary list context vs. primary browsing), same as moss_reference uses both patterns side by side on one screen.
- **Status:** Not yet implemented anywhere in the app — Trips currently has no photo data to populate the thumbnail (see Migration note). Documented now so the pattern exists when photo storage lands, rather than reinventing it ad hoc.

### Session Complete Screen (new signature pattern)
Modeled directly on a "walk complete" summary: a clear, earned end-of-session moment, not just a form confirmation.
- **Checkmark:** A simple circular check, Moss by default; Gold instead if the session included a personal best or a new species.
- **Stat row:** Three stats side by side — location, duration, species caught — as large Title-weight numerals with a Label-weight caption beneath each, mirroring a distance/time/discoveries layout.
- **Photo filmstrip:** A horizontal scrollable strip of the actual catch photos from the session. This is the one place small thumbnails are correct — a recap filmstrip is a secondary, retrospective use, not the primary discovery/browsing pattern that the full-bleed Photo Card owns.
- **New species flag:** Any first-time species in the session gets an explicit "Added to your FishDex" tag (Gold text on Gold Tint), separate from and in addition to a personal-best flag on the same catch if both apply.

### Discovery Progress Trail (new component)
- **Structure:** A horizontal segmented trail of dots/marks — filled Moss for logged/discovered, outline Twig/Ash for not-yet-found — with a caption like "3 of 5 discoveries." Used for collection/dex-style progress (species found within a family, a challenge, a trip's discovery count).
- **Completion:** If a trail reaches full completion, the final segment and caption take Gold instead of Moss — a small, earned moment, consistent with The Earned Gold Rule.
- **Distinct from the map's exploration-score bar** (below), which is a continuous 0–100 measurement, not a discrete collection count — the trail and the bar are not interchangeable.

### Instrument Dial (new component)
- **Structure:** A circular gauge — a stroke-based progress ring with a centered numeric readout (value + unit, e.g. "18°C", "1013 hPa") and a Label-weight caption beneath or beside it. Used for temperature, barometric pressure, and water clarity wherever they currently render as small text chips.
- **Color:** The ring arc is colored by reading quality using the existing semantic set — Moss for favorable, Ash/Wet Stone for neutral, Rust for unfavorable — no new hexes introduced for this purpose.
- **Feel:** Confident and scientific-instrument-like (compass/altimeter register), not playful or rounded-cartoon.

### Buttons
- **Shape:** Pill radius (`{rounded.pill}`, 20px) for primary/full-width CTAs — corrected from an earlier 10px-rectangle spec after checking moss_reference directly: every button in the primary reference is fully pill-shaped (the inline "Start walk" CTA on the hero photo card, and both full-width buttons on the completion screen). Segmented controls, chips, and badges keep their own previously-specified radii (`{rounded.sm}`, `{rounded.pill}` for badges) — this shape change is scoped to primary CTA buttons only, not every interactive element.
- **Primary:** Moss Fill background, Bone text, 600 weight.
- **Disabled / Loading:** Twig+Ash / Moss Fill Dim, as before.
- **Focus:** Visible Moss ring on `:focus-visible` — required on every button.
- **On photography:** Icon-only buttons that float over a photo (e.g. a back button on a photo-first hero) use the Overlay Chrome treatment — translucent Bark circle with blur — never a solid fill.

### Chips / Pills
- **Species tag:** Sage Tint background, Moss text — inline/no-photo contexts only now (see Photo Card above).
- **Alarm pill:** Rust text/border on Rust Tint — the single style for any warning or error state.

### Cards / Containers (non-photo screens only)
- **Corner Style:** 10–12px radius.
- **Background:** Loam on Bark, 1px Twig border, no shadow.
- **Use:** Trip list rows, form containers, chat — anywhere there isn't a dominant photo. This is now explicitly the secondary pattern; photo-first screens use the Photo Card and Overlay Chrome treatments instead.

### Inputs / Fields
- Loam background, 1px Twig stroke, Bone text, Ash label. Required Moss `:focus-visible` ring/border — previously missing, still a hard requirement.

### Navigation
- **Solid variant** (non-photo screens: chat, list, forms): Loam background, 1px Twig top edge, Moss active state, Ash inactive.
- **Overlay variant** (photo-first screens): translucent Bark scrim + backdrop blur (The Overlay Chrome Rule) instead of solid Loam, same Moss/Ash color logic for active/inactive.

### Message Bubbles
- Bot: Loam background, 2px Moss left accent, asymmetric radius. User: Moss Fill background, Bone text, mirrored radius. Unchanged.

### Exploration Score Bar (no longer rendered — distinct from the Discovery Trail above)
- 6px height, Twig track, fill uses the Heat data scale, 0.3s width transition. It was the old segment-score explore view's meter and has no current caller; the Discovery Progress Trail covers collection progress.

## 6. Do's and Don'ts

### Do:
- **Do** let full-bleed real photography be the interface on species, catch, and session screens — chrome floats thin and minimal on top of it, never the reverse.
- **Do** put species name, PB flag, and count directly on the photo via a bottom gradient scrim — never beside or below it in a separate text zone.
- **Do** give session completion a real moment: checkmark, stat row, catch-photo filmstrip, explicit "Added to your FishDex" flags for new species.
- **Do** use a segmented, storied trail for collection/discovery progress ("3 of 5 discoveries") — reserve the plain thin bar for the map's continuous score meter only.
- **Do** give instrument data (temp, pressure, clarity) a circular dial/gauge treatment, colored with the existing Moss/Ash/Rust semantic set.
- **Do** use a translucent blurred scrim, not solid Loam, for any chrome floating over full-bleed photography (The Overlay Chrome Rule).
- **Do** keep chrome thin, precise, and out of the photo's way — the photo carries emotion, the chrome carries function.
- **Do** keep the tone adventurous *and* scientific — curious, precise, a little reverent about nature. Never gamified or cutesy.
- **Do** give every input and button a visible focus state (Moss ring) — still a hard requirement.

### Don't:
- **Don't** use small square photo thumbnails inside dark cards as the primary species/catch browsing pattern — that dashboard-of-cards look is retired. (Small thumbnails are correct only in the Session Complete filmstrip, a secondary recap context.)
- **Don't** use illustrated or flat-icon fish artwork anywhere — real photography only, for any species or catch.
- **Don't** borrow literal hiking/outdoor-brand tropes — boot icons, leaf icons, expedition-adventure typography or taglines ("go further," "conquer"). This stays precise and quiet, not expedition-branded.
- **Don't** use pure black backgrounds, Inter, or purple/generic SaaS gradients.
- **Don't** nest cards inside cards.
- **Don't** default collection/discovery progress to a plain thin bar when the trail motif fits — and don't use the trail motif for the map's continuous score meter, which is a different data shape.
- **Don't** add badges, leaderboards, streak counters, or social-feed gamification patterns — Gold-marked personal bests and FishDex additions are the one deliberate, earned exception.
- **Don't** let a dial, trail, or any instrument element read as playful or rounded-cartoon — the register is scientific-instrument, not gamified.
- **Don't** use `border-left` as a decorative colored stripe anywhere except the bot message bubble's tail accent.

## 7. The Naturalist Palette — Primary App-Wide System (Phase 2, applied)

**Status: this section now governs `web/src/screens/FishDex.jsx`,
`Chat.jsx`, `LogTrip.jsx`, and `Map.jsx`.** It originated as a screen-scoped
system for FishDex only (the `design_handoff_fishdex_collection/` handoff)
— that was Phase 1. Phase 2, applying it app-wide, is now complete for
those four screens. **Trips.jsx and Login.jsx still run the legacy
Bark/Moss system in Sections 2–6** — they were out of scope for this pass
and were not touched; there is currently no Profile screen in the app (see
"App-Wide Migration" below). See `fishdex-tokens.css` (scoped custom
properties, parallel to `tokens.css`, loaded app-wide) and the
`fishdexColors`/`fishdexTypography`/`fishdexRounded` frontmatter keys above.

**Creative direction:** "the photograph as the interface," rendered as a
naturalist's field journal — warm dusk-on-the-water dark theme, Spectral
serif for names/figures, weathered-brass + pigmented-moss accents, a whisper
of paper grain. The handoff's own framing: **Pokémon Go's proven collection
psychology** (visible gaps, personal bests, the pull to fill in what's
missing) **rendered as a field journal, not a game console** — the ❦
"new find" mark and the PB star/pill are the one deliberately game-adjacent
mechanic kept, dressed in journal materials rather than neon/plastic/mascot
chrome.

**Adaptive layout (not a user toggle):** the number of distinct taxonomic
families represented in the user's actual catches decides the view —
`< 3` families renders the **flat view** (one ungrouped stack of full-bleed
photo plates), `>= 3` renders the **grouped view** (species folded into
plain-language family folders, one expanded accordion-style, untouched
families surfaced as a lighter "explore" row). Computed live from
`GET /fishdex`, never chosen by the user.

**Species reference data now carries a family.** `src/services/species_family.py`
maps every species in `species_mapping.py`'s `COMMON_TO_SCIENTIFIC` and the
CA-ON `species_ranges` pool to `(family, familyCommonName)` — this data did
not exist before this handoff; see the PR/session notes for how it was added.
Family display names (`Centrarchidae` → "Bass & Sunfish", `Percidae` →
"Perch, Walleye & Darters", `Ictaluridae` → "Catfish & Bullheads",
`Catostomidae` → "Redhorse & Suckers", `Esocidae` → "Pike & Musky",
`Salmonidae` → "Trout & Salmon", `Amiidae` → "Bowfin") are exactly as
specified in the handoff; the rest (`Cyprinidae` → "Minnows & Chubs",
`Lepisosteidae` → "Gar", etc.) extend the same convention to cover the full
Ontario pool. **Documented Carp exception:** Common Carp (`Cyprinus carpio`)
gets its own folder labeled "Carp," subtitled with its binomial rather than
folded into the wider Cyprinidae most anglers don't associate it with — the
rest of Cyprinidae (shiners, chubs, dace) reads as unrelated bait-fish. This
is data-encoded in `get_family()`, not a frontend special case.

**Known, honestly-handled real-data gaps** (do not paper over these —
surface the real state instead of the mock's sample numbers):
- **No photo yet for a catch** → a quiet placeholder tile (a faint outline
  fish glyph on `card-fill-quiet`), never a broken `<img>` icon. This is the
  handoff's `<image-slot>` concept, mapped to the app's own catch photos.
- **Personal-best length has no real data source today.** `catches.biggest_size`
  is a free-text column with no populating input path yet (no NL size
  extraction, no manual entry field) — species with no recorded size show a
  plain catch count, never a fabricated "x″" figure or PB pill. This will
  start working the moment a real size-entry path lands; nothing in the
  frontend needs to change for that.
- **"New find" has no acknowledgment-state table.** The handoff's "until
  acknowledged" language implies dismissible state this app doesn't have yet.
  A species caught exactly once is used as an honest proxy for "new" instead
  of inventing dismissal tracking.
- **Region title reads "Ontario"**, the real jurisdiction the data is scoped
  to (`CA-ON`, hardcoded pending a per-user jurisdiction field) — not the
  prototype's fictional "Credit River watershed" sample name.

**Components (screen-scoped, see `FishDex.jsx` for implementation):**
Discovery header (eyebrow + title + inert search button + continuous
progress rail with a glowing pin-node — distinct from Section 5's segmented
Discovery Progress Trail, which remains the app-wide discrete-count pattern
elsewhere), Caught Plate (full-bleed, bottom scrim, PB/new-find pill),
Undiscovered Row (dashed border, hatched thumbnail), Family Grid Cell
(2-column, grouped view), Collapsed Family Banner (96px, left-scrim,
accordion trigger), Explore Card (untouched-family horizontal scroll prompt).

### Migration note
This revision was cross-checked directly against its reference images, now committed at `web/design-references/` (`moss_reference.jpg` primary, `hiking_app_concept.jpg` secondary for the dial/compass treatment only, `fishing_app_reference.jpg` a rejected reference kept only to document the illustrated-fish style being designed away from). That check corrected three things the original written spec got wrong or missed: primary CTA buttons are pill-shaped, not 10px-rectangle (see Buttons); the Photo Card conflated two distinct full-bleed treatments — an inset browsing card vs. a true full-screen backdrop (see Catch/Species Photo Card); and a Compact List Row pattern was missing entirely (see above).

The naturalist palette is fully implemented: `web/src/tokens.css` (`:root` custom properties) and `web/src/tokens.js` (the Leaflet-consumed mirror) are the single source of truth, and all 7 components reference them — no raw hex remains in the app outside those two files.

Of the four flagship photography-led patterns, three are now live and backed by real data:
- **LogTrip's photo preview** uses the near-full-bleed Catch Photo Card treatment — GPS status sits directly on the image via a bottom scrim, not as separate pills below it.
- **Instrument Dial** is live for air temperature and barometric pressure in Trips and the Map bottom sheet (`web/src/components/InstrumentDial.jsx`). Deliberately not colored by reading quality (favorable/neutral/unfavorable) as originally specified — the app has no real thresholds for what counts as "favorable" fishing conditions, and inventing one would be exactly the kind of unsupported presence/quality claim CLAUDE.md's core principle warns against. The ring shows position-in-range only, in a single neutral Moss fill.
- **Full-bleed Catch Photo Cards in Trips** — the earlier note here ("needs backend work first") was verified stale: `src/services/photo_storage.py`, `src/storage/catches.py`, and `GET /sessions`' `catches[].photo_url` now exist, so `Trips.jsx` was migrated onto the same `CaughtPlate` pattern (see "Trips Migration" below). Map still shows no full-bleed treatment — its stops/segments aren't single-photo objects the way a session or species is, so the pattern doesn't map cleanly there yet.

One is still intentionally **not yet implemented** — needs backend work first, not just frontend polish:
- **Discovery Progress Trail** — there's no species-collection/"discoveries" concept in the API at all yet (no endpoint, no aggregate). This is a real product decision (what counts as a "discovery," per-family or global count) as much as a backend one.

This is now the reference `/impeccable audit` and `/impeccable critique` should check against, with the above gap understood as backend-blocked, not an oversight.

### App-Wide Migration (Phase 2)

Applied the live FishDex palette/type to Chat, LogTrip, and Map as a
visual/token pass — no layout, navigation, or data-logic changes on any of
the three. Cross-checked against `FishDex.jsx` as actually rendered today,
not the original screenshots, per instruction.

**Chat (`Chat.jsx`, `components/Message.jsx`):** Header title and bot avatar
now Spectral serif; body text, timestamps, and the input bar stay
system-ui. Bot/user message bubbles moved to `--fx-card-fill` /
`--fx-moss-dark` with `--fx-moss-light` left-accent on the bot bubble. Log
and send buttons use `--fx-moss-light` fill with `--fx-on-accent` text,
matching FishDex's own CTA convention (`EmptyState`'s "Log a catch"
button), not the legacy Moss Fill token.

**Log (`LogTrip.jsx`):** Photo dropzone, GPS pill, textarea, and the
species-confirm card (`SpeciesConfirmCard`) all moved to `--fx-*`. The
success state was redesigned per instruction — no longer a plain green
checkmark box. It now uses the same ❦ "new find" mark and moss-lightest
uppercase label FishDex's `CaughtPlate` uses for a first-time species, with
the confirmation headline in Spectral serif, so logging a trip reads as a
small earned moment rather than a form-submit acknowledgment.

**Map (`Map.jsx`, `components/InstrumentDial.jsx`):** Header, mode toggle,
explore-mode dropdown, empty state, bottom sheet (stop/segment detail),
species chips, and the "Open in Maps"/"Satellite view" links all moved to
`--fx-*`. **Not touched, deliberately:** `MapContainer`/`TileLayer`/
`CircleMarker` (react-leaflet, can't consume CSS custom properties anyway —
see `tokens.js`), and `stopColor`'s Moss/Ash marker colors. (The former `scoreColor`
heat-scale was removed with the old explore view.) `InstrumentDial.jsx` also moved to
`--fx-*` — it's shared with Trips.jsx, so Trips' instrument dial now
inherits the new moss ring color too, as a side effect of the shared
component; nothing else on Trips changed.

**Corrections found while cross-checking the live app against the request
(flagged rather than guessed on, per instruction):**
- **No Profile screen exists.** There is no `Profile.jsx`, no `/profile`
  route, no avatar/stat-strip/angler-card component anywhere in
  `web/src/`. The five real screens are Chat, Map, Log, Trips, FishDex
  (`components/NavBar.jsx`). This work item is on hold pending
  clarification of what "Profile" should actually point at.
- **Map is not a placeholder.** `Map.jsx` is a fully working react-leaflet
  map with live segment data, personal/explore layers, and instrument
  dials in a bottom sheet — not a "coming soon" state. The token pass was
  applied to its real chrome; nothing was held back waiting for it to be
  built.
- **Chat has no starter-chip cards or conditions chip.** Neither exists in
  `Chat.jsx` or any component today — there was nothing there to reskin.
  If these are planned but unbuilt, they need to be scoped as new work,
  not a token pass.
- **Grain texture is now implemented — it was not before, including on
  `FishDex.jsx` itself.** This was flagged here as "correctly never built,"
  optional per the handoff README. Re-checking the live render (not this
  doc) surfaced that FishDex itself had no grain either, so nothing was
  actually being matched. Added as a shared `.fx-grain` utility
  (`fishdex-tokens.css`) + `GrainOverlay.jsx`, using the exact SVG
  fractal-noise snippet from the original mockup at opacity .05, now live on
  Chat, Log, Trips, and FishDex — see "Trips Migration" below for the
  z-index/paint-order note on why it stays clear of photography.

### Trips Migration

Applied after the above pass, once cross-checking the live app surfaced two
things this doc previously got wrong: (1) Trips was still described as
legacy/not-yet-migrated, and (2) its full-bleed photo cards were described as
backend-blocked — both stale. `src/storage/catches.py` /
`src/services/photo_storage.py` already exist and `GET /sessions` already
returns `catches[].photo_url`; `Trips.jsx` was just never updated to consume
the pattern.

**Trips (`Trips.jsx`):** Migrated fully onto `--fx-*` and Spectral, joining
Chat/Log/Map/FishDex. The session-card list (small thumbnail + text below)
is replaced by `TripPlate`, the same full-bleed-photo-plus-bottom-scrim
structure as FishDex's `CaughtPlate`, applied to a trip/session instead of a
species: location name + comma-joined species list on the left (serif
title / italic serif caption, identical treatment to `CaughtPlate`'s common
name / scientific name), date + species count on the right (mirroring
`CaughtPlate`'s figure/caption column). Sessions with no catch photo fall
back to the same quiet fish-glyph placeholder tile FishDex uses for a
missing photo — never a broken `<img>`. The two `InstrumentDial` readouts
(air temp, pressure) move from a below-the-fold row to an Overlay-Chrome
scrim badge in the plate's top-left corner (translucent Bark + blur, scaled
down to fit), and an anomaly flag, when present, becomes a small Rust pill
in the top-right corner — the one alarm-color exception noted above, in the
badge slot `CaughtPlate` reserves for its PB/new-find pill.

**Grain and the photo plates — a paint-order detail worth keeping.**
`.fx-grain` is `z-index: 0`, not a positive value, and is always the first
child of its screen root. Photo plates use `position: relative` with no
explicit `z-index` (so, per CSS stacking rules, they land in the same
z-index:auto/0 bucket as the grain layer) — with grain first in DOM order,
plates paint after it, i.e. on top. A positive z-index on `.fx-grain` would
invert this and put the noise texture visibly over real photography, which
the Sparing Grain Rule below explicitly rules out. Keep this in mind before
"simplifying" the z-index — it's load-bearing.
