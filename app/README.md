# FishDex app

One app on top of the FishDex API, in the Lavish look. Plain files, no build step.

| Screen | Status |
|---|---|
| Map | Live. Rivers and spots from `/map/sections` and `/map/spots`, a tap opens `/map/spots/{id}`. Real Leaflet map on OpenStreetMap tiles. |
| Coach | Live. Talks to `POST /chat` (the `src/agent` coach). |
| Field guide | Mockup, plus a live strip of the species on record near the picked place. |
| Anglers, Theme | Unchanged Lavish mockups, sample data. Connected later. |

What you pick on the map (a spot or a river) is shared state: it shows in the header, rides along as
context on every coach question, and drives the field guide strip.

## Run it

```
make serve                                   # the API, http://localhost:8000
uv run python -m src.cli.main token          # prints a token (valid 90 days)
make app                                     # this app, http://localhost:8765/app/
```

Open http://localhost:8765/app/ and paste the token (or a friend's invite code). The address and token
are kept in the browser. A link like `http://localhost:8765/app/?token=...` also connects, then removes
the token from the address bar.

The coach needs `ANTHROPIC_API_KEY` in `.env`, as for `make run`. Without it the coach shows the
server's own message as an error.

The server is rooted at the repo root on purpose: the app loads Leaflet, the Fraunces font and the
unchanged mockups straight from `design/lavish/`.
