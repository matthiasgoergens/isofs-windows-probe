#!/bin/sh
# Expand the partial real-disc images (tools/partial.py) into full-size
# image files next to their oracles.
set -eu
IN=$1
OUT=$2
HERE=$(cd "$(dirname "$0")" && pwd)
PY=$(command -v python3 || command -v python3.12 || command -v python3.11)
mkdir -p "$OUT"
for p in "$IN"/*.partial; do
    b=$(basename "$p" .partial)
    "$PY" "$HERE/../tools/partial.py" expand "$p" "$OUT/$b.iso"
    cp "$IN/$b.oracle.json" "$OUT/"
done
