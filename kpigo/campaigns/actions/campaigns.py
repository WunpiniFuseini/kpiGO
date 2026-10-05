"""Create, read and manage campaigns (PRD CM-1, CM-4; App Flow §5.1, §5.4).

A campaign is created with its events in one go, the way the builder submits
it; every event starts as a draft. ``campaign.list`` is the board's list,
filtered by tab; ``campaign.get`` is the detail the builder edits.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field, model_validator

from kpigo.action import ActionContext, Conflict, InvalidInput, action
from kpigo.action.pipeline import approval_enabled
from kpigo.campaigns import authoring as au
from kpigo.campaigns.models import CHANNELS, OBJECTIVES, Campaign, CampaignEvent
from kpigo.campaigns.scope import get_visible, is_visible, visible
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import Code, CurrencyCode

Objective = Literal[
    "acquisition",
    "deposit_growth",
    "activation",
    "cross_sell",
    "attrition_winback",
    "collections",
    "awareness",
]
Channel = Literal["sms", "email", "call", "ussd", "branch", "app_push", "whatsapp"]
assert set(Objective.__args__) == set(OBJECTIVES)  # type: ignore[attr-defined]
assert set(Channel.__args__) == set(CHANNELS)  # type: ignore[attr-defined]

Name = Field(min_length=1, max_length=120)
Budget = Field(ge=0, max_digits=18, decimal_places=2)
CODE_TAKEN = {"campaign_code_unique": "Another campaign already uses that code."}


# ── shapes ───────────────────────────────────────────────────────────────────


class CampaignCriterionIn(BaseModel):
    dimension_type: Code
    member_code: Code


class CampaignCriterionOut(BaseModel):
    dimension_type: str
    member_code: str


class CampaignEventIn(BaseModel):
    event_name: str = Name
    period_start: date
    period_end: date
    # Defaults to the objective's window (Starter Packs §6.1).
    attribution_window_days: int | None = Field(default=None, ge=0, le=730)
    budget_amount: Decimal = Budget
    budget_currency: CurrencyCode
    channels: list[Channel] = Field(default_factory=list)
    audience: list[CampaignCriterionIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _dates(self) -> CampaignEventIn:
        if self.period_end < self.period_start:
            raise ValueError("an event ends on or after the day it starts")
        return self


class CampaignPendingBudgetOut(BaseModel):
    approval_request_id: str
    budget_amount: str
    budget_currency: str
    requested_by_id: int | None
    requested_at: str


class CampaignEventOut(BaseModel):
    event_id: str
    campaign_id: str
    sequence_no: int
    event_name: str
    period_start: date
    period_end: date
    attribution_window_days: int
    # The last day an outcome can still attribute to this event.
    window_end: date
    budget_amount: str
    budget_currency: str
    spend_to_date: str | None
    channels: list[str]
    audience: list[CampaignCriterionOut]
    state: str
    status: str
    version: int
    reattribute_from: date | None
    pending_budget: CampaignPendingBudgetOut | None


class CampaignOut(BaseModel):
    campaign_id: str
    code: str
    name: str
    campaign_type: str
    objective: str
    product_code: str | None
    owner_user_id: int | None
    owner_name: str | None
    description: str
    priority: int | None
    status: str
    events: list[CampaignEventOut]
    # True while a live event's budget change waits for a checker (CM-5): the
    # builder says so before the user saves.
    budget_needs_approval: bool


def event_out(event: CampaignEvent) -> CampaignEventOut:
    pending = au.pending_budget(event) if event.state != "draft" else None
    return CampaignEventOut(
        event_id=str(event.event_id),
        campaign_id=str(event.campaign_id),
        sequence_no=event.sequence_no,
        event_name=event.event_name,
        period_start=event.period_start,
        period_end=event.period_end,
        attribution_window_days=event.attribution_window_days,
        window_end=au.window_end(event),
        budget_amount=str(au.money(event.budget_amount)),
        budget_currency=event.budget_currency,
        spend_to_date=None if event.spend_to_date is None else str(event.spend_to_date),
        channels=list(event.channels),
        audience=[
            CampaignCriterionOut(dimension_type=d, member_code=m) for d, m in au.criteria_of(event)
        ],
        state=event.state,
        status=au.event_status(event),
        version=event.version,
        reattribute_from=event.reattribute_from,
        pending_budget=None
        if pending is None
        else CampaignPendingBudgetOut(
            approval_request_id=str(pending.request_id),
            budget_amount=str(pending.payload.get("budget_amount")),
            budget_currency=str(pending.payload.get("budget_currency") or event.budget_currency),
            requested_by_id=pending.requested_by_id,
            requested_at=pending.requested_at.isoformat(),
        ),
    )


def owner_names(org_id: str, user_ids: list[int | None]) -> dict[int | None, str]:
    from kpigo.access.models import AppUser

    wanted = [u for u in user_ids if u is not None]
    return dict(
        AppUser.objects.filter(org_id=org_id, auth_user_id__in=wanted).values_list(
            "auth_user_id", "display_name"
        )
    )


def events_of(campaign: Campaign) -> list[CampaignEvent]:
    return list(
        CampaignEvent.objects.filter(campaign=campaign)
        .select_related("campaign")
        .order_by("sequence_no")
    )


def campaign_out(campaign: Campaign) -> CampaignOut:
    events = events_of(campaign)
    return CampaignOut(
        campaign_id=str(campaign.campaign_id),
        code=campaign.code,
        name=campaign.name,
        campaign_type=campaign.campaign_type,
        objective=campaign.objective,
        product_code=campaign.product_code,
        owner_user_id=campaign.owner_user_id,
        owner_name=owner_names(str(campaign.org_id), [campaign.owner_user_id]).get(
            campaign.owner_user_id
        ),
        description=campaign.description,
        priority=campaign.priority,
        status=au.campaign_status(campaign, events),
        events=[event_out(e) for e in events],
        budget_needs_approval=approval_enabled("budget", str(campaign.org_id)),
    )


def ensure_still_visible(campaign: Campaign, ctx: ActionContext) -> None:
    """Refuse a change that would take the campaign out of its author's own scope."""
    if not is_visible(ctx, campaign.campaign_id):
        raise InvalidInput(
            "After this change the campaign would be outside your campaign scope, and you "
            "could no longer see it. Keep yourself as owner, or ask an Admin for a grant."
        )


def create_event(
    campaign: Campaign, params: CampaignEventIn, ctx: ActionContext, sequence_no: int
) -> CampaignEvent:
    window = params.attribution_window_days
    if window is None:
        window = au.default_window(ctx.org_id, campaign.objective)
    au.check_currency(ctx.org_id, params.budget_currency)
    criteria = [(c.dimension_type, c.member_code) for c in params.audience]
    au.check_audience(ctx.org_id, criteria)
    event = CampaignEvent.objects.create(
        org_id=ctx.org_id,
        campaign=campaign,
        event_name=params.event_name,
        sequence_no=sequence_no,
        period_start=params.period_start,
        period_end=params.period_end,
        attribution_window_days=window,
        budget_amount=au.money(params.budget_amount),
        budget_currency=params.budget_currency,
        channels=sorted(set(params.channels)),
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    au.replace_audience(event, criteria, ctx)
    return event


# ── create ───────────────────────────────────────────────────────────────────


class CampaignCreateIn(BaseModel):
    code: Code
    name: str = Name
    campaign_type: Code
    objective: Objective
    product_code: Code | None = None
    # Defaults to the author.
    owner_user_id: int | None = None
    description: str = Field(default="", max_length=2000)
    priority: int | None = Field(default=None, ge=1)
    events: list[CampaignEventIn] = Field(default_factory=list, max_length=60)


@action(
    name="campaign.create",
    summary="Author a campaign and its events. Every event starts as a draft.",
    schema=CampaignCreateIn,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    audit="campaign.created",
    example={
        "code": "SAVE-Q4",
        "name": "Save more this quarter",
        "campaign_type": "seasonal",
        "objective": "deposit_growth",
        "product_code": "savings",
        "events": [
            {
                "event_name": "October SMS wave",
                "period_start": "2026-10-01",
                "period_end": "2026-10-31",
                "budget_amount": "25000.00",
                "budget_currency": "GHS",
                "channels": ["sms"],
                "audience": [{"dimension_type": "segment", "member_code": "retail"}],
            }
        ],
    },
)
def create_campaign(params: CampaignCreateIn, ctx: ActionContext) -> CampaignOut:
    owner = params.owner_user_id if params.owner_user_id is not None else ctx.user_id
    au.check_owner(ctx.org_id, owner)
    au.check_product(ctx.org_id, params.product_code)
    with conflicts(CODE_TAKEN):
        campaign = Campaign.objects.create(
            org_id=ctx.org_id,
            code=params.code,
            name=params.name,
            campaign_type=params.campaign_type,
            objective=params.objective,
            product_code=params.product_code,
            owner_user_id=owner,
            description=params.description,
            priority=params.priority,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    for number, event in enumerate(params.events, start=1):
        create_event(campaign, event, ctx, number)
    ensure_still_visible(campaign, ctx)
    return campaign_out(campaign)


# ── read ─────────────────────────────────────────────────────────────────────


class CampaignListIn(BaseModel):
    # The board's tabs (App Flow §5.4), plus drafts and everything.
    tab: Literal["running", "scheduled", "closed", "draft", "paused", "all"] = "all"
    objective: Objective | None = None


class CampaignBudgetOut(BaseModel):
    currency: str
    amount: str


class CampaignSummaryOut(BaseModel):
    campaign_id: str
    code: str
    name: str
    campaign_type: str
    objective: str
    product_code: str | None
    owner_user_id: int | None
    owner_name: str | None
    status: str
    event_count: int
    # "event n of m": the event running now, else the next one, else the last.
    current_event: CampaignEventOut | None
    channels: list[str]
    budgets: list[CampaignBudgetOut]


class CampaignListOut(BaseModel):
    campaigns: list[CampaignSummaryOut]
    # Why the list is empty when it is: no scope at all, or nothing on this tab.
    scoped: bool


def _current(events: list[CampaignEvent], on: date) -> CampaignEvent | None:
    live = [e for e in events if e.state != "draft"]
    for status in ("running", "scheduled", "paused"):
        found = [e for e in live if au.event_status(e, on) == status]
        if found:
            return min(found, key=lambda e: e.period_start)
    if live:
        return max(live, key=lambda e: e.period_end)
    return events[0] if events else None


@action(
    name="campaign.list",
    summary="Campaigns in your scope for a board tab, with budgets and the event in play.",
    schema=CampaignListIn,
    output=CampaignListOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={"tab": "running"},
)
def list_campaigns(params: CampaignListIn, ctx: ActionContext) -> CampaignListOut:
    on = au.today()
    query = visible(ctx).order_by("name", "code")
    if params.objective:
        query = query.filter(objective=params.objective)
    campaigns = list(query)
    events: dict[uuid.UUID, list[CampaignEvent]] = defaultdict(list)
    for e in (
        CampaignEvent.objects.filter(campaign__in=campaigns)
        .select_related("campaign")
        .order_by("sequence_no")
    ):
        events[e.campaign_id].append(e)
    names = owner_names(ctx.org_id, [c.owner_user_id for c in campaigns])
    rows: list[CampaignSummaryOut] = []
    for c in campaigns:
        mine = events[c.campaign_id]
        status = au.campaign_status(c, mine, on)
        if params.tab != "all" and status != params.tab:
            continue
        budgets: dict[str, Decimal] = defaultdict(Decimal)
        for e in mine:
            budgets[e.budget_currency] += e.budget_amount
        current = _current(mine, on)
        rows.append(
            CampaignSummaryOut(
                campaign_id=str(c.campaign_id),
                code=c.code,
                name=c.name,
                campaign_type=c.campaign_type,
                objective=c.objective,
                product_code=c.product_code,
                owner_user_id=c.owner_user_id,
                owner_name=names.get(c.owner_user_id),
                status=status,
                event_count=len(mine),
                current_event=None if current is None else event_out(current),
                channels=sorted({ch for e in mine for ch in e.channels}),
                budgets=[
                    CampaignBudgetOut(currency=k, amount=str(au.money(v)))
                    for k, v in sorted(budgets.items())
                ],
            )
        )
    scoped = any(g.module == "campaign" for g in ctx.data_scopes) or bool(campaigns)
    return CampaignListOut(campaigns=rows, scoped=scoped)


class CampaignGetIn(BaseModel):
    campaign_id: uuid.UUID


@action(
    name="campaign.get",
    summary="One campaign with its events, budgets, audiences and any budget change pending.",
    schema=CampaignGetIn,
    output=CampaignOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={"campaign_id": "00000000-0000-0000-0000-000000000000"},
)
def get_campaign(params: CampaignGetIn, ctx: ActionContext) -> CampaignOut:
    return campaign_out(get_visible(ctx, str(params.campaign_id)))


# ── manage ───────────────────────────────────────────────────────────────────


class CampaignUpdateIn(BaseModel):
    campaign_id: uuid.UUID
    name: str | None = Field(default=None, min_length=1, max_length=120)
    campaign_type: Code | None = None
    # Only while no event has been published: attribution reads it.
    objective: Objective | None = None
    product_code: Code | None = None
    owner_user_id: int | None = None
    description: str | None = Field(default=None, max_length=2000)
    priority: int | None = Field(default=None, ge=1)
    clear_priority: bool = False


@action(
    name="campaign.update",
    summary="Change a campaign's name, type, product, owner, description or priority.",
    schema=CampaignUpdateIn,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    audit="campaign.updated",
    example={"campaign_id": "00000000-0000-0000-0000-000000000000", "name": "Save more"},
)
def update_campaign(params: CampaignUpdateIn, ctx: ActionContext) -> CampaignOut:
    with transaction.atomic():
        campaign = get_visible(ctx, str(params.campaign_id), lock=True)
        if campaign.status == "closed":
            raise Conflict("The campaign is closed.")
        before = {
            "name": campaign.name,
            "campaign_type": campaign.campaign_type,
            "objective": campaign.objective,
            "product_code": campaign.product_code,
            "owner_user_id": campaign.owner_user_id,
            "priority": campaign.priority,
        }
        if params.objective is not None and params.objective != campaign.objective:
            if CampaignEvent.objects.filter(campaign=campaign).exclude(state="draft").exists():
                raise Conflict(
                    "The objective decides which outcomes count, so it is fixed once an event "
                    "is published. Start a new campaign for a different objective."
                )
            campaign.objective = params.objective
        if params.product_code is not None:
            au.check_product(ctx.org_id, params.product_code)
            campaign.product_code = params.product_code
        if params.owner_user_id is not None:
            au.check_owner(ctx.org_id, params.owner_user_id)
            campaign.owner_user_id = params.owner_user_id
        for name in ("name", "campaign_type", "description"):
            value = getattr(params, name)
            if value is not None:
                setattr(campaign, name, value)
        if params.clear_priority:
            campaign.priority = None
        elif params.priority is not None:
            campaign.priority = params.priority
        campaign.updated_by = ctx.user_id
        campaign.updated_at = timezone.now()
        campaign.save()
        ensure_still_visible(campaign, ctx)
        after = {k: getattr(campaign, k) for k in before}
        ctx.audit(
            "campaign.changed",
            campaign_id=str(campaign.campaign_id),
            changed={k: [before[k], after[k]] for k in before if before[k] != after[k]},
        )
    return campaign_out(campaign)


class CampaignCloseIn(BaseModel):
    campaign_id: uuid.UUID


@action(
    name="campaign.close",
    summary="Close a campaign and every event in it. Final: a closed campaign is not reopened.",
    schema=CampaignCloseIn,
    output=CampaignOut,
    permission="campaign.manage",
    read_only=False,
    module="campaign",
    audit="campaign.closed",
    example={"campaign_id": "00000000-0000-0000-0000-000000000000"},
)
def close_campaign(params: CampaignCloseIn, ctx: ActionContext) -> CampaignOut:
    with transaction.atomic():
        campaign = get_visible(ctx, str(params.campaign_id), lock=True)
        if campaign.status == "closed":
            raise Conflict("The campaign is already closed.")
        now = timezone.now()
        for event in events_of(campaign):
            if event.state == "closed":
                continue
            was_draft = event.state == "draft"
            event.state = "closed"
            event.closed_at = now
            event.updated_by = ctx.user_id
            event.updated_at = now
            event.save()
            if not was_draft:
                au.record_version(event, "closed", ctx)
        campaign.status = "closed"
        campaign.closed_at = now
        campaign.updated_by = ctx.user_id
        campaign.updated_at = now
        campaign.save()
    return campaign_out(campaign)
