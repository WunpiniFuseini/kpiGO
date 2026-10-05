"""Events: add, edit, publish, pause, close, repeat, and the budget change (CM-2, CM-4, CM-5).

A draft is edited freely. A published event's budget changes only through
``campaign.event.budget.set``, which waits for a checker by default; its period,
window, channels and audience change through ``campaign.event.update``, each
change versioned, and a change to its dates or audience re-opens attribution.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.campaigns import attribution
from kpigo.campaigns import authoring as au
from kpigo.campaigns.actions.campaigns import (
    Budget,
    CampaignCriterionIn,
    CampaignEventIn,
    CampaignOut,
    Channel,
    campaign_out,
    create_event,
    ensure_still_visible,
)
from kpigo.campaigns.models import MAX_HOLDOUT_PCT, CampaignEvent, CampaignEventVersion
from kpigo.campaigns.scope import get_visible, is_visible
from kpigo.platform.vocab import CurrencyCode

EXAMPLE_EVENT = "00000000-0000-0000-0000-000000000000"


def get_event(ctx: ActionContext, event_id: uuid.UUID, *, lock: bool = False) -> CampaignEvent:
    query = CampaignEvent.objects.filter(org_id=ctx.org_id).select_related("campaign")
    if lock:
        query = query.select_for_update(of=("self",))
    event = query.filter(event_id=event_id).first()
    if event is None or not is_visible(ctx, event.campaign_id):
        raise NotFound("No such campaign event in your scope.")
    return event


def _touch(event: CampaignEvent, ctx: ActionContext) -> None:
    event.updated_by = ctx.user_id
    event.updated_at = timezone.now()
    event.save()


# ── add / remove ─────────────────────────────────────────────────────────────


class CampaignEventAddIn(CampaignEventIn):
    campaign_id: uuid.UUID


@action(
    name="campaign.event.add",
    summary="Add an event to a campaign as a draft: a recurring campaign runs many.",
    schema=CampaignEventAddIn,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    audit="campaign.event.added",
    example={
        "campaign_id": EXAMPLE_EVENT,
        "event_name": "November SMS wave",
        "period_start": "2026-11-01",
        "period_end": "2026-11-30",
        "budget_amount": "20000.00",
        "budget_currency": "GHS",
        "channels": ["sms"],
        "audience": [{"dimension_type": "segment", "member_code": "retail"}],
    },
)
def add_event(params: CampaignEventAddIn, ctx: ActionContext) -> CampaignOut:
    with transaction.atomic():
        campaign = get_visible(ctx, str(params.campaign_id), lock=True)
        if campaign.status == "closed":
            raise Conflict("The campaign is closed.")
        create_event(campaign, params, ctx, au.next_sequence(campaign))
        ensure_still_visible(campaign, ctx)
    return campaign_out(campaign)


class CampaignEventRef(BaseModel):
    event_id: uuid.UUID


@action(
    name="campaign.event.remove",
    summary="Delete a draft event. A published event is closed, never deleted.",
    schema=CampaignEventRef,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    audit="campaign.event.removed",
    example={"event_id": EXAMPLE_EVENT},
)
def remove_event(params: CampaignEventRef, ctx: ActionContext) -> CampaignOut:
    event = get_event(ctx, params.event_id, lock=True)
    if event.state != "draft":
        raise Conflict("Only a draft is deleted. Close a published event instead.")
    campaign = event.campaign
    event.delete()
    return campaign_out(campaign)


# ── edit ─────────────────────────────────────────────────────────────────────


class CampaignEventUpdateIn(BaseModel):
    event_id: uuid.UUID
    event_name: str | None = Field(default=None, min_length=1, max_length=120)
    period_start: date | None = None
    period_end: date | None = None
    attribution_window_days: int | None = Field(default=None, ge=0, le=730)
    channels: list[Channel] | None = None
    # Replaces the criteria when given.
    audience: list[CampaignCriterionIn] | None = None
    # Draft only. A published event's budget goes through campaign.event.budget.set.
    budget_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    budget_currency: CurrencyCode | None = None
    # Planned control group share; 0 removes it. Fixed once the event has started.
    holdout_pct: int | None = Field(default=None, ge=0, le=MAX_HOLDOUT_PCT)


@action(
    name="campaign.event.update",
    summary="Change an event's name, period, window, channels or audience (and a draft's budget).",
    schema=CampaignEventUpdateIn,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    audit="campaign.event.updated",
    example={"event_id": EXAMPLE_EVENT, "period_end": "2026-11-15"},
)
def update_event(params: CampaignEventUpdateIn, ctx: ActionContext) -> CampaignOut:
    with transaction.atomic():
        event = get_event(ctx, params.event_id, lock=True)
        au.require_editable(event)
        draft = event.state == "draft"
        was = (event.period_start, au.window_end(event))
        if not draft and (params.budget_amount is not None or params.budget_currency is not None):
            raise Conflict(
                "A published event's budget changes through a budget change, which waits "
                "for approval. Use campaign.event.budget.set.",
                detail={"action": au.BUDGET_ACTION},
            )
        start = params.period_start or event.period_start
        end = params.period_end or event.period_end
        window = (
            event.attribution_window_days
            if params.attribution_window_days is None
            else params.attribution_window_days
        )
        au.check_period(start, end, window)
        if not draft and start != event.period_start and au.today() >= event.period_start:
            raise Conflict(
                "The event has started, so its first day is fixed. Change its end instead.",
                detail={"field": "period_start"},
            )
        dates_changed = (start, end, window) != (
            event.period_start,
            event.period_end,
            event.attribution_window_days,
        )
        if not draft and dates_changed:
            au.reopen(event, min(start, event.period_start))
        fields_changed = dates_changed
        event.period_start, event.period_end, event.attribution_window_days = start, end, window
        if params.event_name is not None and params.event_name != event.event_name:
            event.event_name = params.event_name
            fields_changed = True
        if params.channels is not None and sorted(set(params.channels)) != event.channels:
            event.channels = sorted(set(params.channels))
            fields_changed = True
        if params.budget_currency is not None:
            au.check_currency(ctx.org_id, params.budget_currency)
            event.budget_currency = params.budget_currency
        if params.budget_amount is not None:
            event.budget_amount = au.money(params.budget_amount)
        holdout = None if params.holdout_pct in (None, 0) else params.holdout_pct
        if params.holdout_pct is not None and holdout != event.holdout_pct:
            if not draft and au.today() >= event.period_start:
                raise Conflict(
                    "The event has started, so its control group is fixed.",
                    detail={"field": "holdout_pct"},
                )
            event.holdout_pct = holdout
            fields_changed = True
        audience_changed = False
        if params.audience is not None:
            criteria = [(c.dimension_type, c.member_code) for c in params.audience]
            au.check_audience(ctx.org_id, criteria)
            if not draft and not criteria:
                raise InvalidInput(
                    "A published event keeps at least one audience criterion.",
                    detail={"field": "audience"},
                )
            audience_changed = au.replace_audience(event, criteria, ctx)
            if audience_changed and not draft:
                au.reopen(event, min(event.period_start, start))
        _touch(event, ctx)
        if not draft and (fields_changed or audience_changed):
            au.record_version(
                event, "audience" if audience_changed and not fields_changed else "updated", ctx
            )
        if event.reattribute_from is not None:
            attribution.reattribute(
                ctx.org_id,
                min(was[0], event.reattribute_from),
                max(was[1], au.window_end(event)),
                events=[event],
            )
        ensure_still_visible(event.campaign, ctx)
    return campaign_out(event.campaign)


class CampaignBudgetSetIn(BaseModel):
    event_id: uuid.UUID
    budget_amount: Decimal = Budget
    # Defaults to the event's current currency.
    budget_currency: CurrencyCode | None = None
    reason: str = Field(default="", max_length=2000)


@action(
    name="campaign.event.budget.set",
    summary="Change an event's budget. Waits for a second person's approval unless turned off.",
    schema=CampaignBudgetSetIn,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    requires_approval="budget",
    audit="campaign.budget.changed",
    config_change=True,
    example={"event_id": EXAMPLE_EVENT, "budget_amount": "30000.00", "reason": "Extra wave"},
)
def set_budget(params: CampaignBudgetSetIn, ctx: ActionContext) -> CampaignOut:
    with transaction.atomic():
        event = get_event(ctx, params.event_id, lock=True)
        au.require_editable(event)
        currency = params.budget_currency or event.budget_currency
        au.check_currency(ctx.org_id, currency)
        before = (str(au.money(event.budget_amount)), event.budget_currency)
        event.budget_amount = au.money(params.budget_amount)
        event.budget_currency = currency
        _touch(event, ctx)
        if event.state != "draft":
            au.record_version(event, "budget", ctx)
        ctx.audit(
            "campaign.budget.change_detail",
            event_id=str(event.event_id),
            before={"amount": before[0], "currency": before[1]},
            after={"amount": str(event.budget_amount), "currency": currency},
            reason=params.reason,
        )
    return campaign_out(event.campaign)


# ── lifecycle ────────────────────────────────────────────────────────────────


@action(
    name="campaign.event.publish",
    summary="Publish a draft event: it is scheduled, then runs on its dates.",
    schema=CampaignEventRef,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    audit="campaign.event.published",
    example={"event_id": EXAMPLE_EVENT},
)
def publish_event(params: CampaignEventRef, ctx: ActionContext) -> CampaignOut:
    with transaction.atomic():
        event = get_event(ctx, params.event_id, lock=True)
        au.require_editable(event)
        if event.state != "draft":
            raise Conflict("The event is already published.")
        if not au.criteria_of(event):
            raise InvalidInput(
                "Say who the event is for before publishing it: at least one audience "
                "criterion (segment, product, region...).",
                detail={"field": "audience"},
            )
        au.check_currency(ctx.org_id, event.budget_currency)
        event.state = "live"
        event.published_at = timezone.now()
        _touch(event, ctx)
        au.record_version(event, "published", ctx)
        # Outcomes already loaded for its window are credited now, not at the next load.
        attribution.reattribute(ctx.org_id, event.period_start, au.window_end(event))
    return campaign_out(event.campaign)


class CampaignEventStatusIn(BaseModel):
    event_id: uuid.UUID
    to: Literal["pause", "resume", "close"]


_FROM = {"pause": ("live",), "resume": ("paused",), "close": ("draft", "live", "paused")}
_TO = {"pause": "paused", "resume": "live", "close": "closed"}
_CHANGE = {"pause": "paused", "resume": "resumed", "close": "closed"}


@action(
    name="campaign.event.status",
    summary="Pause, resume or close an event. Closing is final.",
    schema=CampaignEventStatusIn,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    audit="campaign.event.status_changed",
    example={"event_id": EXAMPLE_EVENT, "to": "pause"},
)
def change_status(params: CampaignEventStatusIn, ctx: ActionContext) -> CampaignOut:
    with transaction.atomic():
        event = get_event(ctx, params.event_id, lock=True)
        if event.campaign.status == "closed":
            raise Conflict("The campaign is closed.")
        if event.state not in _FROM[params.to]:
            raise Conflict(f"An event that is {event.state} cannot {params.to}.")
        was_draft = event.state == "draft"
        event.state = _TO[params.to]
        if params.to == "close":
            event.closed_at = timezone.now()
        _touch(event, ctx)
        if not was_draft:
            au.record_version(event, _CHANGE[params.to], ctx)
    return campaign_out(event.campaign)


# ── recurrence ───────────────────────────────────────────────────────────────


def _add_months(day: date, months: int, *, month_end: bool) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, last if month_end else min(day.day, last))


def _is_month_end(day: date) -> bool:
    return (day + timedelta(days=1)).day == 1


def shifted(start: date, end: date, every: str, n: int) -> tuple[date, date]:
    """The n-th repeat of a run: whole months stay whole months."""
    if every == "week":
        return start + timedelta(weeks=n), end + timedelta(weeks=n)
    months = n * (3 if every == "quarter" else 1)
    return (
        _add_months(start, months, month_end=False),
        _add_months(end, months, month_end=_is_month_end(end)),
    )


class CampaignEventRepeatIn(BaseModel):
    event_id: uuid.UUID
    every: Literal["week", "month", "quarter"] = "month"
    count: int = Field(ge=1, le=24)


@action(
    name="campaign.event.repeat",
    summary="Repeat an event: draft copies on the following weeks, months or quarters.",
    schema=CampaignEventRepeatIn,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    audit="campaign.event.repeated",
    example={"event_id": EXAMPLE_EVENT, "every": "month", "count": 3},
)
def repeat_event(params: CampaignEventRepeatIn, ctx: ActionContext) -> CampaignOut:
    with transaction.atomic():
        source = get_event(ctx, params.event_id)
        campaign = get_visible(ctx, str(source.campaign_id), lock=True)
        if campaign.status == "closed":
            raise Conflict("The campaign is closed.")
        criteria = au.criteria_of(source)
        sequence = au.next_sequence(campaign)
        label = "%d %b %Y" if params.every == "week" else "%b %Y"
        for n in range(1, params.count + 1):
            start, end = shifted(source.period_start, source.period_end, params.every, n)
            event = CampaignEvent.objects.create(
                org_id=ctx.org_id,
                campaign=campaign,
                event_name=f"{source.event_name} ({start.strftime(label)})"[:120],
                sequence_no=sequence,
                period_start=start,
                period_end=end,
                attribution_window_days=source.attribution_window_days,
                budget_amount=source.budget_amount,
                budget_currency=source.budget_currency,
                channels=list(source.channels),
                holdout_pct=source.holdout_pct,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
            au.replace_audience(event, criteria, ctx)
            sequence += 1
    return campaign_out(campaign)


# ── history ──────────────────────────────────────────────────────────────────


class CampaignVersionOut(BaseModel):
    version_no: int
    change: str
    snapshot: dict[str, object]
    approval_request_id: str | None
    created_by: int | None
    created_at: str


class CampaignEventHistoryOut(BaseModel):
    event_id: str
    versions: list[CampaignVersionOut]


@action(
    name="campaign.event.history",
    summary="Every published version of an event: what changed, when, by whom, and approvals.",
    schema=CampaignEventRef,
    output=CampaignEventHistoryOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={"event_id": EXAMPLE_EVENT},
)
def event_history(params: CampaignEventRef, ctx: ActionContext) -> CampaignEventHistoryOut:
    event = get_event(ctx, params.event_id)
    return CampaignEventHistoryOut(
        event_id=str(event.event_id),
        versions=[
            CampaignVersionOut(
                version_no=v.version_no,
                change=v.change,
                snapshot=v.snapshot,
                approval_request_id=None
                if v.approval_request_id is None
                else str(v.approval_request_id),
                created_by=v.created_by,
                created_at=v.created_at.isoformat(),
            )
            for v in CampaignEventVersion.objects.filter(event=event).order_by("version_no")
        ],
    )
