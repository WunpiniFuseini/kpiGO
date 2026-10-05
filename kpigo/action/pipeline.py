"""The wrapper every invocation passes through (TDD §3.4).

validate → authorise → narrow scope → licence gate → maintenance gate →
idempotency → approval intercept → run (→ bump config_version) → audit → span

These are properties of the pipeline, not of each action's diligence: an action
cannot be written that skips its permission check or its audit row.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from django.conf import settings
from django.db import transaction
from opentelemetry import trace
from pydantic import BaseModel, ValidationError

from kpigo.action.context import ActionContext, AuditEvent
from kpigo.action.definition import ActionDefinition
from kpigo.action.errors import (
    ActionError,
    InvalidInput,
    MaintenanceMode,
    OutOfScope,
    PermissionDenied,
)

logger = logging.getLogger("kpigo.action")
tracer = trace.get_tracer("kpigo.action")


class Proposal(BaseModel):
    """Returned instead of the action's output when maker-checker intercepts it."""

    approval_request_id: str
    action_name: str
    status: str = "pending"
    message: str = "Submitted for approval."


class _DryRunRollback(Exception):
    """Raised inside the transaction to discard a dry run's writes."""


def approval_enabled(approval_class: str, org_id: str | None = None) -> bool:
    """Maker-checker is per action class and off by default (PRD AD-3).

    ``APPROVAL_DEFAULT_ON`` classes (campaign budgets) ship on instead.

    An Admin turns a class on with ``approval.policy.set``; an install may also
    force classes on in settings, which the database cannot turn off.
    """
    enabled: Any = getattr(settings, "KPIGO_APPROVAL_CLASSES_ENABLED", ())
    if approval_class in set(enabled):
        return True
    from kpigo.platform.models import APPROVAL_DEFAULT_ON, ApprovalPolicy

    default = approval_class in APPROVAL_DEFAULT_ON
    if org_id is None:
        return default
    stored = (
        ApprovalPolicy.objects.filter(org_id=org_id, approval_class=approval_class)
        .values_list("enabled", flat=True)
        .first()
    )
    return default if stored is None else bool(stored)


def maintenance_mode() -> bool:
    return bool(getattr(settings, "KPIGO_MAINTENANCE_MODE", False))


def validate(definition: ActionDefinition, raw: Mapping[str, Any] | BaseModel) -> BaseModel:
    if isinstance(raw, definition.schema):
        return raw
    data = raw.model_dump() if isinstance(raw, BaseModel) else dict(raw)
    try:
        return definition.schema.model_validate(data)
    except ValidationError as exc:
        raise InvalidInput(
            f"Invalid input for '{definition.name}'.",
            detail=exc.errors(include_url=False, include_context=False),
        ) from None


def authorise(definition: ActionDefinition, ctx: ActionContext) -> None:
    """The permission check. The matrix test calls this exact function."""
    if definition.public:
        return
    if definition.permission not in ctx.permissions:
        raise PermissionDenied(
            f"'{definition.name}' requires permission '{definition.permission}'."
        )


def narrow_scope(definition: ActionDefinition, params: BaseModel, ctx: ActionContext) -> None:
    if definition.scope == "subject":
        subject_id = getattr(params, "subject_id", None)
        if subject_id is None or not ctx.visible_subjects.contains(str(subject_id)):
            raise OutOfScope(f"Subject is outside the caller's visibility for '{definition.name}'.")


def licence_gate(definition: ActionDefinition) -> None:
    """Grace and lock states, and the module list, of the installed licence."""
    from kpigo.licence.gate import check

    check(definition)


def invoke(
    definition: ActionDefinition,
    raw: Mapping[str, Any] | BaseModel,
    ctx: ActionContext,
) -> BaseModel:
    """Run an action through the full pipeline. Returns its output or a Proposal."""
    started = time.monotonic()
    with tracer.start_as_current_span(f"action {definition.name}") as span:
        span.set_attribute("kpigo.action", definition.name)
        span.set_attribute("kpigo.caller", ctx.caller)
        span.set_attribute("kpigo.request_id", ctx.request_id)
        params: BaseModel | None = None
        try:
            params = validate(definition, raw)
            authorise(definition, ctx)
            narrow_scope(definition, params, ctx)
            licence_gate(definition)
            if not definition.read_only and maintenance_mode():
                raise MaintenanceMode("The system is updating; changes are paused.")
            result, outcome = _execute(definition, params, ctx, started)
        except ActionError as exc:
            denied = isinstance(exc, PermissionDenied | OutOfScope)
            _fail(
                definition,
                ctx,
                params,
                span,
                started,
                outcome=exc.code,
                reason=exc.message,
                event="action.denied" if denied else "action.failed",
            )
            raise
        except Exception as exc:
            _fail(
                definition,
                ctx,
                params,
                span,
                started,
                outcome="error",
                reason=type(exc).__name__,
                event="action.failed",
            )
            raise
        span.set_attribute("kpigo.outcome", outcome)
        _log(definition, ctx, outcome, started)
        return result


def _execute(
    definition: ActionDefinition, params: BaseModel, ctx: ActionContext, started: float
) -> tuple[BaseModel, str]:
    from kpigo.platform.models import ActionIdempotency, ApprovalRequest

    if definition.read_only:
        output = _run_handler(definition, params, ctx)
        _write_audit(
            definition, ctx, params, event=definition.audit_event, outcome="ok", started=started
        )
        return output, "ok"

    key = definition.idempotency_key(params) if definition.idempotency_key else None
    refused: ActionError | None = None
    replaying_approval = ctx.approval is not None and ctx.approval.action_name == definition.name

    try:
        with transaction.atomic():
            if key is not None and not ctx.dry_run:
                prior = ActionIdempotency.objects.filter(
                    org_id=ctx.org_id, action_name=definition.name, key=key
                ).first()
                if prior is not None:
                    output = definition.output.model_validate(prior.result)
                    _write_audit(
                        definition,
                        ctx,
                        params,
                        event=definition.audit_event,
                        outcome="idempotent_replay",
                        started=started,
                        extra={"idempotency_key": key},
                    )
                    return output, "idempotent_replay"

            if (
                definition.requires_approval
                and approval_enabled(definition.requires_approval, ctx.org_id)
                and not replaying_approval
                and not ctx.dry_run
            ):
                request = ApprovalRequest.objects.create(
                    org_id=ctx.org_id,
                    action_name=definition.name,
                    payload=params.model_dump(mode="json"),
                    requested_by_id=ctx.user_id,
                    caller=ctx.caller,
                )
                proposal = Proposal(
                    approval_request_id=str(request.request_id), action_name=definition.name
                )
                _write_audit(
                    definition,
                    ctx,
                    params,
                    event="approval.requested",
                    outcome="pending_approval",
                    started=started,
                    object_type="approval_request",
                    object_id=str(request.request_id),
                )
                return proposal, "pending_approval"

            try:
                output = _run_handler(definition, params, ctx)
            except ActionError as exc:
                if not exc.commit_writes or ctx.dry_run:
                    raise
                # Leave the atomic block normally so the bookkeeping commits.
                refused = exc
            if refused is None:
                return _finish(definition, params, ctx, started, output, key)
    except _DryRunRollback as rollback:
        dry_output: BaseModel = rollback.args[0]
        ctx.pending_audit.clear()
        _write_audit(
            definition,
            ctx,
            params,
            event=definition.audit_event,
            outcome="dry_run",
            started=started,
        )
        return dry_output, "dry_run"
    assert refused is not None
    raise refused


def _finish(
    definition: ActionDefinition,
    params: BaseModel,
    ctx: ActionContext,
    started: float,
    output: BaseModel,
    key: str | None,
) -> tuple[BaseModel, str]:
    from kpigo.platform.models import ActionIdempotency

    version_extra: dict[str, Any] | None = None
    if definition.config_change:
        version_extra = {"config_version": _bump_config_version(ctx)}
    if key is not None and not ctx.dry_run:
        ActionIdempotency.objects.create(
            org_id=ctx.org_id,
            action_name=definition.name,
            key=key,
            result=output.model_dump(mode="json"),
            request_id=ctx.request_id,
        )
    if ctx.dry_run:
        raise _DryRunRollback(output)
    _write_audit(
        definition,
        ctx,
        params,
        event=definition.audit_event,
        outcome="ok",
        started=started,
        extra=version_extra,
    )
    return output, "ok"


def _bump_config_version(ctx: ActionContext) -> int:
    from kpigo.platform.config import bump_config_version

    return bump_config_version(ctx.org_id)


def _fail(
    definition: ActionDefinition,
    ctx: ActionContext,
    params: BaseModel | None,
    span: trace.Span,
    started: float,
    *,
    outcome: str,
    reason: str,
    event: str,
) -> None:
    span.set_attribute("kpigo.outcome", outcome)
    _log(definition, ctx, outcome, started)
    ctx.pending_audit.clear()
    _write_audit(
        definition,
        ctx,
        params,
        event=event,
        outcome=outcome,
        started=started,
        extra={"reason": reason},
    )


def _run_handler(definition: ActionDefinition, params: BaseModel, ctx: ActionContext) -> BaseModel:
    output = definition.handler(params, ctx)
    if not isinstance(output, definition.output):
        raise TypeError(
            f"Action '{definition.name}' returned {type(output).__name__}, "
            f"expected {definition.output.__name__}."
        )
    return output


def _write_audit(
    definition: ActionDefinition,
    ctx: ActionContext,
    params: BaseModel | None,
    *,
    event: str,
    outcome: str,
    started: float,
    object_type: str = "",
    object_id: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    from kpigo.platform.models import AuditLog

    base: dict[str, Any] = {
        "outcome": outcome,
        "params": params.model_dump(mode="json") if params is not None else None,
        "dry_run": ctx.dry_run,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    }
    if ctx.approval is not None:
        base["approval_request_id"] = ctx.approval.approval_request_id
        base["approved_by_id"] = ctx.approval.approved_by_id
    if extra:
        base.update(extra)
    events = [AuditEvent(event=event, payload=base)]
    events += [
        AuditEvent(event=e.event, payload={**e.payload, "outcome": outcome})
        for e in ctx.pending_audit
    ]
    ctx.pending_audit.clear()
    AuditLog.objects.bulk_create(
        [
            AuditLog(
                org_id=ctx.org_id,
                actor_user_id=ctx.user_id,
                caller=ctx.caller,
                action_name=definition.name,
                event=e.event,
                object_type=object_type,
                object_id=object_id,
                payload=e.payload,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
            )
            for e in events
        ]
    )


def _log(definition: ActionDefinition, ctx: ActionContext, outcome: str, started: float) -> None:
    logger.info(
        "action %s %s",
        definition.name,
        outcome,
        extra={
            "action_name": definition.name,
            "request_id": ctx.request_id,
            "caller": ctx.caller,
            "outcome": outcome,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        },
    )
