"""The input escalation ladder (PRD MI-8, NT-4, NT-5; TDD §5.5; App Flow §7.6).

An input still owed chases itself, one rung at a time, on working days counted
from the due day:

1. remind the contributor (N working days before it is due);
2. tell the contributor's line manager, resolved against the hierarchy as it
   stands when the ladder runs, so staff changes need no re-assignment;
3. tell the stakeholders: the slice's own list, else the org's, else everyone
   who manages input, so an overdue input never escalates into a void.

The first two rungs only make sense while the input can still be entered, so
they are passed over once it locks; the third is how the stakeholders learn an
input went unreported. Each run sends one email per recipient (a digest of
every slice that reached them), never one per slice, and only through the
install's own relay. In-app, the contributor sees how far each input has gone
and everyone told sees it under "Escalated to you" on My inputs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field, model_validator

from kpigo.access.identity import role_codes, role_permissions
from kpigo.access.models import AppUser
from kpigo.action import ActionContext, InvalidInput, NotFound, action
from kpigo.hierarchy.scope import current_period_key
from kpigo.platform import mail
from kpigo.platform.config import reporting_zone
from kpigo.platform.vocab import PeriodKey
from kpigo.scorecards import inputs
from kpigo.scorecards.actions.inputs import (
    EXAMPLE_ID,
    InputAssignmentOut,
    _account,
    _assignment_out,
    _labels,
    _metric_names,
)
from kpigo.scorecards.config import period_status, settings_for
from kpigo.scorecards.cycles import shift
from kpigo.scorecards.models import (
    InputAssignment,
    InputEscalation,
    InputSchedule,
    ScorecardSettings,
)

FOLLOWUP = "input.followup"
# How many months back the ladder looks: a deadline late in the following month
# plus a few working days can spill into the month after.
_LOOKBACK = 2


def _holders(org_id: str, permission: str) -> list[AppUser]:
    """Active accounts whose roles grant a permission."""
    return [
        u
        for u in AppUser.objects.filter(org_id=org_id, status="active").order_by("display_name")
        if permission in role_permissions(org_id, role_codes(u))
    ]


def _stakeholder_accounts(ctx: ActionContext, ids: list[uuid.UUID]) -> list[AppUser]:
    """The accounts named, each active and able to see what is escalated to them."""
    found = {
        u.user_id: u
        for u in AppUser.objects.filter(org_id=ctx.org_id, user_id__in=ids, status="active")
    }
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise NotFound(f"No active account for {', '.join(missing)}.")
    for u in found.values():
        if FOLLOWUP not in role_permissions(ctx.org_id, role_codes(u)):
            raise InvalidInput(
                f"{u.display_name} cannot see escalated inputs. Give them a role that can "
                "(Admin, Executive or Line Manager) first."
            )
    return [found[i] for i in ids]


def _unique(ids: list[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(ids))


# ── the ladder (Admin) ──────────────────────────────────────────────────────


class StakeholderOut(BaseModel):
    user_id: uuid.UUID
    display_name: str


class InputLadderOut(BaseModel):
    # Working days before the due day the contributor is reminded.
    contributor_working_days_before: int
    # Working days from the due day (negative = before) each later rung goes;
    # null when that rung is off.
    manager_working_days: int | None
    stakeholder_working_days: int | None
    # The org's stakeholders; empty means everyone who manages input is told.
    stakeholders: list[StakeholderOut]
    # Who the last rung tells when no slice names its own.
    default_stakeholders: list[StakeholderOut]
    # The ladder worked out for one month, so the setting reads as dates.
    period_key: str
    due_on: date
    contributor_on: date
    manager_on: date | None
    stakeholders_on: date | None
    email: bool


class InputLadderGetIn(BaseModel):
    # Defaults to last month, the one whose inputs are being collected.
    period_key: PeriodKey | None = None


def _ladder_out(org_id: str, period_key: str) -> InputLadderOut:
    s = settings_for(org_id)
    days = inputs.ladder(org_id, period_key)
    named = list(
        AppUser.objects.filter(org_id=org_id, user_id__in=s.input_stakeholders, status="active")
    )
    told = named or _holders(org_id, "input.manage")
    return InputLadderOut(
        contributor_working_days_before=s.input_reminder_working_days,
        manager_working_days=s.input_manager_working_days,
        stakeholder_working_days=s.input_stakeholder_working_days,
        stakeholders=[
            StakeholderOut(user_id=u.user_id, display_name=u.display_name) for u in named
        ],
        default_stakeholders=[
            StakeholderOut(user_id=u.user_id, display_name=u.display_name) for u in told
        ],
        period_key=period_key,
        due_on=inputs.due_day(org_id, period_key),
        contributor_on=inputs.remind_on(org_id, period_key),
        manager_on=days[2],
        stakeholders_on=days[3],
        email=mail.enabled(),
    )


@action(
    name="input.ladder.get",
    summary="The escalation ladder for overdue inputs, and the days it falls on for a month.",
    schema=InputLadderGetIn,
    output=InputLadderOut,
    permission="input.manage",
    read_only=True,
    module="scorecards",
    example={"period_key": "202609"},
)
def get_ladder(params: InputLadderGetIn, ctx: ActionContext) -> InputLadderOut:
    period_key = params.period_key or shift(current_period_key(ctx.org_id), -1)
    return _ladder_out(ctx.org_id, period_key)


class InputLadderSetIn(BaseModel):
    contributor_working_days_before: int = Field(ge=0, le=10)
    # Null switches the rung off.
    manager_working_days: int | None = Field(ge=-10, le=10)
    stakeholder_working_days: int | None = Field(ge=-10, le=10)
    # Empty: everyone who manages input is told at the last rung.
    stakeholder_user_ids: list[uuid.UUID] = Field(default=[], max_length=50)

    @model_validator(mode="after")
    def _in_order(self) -> InputLadderSetIn:
        rungs = [
            -self.contributor_working_days_before,
            *(
                d
                for d in (self.manager_working_days, self.stakeholder_working_days)
                if d is not None
            ),
        ]
        if rungs != sorted(rungs):
            raise ValueError(
                "the ladder climbs in order: contributor, then line manager, then stakeholders"
            )
        return self


@action(
    name="input.ladder.set",
    summary="Change when overdue inputs escalate, and who the stakeholders are.",
    schema=InputLadderSetIn,
    output=InputLadderOut,
    permission="input.manage",
    read_only=False,
    module="scorecards",
    requires_approval="config_change",
    audit="input.ladder_set",
    example={
        "contributor_working_days_before": 2,
        "manager_working_days": 0,
        "stakeholder_working_days": 1,
        "stakeholder_user_ids": [],
    },
)
def set_ladder(params: InputLadderSetIn, ctx: ActionContext) -> InputLadderOut:
    chosen = _stakeholder_accounts(ctx, _unique(params.stakeholder_user_ids))
    row, _ = ScorecardSettings.objects.get_or_create(
        org_id=ctx.org_id, defaults={"created_by": ctx.user_id}
    )
    row.input_reminder_working_days = params.contributor_working_days_before
    row.input_manager_working_days = params.manager_working_days
    row.input_stakeholder_working_days = params.stakeholder_working_days
    row.input_stakeholders = [str(u.user_id) for u in chosen]
    row.updated_by = ctx.user_id
    row.updated_at = timezone.now()
    row.save()
    return _ladder_out(ctx.org_id, shift(current_period_key(ctx.org_id), -1))


class InputStakeholdersSetIn(BaseModel):
    assignment_id: uuid.UUID
    # Empty: the org's stakeholders are told for this slice.
    stakeholder_user_ids: list[uuid.UUID] = Field(default=[], max_length=50)


@action(
    name="input.assignment.set_stakeholders",
    summary="Name who is told when this slice's input is overdue, in place of the org's stakeholders.",
    schema=InputStakeholdersSetIn,
    output=InputAssignmentOut,
    permission="input.manage",
    read_only=False,
    module="scorecards",
    audit="input.stakeholders_set",
    example={"assignment_id": EXAMPLE_ID, "stakeholder_user_ids": []},
)
def set_stakeholders(params: InputStakeholdersSetIn, ctx: ActionContext) -> InputAssignmentOut:
    row = (
        InputAssignment.objects.select_for_update(of=("self",))
        .filter(org_id=ctx.org_id, assignment_id=params.assignment_id)
        .first()
    )
    if row is None:
        raise NotFound("No such assignment.")
    chosen = _stakeholder_accounts(ctx, _unique(params.stakeholder_user_ids))
    config = dict(row.escalation_config or {})
    config["stakeholder_user_ids"] = [str(u.user_id) for u in chosen]
    row.escalation_config = config
    row.updated_by = ctx.user_id
    row.updated_at = timezone.now()
    row.save(update_fields=["escalation_config", "updated_by", "updated_at"])
    row = InputAssignment.objects.select_related("assignee_user").get(pk=row.pk)
    return _assignment_out(ctx.org_id, [row], current_period_key(ctx.org_id))[0]


# ── the daily run ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Item:
    step: int
    period_key: str
    ia: InputAssignment
    contributor: AppUser | None
    due: datetime
    escalation_id: uuid.UUID


class InputRemindIn(BaseModel):
    # For testing and catch-up; defaults to now.
    as_of: datetime | None = None


class InputRemindOut(BaseModel):
    # Slices whose contributor was reminded this run (the first rung).
    reminded: int
    # Contributor names reminded this run.
    recipients: list[str]
    # Rungs that reached nobody: a role or a line manager that resolves to no account.
    unresolved: int
    # People emailed (one message each, listing every slice that reached them).
    emailed: int = 0
    # People the relay refused or who have no address; told in-app only.
    not_emailed: list[str] = []
    # Slices escalated past the contributor this run (line manager or stakeholders).
    escalated: int
    # Who those escalations reached.
    escalated_to: list[str]


@action(
    name="input.remind",
    summary="Climb the escalation ladder for inputs still owed: remind the contributor, then tell their line manager, then the stakeholders, in-app and by email when a relay is set (scheduled daily).",
    schema=InputRemindIn,
    output=InputRemindOut,
    permission="input.manage",
    read_only=False,
    module="scorecards",
    audit="input.remind_run",
    example={},
)
def remind(params: InputRemindIn, ctx: ActionContext) -> InputRemindOut:
    org_id = ctx.org_id
    now = params.as_of or timezone.now()
    today = now.astimezone(reporting_zone(org_id)).date()
    current = current_period_key(org_id)
    managers: list[AppUser] | None = None
    reminded, escalated, unresolved = 0, 0, 0
    recipients: set[str] = set()
    escalated_to: set[str] = set()
    digest: dict[str, tuple[AppUser, list[_Item]]] = {}
    for period_key in (shift(current, -n) for n in range(_LOOKBACK, -1, -1)):
        if period_status(org_id, period_key) != "open":
            continue
        days = inputs.ladder(org_id, period_key)
        reached = [step for step, day in days.items() if day is not None and today >= day]
        if not reached:
            continue
        is_locked = inputs.locked(org_id, period_key, now)
        rows = inputs.assignments_in_force(org_id, period_key)
        have = inputs.current(org_id, period_key, rows)
        due = inputs.due_at(org_id, period_key)
        for ia in rows:
            sub = have.get(str(ia.assignment_id))
            if sub is not None and sub.state != "draft":
                continue
            sent: list[tuple[int, AppUser | None, uuid.UUID]] = []
            with transaction.atomic():
                schedule, _ = InputSchedule.objects.select_for_update().get_or_create(
                    input_assignment=ia,
                    period_key=period_key,
                    defaults={"org_id": org_id, "due_at": due, "created_by": ctx.user_id},
                )
                pending = [step for step in reached if step > schedule.last_reminder_step]
                if not pending:
                    continue
                who = inputs.contributor(ia, period_key)
                for step in pending:
                    if step < 3 and is_locked:
                        # Too late to enter it; only the stakeholders still need to know.
                        continue
                    targets: list[AppUser | None]
                    if step == 1:
                        targets = [who] if who else []
                    elif step == 2:
                        boss = inputs.line_manager_of(who, today) if who else None
                        targets = [boss] if boss else []
                    else:
                        if managers is None:
                            managers = _holders(org_id, "input.manage")
                        targets = list(inputs.stakeholders(ia, managers))
                    # None records a rung that reached nobody, so the gap is visible.
                    for target in targets or [None]:
                        row = InputEscalation.objects.create(
                            org_id=org_id,
                            schedule=schedule,
                            step=step,
                            recipient=target,
                            contributor=who,
                            sent_at=now,
                            emailed=None,
                            created_by=ctx.user_id,
                        )
                        sent.append((step, target, row.escalation_id))
                schedule.last_reminder_step = max(pending)
                schedule.last_reminded_at = now
                if 1 in pending and who is not None and not is_locked:
                    schedule.reminded_user = who
                schedule.save(
                    update_fields=["last_reminder_step", "last_reminded_at", "reminded_user"]
                )
            scope = f"{ia.scope_type}:{ia.scope_code}"
            steps_sent = {step for step, target, _ in sent if target is not None}
            for step, target, escalation_id in sent:
                if target is None:
                    unresolved += 1
                    ctx.audit(
                        "input.escalation_unresolved",
                        step=step,
                        metric_code=ia.metric_code,
                        scope=scope,
                        period_key=period_key,
                    )
                    continue
                ctx.audit(
                    "input.reminded" if step == 1 else "input.escalated",
                    step=step,
                    to_user_id=str(target.user_id),
                    contributor_user_id=str(who.user_id) if who else None,
                    metric_code=ia.metric_code,
                    scope=scope,
                    period_key=period_key,
                    due_at=due.isoformat(),
                )
                if step == 1:
                    recipients.add(target.display_name)
                else:
                    escalated_to.add(target.display_name)
                digest.setdefault(str(target.user_id), (target, []))[1].append(
                    _Item(step, period_key, ia, who, due, escalation_id)
                )
            if 1 in steps_sent:
                reminded += 1
            if steps_sent & {2, 3}:
                escalated += 1
    emailed, not_emailed = _email(ctx, digest)
    return InputRemindOut(
        reminded=reminded,
        recipients=sorted(recipients),
        unresolved=unresolved,
        emailed=emailed,
        not_emailed=not_emailed,
        escalated=escalated,
        escalated_to=sorted(escalated_to),
    )


_HEADINGS = {
    1: "These inputs are still waiting for you:",
    2: "Your team has not submitted these inputs yet:",
    3: "These inputs are overdue:",
}


def _email(
    ctx: ActionContext, digest: dict[str, tuple[AppUser, list[_Item]]]
) -> tuple[int, list[str]]:
    """One email per person listing every slice that reached them; none on a dry run."""
    if not mail.enabled() or ctx.dry_run or not digest:
        return 0, []
    zone = reporting_zone(ctx.org_id)
    ladder_on = any(
        d is not None
        for d in (
            settings_for(ctx.org_id).input_manager_working_days,
            settings_for(ctx.org_id).input_stakeholder_working_days,
        )
    )
    sent, missed = 0, []
    for who, items in digest.values():
        # A person told twice about one slice (manager and stakeholder) reads it once.
        best: dict[tuple[str, str], _Item] = {}
        for item in items:
            key = (item.period_key, str(item.ia.assignment_id))
            if key not in best or item.step > best[key].step:
                best[key] = item
        lines: list[str] = []
        for step in (1, 2, 3):
            mine = [i for i in best.values() if i.step == step]
            if not mine:
                continue
            lines += [_HEADINGS[step], ""]
            for period_key in sorted({i.period_key for i in mine}):
                rows = [i for i in mine if i.period_key == period_key]
                names = _metric_names(
                    ctx.org_id, sorted({i.ia.metric_code for i in rows}), period_key
                )
                labels = _labels(ctx.org_id, [i.ia for i in rows])
                # The deadline is the start of the next day; people read the day itself.
                last = (rows[0].due.astimezone(zone) - timedelta(seconds=1)).date()
                month = date(int(period_key[:4]), int(period_key[4:]), 1)
                passed = "was due" if step == 3 and timezone.now() >= rows[0].due else "due"
                lines.append(f"{month:%B %Y}, {passed} by the end of {last.day} {last:%B %Y}:")
                for i in rows:
                    m = names.get(i.ia.metric_code)
                    line = f"  - {m.display_name if m else i.ia.metric_code}, for {labels[str(i.ia.assignment_id)]}"
                    if step > 1:
                        owner = i.contributor.display_name if i.contributor else "nobody"
                        line += f" (owed by {owner})"
                    lines.append(line)
                lines.append("")
        where = mail.link("/my-inputs")
        own = any(i.step == 1 for i in best.values())
        body = "\n".join(
            [
                f"Hello {who.display_name},",
                "",
                *lines,
                (
                    f"Enter them on My inputs: {where}"
                    if where
                    else "Enter them on My inputs in kpiGo."
                )
                if own
                else (
                    f"See them under Escalated to you on My inputs: {where}"
                    if where
                    else "See them under Escalated to you on My inputs in kpiGo."
                ),
                "Values left unsubmitted at the deadline stay unreported for that month.",
                *(
                    ["kpiGo keeps chasing these up the escalation ladder until they are submitted."]
                    if own and ladder_on
                    else []
                ),
            ]
        )
        subject = (
            f"kpiGo: {len(best)} input(s) due soon"
            if all(i.step == 1 for i in best.values())
            else f"kpiGo: {len(best)} input(s) need chasing"
        )
        ok = bool(who.email) and mail.send(who.email, subject, body)
        InputEscalation.objects.filter(escalation_id__in=[i.escalation_id for i in items]).update(
            emailed=ok
        )
        ctx.audit(
            "input.reminder_emailed" if ok else "input.reminder_email_failed",
            to_user_id=str(who.user_id),
            slices=len(best),
        )
        if ok:
            sent += 1
        else:
            missed.append(who.display_name)
    return sent, sorted(missed)


# ── escalated to you ────────────────────────────────────────────────────────


class EscalatedInputOut(BaseModel):
    assignment_id: uuid.UUID
    period_key: str
    metric_code: str
    metric_name: str
    scope_label: str
    # Who owes it; null when the slice resolves to nobody this month.
    contributor_name: str | None
    # 2: you are their line manager; 3: you are a stakeholder.
    step: int
    escalated_at: datetime
    due_at: datetime
    locked: bool
    # pending | draft: still owed.
    state: str


class EscalatedInputListIn(BaseModel):
    pass


class EscalatedInputListOut(BaseModel):
    items: list[EscalatedInputOut]


@action(
    name="input.escalation.list",
    summary="Inputs escalated to you that are still owed.",
    schema=EscalatedInputListIn,
    output=EscalatedInputListOut,
    permission=FOLLOWUP,
    read_only=True,
    module="scorecards",
    example={},
)
def list_escalated(params: EscalatedInputListIn, ctx: ActionContext) -> EscalatedInputListOut:
    account = _account(ctx)
    current = current_period_key(ctx.org_id)
    periods = [shift(current, -n) for n in range(_LOOKBACK, -1, -1)]
    rows = (
        InputEscalation.objects.filter(
            org_id=ctx.org_id,
            recipient=account,
            step__gte=2,
            schedule__period_key__in=periods,
        )
        .select_related("schedule__input_assignment__assignee_user", "contributor")
        .order_by("schedule__period_key", "sent_at")
    )
    latest: dict[str, InputEscalation] = {}
    for r in rows:
        latest[str(r.schedule_id)] = r
    items: list[EscalatedInputOut] = []
    for period_key in periods:
        found = [r for r in latest.values() if r.schedule.period_key == period_key]
        if not found or period_status(ctx.org_id, period_key) != "open":
            continue
        slices = [r.schedule.input_assignment for r in found]
        have = inputs.current(ctx.org_id, period_key, slices)
        names = _metric_names(ctx.org_id, sorted({s.metric_code for s in slices}), period_key)
        labels = _labels(ctx.org_id, slices)
        due = inputs.due_at(ctx.org_id, period_key)
        for r in found:
            ia = r.schedule.input_assignment
            sub = have.get(str(ia.assignment_id))
            if sub is not None and sub.state != "draft":
                continue
            m = names.get(ia.metric_code)
            who = inputs.contributor(ia, period_key)
            items.append(
                EscalatedInputOut(
                    assignment_id=ia.assignment_id,
                    period_key=period_key,
                    metric_code=ia.metric_code,
                    metric_name=m.display_name if m else ia.metric_code,
                    scope_label=labels[str(ia.assignment_id)],
                    contributor_name=who.display_name if who else None,
                    step=r.step,
                    escalated_at=r.sent_at,
                    due_at=due,
                    locked=inputs.locked(ctx.org_id, period_key),
                    state=sub.state if sub else "pending",
                )
            )
    return EscalatedInputListOut(items=items)
