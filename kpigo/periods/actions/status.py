"""Period status and feed deadlines (Schema §4: period_status, period_deadline).

The state machine is ``open → closing → closed → restating → closed`` (App Flow
§8). Scorecards closes and restates through its own actions
(``scorecard.period.close``, ``scorecard.period.restate``), which run the
pre-checks and freeze the snapshot; here each close increments
``snapshot_version``, so a restated period's next close is version n+1.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from django.utils import timezone
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from kpigo.action import ActionContext, Conflict, InvalidInput, action
from kpigo.periods.models import PeriodDeadline, PeriodStatus
from kpigo.platform.vocab import PeriodKey, Product

Status = Literal["open", "closing", "closed", "restating"]

TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"open"}),
    "open": frozenset({"closing"}),
    # A failed pre-check returns the period to open.
    "closing": frozenset({"open", "closed"}),
    "closed": frozenset({"restating"}),
    "restating": frozenset({"closed"}),
}


class PeriodStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product: str
    period_key: str
    status: str
    closed_at: datetime | None
    closed_by: int | None
    snapshot_version: int
    auto_close_at: datetime | None
    grace_until: datetime | None


class StatusListIn(BaseModel):
    product: Product | None = None
    period_key: PeriodKey | None = None


class StatusListOut(BaseModel):
    periods: list[PeriodStatusOut]


@action(
    name="period.list",
    summary="Period status per product.",
    schema=StatusListIn,
    output=StatusListOut,
    permission="calendar.view",
    read_only=True,
    example={"product": "scorecards"},
)
def list_periods(params: StatusListIn, ctx: ActionContext) -> StatusListOut:
    rows = PeriodStatus.objects.filter(org_id=ctx.org_id)
    if params.product is not None:
        rows = rows.filter(product=params.product)
    if params.period_key is not None:
        rows = rows.filter(period_key=params.period_key)
    return StatusListOut(
        periods=[PeriodStatusOut.model_validate(p) for p in rows.order_by("product", "-period_key")]
    )


class TransitionIn(BaseModel):
    product: Product
    period_key: PeriodKey
    to_status: Status
    # Required to restate a closed period.
    reason: str = Field(default="", max_length=2000)


@action(
    name="period.transition",
    summary="Move a period through open, closing, closed and restating.",
    schema=TransitionIn,
    output=PeriodStatusOut,
    permission="calendar.manage",
    read_only=False,
    requires_approval="period_close",
    audit="period.transitioned",
    config_change=True,
    example={"product": "scorecards", "period_key": "202610", "to_status": "open"},
)
def transition(params: TransitionIn, ctx: ActionContext) -> PeriodStatusOut:
    if params.product == "scorecards" and params.to_status in ("closing", "closed", "restating"):
        raise Conflict(
            "Scorecards periods close and restate through scorecard.period.close and "
            "scorecard.period.restate, which run the pre-checks and freeze the snapshot."
        )
    row = (
        PeriodStatus.objects.select_for_update()
        .filter(org_id=ctx.org_id, product=params.product, period_key=params.period_key)
        .first()
    )
    current = row.status if row is not None else None
    if params.to_status not in TRANSITIONS[current]:
        raise Conflict(
            f"A {current or 'missing'} period cannot become {params.to_status}.",
            detail={"allowed": sorted(TRANSITIONS[current])},
        )
    if params.to_status == "restating" and not params.reason.strip():
        raise InvalidInput("Restating a closed period requires a reason.")
    now = timezone.now()
    if row is None:
        row = PeriodStatus.objects.create(
            org_id=ctx.org_id,
            product=params.product,
            period_key=params.period_key,
            status="open",
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    else:
        row.status = params.to_status
        if params.to_status == "restating":
            row.status_reason = params.reason
        if params.to_status == "closed":
            row.closed_at = now
            row.closed_by = ctx.user_id
            row.snapshot_version += 1
        row.updated_by = ctx.user_id
        row.updated_at = now
        row.save()
    if params.reason:
        ctx.audit(
            "period.reason",
            product=params.product,
            period_key=params.period_key,
            to_status=params.to_status,
            reason=params.reason,
        )
    return PeriodStatusOut.model_validate(row)


# ── deadlines ───────────────────────────────────────────────────────────────


class DeadlineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feed_id: uuid.UUID
    period_key: str
    due_at: datetime


class DeadlineSetIn(BaseModel):
    feed_id: uuid.UUID
    period_key: PeriodKey
    due_at: AwareDatetime


@action(
    name="period.deadline.set",
    summary="Set when a feed's data for a period is due.",
    schema=DeadlineSetIn,
    output=DeadlineOut,
    permission="calendar.manage",
    read_only=False,
    requires_approval="calendar_change",
    audit="period.deadline_set",
    config_change=True,
    example={
        "feed_id": "00000000-0000-0000-0000-000000000000",
        "period_key": "202610",
        "due_at": "2026-11-05T17:00:00+00:00",
    },
)
def set_deadline(params: DeadlineSetIn, ctx: ActionContext) -> DeadlineOut:
    updated = PeriodDeadline.objects.filter(
        org_id=ctx.org_id, feed_id=params.feed_id, period_key=params.period_key
    ).update(due_at=params.due_at, updated_by=ctx.user_id, updated_at=timezone.now())
    if not updated:
        PeriodDeadline.objects.create(
            org_id=ctx.org_id,
            **params.model_dump(),
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    return DeadlineOut(feed_id=params.feed_id, period_key=params.period_key, due_at=params.due_at)


class DeadlineListIn(BaseModel):
    period_key: PeriodKey


class DeadlineListOut(BaseModel):
    deadlines: list[DeadlineOut]


@action(
    name="period.deadline.list",
    summary="Feed deadlines for a period, earliest first.",
    schema=DeadlineListIn,
    output=DeadlineListOut,
    permission="calendar.view",
    read_only=True,
    example={"period_key": "202610"},
)
def list_deadlines(params: DeadlineListIn, ctx: ActionContext) -> DeadlineListOut:
    rows = PeriodDeadline.objects.filter(org_id=ctx.org_id, period_key=params.period_key)
    return DeadlineListOut(
        deadlines=[DeadlineOut.model_validate(d) for d in rows.order_by("due_at")]
    )
