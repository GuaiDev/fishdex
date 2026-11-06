#!/bin/sh
# Assemble the static site Vercel serves into dist/:
#   dist/app/            the clickable app (app/)
#   dist/design/lavish/  what the app loads from the repo root (Leaflet, fonts, mockups)
# and write dist/app/config.js so the app talks to the hosted API by default.
#
# FISHDEX_API_URL (set in Vercel project settings) is the backend address; it falls back to the
# fly.toml app name.
set -e
cd "$(dirname "$0")/.."
API_URL="${FISHDEX_API_URL:-https://fishdex-api.fly.dev}"
API_URL="${API_URL%/}"

rm -rf dist
mkdir -p dist/design
cp -R app dist/app
cp -R design/lavish dist/design/lavish
rm -f dist/app/serve.py dist/app/README.md
printf 'window.FISHDEX_CONFIG = { api: "%s" };\n' "$API_URL" > dist/app/config.js
echo "built dist/ (API: $API_URL)"
