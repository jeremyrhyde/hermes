#!/usr/bin/env bash
# Rasterize public/icon.svg into the PNGs the manifest and iOS need.
# Run by hand after editing icon.svg; the PNGs are committed, so a normal
# build never needs a rasterizer. Fetches @resvg/resvg-js-cli on demand.
set -euo pipefail
cd "$(dirname "$0")/../public"
render() { npx --yes @resvg/resvg-js-cli --fit-width "$1" icon.svg "$2"; }
render 192 icon-192.png
render 512 icon-512.png
render 180 apple-touch-icon.png
