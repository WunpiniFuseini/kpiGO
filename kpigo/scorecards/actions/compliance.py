"""Contributor compliance (PRD MI-12; App Flow §7.6; TDD §5.5).

Who was asked, who submitted, and when, per contributor over time: the view
that surfaces the team lead who is late every month. It is a read over what is
already recorded: the assignments in force each month, the submissions against
them (the earliest submitted time is when the input arrived; a resubmission or
restatement does not make it later), the deadline, and how far up the
escalation ladder each one climbed.

A slice is owed by whoever its assignment resolves to for that month, so a
"line manager of" slice counts against the manager in post at month end. An
Admin (``input.manage``) sees every contributor, including slices nobody could
be asked for; anyone else sees the contributors in their visibility scope.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Min, Q
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, action
from kpigo.hierarchy.scope import current_period_key
from kpigo.platform.vocab import PeriodKey
from kpigo.scorecards import inputs
from kpigo.scorecards.actions.escalation import FOLLOWUP
from kpigo.scorecards.cycles import shift
from kpigo.scorecards.models import InputSchedule, InputSubmission

# Late or missing in this many of the months shown marks a contributor as
# chronically late: often enough to be a pattern, not a bad month.
CHRONIC_MONTHS = 3


class ComplianceIn(BaseModel):
    # The last month shown; defaults to last month, the one most recently due.
    period_key: PeriodKey | None = None
    months: int = Field(default=6, ge=1, le=24)


class ComplianceMonthOut(BaseModel):
    period_key: str
    # Slices this contributor was asked for that month (0: not asked).
    asked: int
    on_time: int
    late: int
    # Never submitted, and the deadline has passed.
    missing: int
    # Not submitted yet, but the deadline has not passed.
    open: int
    # How far up the escalation ladder the month's inputs went (0-3).
    step: int
    # When the last of the month's inputs arrived; null if none did.
    last_submitted_at: datetime | None


class ComplianceRowOut(BaseModel):
    # Null for slices that resolved to nobody (a role with no line manager).
    user_id: uuid.UUID | None
    name: str
    months: list[ComplianceMonthOut]
    asked: int
    on_time: int
    late: int
    missing: int
    # Share of decided inputs (on time, late or missing) that were on time; null when none.
    on_time_rate: Decimal | None
    # Months with at least one late or missing input.
    late_months: int
    chronic: bool


class ComplianceOut(BaseModel):
    periods: list[str]
    rows: list[ComplianceRowOut]
    chronic_months: int
    # ``all``: every contributor; ``team``: those in your visibility scope.
    scope: str


@dataclass
class _Cell:
    period_key: str
    asked: int = 0
    on_time: int = 0
    late: int = 0
    missing: int = 0
    open: int = 0
    step: int = 0
    last_submitted_at: datetime | None = None


@action(
    name="input.compliance",
    summary="Who was asked for manual inputs, who submitted, and when, per contributor over time.",
    schema=ComplianceIn,
    output=ComplianceOut,
    permission=FOLLOWUP,
    read_only=True,
    module="scorecards",
    example={"period_key": "202609", "months": 6},
)
def compliance(params: ComplianceIn, ctx: ActionContext) -> ComplianceOut:
    org_id = ctx.org_id
    last = params.period_key or shift(current_period_key(org_id), -1)
    periods = [shift(last, -n) for n in range(params.months - 1, -1, -1)]
    everyone = ctx.has("input.manage")
    names: dict[str, str] = {}
    cells: dict[str, dict[str, _Cell]] = defaultdict(dict)
    for period_key in periods:
        slices = inputs.assignments_in_force(org_id, period_key)
        if not slices:
            continue
        due = inputs.due_at(org_id, period_key)
        is_locked = inputs.locked(org_id, period_key)
        q = Q()
        for ia in slices:
            q |= Q(metric_code=ia.metric_code, scope_type=ia.scope_type, scope_code=ia.scope_code)
        arrived = {
            (r["metric_code"], r["scope_type"], r["scope_code"]): r["first"]
            for r in InputSubmission.objects.filter(q, org_id=org_id, period_key=period_key)
            .exclude(state="draft")
            .values("metric_code", "scope_type", "scope_code")
            .annotate(first=Min("submitted_at"))
        }
        steps = {
            str(s.input_assignment_id): s.last_reminder_step
            for s in InputSchedule.objects.filter(
                input_assignment__in=slices, period_key=period_key
            )
        }
        for ia in slices:
            who = inputs.contributor(ia, period_key)
            if who is None:
                if not everyone:
                    continue
                key = ""
                names[key] = "Nobody (no line manager)"
            else:
                if not everyone and not (
                    who.subject_id is not None
                    and ctx.visible_subjects.contains(str(who.subject_id))
                ):
                    continue
                key = str(who.user_id)
                names[key] = who.display_name
            cell = cells[key].setdefault(period_key, _Cell(period_key))
            cell.asked += 1
            first = arrived.get((ia.metric_code, ia.scope_type, ia.scope_code))
            if first is None:
                cell.missing += is_locked
                cell.open += not is_locked
            else:
                cell.on_time += first < due
                cell.late += first >= due
                if cell.last_submitted_at is None or first > cell.last_submitted_at:
                    cell.last_submitted_at = first
            cell.step = max(cell.step, steps.get(str(ia.assignment_id), 0))
    rows: list[ComplianceRowOut] = []
    for key, by_month in cells.items():
        months = [ComplianceMonthOut(**asdict(by_month.get(p) or _Cell(p))) for p in periods]
        on_time = sum(m.on_time for m in months)
        late = sum(m.late for m in months)
        missing = sum(m.missing for m in months)
        decided = on_time + late + missing
        late_months = sum(1 for m in months if m.late or m.missing)
        rows.append(
            ComplianceRowOut(
                user_id=uuid.UUID(key) if key else None,
                name=names[key],
                months=months,
                asked=sum(m.asked for m in months),
                on_time=on_time,
                late=late,
                missing=missing,
                on_time_rate=(Decimal(on_time) / decided).quantize(Decimal("0.001"))
                if decided
                else None,
                late_months=late_months,
                chronic=late_months >= CHRONIC_MONTHS,
            )
        )
    rows.sort(key=lambda r: (not r.chronic, -r.late_months, r.user_id is None, r.name.lower()))
    return ComplianceOut(
        periods=periods,
        rows=rows,
        chronic_months=CHRONIC_MONTHS,
        scope="all" if everyone else "team",
    )
