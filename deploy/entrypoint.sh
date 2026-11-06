#!/bin/sh
# Point the code's relative data/cache path at the volume so fetched-and-cached responses
# survive a restart. fishing.db and photos already follow DATA_DIR.
set -e
DATA_DIR="${DATA_DIR:-/data}"
mkdir -p "$DATA_DIR/cache" "$DATA_DIR/photos"
rm -rf /app/data/cache
ln -sfn "$DATA_DIR/cache" /app/data/cache
exec "$@"
