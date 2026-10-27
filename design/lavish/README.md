# Lavish mockups

Static design mockups for FishDex, kept exactly as designed. No build step, no backend.

## Pages

| File | What it is |
|---|---|
| `fishdex-field-guide.html` | Species as a nature field guide |
| `fishdex-anglers.html` | Layout sized to the angler |
| `index.html` | Clickable prototype: one shell, persistent navigation across every screen below |
| `fishdex-map.html` | First explore map (filters, layers, shorelines, weeds, conditions) |
| `fishdex-map-interactive.html` | Interactive version of the explore map |
| `fishdex-map-revision.html` | Latest explore map, all landed layers |
| `theme-rebuild.html` | Theme rebuild |
| `exports/` | Standalone exports of the field guide, anglers, map and theme pages |

`map-assets/` holds the vendored Leaflet, the Fraunces font and the map tiles (zoom 7-13). The pages load them by relative path, so keep the folder layout as is.

## Serve locally

```
cd design/lavish
python3 -m http.server 8765
```

Then open http://localhost:8765/ (the clickable prototype; any page also opens on its own). Opening the files directly from disk may not load the tiles; use a server.
