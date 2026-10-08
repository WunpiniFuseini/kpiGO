"""Close, exclude, restate (PRD SC-9, SC-10, SC-12, SC-13; App Flow §7.2, §7.3).

A Scorecards period closes here, never through ``period.transition``: this is
the path that runs the pre-checks and writes the frozen snapshot.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from django.utils import timezone
from pydantic import BaseModel, StringConstraints

from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.hierarchy.models import Subject
from kpigo.hierarchy.scope import current_period_key
from kpigo.metrics.actions.metric import MetricCode
from kpigo.periods.models import PeriodStatus
from kpigo.platform.vocab import PeriodKey
from kpigo.scorecards import roster
from kpigo.scorecards.close import CloseCheck, Issue, freeze, pre_check, status_row
from kpigo.scorecards.models import ScoreExclusion, ScoreSnapshot

PRODUCT = "scorecards"
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


class CloseIssueOut(BaseModel):
    # not_reported | no_target | no_fx_rate | weights | pending_overrides | feed
    kind: str
    message: str
    metric_code: str | None
    profile_code: str | None
    subjects: list[str]
    count: int
    # Manual input: who still owes it.
    owed_by: list[str]


def _issue(i: Issue) -> CloseIssueOut:
    return CloseIssueOut(
        kind=i.kind,
        message=i.message,
        metric_code=i.metric_code,
        profile_code=i.profile_code,
        subjects=i.subjects,
        count=i.count,
        owed_by=i.owed_by,
    )


class CloseCheckIn(BaseModel):
    period_key: PeriodKey


class CloseCheckOut(BaseModel):
    period_key: str
    status: str
    snapshot_version: int
    # True when close would go ahead now.
    ready: bool
    # Why the period cannot be closed at all (already closed, not started), if so.
    refused: str | None
    subjects: int
    blockers: list[CloseIssueOut]
    warnings: list[CloseIssueOut]


def _staff(org_id: str) -> dict[str, str]:
    return {
        str(sid): no
        for sid, no in Subject.objects.filter(org_id=org_id).values_list("subject_id", "staff_no")
    }


def _refusal(org_id: str, period_key: str, row: PeriodStatus | None) -> str | None:
    status = row.status if row else "open"
    if status in ("closed", "closing"):
        return f"{period_key} is {status}. Restate it to change its scores."
    if period_key > current_period_key(org_id):
        return f"{period_key} has not started yet."
    return None


def _check_out(check: CloseCheck, row: PeriodStatus | None, refused: str | None) -> CloseCheckOut:
    return CloseCheckOut(
        period_key=check.period_key,
        status=check.status,
        snapshot_version=row.snapshot_version if row else 0,
        ready=check.ready and refused is None,
        refused=refused,
        subjects=check.subjects,
        blockers=[_issue(i) for i in check.blockers],
        warnings=[_issue(i) for i in check.warnings],
    )


@action(
    name="scorecard.close.check",
    summary="What stands between a Scorecards period and its close.",
    schema=CloseCheckIn,
    output=CloseCheckOut,
    permission="period.close",
    read_only=True,
    module="scorecards",
    example={"period_key": "202609"},
)
def check_close(params: CloseCheckIn, ctx: ActionContext) -> CloseCheckOut:
    row = status_row(ctx.org_id, params.period_key)
    refused = _refusal(ctx.org_id, params.period_key, row)
    status = row.status if row else "open"
    if refused is not None and status in ("closed", "closing"):
        empty = CloseCheck(params.period_key, status, 0, [], [], [])
        return _check_out(empty, row, refused)
    check = pre_check(ctx.org_id, params.period_key, status, _staff(ctx.org_id))
    return _check_out(check, row, refused)


class PeriodCloseIn(BaseModel):
    period_key: PeriodKey


class SnapshotOut(BaseModel):
    snapshot_id: uuid.UUID
    period_key: str
    snapshot_version: int
    kind: str
    reason: str
    subjects: int
    created_at: datetime
    created_by: int | None


def _snapshot(s: ScoreSnapshot) -> SnapshotOut:
    return SnapshotOut(
        snapshot_id=s.snapshot_id,
        period_key=s.period_key,
        snapshot_version=s.snapshot_version,
        kind=s.kind,
        reason=s.reason,
        subjects=s.subjects,
        created_at=s.created_at,
        created_by=s.created_by,
    )


@action(
    name="scorecard.period.close",
    agent_forbidden=True,
    summary="Close a Scorecards period: pre-check, score everyone, freeze the snapshot.",
    schema=PeriodCloseIn,
    output=SnapshotOut,
    permission="period.close",
    read_only=False,
    module="scorecards",
    requires_approval="period_close",
    audit="scorecard.period_closed",
    config_change=True,
    example={"period_key": "202609"},
)
def close_period(params: PeriodCloseIn, ctx: ActionContext) -> SnapshotOut:
    row = status_row(ctx.org_id, params.period_key, lock=True)
    refused = _refusal(ctx.org_id, params.period_key, row)
    if refused is not None:
        raise Conflict(refused)
    status = row.status if row else "open"
    check = pre_check(ctx.org_id, params.period_key, status, _staff(ctx.org_id))
    if not check.ready:
        raise Conflict(
            f"{params.period_key} cannot close: "
            + "; ".join(i.message for i in check.blockers[:5])
            + (" …" if len(check.blockers) > 5 else ""),
            detail={"blockers": [_issue(i).model_dump() for i in check.blockers]},
        )
    now = timezone.now()
    if row is None:
        row = PeriodStatus.objects.create(
            org_id=ctx.org_id,
            product=PRODUCT,
            period_key=params.period_key,
            status="open",
            created_by=ctx.user_id,
        )
    version = row.snapshot_version + 1
    snapshot = freeze(
        ctx.org_id,
        params.period_key,
        check.scores,
        version=version,
        reason=(row.status_reason or "") if status == "restating" else "",
        user_id=ctx.user_id,
    )
    row.status = "closed"
    row.snapshot_version = version
    row.closed_at = now
    row.closed_by = ctx.user_id
    row.updated_at = now
    row.updated_by = ctx.user_id
    row.save()
    return _snapshot(snapshot)


class PeriodRestateIn(BaseModel):
    period_key: PeriodKey
    reason: Reason


@action(
    name="scorecard.period.restate",
    agent_forbidden=True,
    summary="Reopen a closed Scorecards period for restatement, saying why.",
    schema=PeriodRestateIn,
    output=CloseCheckOut,
    permission="period.close",
    read_only=False,
    module="scorecards",
    requires_approval="period_close",
    audit="scorecard.period_restating",
    config_change=True,
    example={"period_key": "202609", "reason": "Corrected deposits feed for Kumasi branches."},
)
def restate_period(params: PeriodRestateIn, ctx: ActionContext) -> CloseCheckOut:
    row = status_row(ctx.org_id, params.period_key, lock=True)
    if row is None or row.status != "closed":
        raise Conflict(
            f"Only a closed period can be restated; {params.period_key} is "
            f"{row.status if row else 'open'}."
        )
    row.status = "restating"
    row.status_reason = params.reason
    row.updated_at = timezone.now()
    row.updated_by = ctx.user_id
    row.save()
    check = pre_check(ctx.org_id, params.period_key, row.status, _staff(ctx.org_id))
    return _check_out(check, row, None)


class SnapshotListIn(BaseModel):
    period_key: PeriodKey


class SnapshotListOut(BaseModel):
    period_key: str
    status: str
    snapshots: list[SnapshotOut]


@action(
    name="scorecard.snapshot.list",
    summary="Every frozen version of a Scorecards period, newest first.",
    schema=SnapshotListIn,
    output=SnapshotListOut,
    permission="scorecard.view",
    read_only=True,
    module="scorecards",
    example={"period_key": "202609"},
)
def list_snapshots(params: SnapshotListIn, ctx: ActionContext) -> SnapshotListOut:
    row = status_row(ctx.org_id, params.period_key)
    return SnapshotListOut(
        period_key=params.period_key,
        status=row.status if row else "open",
        snapshots=[
            _snapshot(s)
            for s in ScoreSnapshot.objects.filter(
                org_id=ctx.org_id, product=PRODUCT, period_key=params.period_key
            ).order_by("-snapshot_version")
        ],
    )


# ── exclusions ──────────────────────────────────────────────────────────────


class ExclusionOut(BaseModel):
    exclusion_id: uuid.UUID
    period_key: str
    metric_code: str
    metric_name: str
    # Null: everyone whose scorecard carries the metric this period.
    subject_id: uuid.UUID | None
    staff_no: str | None
    reason: str
    created_at: datetime
    created_by: int | None


def _exclusion(e: ScoreExclusion) -> ExclusionOut:
    return ExclusionOut(
        exclusion_id=e.exclusion_id,
        period_key=e.period_key,
        metric_code=e.metric.metric_code,
        metric_name=e.metric.display_name,
        subject_id=e.subject_id,
        staff_no=e.subject.staff_no if e.subject else None,
        reason=e.reason,
        created_at=e.created_at,
        created_by=e.created_by,
    )


class ExclusionAddIn(BaseModel):
    period_key: PeriodKey
    metric_code: MetricCode
    # Staff number or subject_id; leave empty to exclude the metric for everyone.
    subject: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = None
    reason: Reason


@action(
    name="scorecard.exclusion.add",
    summary="Leave an unscored metric out of a period's scores, with a reason.",
    schema=ExclusionAddIn,
    output=ExclusionOut,
    permission="period.close",
    read_only=False,
    module="scorecards",
    audit="scorecard.exclusion_added",
    config_change=True,
    example={
        "period_key": "202609",
        "metric_code": "casa_growth",
        "subject": "E1001",
        "reason": "Core banking migration lost the month's balances.",
    },
)
def add_exclusion(params: ExclusionAddIn, ctx: ActionContext) -> ExclusionOut:
    row = status_row(ctx.org_id, params.period_key)
    if row is not None and row.status in ("closing", "closed"):
        raise Conflict(f"{params.period_key} is {row.status}. Restate it first.")
    metric = roster.metric_in_force(ctx.org_id, params.metric_code, params.period_key)
    if metric is None:
        raise InvalidInput(f"No metric '{params.metric_code}' in force in {params.period_key}.")
    subject = None
    if params.subject:
        try:
            subject = Subject.objects.filter(
                org_id=ctx.org_id, subject_id=uuid.UUID(params.subject)
            ).first()
        except ValueError:
            subject = Subject.objects.filter(org_id=ctx.org_id, staff_no=params.subject).first()
        if subject is None:
            raise InvalidInput(f"No subject '{params.subject}'.")
    if ScoreExclusion.objects.filter(
        org_id=ctx.org_id,
        product=PRODUCT,
        period_key=params.period_key,
        metric=metric,
        subject=subject,
    ).exists():
        raise Conflict("That exclusion is already recorded.")
    e = ScoreExclusion.objects.create(
        org_id=ctx.org_id,
        product=PRODUCT,
        period_key=params.period_key,
        metric=metric,
        subject=subject,
        reason=params.reason,
        created_by=ctx.user_id,
    )
    return _exclusion(e)


class ExclusionRemoveIn(BaseModel):
    exclusion_id: uuid.UUID


class ExclusionRemoveOut(BaseModel):
    removed: bool


@action(
    name="scorecard.exclusion.remove",
    summary="Withdraw an exclusion from a period that has not closed.",
    schema=ExclusionRemoveIn,
    output=ExclusionRemoveOut,
    permission="period.close",
    read_only=False,
    module="scorecards",
    audit="scorecard.exclusion_removed",
    config_change=True,
    example={"exclusion_id": "00000000-0000-0000-0000-000000000000"},
)
def remove_exclusion(params: ExclusionRemoveIn, ctx: ActionContext) -> ExclusionRemoveOut:
    e = ScoreExclusion.objects.filter(org_id=ctx.org_id, exclusion_id=params.exclusion_id).first()
    if e is None:
        raise NotFound("No such exclusion.")
    row = status_row(ctx.org_id, e.period_key)
    if row is not None and row.status in ("closing", "closed"):
        raise Conflict(f"{e.period_key} is {row.status}; its exclusions are part of the snapshot.")
    ctx.audit(
        "scorecard.exclusion_detail",
        period_key=e.period_key,
        metric_id=str(e.metric_id),
        subject_id=str(e.subject_id) if e.subject_id else None,
        reason=e.reason,
    )
    e.delete()
    return ExclusionRemoveOut(removed=True)


class ExclusionListIn(BaseModel):
    period_key: PeriodKey


class ExclusionListOut(BaseModel):
    exclusions: list[ExclusionOut]


@action(
    name="scorecard.exclusion.list",
    summary="The exclusions recorded for a period.",
    schema=ExclusionListIn,
    output=ExclusionListOut,
    permission="period.close",
    read_only=True,
    module="scorecards",
    example={"period_key": "202609"},
)
def list_exclusions(params: ExclusionListIn, ctx: ActionContext) -> ExclusionListOut:
    rows = (
        ScoreExclusion.objects.filter(
            org_id=ctx.org_id, product=PRODUCT, period_key=params.period_key
        )
        .select_related("metric", "subject")
        .order_by("metric__metric_code", "subject__staff_no")
    )
    return ExclusionListOut(exclusions=[_exclusion(e) for e in rows])
