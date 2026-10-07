#!/usr/bin/env bash
# Install or upgrade kpiGo from a carried-in offline bundle. Runs on the client's
# air-gapped host: no network, Docker and Python 3 only.
#
#   scripts/load_bundle.sh dist/kpigo-1.0.0-bundle.tar [--into /opt/kpigo]
#
# Verifies every artefact against the manifest's SHA-256 BEFORE loading anything,
# then docker-loads the image set. It does not start the stack or run migrations:
# for a first install, follow the runbook (README) from `docker compose up`; for
# an upgrade, the update service runs the mandatory-backup → dry-run → apply
# sequence as an audited action.
set -euo pipefail

bundle="${1:?usage: load_bundle.sh <bundle.tar> [--into <dir>]}"
shift || true
into=""
while [ $# -gt 0 ]; do
  case "$1" in
    --into) into="$2"; shift 2;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tools="${here}/tools/bundle.py"
[ -f "$tools" ] || { echo "tools/bundle.py not found beside this script" >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
echo "Unpacking ${bundle}…"
tar -C "$work" -xf "$bundle"

root="$(find "$work" -maxdepth 2 -name manifest.json -printf '%h\n' | head -n1)"
[ -n "$root" ] || { echo "no manifest.json in the bundle" >&2; exit 2; }

echo "Verifying artefacts against the manifest…"
python3 "$tools" verify --manifest "${root}/manifest.json" --root "$root"

echo "Loading image set…"
docker load -i "${root}/images.tar"

if [ -n "$into" ]; then
  mkdir -p "$into"
  cp "${root}/compose.yaml" "$into/compose.yaml"
  [ -f "${into}/.env" ] || cp "${root}/.env.example" "${into}/.env.example"
  cp -r "${root}/deploy" "${into}/deploy"
  echo "Compose files placed in ${into}. Next: set ${into}/.env, then 'docker compose up -d'."
fi
echo "Done. Images loaded for $(python3 "$tools" show --manifest "${root}/manifest.json" | head -n1)"
