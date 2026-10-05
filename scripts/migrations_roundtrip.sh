#!/usr/bin/env bash
# Migration discipline, rollback leg (Schema §15.4): apply every migration, dry-run
# the plan, roll each kpiGo app back to zero, then apply forward again. A
# migration that cannot reverse within a major fails here, not on a client server.
set -euo pipefail

manage() { ${PYTHON:-uv run python} manage.py "$@"; }

manage migrate --noinput
# Reverse dependency order: ingestion sits on metrics and hierarchy, which sit on
# periods and platform.
for app in licence access ingestion metrics hierarchy periods platform; do
  manage migrate "$app" zero --noinput
done
manage migrate --noinput
manage showmigrations --plan | grep -v '\[X\]' && { echo "unapplied migrations remain" >&2; exit 1; }
echo "migrations round-trip OK"
