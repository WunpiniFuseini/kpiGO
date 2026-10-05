"""Overrides and exemptions (PRD SC-5, Scope §6.4).

An override changes one input (target, weight, cap, actual or target type) for
a subject, a profile or a dimension member over a range of periods. It is the
most audited object in the product: a reason is mandatory, it is requested by
one person and approved by another, and only an approved override moves a
score. Precedence is subject > profile > dimension, for the overlapping periods
only; the engine applies it (``kpigo.scorecards.engine.winning``).

A line manager may request an override for a subject they can see; profile and
dimension overrides change many people's scores and need Scorecards
configuration rights. Periods from ``closing`` on are not open to overrides:
changing a published month is a restatement (Scope §7.4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, get_args

from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints, model_validator

from kpigo.access.models import AppUser
from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, OutOfScope, action
from kpigo.hierarchy.models import Subject
from kpigo.metrics.actions.metric import MetricCode
from kpigo.periods.models import PeriodStatus
from kpigo.platform.vocab import PeriodKey
from kpigo.scorecards import roster
from kpigo.scorecards.config import LOCKED
from kpigo.scorecards.models import OVERRIDE_DIMENSIONS, TARGET_TYPES, Override

PRODUCT = "scorecards"
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
ScopeType = Literal["subject", "profile", "dimension"]
ChangeType = Literal["target", "weight", "cap", "actual", "target_type"]
OverrideStatus = Literal["pending", "approved", "rejected", "withdrawn", "revoked"]
TargetType = Literal["monthly", "yearly", "cumulative", "quarterly", "prorated"]
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"


class OverrideOut(BaseModel):
    override_id: uuid.UUID
    scope_type: str
    scope_code: str
    # Who or what the scope names: the subject's staff number and name, else the code.
    scope_label: str
    metric_code: str
    metric_name: str
    period_from: str
    period_to: str | None
    change_type: str
    override_value: Decimal | None
    override_text: str | None
    reason: str
    status: str
    requested_by: int | None
    requested_by_name: str | None
    requested_at: datetime
    approved_by: int | None
    approved_by_name: str | None
    approved_at: datetime | None
    decision_note: str
    ended_by: int | None
    ended_at: datetime | None
    # True when the caller requested it: they may withdraw it and may not approve it.
    mine: bool


@dataclass(frozen=True)
class _Names:
    scopes: dict[str, str]
    people: dict[int, str]


def _names(rows: list[Override]) -> _Names:
    ids = [o.scope_code for o in rows if o.scope_type == "subject"]
    users = {u for o in rows for u in (o.requested_by, o.approved_by) if u is not None}
    return _Names(
        scopes={
            str(s.subject_id): f"{s.staff_no} · {s.full_name}"
            for s in Subject.objects.filter(subject_id__in=ids)
        },
        people=dict(
            AppUser.objects.filter(auth_user_id__in=users).values_list(
                "auth_user_id", "display_name"
            )
        ),
    )


def _out(o: Override, ctx: ActionContext, names: _Names | None = None) -> OverrideOut:
    names = names if names is not None else _names([o])
    return OverrideOut(
        override_id=o.override_id,
        scope_type=o.scope_type,
        scope_code=o.scope_code,
        scope_label=names.scopes.get(o.scope_code, o.scope_code),
        metric_code=o.metric.metric_code,
        metric_name=o.metric.display_name,
        period_from=o.period_from,
        period_to=o.period_to,
        change_type=o.change_type,
        override_value=o.override_value,
        override_text=o.override_text,
        reason=o.reason,
        status=o.status,
        requested_by=o.requested_by,
        requested_by_name=names.people.get(o.requested_by) if o.requested_by else None,
        requested_at=o.created_at,
        approved_by=o.approved_by,
        approved_by_name=names.people.get(o.approved_by) if o.approved_by else None,
        approved_at=o.approved_at,
        decision_note=o.decision_note,
        ended_by=o.ended_by,
        ended_at=o.ended_at,
        mine=o.requested_by is not None and o.requested_by == ctx.user_id,
    )


def _locked(org_id: str, period_from: str, period_to: str | None) -> list[str]:
    return sorted(
        PeriodStatus.objects.filter(
            org_id=org_id,
            product=PRODUCT,
            period_key__gte=period_from,
            period_key__lte=period_to or period_from,
            status__in=sorted(LOCKED),
        ).values_list("period_key", flat=True)
    )


def _refuse_locked(org_id: str, period_from: str, period_to: str | None) -> None:
    locked = _locked(org_id, period_from, period_to)
    if locked:
        raise Conflict(
            "Overrides cannot touch a period from closing on; a published month changes "
            f"only by restatement. Locked: {', '.join(locked)}."
        )


def _get(org_id: str, override_id: uuid.UUID) -> Override:
    o = (
        Override.objects.select_for_update(of=("self",))
        .select_related("metric")
        .filter(org_id=org_id, override_id=override_id)
        .first()
    )
    if o is None:
        raise NotFound("No such override.")
    return o


def _visible(ctx: ActionContext, o: Override) -> bool:
    return o.scope_type != "subject" or ctx.visible_subjects.contains(o.scope_code)


# ── request ─────────────────────────────────────────────────────────────────


class OverrideRequestIn(BaseModel):
    scope_type: ScopeType
    # subject: subject_id or staff number; profile: profile_code;
    # dimension: ``branch:<code>``, ``region:<code>``, ``segment:<code>`` or ``portfolio:<code>``.
    scope_code: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    metric_code: MetricCode
    period_from: PeriodKey
    # Inclusive; leave empty for the single period ``period_from``.
    period_to: PeriodKey | None = None
    change_type: ChangeType
    override_value: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    target_type: TargetType | None = None
    reason: Reason

    @model_validator(mode="after")
    def _shape(self) -> OverrideRequestIn:
        if self.period_to is not None and self.period_to < self.period_from:
            raise ValueError("period_to must not be before period_from")
        if self.change_type == "target_type":
            if self.target_type is None:
                raise ValueError("a target_type override needs target_type")
            if self.override_value is not None:
                raise ValueError("a target_type override carries no override_value")
            return self
        if self.target_type is not None:
            raise ValueError("target_type is only for a target_type override")
        if self.override_value is None:
            raise ValueError(f"a {self.change_type} override needs override_value")
        if self.change_type == "target" and self.override_value <= 0:
            raise ValueError("an overridden target must be greater than zero")
        if self.change_type in ("weight", "cap") and self.override_value < 0:
            raise ValueError(f"an overridden {self.change_type} cannot be negative")
        if self.change_type in ("weight", "cap") and self.override_value >= 1000:
            raise ValueError(f"an overridden {self.change_type} must be under 1000")
        return self


def _resolve_scope(params: OverrideRequestIn, ctx: ActionContext) -> str:
    if params.scope_type == "subject":
        code = params.scope_code
        try:
            found = Subject.objects.filter(org_id=ctx.org_id, subject_id=uuid.UUID(code)).first()
        except ValueError:
            found = Subject.objects.filter(org_id=ctx.org_id, staff_no=code).first()
        if found is None:
            raise InvalidInput(f"No subject '{code}'.")
        subject_id = str(found.subject_id)
        if not ctx.visible_subjects.contains(subject_id):
            raise OutOfScope("That subject is outside your visibility.")
        return subject_id
    if not ctx.has("scorecard.config.manage"):
        raise OutOfScope(
            "Profile and dimension overrides change many people's scores; they need "
            "Scorecards configuration rights. Request a subject override instead."
        )
    if params.scope_type == "dimension":
        dim, _, member = params.scope_code.partition(":")
        if dim not in OVERRIDE_DIMENSIONS or not member:
            raise InvalidInput(
                "A dimension scope is '<dimension>:<code>' with dimension one of "
                f"{', '.join(OVERRIDE_DIMENSIONS)}."
            )
    elif params.scope_code not in roster.known_profiles(ctx.org_id):
        raise InvalidInput(f"No profile '{params.scope_code}'.")
    return params.scope_code


@action(
    name="override.request",
    summary="Request an override of a target, weight, cap, actual or target type, with a reason.",
    schema=OverrideRequestIn,
    output=OverrideOut,
    permission="override.request",
    read_only=False,
    module="scorecards",
    audit="override.requested",
    example={
        "scope_type": "subject",
        "scope_code": "E1001",
        "metric_code": "casa_growth",
        "period_from": "202610",
        "change_type": "target",
        "override_value": "80",
        "reason": "Branch closed for refurbishment for two weeks.",
    },
)
def request_override(params: OverrideRequestIn, ctx: ActionContext) -> OverrideOut:
    scope_code = _resolve_scope(params, ctx)
    metric = roster.metric_in_force(ctx.org_id, params.metric_code, params.period_from)
    if metric is None or not metric.bindings.filter(product=PRODUCT, is_active=True).exists():
        raise InvalidInput(
            f"No Scorecards metric '{params.metric_code}' in force in {params.period_from}."
        )
    _refuse_locked(ctx.org_id, params.period_from, params.period_to)
    o = Override.objects.create(
        org_id=ctx.org_id,
        scope_type=params.scope_type,
        scope_code=scope_code,
        metric=metric,
        product=PRODUCT,
        period_from=params.period_from,
        period_to=params.period_to,
        change_type=params.change_type,
        override_value=params.override_value,
        override_text=params.target_type,
        reason=params.reason,
        requested_by=ctx.user_id,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    o.refresh_from_db()
    return _out(o, ctx)


# ── decisions ───────────────────────────────────────────────────────────────


class OverrideDecideIn(BaseModel):
    override_id: uuid.UUID
    note: Note = ""


class OverrideRejectIn(BaseModel):
    override_id: uuid.UUID
    note: Reason


@action(
    name="override.approve",
    summary="Approve someone else's pending override; it applies from now on.",
    schema=OverrideDecideIn,
    output=OverrideOut,
    permission="override.approve",
    read_only=False,
    module="scorecards",
    audit="override.approved",
    config_change=True,
    example={"override_id": EXAMPLE_ID},
)
def approve_override(params: OverrideDecideIn, ctx: ActionContext) -> OverrideOut:
    o = _get(ctx.org_id, params.override_id)
    if o.status != "pending":
        raise Conflict(f"This override is {o.status}, not pending.")
    if ctx.user_id is None or ctx.user_id == o.requested_by:
        raise Conflict("An override needs a second person: you cannot approve your own request.")
    if not _visible(ctx, o):
        raise OutOfScope("That subject is outside your visibility.")
    _refuse_locked(ctx.org_id, o.period_from, o.period_to)
    o.status = "approved"
    o.approved_by = ctx.user_id
    o.approved_at = timezone.now()
    o.decision_note = params.note
    o.updated_by = ctx.user_id
    o.updated_at = timezone.now()
    o.save()
    return _out(o, ctx)


@action(
    name="override.reject",
    summary="Reject a pending override, saying why.",
    schema=OverrideRejectIn,
    output=OverrideOut,
    permission="override.approve",
    read_only=False,
    module="scorecards",
    audit="override.rejected",
    example={"override_id": EXAMPLE_ID, "note": "Covered by the branch-wide exemption."},
)
def reject_override(params: OverrideRejectIn, ctx: ActionContext) -> OverrideOut:
    o = _get(ctx.org_id, params.override_id)
    if o.status != "pending":
        raise Conflict(f"This override is {o.status}, not pending.")
    if not _visible(ctx, o):
        raise OutOfScope("That subject is outside your visibility.")
    o.status = "rejected"
    o.decision_note = params.note
    o.ended_by = ctx.user_id
    o.ended_at = timezone.now()
    o.updated_by = ctx.user_id
    o.updated_at = timezone.now()
    o.save()
    return _out(o, ctx)


@action(
    name="override.withdraw",
    summary="Withdraw your own pending override request.",
    schema=OverrideDecideIn,
    output=OverrideOut,
    permission="override.request",
    read_only=False,
    module="scorecards",
    audit="override.withdrawn",
    example={"override_id": EXAMPLE_ID},
)
def withdraw_override(params: OverrideDecideIn, ctx: ActionContext) -> OverrideOut:
    o = _get(ctx.org_id, params.override_id)
    if o.status != "pending":
        raise Conflict(f"This override is {o.status}, not pending.")
    if o.requested_by != ctx.user_id:
        raise Conflict("Only the person who requested an override can withdraw it.")
    o.status = "withdrawn"
    o.decision_note = params.note
    o.ended_by = ctx.user_id
    o.ended_at = timezone.now()
    o.updated_by = ctx.user_id
    o.updated_at = timezone.now()
    o.save()
    return _out(o, ctx)


@action(
    name="override.revoke",
    summary="Stop an approved override applying, saying why; scores revert to the inputs beneath it.",
    schema=OverrideRejectIn,
    output=OverrideOut,
    permission="override.approve",
    read_only=False,
    module="scorecards",
    audit="override.revoked",
    config_change=True,
    example={"override_id": EXAMPLE_ID, "note": "Raised against the wrong branch."},
)
def revoke_override(params: OverrideRejectIn, ctx: ActionContext) -> OverrideOut:
    o = _get(ctx.org_id, params.override_id)
    if o.status != "approved":
        raise Conflict(f"This override is {o.status}; only an approved one can be revoked.")
    if not _visible(ctx, o):
        raise OutOfScope("That subject is outside your visibility.")
    _refuse_locked(ctx.org_id, o.period_from, o.period_to)
    o.status = "revoked"
    o.decision_note = params.note
    o.ended_by = ctx.user_id
    o.ended_at = timezone.now()
    o.updated_by = ctx.user_id
    o.updated_at = timezone.now()
    o.save()
    return _out(o, ctx)


# ── list ────────────────────────────────────────────────────────────────────


class OverrideListIn(BaseModel):
    status: OverrideStatus | None = None
    metric_code: MetricCode | None = None
    scope_type: ScopeType | None = None
    # Only overrides whose range covers this period.
    period_key: PeriodKey | None = None
    subject_id: uuid.UUID | None = None
    limit: int = Field(default=200, ge=1, le=1000)


class OverrideListOut(BaseModel):
    overrides: list[OverrideOut]
    pending: int
    # More matched than ``limit``. Subject overrides outside the caller's visibility
    # are never listed or counted.
    truncated: bool


@action(
    name="override.list",
    summary="Overrides, newest first, filtered by status, metric, scope or period.",
    schema=OverrideListIn,
    output=OverrideListOut,
    permission="override.view",
    read_only=True,
    module="scorecards",
    example={"status": "pending"},
)
def list_overrides(params: OverrideListIn, ctx: ActionContext) -> OverrideListOut:
    rows = Override.objects.filter(org_id=ctx.org_id, product=PRODUCT).select_related("metric")
    if params.status:
        rows = rows.filter(status=params.status)
    if params.metric_code:
        rows = rows.filter(metric__metric_code=params.metric_code)
    if params.scope_type:
        rows = rows.filter(scope_type=params.scope_type)
    if params.subject_id:
        rows = rows.filter(scope_type="subject", scope_code=str(params.subject_id))
    if params.period_key:
        p = params.period_key
        rows = rows.filter(period_from__lte=p).filter(
            Q(period_to__gte=p) | Q(period_to__isnull=True, period_from=p)
        )
    visible = [o for o in rows.order_by("-created_at", "override_id") if _visible(ctx, o)]
    shown = visible[: params.limit]
    names = _names(shown)
    return OverrideListOut(
        overrides=[_out(o, ctx, names) for o in shown],
        pending=sum(1 for o in visible if o.status == "pending"),
        truncated=len(visible) > params.limit,
    )


assert set(TARGET_TYPES) == set(get_args(TargetType))
