#!/usr/bin/env bash
# Apply a carried-in kpiGo upgrade on the client's air-gapped host (PRD UP-*).
#
#   scripts/apply_update.sh <bundle-dir> --run-as <ops-user> [--to <version>]
#
# <bundle-dir> is the unpacked bundle (a signed manifest.json beside images.tar),
# the same directory that was staged under KPIGO_UPDATE_DIR for the update service.
# This wraps the one step an action cannot do — maintenance mode is env-driven, and
# loading images and running migrations are host operations — around the audited
# authorisation, so the ledger entry and the actual upgrade stay together:
#
#   1. verify the bundle's checksums          (tools/bundle.py verify)
#   2. authorise it + take the pre-upgrade backup (system.update.apply, audited)
#   3. load the image set                      (docker load)
#   4. maintenance on → migrate → maintenance off  (operator sets KPIGO_MAINTENANCE_MODE)
#
# The update service refuses an unsigned, untrusted, un-entitled or out-of-path
# bundle before step 2, so nothing is loaded for a bundle kpiGo will not run.
set -euo pipefail

folder="${1:?usage: apply_update.sh <bundle-dir> --run-as <user> [--to <version>]}"
shift || true
run_as="${KPIGO_UPDATE_USER:-}"
to=""
while [ $# -gt 0 ]; do
  case "$1" in
    --run-as) run_as="$2"; shift 2;;
    --to) to="$2"; shift 2;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[ -n "$run_as" ] || { echo "set --run-as <user> (or KPIGO_UPDATE_USER): upgrades are authorised by a named user" >&2; exit 2; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tools="${here}/tools/bundle.py"
manifest="${folder}/manifest.json"
[ -f "$manifest" ] || { echo "no manifest.json in ${folder}" >&2; exit 2; }

echo "1/4  Verifying the bundle against its manifest…"
python3 "$tools" verify --manifest "$manifest" --root "$folder"
[ -n "$to" ] || to="$(python3 -c "import json; print(json.load(open('${manifest}'))['to_version'])")"

echo "2/4  Authorising ${to} (re-checks entitlement/path, takes the mandatory backup)…"
docker compose exec -T app python manage.py action system.update.apply \
  --user "$run_as" --json "{\"confirm_version\": \"${to}\"}"

echo "3/4  Loading the image set…"
docker load -i "${folder}/images.tar"

cat <<EOF
4/4  Apply the upgrade (operator step — maintenance mode is env-driven):

  # In your compose .env:
  KPIGO_MAINTENANCE_MODE=1
  docker compose up -d --wait app
  docker compose exec app python manage.py migrate
  # then:
  KPIGO_MAINTENANCE_MODE=0
  docker compose up -d --wait

Finally, confirm the health page is green and the version reads ${to}.
The pre-upgrade backup from step 2 is your restore point; scripts/restore.sh drills it.
EOF
