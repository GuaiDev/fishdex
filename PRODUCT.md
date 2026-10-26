# Product

## Register

product

## Platform

API backend only. The client UI is being rebuilt separately and is not in this repository.

## Users

Right now the only user is the builder: an intermediate angler based in Oakville, ON, fishing streams and small lakes, targeting every species from popular gamefish down to microfishing targets (darters, dace, madtoms, shiners, chubs, lampreys). Exploration matters more than catch optimization. The app is used both stream-side — outdoors, one-handed, often in direct daylight — and at a desk between trips for planning.

The plan is to expand deliberately: a small trusted group next, then a full-scale multi-user app once the data layer and product are solid. Design should stay direct and personal at every stage rather than assuming an anonymous mass audience too early.

## Product Purpose

FishBot pairs a chat-based coaching bot with a habitat-and-presence intelligence layer, a map, and trip logging. It isn't a lookup tool — it exists to help an angler explore untapped water well, backed by data that actually distinguishes where fish live from where people report catching them. Success is dual: fast, trustworthy answers and easy logging in the moment, and a deeper planning/exploration tool to sit with between trips.

## Positioning

FishBot gets the habitat and presence prediction right — separating fish presence from angler pressure instead of collapsing crowdsourced reports into one score — and pairs that rigor with a bot that understands what anglers actually want from a coach, not just what they literally typed. Convenience features (voice logging, pulling in saved Google Maps pins) and, later, light social features exist to make exploration feel effortless, not to replace the exploration itself.

## Brand Personality

Sharp and precise where it counts — data-dense, exact, and honest about what it doesn't know yet, never overselling a prediction. Warm and personal alongside that — it should feel like a field notebook built for one person, not a generic product. A small amount of gamification is welcome as a delighter, but it should never be loud enough to undercut trust in the underlying data. Think knowledgeable fishing buddy who did the research homework, not a badge-and-leaderboard app.

## Anti-references

None specified yet — no bad examples to actively design away from at this stage.

## Design Principles

Presence is never pressure. Any screen surfacing fish likelihood keeps habitat/survey signal visually and structurally distinct from crowdsourced report density — never a single collapsed score.

Coach, don't just answer. The interface should reflect understanding of what the angler is trying to accomplish (explore, decide, log a catch), not just respond to the literal query.

Convenience is in service of exploration, not a replacement for it. Friction-reducing features (voice logging, pin import) exist to free up attention for exploring more water, not to make the app feel more "featured."

Built for one, ready for a few. The product currently serves a single power user and will grow to a small trusted group before any wider release — interactions should stay direct and personal rather than defaulting to generic multi-tenant patterns before they're needed.

Precise, never cold. Sharp, data-forward answers delivered with warmth. Personality and light gamification are welcome, but never at the expense of the data feeling trustworthy.

## Accessibility & Inclusion

Outdoor daylight legibility is the one concrete requirement — this is used stream-side in direct sun, so contrast needs to hold up outside typical indoor-screen norms. No other specific accessibility needs flagged at this time; standard accessible-by-default practices (contrast, touch target size, keyboard/screen-reader basics) still apply as a baseline.
