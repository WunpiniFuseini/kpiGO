#!/usr/bin/env bash
# Build an offline install/update bundle on a machine with a network and Docker.
#
#   KPIGO_VERSION=1.0.0 scripts/build_bundle.sh [--min-from 1.0.0] [--from 0.9.0]
#
# Produces dist/kpigo-<version>-bundle.tar: the image set (docker save), a
# manifest with a SHA-256 over each artefact, and nothing that reaches the
# internet at install time. The client carries this one file in, verifies it,
# and loads it with scripts/load_bundle.sh (offline). Signing is a separate
# vendor-side step (tools/bundle_vendor.py), added by the update-service work.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

version="${KPIGO_VERSION:?set KPIGO_VERSION to the product version being built, e.g. 1.0.0}"
from_version=""
min_from="$version"
while [ $# -gt 0 ]; do
  case "$1" in
    --from) from_version="$2"; shift 2;;
    --min-from) min_from="$2"; shift 2;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done

out="dist/kpigo-${version}"
rm -rf "$out"
mkdir -p "$out"

echo "Building images for ${version}…"
KPIGO_VERSION="$version" KPIGO_BUILD_TARGET=runtime \
  docker compose build app web

app_image="kpigo/app:${version}"
web_image="kpigo/web:${version}"
images_tar="${out}/images.tar"

echo "Saving image set → ${images_tar}"
docker save "$app_image" "$web_image" "postgres:16" "redis:7" -o "$images_tar"

# Carry the compose file and nginx config so the client install is self-contained.
cp compose.yaml "$out/compose.yaml"
cp .env.example "$out/.env.example"
mkdir -p "$out/deploy"
cp -r deploy/nginx "$out/deploy/nginx"

echo "Writing manifest…"
python tools/bundle.py manifest \
  --to "$version" --from "$from_version" --min-from "$min_from" \
  --root "$out" \
  --artefact "$images_tar" \
  --artefact "$out/compose.yaml" \
  > "${out}/manifest.json"

bundle="dist/kpigo-${version}-bundle.tar"
tar -C dist -cf "$bundle" "kpigo-${version}"
echo "Bundle written: ${bundle}"
python tools/bundle.py show --manifest "${out}/manifest.json"
