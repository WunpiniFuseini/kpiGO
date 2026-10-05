"""Manual metric input (PRD MI-1–MI-13; App Flow §7.6; TDD §5.5).

An Admin assigns each slice of a manual-input metric to a contributor: a named
account, or "the line manager of" a subject, resolved for the month. The
contributor's landing page lists what they owe and by when; they save drafts
and submit. A submitted value conforms to ``fact_actual_monthly`` for every
member of the slice and is scored like any actual, but stays hidden from
scorecards (other than an Admin's) until the month closes (MI-10).

Inputs still owed chase themselves up the escalation ladder (``input.remind``,
a scheduled job, in ``escalation.py``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints, model_validator

from kpigo.access.identity import app_user_for, role_codes, role_permissions
from kpigo.access.models import AppUser
from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, OutOfScope, action
from kpigo.hierarchy.models import Subject
from kpigo.hierarchy.scope import current_period_key
from kpigo.metrics.actions.metric import MetricCode
from kpigo.metrics.models import Metric
from kpigo.platform import mail
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import PeriodKey
from kpigo.scorecards import inputs, roster
from kpigo.scorecards.config import period_status
from kpigo.scorecards.cycles import last_day, shift
from kpigo.scorecards.models import (
    OVERRIDE_DIMENSIONS,
    InputAssignment,
    InputSchedule,
    InputSubmission,
)

PRODUCT = "scorecards"
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
ScopeType = Literal["subject", "profile", "dimension"]

_CONFLICTS = {
    "input_assignment_no_overlap": (
        "Someone already enters this metric for that slice over an overlapping period. "
        "End that assignment first."
    ),
    "input_assignment_range_valid": "effective_to must be after effective_from.",
}


def _manual_metric(org_id: str, code: str, day: date) -> Metric:
    metric = (
        Metric.objects.filter(org_id=org_id, metric_code=code).filter(roster.in_force(day)).first()
    )
    if metric is None:
        raise NotFound(f"No metric '{code}' in force on {day.isoformat()}.")
    if metric.collection_method != "manual_input":
        raise InvalidInput(
            f"'{code}' is collected by a feed. Set its collection to manual input in the "
            "metric registry first."
        )
    if not metric.bindings.filter(product=PRODUCT, is_active=True).exists():
        raise InvalidInput(f"'{code}' is not on Scorecards.")
    return metric


def _scope(ctx: ActionContext, scope_type: str, code: str) -> tuple[str, str]:
    """The stored scope code and a label people read."""
    if scope_type == "subject":
        try:
            found = Subject.objects.filter(org_id=ctx.org_id, subject_id=uuid.UUID(code)).first()
        except ValueError:
            found = Subject.objects.filter(org_id=ctx.org_id, staff_no=code).first()
        if found is None:
            raise InvalidInput(f"No subject '{code}'.")
        return str(found.subject_id), f"{found.full_name} · {found.staff_no}"
    if scope_type == "dimension":
        dim, _, member = code.partition(":")
        if dim not in OVERRIDE_DIMENSIONS or not member:
            raise InvalidInput(
                "A team slice is '<dimension>:<code>' with dimension one of "
                f"{', '.join(OVERRIDE_DIMENSIONS)}, e.g. 'branch:ACC'."
            )
        return code, f"{dim.title()} {member}"
    if code not in roster.known_profiles(ctx.org_id):
        raise InvalidInput(f"No profile '{code}'.")
    return code, f"Everyone on {code}"


def _labels(org_id: str, rows: list[InputAssignment]) -> dict[str, str]:
    subjects = {
        str(s.subject_id): f"{s.full_name} · {s.staff_no}"
        for s in Subject.objects.filter(
            org_id=org_id, subject_id__in=[r.scope_code for r in rows if r.scope_type == "subject"]
        )
    }
    out: dict[str, str] = {}
    for r in rows:
        if r.scope_type == "subject":
            out[str(r.assignment_id)] = subjects.get(r.scope_code, r.scope_code)
        elif r.scope_type == "dimension":
            dim, _, member = r.scope_code.partition(":")
            out[str(r.assignment_id)] = f"{dim.title()} {member}"
        else:
            out[str(r.assignment_id)] = f"Everyone on {r.scope_code}"
    return out


def _metric_names(org_id: str, codes: list[str], period_key: str) -> dict[str, Metric]:
    return roster.metrics_in_force(org_id, codes, period_key)


# ── assignment (Admin) ──────────────────────────────────────────────────────


class InputAssignmentOut(BaseModel):
    assignment_id: uuid.UUID
    metric_code: str
    metric_name: str
    scope_type: str
    scope_code: str
    scope_label: str
    assignee_type: str
    assignee_user_id: uuid.UUID | None
    assignee_role: str | None
    # Who owes it for the month asked about; null when a role resolves to nobody.
    contributor_name: str | None
    # How many people the value lands on this month.
    members: int
    effective_from: date
    effective_to: date | None
    # pending | draft | submitted | restated, for the month asked about.
    state: str
    submitted_at: datetime | None
    # How far up the escalation ladder the month's input has gone: 0 not yet,
    # 1 contributor reminded, 2 line manager told, 3 stakeholders told.
    escalation_step: int
    # Who the last rung tells for this slice; empty means the org's stakeholders.
    stakeholder_user_ids: list[uuid.UUID]
    stakeholder_names: list[str]


class InputAssignmentCreateIn(BaseModel):
    metric_code: MetricCode
    scope_type: ScopeType
    # subject: subject_id or staff number; profile: profile_code; dimension: ``branch:ACC``.
    scope_code: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    assignee_type: Literal["user", "role_relative"] = "user"
    assignee_user_id: uuid.UUID | None = None
    # role_relative: who enters a subject's value, resolved each month.
    assignee_role: Literal["line_manager_of"] | None = None
    # Defaults to the first day of the current month.
    effective_from: date | None = None

    @model_validator(mode="after")
    def _shape(self) -> InputAssignmentCreateIn:
        if self.assignee_type == "user":
            if self.assignee_user_id is None or self.assignee_role is not None:
                raise ValueError("a named-user assignment takes assignee_user_id only")
        else:
            if self.assignee_role is None or self.assignee_user_id is not None:
                raise ValueError("a role-relative assignment takes assignee_role only")
            if self.scope_type != "subject":
                raise ValueError(
                    "'line manager of' resolves per person, so it needs a subject slice; "
                    "name a user for a profile or team slice"
                )
        return self


def _assignment_out(
    org_id: str, rows: list[InputAssignment], period_key: str
) -> list[InputAssignmentOut]:
    labels = _labels(org_id, rows)
    names = _metric_names(org_id, sorted({r.metric_code for r in rows}), period_key)
    have = inputs.current(org_id, period_key, rows)
    steps = _steps(rows, period_key)
    named = {
        str(u.user_id): u.display_name
        for u in AppUser.objects.filter(
            org_id=org_id, user_id__in=[i for r in rows for i in _stakeholder_ids(r)]
        )
    }
    out: list[InputAssignmentOut] = []
    for r in rows:
        who = inputs.contributor(r, period_key)
        chosen = _stakeholder_ids(r)
        sub = have.get(str(r.assignment_id))
        metric = names.get(r.metric_code)
        out.append(
            InputAssignmentOut(
                assignment_id=r.assignment_id,
                metric_code=r.metric_code,
                metric_name=metric.display_name if metric else r.metric_code,
                scope_type=r.scope_type,
                scope_code=r.scope_code,
                scope_label=labels[str(r.assignment_id)],
                assignee_type=r.assignee_type,
                assignee_user_id=r.assignee_user_id,
                assignee_role=r.assignee_role,
                contributor_name=who.display_name if who else None,
                members=len(inputs.members(org_id, period_key, r.scope_type, r.scope_code)),
                effective_from=r.effective_from,
                effective_to=r.effective_to,
                state=sub.state if sub else "pending",
                submitted_at=sub.submitted_at if sub else None,
                escalation_step=steps.get(str(r.assignment_id), 0),
                stakeholder_user_ids=[uuid.UUID(i) for i in chosen],
                stakeholder_names=[named[i] for i in chosen if i in named],
            )
        )
    return out


def _steps(rows: list[InputAssignment], period_key: str) -> dict[str, int]:
    """How far up the ladder each slice's input has gone for the month."""
    return {
        str(s.input_assignment_id): s.last_reminder_step
        for s in InputSchedule.objects.filter(input_assignment__in=rows, period_key=period_key)
    }


def _stakeholder_ids(ia: InputAssignment) -> list[str]:
    return [str(i) for i in (ia.escalation_config or {}).get("stakeholder_user_ids") or []]


@action(
    name="input.assignment.create",
    summary="Assign a slice of a manual-input metric to a contributor.",
    schema=InputAssignmentCreateIn,
    output=InputAssignmentOut,
    permission="input.manage",
    read_only=False,
    module="scorecards",
    audit="input.assigned",
    example={
        "metric_code": "csat",
        "scope_type": "dimension",
        "scope_code": "branch:ACC",
        "assignee_type": "user",
        "assignee_user_id": EXAMPLE_ID,
    },
)
def create_assignment(params: InputAssignmentCreateIn, ctx: ActionContext) -> InputAssignmentOut:
    period_key = current_period_key(ctx.org_id)
    start = params.effective_from or date(int(period_key[:4]), int(period_key[4:]), 1)
    _manual_metric(ctx.org_id, params.metric_code, max(start, last_day(period_key)))
    scope_code, _ = _scope(ctx, params.scope_type, params.scope_code)
    user = None
    if params.assignee_user_id is not None:
        user = AppUser.objects.filter(
            org_id=ctx.org_id, user_id=params.assignee_user_id, status="active"
        ).first()
        if user is None:
            raise NotFound("No active account with that id.")
        if "input.submit" not in role_permissions(ctx.org_id, role_codes(user)):
            raise InvalidInput(
                f"{user.display_name} cannot enter inputs. Give them the Contributor role "
                "first (Administer → Users & access)."
            )
    with conflicts(_CONFLICTS):
        row = InputAssignment.objects.create(
            org_id=ctx.org_id,
            metric_code=params.metric_code,
            scope_type=params.scope_type,
            scope_code=scope_code,
            assignee_type=params.assignee_type,
            assignee_user=user,
            assignee_role=params.assignee_role,
            effective_from=start,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    row = InputAssignment.objects.select_related("assignee_user").get(pk=row.pk)
    return _assignment_out(ctx.org_id, [row], period_key)[0]


class InputAssignmentEndIn(BaseModel):
    assignment_id: uuid.UUID
    # The first day it no longer applies.
    effective_to: date


@action(
    name="input.assignment.end",
    summary="Stop a contributor's assignment from a date.",
    schema=InputAssignmentEndIn,
    output=InputAssignmentOut,
    permission="input.manage",
    read_only=False,
    module="scorecards",
    audit="input.assignment_ended",
    example={"assignment_id": EXAMPLE_ID, "effective_to": "2026-11-01"},
)
def end_assignment(params: InputAssignmentEndIn, ctx: ActionContext) -> InputAssignmentOut:
    row = (
        InputAssignment.objects.select_for_update(of=("self",))
        .filter(org_id=ctx.org_id, assignment_id=params.assignment_id)
        .first()
    )
    if row is None:
        raise NotFound("No such assignment.")
    if row.effective_to is not None and row.effective_to <= params.effective_to:
        raise Conflict(f"It already ends on {row.effective_to.isoformat()}.")
    row.effective_to = params.effective_to
    row.updated_by = ctx.user_id
    row.updated_at = timezone.now()
    with conflicts(_CONFLICTS):
        row.save(update_fields=["effective_to", "updated_by", "updated_at"])
    row = InputAssignment.objects.select_related("assignee_user").get(pk=row.pk)
    return _assignment_out(ctx.org_id, [row], current_period_key(ctx.org_id))[0]


class InputAssignmentListIn(BaseModel):
    period_key: PeriodKey | None = None


class UnassignedMetricOut(BaseModel):
    metric_code: str
    metric_name: str


class InputAssignmentListOut(BaseModel):
    period_key: str
    due_at: datetime
    locked: bool
    assignments: list[InputAssignmentOut]
    # Manual-input Scorecards metrics nobody is asked to enter this month.
    unassigned: list[UnassignedMetricOut]
    # Whether reminders also go by email (a relay is configured for this install).
    email_reminders: bool


@action(
    name="input.assignment.list",
    summary="Who enters each manual-input metric, and where each slice stands this month.",
    schema=InputAssignmentListIn,
    output=InputAssignmentListOut,
    permission="input.manage",
    read_only=True,
    module="scorecards",
    example={"period_key": "202610"},
)
def list_assignments(params: InputAssignmentListIn, ctx: ActionContext) -> InputAssignmentListOut:
    period_key = params.period_key or current_period_key(ctx.org_id)
    rows = inputs.assignments_in_force(ctx.org_id, period_key)
    manual = (
        Metric.objects.filter(
            org_id=ctx.org_id,
            collection_method="manual_input",
            status="active",
            bindings__product=PRODUCT,
            bindings__is_active=True,
        )
        .filter(roster.in_force(last_day(period_key)))
        .order_by("metric_code")
    )
    covered = {r.metric_code for r in rows}
    return InputAssignmentListOut(
        period_key=period_key,
        due_at=inputs.due_at(ctx.org_id, period_key),
        locked=inputs.locked(ctx.org_id, period_key),
        assignments=_assignment_out(ctx.org_id, rows, period_key),
        unassigned=[
            UnassignedMetricOut(metric_code=m.metric_code, metric_name=m.display_name)
            for m in manual
            if m.metric_code not in covered
        ],
        email_reminders=mail.enabled(),
    )


# ── the contributor's month ─────────────────────────────────────────────────


class InputTaskOut(BaseModel):
    assignment_id: uuid.UUID
    metric_code: str
    metric_name: str
    unit: str
    decimal_places: int
    direction: str
    description: str
    scope_type: str
    scope_label: str
    # How many people this value lands on.
    members: int
    # pending | draft | submitted | restated
    state: str
    value: Decimal | None
    note: str
    submitted_at: datetime | None
    version: int | None
    reminded_at: datetime | None
    # How far up the escalation ladder it has gone: 0 not yet, 1 you were
    # reminded, 2 your line manager was told, 3 the stakeholders were told.
    escalation_step: int


class InputTaskListIn(BaseModel):
    # Defaults to the earliest month still open for input: last month until its
    # deadline passes, then this month.
    period_key: PeriodKey | None = None


class InputTaskListOut(BaseModel):
    period_key: str
    due_at: datetime
    # Past the deadline: values are read-only unless the month is being restated.
    locked: bool
    period_status: str
    # Locked, but the month is being restated: a change writes a new version.
    restating: bool
    tasks: list[InputTaskOut]
    # Other months with something still owed by this account.
    other_periods: list[str]


def _default_period(org_id: str) -> str:
    previous = shift(current_period_key(org_id), -1)
    return current_period_key(org_id) if inputs.locked(org_id, previous) else previous


def _account(ctx: ActionContext) -> AppUser:
    account = app_user_for(ctx.user, ctx.org_id)
    if account is None:
        raise Conflict("This sign-in has no kpiGo account, so no inputs are assigned to it.")
    return account


def tasks_out(ctx: ActionContext, account: AppUser, period_key: str) -> InputTaskListOut:
    org_id = ctx.org_id
    rows = inputs.owed_by(account, period_key)
    labels = _labels(org_id, rows)
    metrics = _metric_names(org_id, sorted({r.metric_code for r in rows}), period_key)
    have = inputs.current(org_id, period_key, rows)
    schedules = InputSchedule.objects.filter(input_assignment__in=rows, period_key=period_key)
    reminded = {str(s.input_assignment_id): s.last_reminded_at for s in schedules}
    steps = {str(s.input_assignment_id): s.last_reminder_step for s in schedules}
    tasks: list[InputTaskOut] = []
    for r in rows:
        m = metrics.get(r.metric_code)
        if m is None:
            continue
        sub = have.get(str(r.assignment_id))
        tasks.append(
            InputTaskOut(
                assignment_id=r.assignment_id,
                metric_code=r.metric_code,
                metric_name=m.display_name,
                unit=m.unit,
                decimal_places=m.decimal_places,
                direction=m.direction,
                description=m.computation_note or "",
                scope_type=r.scope_type,
                scope_label=labels[str(r.assignment_id)],
                members=len(inputs.members(org_id, period_key, r.scope_type, r.scope_code)),
                state=sub.state if sub else "pending",
                value=sub.value if sub else None,
                note=sub.note if sub else "",
                submitted_at=sub.submitted_at if sub else None,
                version=sub.version if sub else None,
                reminded_at=reminded.get(str(r.assignment_id)),
                escalation_step=steps.get(str(r.assignment_id), 0),
            )
        )
    status = period_status(org_id, period_key)
    others = []
    for key in (shift(current_period_key(org_id), -1), current_period_key(org_id)):
        if key == period_key or inputs.locked(org_id, key):
            continue
        owed = inputs.owed_by(account, key)
        done = inputs.current(org_id, key, owed)
        if any(
            str(r.assignment_id) not in done or done[str(r.assignment_id)].state == "draft"
            for r in owed
        ):
            others.append(key)
    return InputTaskListOut(
        period_key=period_key,
        due_at=inputs.due_at(org_id, period_key),
        locked=inputs.locked(org_id, period_key),
        period_status=status,
        restating=status == "restating",
        tasks=tasks,
        other_periods=others,
    )


@action(
    name="input.task.list",
    summary="The manual inputs you owe for a month, and where each stands.",
    schema=InputTaskListIn,
    output=InputTaskListOut,
    permission="input.submit",
    read_only=True,
    module="scorecards",
    example={"period_key": "202609"},
)
def list_tasks(params: InputTaskListIn, ctx: ActionContext) -> InputTaskListOut:
    account = _account(ctx)
    return tasks_out(ctx, account, params.period_key or _default_period(ctx.org_id))


class InputEntryIn(BaseModel):
    assignment_id: uuid.UUID
    value: Decimal | None = Field(default=None, max_digits=18)
    note: Note = ""


class InputSaveIn(BaseModel):
    period_key: PeriodKey
    entries: list[InputEntryIn] = Field(min_length=1, max_length=500)


class InputSubmitIn(InputSaveIn):
    @model_validator(mode="after")
    def _values(self) -> InputSubmitIn:
        if any(e.value is None for e in self.entries):
            raise ValueError("every submitted entry needs a value; save it as a draft instead")
        return self


def _owned(
    ctx: ActionContext, account: AppUser, period_key: str, ids: list[uuid.UUID]
) -> dict[str, InputAssignment]:
    mine = {str(r.assignment_id): r for r in inputs.owed_by(account, period_key)}
    if ctx.has("input.manage"):
        mine |= {
            str(r.assignment_id): r for r in inputs.assignments_in_force(ctx.org_id, period_key)
        }
    missing = [str(i) for i in ids if str(i) not in mine]
    if missing:
        raise OutOfScope("You are not the contributor for one or more of these inputs this month.")
    return mine


def _window(ctx: ActionContext, period_key: str) -> bool:
    """Whether a write is a restatement; refuses one the calendar does not allow."""
    if period_key > current_period_key(ctx.org_id):
        raise Conflict(f"{period_key} has not started; inputs open when the month does.")
    status = period_status(ctx.org_id, period_key)
    if status in ("closing", "closed"):
        raise Conflict(
            f"{period_key} is {status}. A correction needs the month restated first "
            "(Administer → Business calendar)."
        )
    if not inputs.locked(ctx.org_id, period_key):
        return False
    if status == "restating":
        return True
    raise Conflict(
        f"Inputs for {period_key} locked at the deadline. A correction is a restatement: "
        "ask an Admin to restate the month."
    )


def _check(entries: list[InputEntryIn]) -> None:
    for e in entries:
        if e.value is not None and (problem := inputs.check_value(e.value)):
            raise InvalidInput(problem)


@action(
    name="input.save",
    summary="Save manual inputs as drafts; nothing reaches a scorecard until submitted.",
    schema=InputSaveIn,
    output=InputTaskListOut,
    permission="input.submit",
    read_only=False,
    module="scorecards",
    example={"period_key": "202609", "entries": [{"assignment_id": EXAMPLE_ID, "value": "4.2"}]},
)
def save_inputs(params: InputSaveIn, ctx: ActionContext) -> InputTaskListOut:
    account = _account(ctx)
    if _window(ctx, params.period_key):
        raise Conflict("While a month is being restated, submit the corrected value directly.")
    _check(params.entries)
    owned = _owned(ctx, account, params.period_key, [e.assignment_id for e in params.entries])
    have = inputs.current(ctx.org_id, params.period_key, list(owned.values()))
    metrics = _metric_names(
        ctx.org_id,
        sorted({owned[str(e.assignment_id)].metric_code for e in params.entries}),
        params.period_key,
    )
    for e in params.entries:
        ia = owned[str(e.assignment_id)]
        sub = have.get(str(ia.assignment_id))
        if sub is not None and sub.state != "draft":
            raise Conflict(
                f"{ia.metric_code} is already submitted. Change it and submit again; "
                "a submitted value cannot go back to a draft."
            )
        if sub is None:
            InputSubmission.objects.create(
                org_id=ctx.org_id,
                input_assignment=ia,
                metric=metrics[ia.metric_code],
                metric_code=ia.metric_code,
                scope_type=ia.scope_type,
                scope_code=ia.scope_code,
                period_key=params.period_key,
                value=e.value,
                note=e.note,
                state="draft",
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
        else:
            sub.value, sub.note = e.value, e.note
            sub.updated_by, sub.updated_at = ctx.user_id, timezone.now()
            sub.save(update_fields=["value", "note", "updated_by", "updated_at"])
    return tasks_out(ctx, account, params.period_key)


@action(
    name="input.submit",
    summary="Submit manual inputs: each lands as the actual for everyone in its slice.",
    schema=InputSubmitIn,
    output=InputTaskListOut,
    permission="input.submit",
    read_only=False,
    module="scorecards",
    requires_approval="manual_input",
    audit="input.submitted",
    example={"period_key": "202609", "entries": [{"assignment_id": EXAMPLE_ID, "value": "4.2"}]},
)
def submit_inputs(params: InputSubmitIn, ctx: ActionContext) -> InputTaskListOut:
    account = _account(ctx)
    restating = _window(ctx, params.period_key)
    _check(params.entries)
    owned = _owned(ctx, account, params.period_key, [e.assignment_id for e in params.entries])
    have = inputs.current(ctx.org_id, params.period_key, list(owned.values()))
    metrics = _metric_names(
        ctx.org_id,
        sorted({owned[str(e.assignment_id)].metric_code for e in params.entries}),
        params.period_key,
    )
    now = timezone.now()
    for e in params.entries:
        ia = owned[str(e.assignment_id)]
        metric = metrics.get(ia.metric_code)
        if metric is None:
            raise Conflict(f"{ia.metric_code} is not in force for {params.period_key}.")
        sub = have.get(str(ia.assignment_id))
        if restating and sub is not None and sub.state != "draft":
            # After the deadline the old value stays on record beside the new one.
            InputSubmission.objects.filter(pk=sub.pk).update(is_current=False)
            sub = InputSubmission.objects.create(
                org_id=ctx.org_id,
                input_assignment=ia,
                metric=metric,
                metric_code=ia.metric_code,
                scope_type=ia.scope_type,
                scope_code=ia.scope_code,
                period_key=params.period_key,
                value=e.value,
                note=e.note,
                state="restated",
                submitted_by=ctx.user_id,
                submitted_at=now,
                version=sub.version + 1,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
        elif sub is None:
            sub = InputSubmission.objects.create(
                org_id=ctx.org_id,
                input_assignment=ia,
                metric=metric,
                metric_code=ia.metric_code,
                scope_type=ia.scope_type,
                scope_code=ia.scope_code,
                period_key=params.period_key,
                value=e.value,
                note=e.note,
                state="restated" if restating else "submitted",
                submitted_by=ctx.user_id,
                submitted_at=now,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
        else:
            sub.value, sub.note, sub.metric = e.value, e.note, metric
            sub.state = "restated" if restating else "submitted"
            sub.submitted_by, sub.submitted_at = ctx.user_id, now
            sub.updated_by, sub.updated_at = ctx.user_id, now
            sub.save()
        landed = inputs.conform(sub, now)
        ctx.audit(
            "input.conformed",
            metric_code=ia.metric_code,
            scope=f"{ia.scope_type}:{ia.scope_code}",
            period_key=params.period_key,
            version=sub.version,
            subjects=landed,
        )
    return tasks_out(ctx, account, params.period_key)
