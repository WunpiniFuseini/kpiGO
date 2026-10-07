#!/usr/bin/env bash
# Take a kpiGo backup from the host (PRD OP-5). Runs system.backup through the
# action CLI so the backup is recorded and audited exactly as a scheduled or
# UI-triggered one is — never a raw pg_dump that leaves no trace.
#
#   scripts/backup.sh [--kind manual|scheduled|pre_upgrade] [--run-as ops]
#
# The artefact (database.dump + config.json + manifest.json) lands under
# KPIGO_BACKUP_DIR on the backup volume. For a bare pg_dump outside the app
# (e.g. the container won't start), see the restore runbook in the README.
set -euo pipefail

kind="manual"
run_as="${KPIGO_BACKUP_USER:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --kind) kind="$2"; shift 2;;
    --run-as) run_as="$2"; shift 2;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[ -n "$run_as" ] || { echo "set --run-as <user> (or KPIGO_BACKUP_USER): backups run as a named user" >&2; exit 2; }

exec docker compose exec -T app python manage.py action system.backup \
  --user "$run_as" --json "{\"kind\": \"${kind}\"}"
