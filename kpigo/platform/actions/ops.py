"""Operations actions: backup, backup history, and the diagnostic bundle.

These are the install team's recovery and support tools (PRD OP-4, OP-5). A
backup is a mutating action so it can be scheduled (``KPIGO_SCHEDULED_ACTIONS``)
and audited like any other; the diagnostic bundle is read-only and carries no
client data, so it is safe to hand to kpiGo support.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from django.conf import settings
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, action
from kpigo.platform import backup as backup_mod


class BackupIn(BaseModel):
    kind: Literal["scheduled", "manual", "pre_upgrade"] = "manual"


class BackupRunOut(BaseModel):
    backup_id: str
    kind: str
    outcome: str
    created_at: datetime
    product_version: str
    config_version: int
    artefact_path: str
    sha256: str
    size_bytes: int
    detail: str = ""


@action(
    name="system.backup",
    summary="Take a backup: a consistent database dump and a configuration summary.",
    schema=BackupIn,
    output=BackupRunOut,
    permission="system.backup.manage",
    read_only=False,
    audit="system.backup.taken",
    http={"method": "POST", "path": "/ops/backup"},
    example={"kind": "manual"},
)
def take_backup(params: BackupIn, ctx: ActionContext) -> BackupRunOut:
    from kpigo.platform.models import BackupRun

    directory = Path(str(settings.KPIGO_BACKUP_DIR))
    try:
        result = backup_mod.run_backup(ctx.org_id, directory)
    except Exception as exc:
        row = BackupRun.objects.create(
            org_id=ctx.org_id,
            kind=params.kind,
            outcome="failed",
            product_version=backup_mod.config_summary(ctx.org_id)["product_version"],
            config_version=0,
            artefact_path="",
            detail=str(exc)[:500],
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        ctx.audit("system.backup.failed", backup_id=str(row.backup_id), detail=row.detail)
        return _to_out(row)

    row = BackupRun.objects.create(
        org_id=ctx.org_id,
        kind=params.kind,
        outcome=result.outcome,
        product_version=result.product_version,
        config_version=result.config_version,
        artefact_path=result.artefact_path,
        sha256=result.sha256,
        size_bytes=result.size_bytes,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    ctx.audit(
        "system.backup.taken",
        backup_id=str(row.backup_id),
        kind=row.kind,
        size_bytes=row.size_bytes,
    )
    return _to_out(row)


class BackupListIn(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class BackupListOut(BaseModel):
    backups: list[BackupRunOut]
    latest_ok_at: datetime | None


@action(
    name="system.backup.list",
    summary="Recent backups and when the last good one ran (for the health page).",
    schema=BackupListIn,
    output=BackupListOut,
    permission="system.backup.view",
    read_only=True,
    http={"method": "GET", "path": "/ops/backups"},
    example={"limit": 20},
)
def list_backups(params: BackupListIn, ctx: ActionContext) -> BackupListOut:
    from kpigo.platform.models import BackupRun

    rows = list(BackupRun.objects.filter(org_id=ctx.org_id).order_by("-created_at")[: params.limit])
    latest_ok = backup_mod.latest_ok_backup(ctx.org_id)
    return BackupListOut(
        backups=[_to_out(r) for r in rows],
        latest_ok_at=latest_ok.created_at if latest_ok else None,
    )


class DiagnosticsIn(BaseModel):
    pass


class DiagnosticsOut(BaseModel):
    bundle: dict[str, Any]


@action(
    name="system.diagnostics",
    summary="A diagnostic bundle for kpiGo support: versions, sizes and status, no client data.",
    schema=DiagnosticsIn,
    output=DiagnosticsOut,
    permission="system.diagnostics.view",
    read_only=True,
    http={"method": "GET", "path": "/ops/diagnostics"},
    example={},
)
def diagnostics(params: DiagnosticsIn, ctx: ActionContext) -> DiagnosticsOut:
    return DiagnosticsOut(bundle=backup_mod.diagnostics(ctx.org_id))


def _to_out(row: Any) -> BackupRunOut:
    return BackupRunOut(
        backup_id=str(row.backup_id),
        kind=row.kind,
        outcome=row.outcome,
        created_at=row.created_at,
        product_version=row.product_version,
        config_version=row.config_version,
        artefact_path=row.artefact_path,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        detail=row.detail,
    )
