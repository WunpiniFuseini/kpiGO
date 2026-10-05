"""Win-backs: each event's provisional, confirmed and lapsed win-backs, and the retention window.

``campaign.winbacks`` counts, for each event of a campaign, the win-backs it
earned and how many have held for the retention window. The window is one per
org (Scope §9.4, 90 days by default), changed through approval; shortening it
confirms provisional win-backs at once, lengthening it moves them back.
"""

from __future__ import annotations

import uuid
from datetime import date

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, action
from kpigo.campaigns import authoring as au
from kpigo.campaigns import winbacks as wb
from kpigo.campaigns.models import CampaignEvent, CampaignWinback
from kpigo.campaigns.scope import get_visible
from kpigo.platform.models import OrgSettings


class CampaignWinbackCountsOut(BaseModel):
    qualified: int
    provisional: int
    confirmed: int
    lapsed: int
    # When the earliest provisional win-back confirms, if nothing changes.
    next_confirmation: date | None

    @classmethod
    def of(cls, c: wb.Counts) -> CampaignWinbackCountsOut:
        return cls(
            qualified=c.qualified,
            provisional=c.provisional,
            confirmed=c.confirmed,
            lapsed=c.lapsed,
            next_confirmation=c.next_confirmation,
        )


class CampaignEventWinbacksOut(BaseModel):
    event_id: str
    # None: nothing fed says (absent is not zero), or the event is a draft.
    counts: CampaignWinbackCountsOut | None


class CampaignWinbacksIn(BaseModel):
    campaign_id: uuid.UUID


class CampaignWinbacksOut(BaseModel):
    campaign_id: str
    retention_days: int
    # Whether any win-back has been fed for the org.
    fed: bool
    # Only attrition win-back campaigns earn win-backs.
    earns_winbacks: bool
    events: list[CampaignEventWinbacksOut]


@action(
    name="campaign.winbacks",
    summary="Each event's win-backs: provisional, confirmed after the retention window, lapsed.",
    schema=CampaignWinbacksIn,
    output=CampaignWinbacksOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={"campaign_id": "00000000-0000-0000-0000-000000000000"},
)
def campaign_winbacks(params: CampaignWinbacksIn, ctx: ActionContext) -> CampaignWinbacksOut:
    campaign = get_visible(ctx, str(params.campaign_id))
    days = wb.retention_days(ctx.org_id)
    fed = CampaignWinback.objects.filter(org_id=ctx.org_id).exists()
    events = list(CampaignEvent.objects.filter(campaign=campaign).order_by("sequence_no"))
    earns = campaign.objective == wb.OBJECTIVE
    found = (
        wb.by_event([e for e in events if e.state != "draft"], days, au.today())
        if fed and earns
        else {}
    )
    return CampaignWinbacksOut(
        campaign_id=str(campaign.campaign_id),
        retention_days=days,
        fed=fed,
        earns_winbacks=earns,
        events=[
            CampaignEventWinbacksOut(
                event_id=str(e.event_id),
                counts=CampaignWinbackCountsOut.of(found[e.event_id])
                if e.event_id in found
                else None,
            )
            for e in events
        ],
    )


class CampaignWinbackRetentionIn(BaseModel):
    days: int = Field(ge=1, le=730)
    reason: str = Field(min_length=1, max_length=2000)


class CampaignWinbackRetentionOut(BaseModel):
    retention_days: int
    # Across the org, under the new window.
    counts: CampaignWinbackCountsOut


@action(
    name="campaign.winback.retention.set",
    summary="Change how many days a win-back must hold before it is confirmed.",
    schema=CampaignWinbackRetentionIn,
    output=CampaignWinbackRetentionOut,
    permission="campaign.config.manage",
    read_only=False,
    module="campaign",
    requires_approval="config_change",
    audit="campaign.winback.retention_set",
    config_change=True,
    example={"days": 90, "reason": "Agreed at onboarding"},
)
def set_retention(
    params: CampaignWinbackRetentionIn, ctx: ActionContext
) -> CampaignWinbackRetentionOut:
    with transaction.atomic():
        before = wb.retention_days(ctx.org_id)
        OrgSettings.objects.update_or_create(
            org_id=ctx.org_id,
            defaults={
                "winback_retention_days": params.days,
                "updated_by": ctx.user_id,
                "updated_at": timezone.now(),
            },
            create_defaults={
                "winback_retention_days": params.days,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
            },
        )
        ctx.audit(
            "campaign.winback.retention_detail",
            before=before,
            after=params.days,
            reason=params.reason,
        )
        counts = wb.counts(
            CampaignWinback.objects.filter(org_id=ctx.org_id), params.days, au.today()
        )
    return CampaignWinbackRetentionOut(
        retention_days=params.days, counts=CampaignWinbackCountsOut.of(counts)
    )
