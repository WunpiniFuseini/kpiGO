"""Update-service actions: inspect a staged bundle, authorise an upgrade, read the ledger.

``system.update.check`` is read-only — it runs every gate and reports, so an
operator can see a bundle is good before touching the install. ``system.update.apply``
is the audited authorisation: it re-checks the bundle, takes the mandatory
pre-upgrade backup, writes a ``VersionHistory`` row, and hands the operator the
remaining mechanical steps. Loading images and running migrations are not an
action's to do (maintenance mode is env-driven); ``scripts/apply_update.sh`` wraps
those around this action.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, action
from kpigo.action.errors import InvalidInput
from kpigo.platform import backup as backup_mod
from kpigo.platform import updates as updates_mod


class UpdateCheckIn(BaseModel):
    pass


class UpdateCheckOut(BaseModel):
    found: bool
    installed_version: str
    to_version: str
    from_version: str
    min_from_version: str
    key_id: str
    signature_ok: bool
    artefacts_ok: bool
    entitled: bool
    ceiling: int | None
    path_ok: bool
    is_upgrade: bool
    ready: bool
    reason: str


def _to_check_out(check: updates_mod.BundleCheck) -> UpdateCheckOut:
    return UpdateCheckOut(
        found=check.found,
        installed_version=check.installed_version,
        to_version=check.to_version,
        from_version=check.from_version,
        min_from_version=check.min_from_version,
        key_id=check.key_id,
        signature_ok=check.signature_ok,
        artefacts_ok=check.artefacts_ok,
        entitled=check.entitled,
        ceiling=check.ceiling,
        path_ok=check.path_ok,
        is_upgrade=check.is_upgrade,
        ready=check.ready,
        reason=check.reason,
    )


@action(
    name="system.update.check",
    summary="Inspect the staged update bundle: signature, checksums, entitlement and path.",
    schema=UpdateCheckIn,
    output=UpdateCheckOut,
    permission="system.update.view",
    read_only=True,
    http={"method": "GET", "path": "/ops/update/check"},
    example={},
)
def check_update(params: UpdateCheckIn, ctx: ActionContext) -> UpdateCheckOut:
    return _to_check_out(updates_mod.check_bundle(ctx.org_id))


class UpdateApplyIn(BaseModel):
    confirm_version: str = Field(min_length=1, max_length=40)


class VersionHistoryOut(BaseModel):
    version_id: str
    from_version: str
    to_version: str
    outcome: str
    key_id: str
    backup_id: str | None
    created_at: datetime
    detail: str = ""


class UpdateApplyOut(BaseModel):
    authorised: bool
    record: VersionHistoryOut
    backup_id: str | None
    next_steps: list[str]
    message: str


_NEXT_STEPS = [
    "Load the bundle's images: scripts/load_bundle.sh <bundle-dir>",
    "Put kpiGo in maintenance: set KPIGO_MAINTENANCE_MODE=1 and restart the app.",
    "Apply migrations: docker compose exec app python manage.py migrate",
    "Clear maintenance: set KPIGO_MAINTENANCE_MODE=0 and restart.",
    "Confirm the health page is green and the version has advanced.",
]


@action(
    name="system.update.apply",
    summary="Authorise a staged upgrade: re-check, take the mandatory pre-upgrade backup, log it.",
    schema=UpdateApplyIn,
    output=UpdateApplyOut,
    permission="system.update.manage",
    read_only=False,
    audit="system.update.authorised",
    http={"method": "POST", "path": "/ops/update/apply"},
    example={"confirm_version": "1.2.0"},
)
def apply_update(params: UpdateApplyIn, ctx: ActionContext) -> UpdateApplyOut:
    from kpigo.platform.models import BackupRun, VersionHistory

    check = updates_mod.check_bundle(ctx.org_id)
    if not check.ready:
        raise InvalidInput(f"This bundle cannot be applied: {check.reason}")
    if params.confirm_version != check.to_version:
        raise InvalidInput(
            f"The staged bundle is {check.to_version}, not {params.confirm_version}; "
            "re-check before applying."
        )

    # Mandatory pre-upgrade backup: an authorisation without a fresh restore point
    # is refused, so there is always something to fall back to (TDD §12.1 step 4).
    directory = Path(str(settings.KPIGO_BACKUP_DIR))
    try:
        result = backup_mod.run_backup(ctx.org_id, directory)
    except Exception as exc:
        backup_row = BackupRun.objects.create(
            org_id=ctx.org_id,
            kind="pre_upgrade",
            outcome="failed",
            product_version=check.installed_version,
            config_version=0,
            artefact_path="",
            detail=str(exc)[:500],
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        record = VersionHistory.objects.create(
            org_id=ctx.org_id,
            from_version=check.installed_version,
            to_version=check.to_version,
            outcome="failed",
            key_id=check.key_id,
            backup_id=None,
            detail=f"Pre-upgrade backup failed: {str(exc)[:400]}",
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        ctx.audit(
            "system.update.backup_failed",
            to_version=check.to_version,
            backup_id=str(backup_row.backup_id),
        )
        return UpdateApplyOut(
            authorised=False,
            record=_to_history_out(record),
            backup_id=None,
            next_steps=[],
            message="Refused: the mandatory pre-upgrade backup failed. Fix backups and retry.",
        )

    backup_row = BackupRun.objects.create(
        org_id=ctx.org_id,
        kind="pre_upgrade",
        outcome=result.outcome,
        product_version=result.product_version,
        config_version=result.config_version,
        artefact_path=result.artefact_path,
        sha256=result.sha256,
        size_bytes=result.size_bytes,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    record = VersionHistory.objects.create(
        org_id=ctx.org_id,
        from_version=check.installed_version,
        to_version=check.to_version,
        outcome="authorised",
        key_id=check.key_id,
        backup_id=backup_row.backup_id,
        detail=f"Pre-upgrade backup {backup_row.backup_id} taken; bundle verified.",
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    ctx.audit(
        "system.update.authorised",
        from_version=check.installed_version,
        to_version=check.to_version,
        key_id=check.key_id,
        backup_id=str(backup_row.backup_id),
    )
    return UpdateApplyOut(
        authorised=True,
        record=_to_history_out(record),
        backup_id=str(backup_row.backup_id),
        next_steps=_NEXT_STEPS,
        message=(
            f"Authorised {check.to_version}. A pre-upgrade backup was taken; "
            "run the steps below on the host to apply it."
        ),
    )


class UpdateHistoryIn(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class UpdateHistoryOut(BaseModel):
    updates: list[VersionHistoryOut]


@action(
    name="system.update.history",
    summary="The ledger of upgrades authorised on this install, newest first.",
    schema=UpdateHistoryIn,
    output=UpdateHistoryOut,
    permission="system.update.view",
    read_only=True,
    http={"method": "GET", "path": "/ops/update/history"},
    example={"limit": 20},
)
def update_history(params: UpdateHistoryIn, ctx: ActionContext) -> UpdateHistoryOut:
    from kpigo.platform.models import VersionHistory

    rows = list(
        VersionHistory.objects.filter(org_id=ctx.org_id).order_by("-created_at")[: params.limit]
    )
    return UpdateHistoryOut(updates=[_to_history_out(r) for r in rows])


def _to_history_out(row: Any) -> VersionHistoryOut:
    return VersionHistoryOut(
        version_id=str(row.version_id),
        from_version=row.from_version,
        to_version=row.to_version,
        outcome=row.outcome,
        key_id=row.key_id,
        backup_id=str(row.backup_id) if row.backup_id else None,
        created_at=row.created_at,
        detail=row.detail,
    )
