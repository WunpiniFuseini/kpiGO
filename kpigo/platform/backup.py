"""Backup, configuration export, and the diagnostic bundle (PRD OP-4, OP-5; TDD §12).

A backup is a ``pg_dump`` in Postgres's custom format (a single consistent
snapshot) beside a configuration summary, with a SHA-256 over each so a restore
can prove the artefact intact. The diagnostic bundle is the opposite: it carries
only operational facts kpiGo support may see — versions, migration state, sizes,
counts and licence state — and **never any client data**, so an install kpiGo
cannot otherwise see can still be diagnosed.

``run_backup`` shells out to ``pg_dump`` through ``_pg_dump`` (swappable in tests
so the record-keeping and config export can be exercised without a multi-second
dump). The artefacts land on the install's backup volume, never in the database.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.db import connection

_CHUNK = 1 << 20


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dsn() -> dict[str, str]:
    """Connection parameters for the database currently in use (the test DB in tests)."""
    s = connection.settings_dict
    return {
        "host": s.get("HOST") or "localhost",
        "port": str(s.get("PORT") or "5432"),
        "user": s.get("USER") or "",
        "password": s.get("PASSWORD") or "",
        "dbname": s.get("NAME") or "",
    }


def _pg_dump(target: Path, dsn: dict[str, str]) -> None:
    """Dump the database to ``target`` in custom format. Raises on a non-zero exit."""
    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        f"--host={dsn['host']}",
        f"--port={dsn['port']}",
        f"--username={dsn['user']}",
        f"--dbname={dsn['dbname']}",
        f"--file={target}",
    ]
    env = {"PGPASSWORD": dsn["password"]} if dsn["password"] else {}
    import os

    result = subprocess.run(  # fixed argv, no shell
        cmd, capture_output=True, text=True, env={**os.environ, **env}, timeout=600
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {result.stderr.strip()[:500]}")


def config_summary(org_id: str) -> dict[str, Any]:
    """The configuration that accompanies a dump: settings, licence terms, object counts.

    Deliberately holds no client data — no subject names, metric values or scores —
    only the shape of the configuration the install carries, so it can be reviewed
    beside the full dump without exposing figures.
    """
    from kpigo.licence.state import current, product_version
    from kpigo.platform.models import OrgSettings

    settings_row = OrgSettings.objects.filter(org_id=org_id).first()
    summary: dict[str, Any] = {
        "exported_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "product_version": product_version(),
        "org_settings": None,
        "licence": None,
        "object_counts": _object_counts(org_id),
    }
    if settings_row is not None:
        summary["org_settings"] = {
            "reporting_currency": settings_row.reporting_currency,
            "reporting_timezone": settings_row.reporting_timezone,
            "business_day_cutoff": (
                settings_row.business_day_cutoff.isoformat()
                if settings_row.business_day_cutoff
                else None
            ),
            "attribution_rule": settings_row.attribution_rule,
            "campaign_value_basis": settings_row.campaign_value_basis,
            "winback_retention_days": settings_row.winback_retention_days,
            "config_version": settings_row.config_version,
        }
    state = current(org_id)
    row = state.licence
    summary["licence"] = {
        "state": state.state,
        "mode": state.mode,
        "modules": sorted(state.modules),
        "max_major_version": row.max_major_version if row else None,
        "instance_kind": row.instance_kind if row else None,
    }
    return summary


def _object_counts(org_id: str) -> dict[str, int]:
    """How many of each configuration object the install carries. Counts, not contents."""
    counts: dict[str, int] = {}
    try:
        from kpigo.metrics.models import Metric

        counts["metrics"] = Metric.objects.filter(org_id=org_id).count()
    except Exception:
        pass
    try:
        from kpigo.hierarchy.models import Subject

        counts["subjects"] = Subject.objects.filter(org_id=org_id).count()
    except Exception:
        pass
    try:
        from kpigo.access.models import AppUser

        counts["users"] = AppUser.objects.filter(org_id=org_id).count()
    except Exception:
        pass
    try:
        from kpigo.ingestion.models import Feed

        counts["feeds"] = Feed.objects.filter(org_id=org_id).count()
    except Exception:
        pass
    return counts


@dataclass
class BackupResult:
    outcome: str
    artefact_path: str
    sha256: str
    size_bytes: int
    config_version: int
    product_version: str
    detail: str = ""


def run_backup(
    org_id: str,
    directory: Path,
    *,
    dump: Callable[[Path, dict[str, str]], None] = _pg_dump,
) -> BackupResult:
    """Write a dump + a config summary + a manifest into a timestamped folder.

    Returns a ``BackupResult`` describing the artefact. The caller records it as a
    ``BackupRun`` and audits it; this function does no database writes of its own.
    """
    from kpigo.licence.state import product_version
    from kpigo.platform.models import OrgSettings

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = directory / f"kpigo-backup-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)

    summary = config_summary(org_id)
    config_path = folder / "config.json"
    config_path.write_text(json.dumps(summary, indent=2, sort_keys=True))

    dump_path = folder / "database.dump"
    dump(dump_path, _dsn())

    manifest = {
        "format": "kpigo-backup/1",
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "product_version": summary["product_version"],
        "artefacts": [
            {"name": p.name, "sha256": _sha256(p), "bytes": p.stat().st_size}
            for p in (dump_path, config_path)
        ],
    }
    manifest_path = folder / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    total = sum(a["bytes"] for a in manifest["artefacts"])
    config_version = (
        OrgSettings.objects.filter(org_id=org_id).values_list("config_version", flat=True).first()
        or 0
    )
    # The folder's digest for the record is the dump's digest: it is the artefact a
    # restore consumes, and the manifest records every file's digest alongside.
    dump_sha = next(a["sha256"] for a in manifest["artefacts"] if a["name"] == "database.dump")
    return BackupResult(
        outcome="ok",
        artefact_path=str(folder),
        sha256=dump_sha,
        size_bytes=int(total),
        config_version=int(config_version),
        product_version=product_version(),
    )


def diagnostics(org_id: str) -> dict[str, Any]:
    """Operational facts for kpiGo support, with no client data (PRD OP-4).

    Everything here is a status, a version, a size or a count. No subject names,
    metric codes, feed names, values or scores — nothing that identifies the bank
    or carries its figures — so the bundle is safe to send off-site.
    """
    from kpigo.licence.state import current, install_fingerprint, product_version
    from kpigo.platform.actions.health import _broker, _database

    database_service, database, schema = _database()
    broker_service, depth = _broker()
    bundle: dict[str, Any] = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "product_version": product_version(),
        "install_fingerprint": install_fingerprint(),
        "services": {
            s.name: {"status": s.status, "detail": s.detail}
            for s in (database_service, broker_service)
        },
        "queue_depth": depth,
        "schema": schema,
        "database": None,
        "licence": None,
        "object_counts": _object_counts(org_id),
    }
    if database is not None:
        bundle["database"] = {
            "size_bytes": database.size_bytes,
            "pending_migrations": database.pending_migrations,
            # Table names are schema, not data; sizes and row estimates carry no values.
            "largest_tables": [
                {"table": t.table, "bytes": t.bytes, "rows_estimate": t.rows_estimate}
                for t in database.largest_tables
            ],
        }
    state = current(org_id)
    row = state.licence
    bundle["licence"] = {
        "state": state.state,
        "mode": state.mode,
        "max_major_version": row.max_major_version if row else None,
        "instance_kind": row.instance_kind if row else None,
    }
    return bundle


def latest_ok_backup(org_id: str) -> Any:
    """The most recent successful backup for the org, or ``None``.

    The update service's mandatory-backup gate reads this to answer "is there a
    fresh, verified backup?" before it will apply an upgrade (TDD §12.1 step 4).
    """
    from kpigo.platform.models import BackupRun

    return BackupRun.objects.filter(org_id=org_id, outcome="ok").order_by("-created_at").first()
