#!/usr/bin/env bash
# Restore a kpiGo database from a backup artefact (PRD OP-5). This is the drilled
# recovery path — run it against a SCRATCH database first during onboarding so the
# backup is proven before it is ever needed.
#
#   scripts/restore.sh /var/lib/kpigo/backups/kpigo-backup-<stamp> [--into kpigo_restore]
#
# Verifies the dump's SHA-256 against the backup manifest, then pg_restore into
# the target database. It never drops the live database on its own: restoring over
# the running install is a deliberate, separate step the runbook spells out.
set -euo pipefail

folder="${1:?usage: restore.sh <backup-folder> [--into <dbname>]}"
shift || true
target="${POSTGRES_DB:-kpigo}"
while [ $# -gt 0 ]; do
  case "$1" in
    --into) target="$2"; shift 2;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done

manifest="${folder}/manifest.json"
dump="${folder}/database.dump"
[ -f "$manifest" ] || { echo "no manifest.json in ${folder}" >&2; exit 2; }
[ -f "$dump" ] || { echo "no database.dump in ${folder}" >&2; exit 2; }

echo "Verifying the dump against the backup manifest…"
expected="$(python3 -c "import json,sys; m=json.load(open('${manifest}')); print(next(a['sha256'] for a in m['artefacts'] if a['name']=='database.dump'))")"
actual="$(sha256sum "$dump" | awk '{print $1}')"
if [ "$expected" != "$actual" ]; then
  echo "REFUSING: the dump does not match the manifest (expected ${expected:0:12}…, got ${actual:0:12}…)." >&2
  exit 3
fi

host="${POSTGRES_HOST:-postgres}"
user="${POSTGRES_USER:-kpigo}"
echo "Restoring ${dump} into database '${target}' on ${host}…"
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_restore \
  --host="$host" --username="$user" --dbname="$target" \
  --no-owner --no-privileges --clean --if-exists "$dump"
echo "Restore complete. Bring the stack up and confirm the health page before handover."
