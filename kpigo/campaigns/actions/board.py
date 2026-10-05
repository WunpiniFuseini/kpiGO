"""The tracking board and the reconciliation report (PRD CM-18, CM-20).

``campaign.board`` sums the quarter for the campaigns in your scope: value,
spend, ROI and win-backs, then each campaign's figures for its list row.
``campaign.reconciliation`` accounts for every source outcome over a
campaign's span: credited here, credited to other campaigns, or to none.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from kpigo.action import ActionContext, InvalidInput, action
from kpigo.campaigns import authoring as au
from kpigo.campaigns import board as bd
from kpigo.campaigns import reconcile as rc
from kpigo.campaigns.actions.value import Basis
from kpigo.campaigns.actions.winbacks import CampaignWinbackCountsOut
from kpigo.campaigns.scope import get_visible, visible


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class CampaignMoneyOut(BaseModel):
    currency: str
    budget: str
    spend: str | None
    # None: nothing fed; incremental is also None when any event's is withheld.
    gross: str | None
    incremental: str | None
    roi: str | None
    gross_roi: str | None
    measured_events: int
    # Events whose incremental value is withheld (contaminated baseline, short history).
    withheld_events: int

    @classmethod
    def of(cls, m: bd.Money) -> CampaignMoneyOut:
        return cls(
            currency=m.currency,
            budget=str(m.budget),
            spend=_s(m.spend),
            gross=_s(m.gross),
            incremental=_s(m.incremental),
            roi=_s(m.roi),
            gross_roi=_s(m.gross_roi),
            measured_events=m.measured_events,
            withheld_events=m.withheld_events,
        )


class CampaignBoardRowOut(BaseModel):
    campaign_id: str
    events_in_play: int
    # Distinct customers contacted; None when no contact feed has loaded.
    contacted: int | None
    # Converted customers summed over the events in play.
    converted: int | None
    money: list[CampaignMoneyOut]
    winbacks: CampaignWinbackCountsOut | None


class CampaignBoardIn(BaseModel):
    # Any day in the quarter to sum; blank: this quarter, to date.
    on: date | None = None


class CampaignBoardOut(BaseModel):
    # The quarter, from its first day to today or its last day.
    start: date
    end: date
    basis: Basis
    outcomes_fed: bool
    contacts_fed: bool
    money: list[CampaignMoneyOut]
    winbacks: CampaignWinbackCountsOut | None
    campaigns: list[CampaignBoardRowOut]


@action(
    name="campaign.board",
    summary="The quarter's campaign value, spend, ROI and win-backs, and each campaign's figures.",
    schema=CampaignBoardIn,
    output=CampaignBoardOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={},
)
def campaign_board(params: CampaignBoardIn, ctx: ActionContext) -> CampaignBoardOut:
    today = au.today()
    on = params.on or today
    if on > today:
        raise InvalidInput("Choose a day on or before today.", detail={"field": "on"})
    start = bd.quarter_of(on)
    nxt = date(start.year + (start.month == 10), (start.month + 2) % 12 + 1, 1)
    end = min(today, date.fromordinal(nxt.toordinal() - 1))
    found = bd.build(ctx.org_id, list(visible(ctx)), start, end)
    return CampaignBoardOut(
        start=found.start,
        end=found.end,
        basis=found.basis,
        outcomes_fed=found.outcomes_fed,
        contacts_fed=found.contacts_fed,
        money=[CampaignMoneyOut.of(m) for m in found.money.values()],
        winbacks=None if found.winbacks is None else CampaignWinbackCountsOut.of(found.winbacks),
        campaigns=[
            CampaignBoardRowOut(
                campaign_id=r.campaign_id,
                events_in_play=r.events_in_play,
                contacted=r.contacted,
                converted=r.converted,
                money=[CampaignMoneyOut.of(m) for m in r.money.values()],
                winbacks=None if r.winbacks is None else CampaignWinbackCountsOut.of(r.winbacks),
            )
            for r in found.rows
        ],
    )


# ── reconciliation ───────────────────────────────────────────────────────────


class CampaignReconciliationLineOut(BaseModel):
    metric_code: str
    currency: str | None
    source_total: str
    source_outcomes: int
    credited_here: str
    credited_elsewhere: str
    # Source value no campaign was credited.
    unattributed: str


class CampaignReconciliationEventOut(BaseModel):
    event_id: str
    credited: str
    credited_outcomes: int
    lost: str
    lost_outcomes: int
    held_out_outcomes: int
    # How many matched outcomes each rule decided ("single" had no collision).
    by_rule: dict[
        Literal["single", "holdout", "last_touch", "first_touch", "priority", "split_even"], int
    ]


class CampaignContaminatedOut(BaseModel):
    customer_ref: str
    event_id: str
    contaminated_by: str | None


class CampaignReconciliationIn(BaseModel):
    campaign_id: uuid.UUID


class CampaignReconciliationOut(BaseModel):
    campaign_id: str
    # (first, last]: the days the campaign's published events can attribute; None: none published.
    span_start: date | None
    span_end: date | None
    lines: list[CampaignReconciliationLineOut]
    events: list[CampaignReconciliationEventOut]
    contaminated: list[CampaignContaminatedOut]
    # All of them; the list stops at 200.
    contaminated_total: int
    # Credited never exceeds the source (CM-13).
    invariant_holds: bool


@action(
    name="campaign.reconciliation",
    summary="Attributed value against source totals, with what other events won and why.",
    schema=CampaignReconciliationIn,
    output=CampaignReconciliationOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={"campaign_id": "00000000-0000-0000-0000-000000000000"},
)
def campaign_reconciliation(
    params: CampaignReconciliationIn, ctx: ActionContext
) -> CampaignReconciliationOut:
    campaign = get_visible(ctx, str(params.campaign_id))
    found = rc.build(ctx.org_id, campaign)
    return CampaignReconciliationOut(
        campaign_id=str(campaign.campaign_id),
        span_start=None if found.span is None else found.span[0],
        span_end=None if found.span is None else found.span[1],
        lines=[
            CampaignReconciliationLineOut(
                metric_code=x.metric_code,
                currency=x.currency,
                source_total=str(x.source_total),
                source_outcomes=x.source_outcomes,
                credited_here=str(x.credited_here),
                credited_elsewhere=str(x.credited_elsewhere),
                unattributed=str(x.unattributed),
            )
            for x in found.lines
        ],
        events=[
            CampaignReconciliationEventOut(
                event_id=x.event_id,
                credited=str(x.credited),
                credited_outcomes=x.credited_outcomes,
                lost=str(x.lost),
                lost_outcomes=x.lost_outcomes,
                held_out_outcomes=x.held_out_outcomes,
                by_rule=x.by_rule,
            )
            for x in found.events
        ],
        contaminated=[
            CampaignContaminatedOut(
                customer_ref=x.customer_ref, event_id=x.event_id, contaminated_by=x.contaminated_by
            )
            for x in found.contaminated
        ],
        contaminated_total=found.contaminated_total,
        invariant_holds=found.invariant_holds,
    )
