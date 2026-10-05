"""Reach and attribution: estimate an audience, see who an event reached, set the rule.

``campaign.audience.estimate`` sizes an audience from the population feed while
it is being built. ``campaign.reach`` lays each event's funnel beside its
estimate: targeted, contacted, delivered, responded, then outcome reach (the
audience's customers who did what the objective counts, in the window) and
converted (those credited to this event). The attribution rule is one per org
(PRD CM-11), changed through approval and applied to every outcome again.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal, cast

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints

from kpigo.action import ActionContext, action
from kpigo.campaigns import attribution
from kpigo.campaigns import authoring as au
from kpigo.campaigns import reach as rc
from kpigo.campaigns.actions.objectives import objectives_for
from kpigo.campaigns.models import ATTRIBUTION_RULES, CampaignEvent
from kpigo.campaigns.scope import get_visible
from kpigo.platform.models import OrgSettings

Rule = Literal["last_touch", "first_touch", "priority", "split_even"]
assert set(Rule.__args__) == set(ATTRIBUTION_RULES)  # type: ignore[attr-defined]
EstimateReason = Literal["no_criteria", "no_population", "dimension_not_in_population"]


class CampaignReachEstimateOut(BaseModel):
    # None, with the reason, when the population cannot size the audience.
    targeted: int | None
    population: int | None
    as_of: date | None
    reason: EstimateReason | None
    # The audience's dimensions the population is not broken down by.
    dimensions: list[str]

    @classmethod
    def of(cls, e: rc.Estimate) -> CampaignReachEstimateOut:
        return cls(
            targeted=e.targeted,
            population=e.population,
            as_of=e.as_of,
            reason=e.reason,
            dimensions=e.dimensions,
        )


# "dimension_type:member_code", one per criterion: the query string carries flat values.
Criterion = Annotated[
    str,
    StringConstraints(max_length=130, pattern=r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9_.\-/]*$"),
]


class CampaignAudienceEstimateIn(BaseModel):
    audience: list[Criterion] = Field(max_length=200)
    # Size it as of this day (an event's first contact day); blank: the latest population.
    on: date | None = None


@action(
    name="campaign.audience.estimate",
    summary="Estimate how many customers an audience covers, from the population feed.",
    schema=CampaignAudienceEstimateIn,
    output=CampaignReachEstimateOut,
    permission="campaign.manage",
    read_only=True,
    module="campaign",
    example={"audience": ["segment:affluent", "region:GA"]},
)
def estimate_audience(
    params: CampaignAudienceEstimateIn, ctx: ActionContext
) -> CampaignReachEstimateOut:
    criteria = [cast(tuple[str, str], tuple(c.split(":", 1))) for c in params.audience]
    return CampaignReachEstimateOut.of(rc.estimate(ctx.org_id, criteria, params.on))


class CampaignValueOut(BaseModel):
    currency: str | None
    amount: str


class CampaignEventReachOut(BaseModel):
    event_id: str
    estimate: CampaignReachEstimateOut
    # Distinct customers. None: nothing fed says (absent is not zero).
    contacted: int | None
    delivered: int | None
    responded: int | None
    # Customers in the audience with a counted outcome in the window.
    matched_customers: int | None
    # Of those, customers whose outcome was credited to this event.
    converted_customers: int | None
    credited_outcomes: int | None
    attributed: list[CampaignValueOut]


class CampaignReachIn(BaseModel):
    campaign_id: uuid.UUID


class CampaignReachOut(BaseModel):
    campaign_id: str
    attribution_rule: Rule
    # The outcome metrics the campaign's objective counts; empty: nothing attributes.
    outcome_metric_codes: list[str]
    outcomes_fed: bool
    contacts_fed: bool
    events: list[CampaignEventReachOut]


@action(
    name="campaign.reach",
    summary="Each event's reach: estimated audience, contacts, outcome reach and conversions.",
    schema=CampaignReachIn,
    output=CampaignReachOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={"campaign_id": "00000000-0000-0000-0000-000000000000"},
)
def campaign_reach(params: CampaignReachIn, ctx: ActionContext) -> CampaignReachOut:
    campaign = get_visible(ctx, str(params.campaign_id))
    fed = rc.Fed.of(ctx.org_id)
    trees = attribution.Trees(ctx.org_id)
    objective = next(o for o in objectives_for(ctx.org_id) if o.objective == campaign.objective)
    events: list[CampaignEventReachOut] = []
    for event in CampaignEvent.objects.filter(campaign=campaign).order_by("sequence_no"):
        found = rc.event_reach(event, au.criteria_of(event), fed, trees)
        events.append(
            CampaignEventReachOut(
                event_id=str(event.event_id),
                estimate=CampaignReachEstimateOut.of(found.estimate),
                contacted=found.contacted,
                delivered=found.delivered,
                responded=found.responded,
                matched_customers=found.matched_customers,
                converted_customers=found.converted_customers,
                credited_outcomes=found.credited_outcomes,
                attributed=[
                    CampaignValueOut(currency=currency, amount=str(value))
                    for currency, value in found.attributed
                ],
            )
        )
    return CampaignReachOut(
        campaign_id=str(campaign.campaign_id),
        attribution_rule=attribution.rule_for(ctx.org_id),
        outcome_metric_codes=objective.outcome_metric_codes,
        outcomes_fed=fed.outcomes,
        contacts_fed=fed.contacts,
        events=events,
    )


# ── the attribution rule and re-running it ──────────────────────────────────


class CampaignAttributionSummaryOut(BaseModel):
    rule: Rule
    outcomes: int
    attributed: int
    unattributed: int
    # Outcomes more than one event competed for.
    collisions: int

    @classmethod
    def of(cls, s: attribution.Summary) -> CampaignAttributionSummaryOut:
        return cls(
            rule=s.rule,
            outcomes=s.outcomes,
            attributed=s.attributed,
            unattributed=s.unattributed,
            collisions=s.collisions,
        )


class CampaignAttributionRuleIn(BaseModel):
    rule: Rule
    reason: str = Field(default="", max_length=2000)


@action(
    name="campaign.attribution.rule.set",
    summary="Set the org's collision rule and attribute every outcome again under it.",
    schema=CampaignAttributionRuleIn,
    output=CampaignAttributionSummaryOut,
    permission="campaign.config.manage",
    read_only=False,
    module="campaign",
    requires_approval="config_change",
    audit="campaign.attribution.rule_set",
    config_change=True,
    example={"rule": "last_touch", "reason": "Agreed at onboarding"},
)
def set_rule(
    params: CampaignAttributionRuleIn, ctx: ActionContext
) -> CampaignAttributionSummaryOut:
    with transaction.atomic():
        before = attribution.rule_for(ctx.org_id)
        OrgSettings.objects.update_or_create(
            org_id=ctx.org_id,
            defaults={
                "attribution_rule": params.rule,
                "updated_by": ctx.user_id,
                "updated_at": timezone.now(),
            },
            create_defaults={
                "attribution_rule": params.rule,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
            },
        )
        summary = attribution.attribute(ctx.org_id, Q())
        ctx.audit(
            "campaign.attribution.rule_detail",
            before=before,
            after=params.rule,
            reason=params.reason,
            outcomes=summary.outcomes,
        )
    return CampaignAttributionSummaryOut.of(summary)


class CampaignAttributionRunIn(BaseModel):
    # Outcomes on or after this day; blank: every outcome.
    since: date | None = None


@action(
    name="campaign.attribution.run",
    summary="Attribute outcomes again: every one, or those since a day.",
    schema=CampaignAttributionRunIn,
    output=CampaignAttributionSummaryOut,
    permission="campaign.config.manage",
    read_only=False,
    module="campaign",
    audit="campaign.attribution.ran",
    example={"since": "2026-10-01"},
)
def run_attribution(
    params: CampaignAttributionRunIn, ctx: ActionContext
) -> CampaignAttributionSummaryOut:
    with transaction.atomic():
        found = Q() if params.since is None else Q(activity_date__gte=params.since)
        summary = attribution.attribute(ctx.org_id, found)
        if params.since is None:
            # Everything is current now: no event is waiting on a redo.
            CampaignEvent.objects.filter(org_id=ctx.org_id, reattribute_from__isnull=False).update(
                reattribute_from=None
            )
    return CampaignAttributionSummaryOut.of(summary)
