"""Publishing a campaign result as a registry metric (Scope §9.5, PRD CM-19).

``campaign.metric.publish`` registers one of a campaign's results as a metric in
the registry, bound to the products the client chooses, and records the lineage
link so every surface that shows the metric can badge which campaign it derives
from. Publishing is explicit and goes through the same ``metric_change`` approval
as registering any metric. ``campaign.metric.withdraw`` retires the metric and
frees the result to be published again; ``campaign.metrics`` lists what a campaign
has published.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, Conflict, action
from kpigo.campaigns import publish as pb
from kpigo.campaigns.models import RESULT_KINDS, CampaignPublishedMetric
from kpigo.campaigns.scope import get_visible
from kpigo.metrics.actions.metric import MetricCode, Name, RegisterIn, register
from kpigo.metrics.models import Metric
from kpigo.platform.vocab import Product

ResultKind = Literal[
    "attributed_value", "incremental_value", "conversions", "conversion_rate", "winbacks_confirmed"
]
assert set(ResultKind.__args__) == set(RESULT_KINDS)  # type: ignore[attr-defined]

EXAMPLE_CAMPAIGN = "00000000-0000-0000-0000-000000000000"


class PublishedMetricOut(BaseModel):
    published_id: str
    campaign_id: str
    campaign_code: str
    campaign_name: str
    result_kind: ResultKind
    label: str
    status: Literal["active", "withdrawn"]
    published_at: datetime
    withdrawn_at: datetime | None
    # The registry metric this result became.
    metric_id: str
    metric_code: str
    display_name: str
    unit: str
    direction: str
    aggregation: str
    is_percentage: bool
    products: list[str]

    @classmethod
    def of(cls, pub: CampaignPublishedMetric) -> PublishedMetricOut:
        metric = pub.metric
        products = [b.product for b in metric.bindings.all() if b.is_active]
        return cls(
            published_id=str(pub.published_id),
            campaign_id=str(pub.campaign_id),
            campaign_code=pub.campaign.code,
            campaign_name=pub.campaign.name,
            result_kind=pub.result_kind,
            label=pb.METRIC_SPECS[pub.result_kind].label,
            status=pub.status,
            published_at=pub.created_at,
            withdrawn_at=pub.withdrawn_at,
            metric_id=str(metric.metric_id),
            metric_code=metric.metric_code,
            display_name=metric.display_name,
            unit=metric.unit,
            direction=metric.direction,
            aggregation=metric.aggregation,
            is_percentage=metric.is_percentage,
            products=sorted(products),
        )


def _loaded(published_id: uuid.UUID) -> CampaignPublishedMetric:
    return (
        CampaignPublishedMetric.objects.select_related("campaign", "metric")
        .prefetch_related("metric__bindings")
        .get(published_id=published_id)
    )


class PublishMetricIn(BaseModel):
    campaign_id: uuid.UUID
    result_kind: ResultKind
    # The name the metric carries in the registry; blank takes the result's label.
    display_name: Name | None = None
    metric_code: MetricCode | None = None
    description: str = Field(default="", max_length=4000)
    products: list[Product] = Field(min_length=1)
    effective_from: date | None = None
    # Set after reviewing the comparison panel to register despite similar names.
    acknowledge_similar: bool = False


@action(
    name="campaign.metric.publish",
    summary="Publish a campaign result as a registry metric, with a lineage link.",
    schema=PublishMetricIn,
    output=PublishedMetricOut,
    permission="campaign.metric.publish",
    read_only=False,
    module="campaign",
    requires_approval="metric_change",
    audit="campaign.metric_published",
    config_change=True,
    example={
        "campaign_id": EXAMPLE_CAMPAIGN,
        "result_kind": "attributed_value",
        "products": ["scorecards"],
    },
)
def publish_metric(params: PublishMetricIn, ctx: ActionContext) -> PublishedMetricOut:
    spec = pb.METRIC_SPECS[params.result_kind]
    with transaction.atomic():
        campaign = get_visible(ctx, str(params.campaign_id), lock=True)
        existing = (
            CampaignPublishedMetric.objects.select_for_update()
            .filter(org_id=ctx.org_id, campaign=campaign, result_kind=params.result_kind)
            .first()
        )
        if existing is not None and existing.status == "active":
            raise Conflict(
                "That result is already published for this campaign; withdraw it first to re-publish.",
                detail={"result_kind": params.result_kind},
            )
        if existing is not None:
            # Re-publish: reactivate the metric the result was published as before, so its
            # code and history stay stable rather than minting a second metric.
            metric = existing.metric
            metric.status = "active"
            metric.updated_by = ctx.user_id
            metric.save(update_fields=["status", "updated_by", "updated_at"])
            existing.status = "active"
            existing.withdrawn_at = None
            existing.updated_by = ctx.user_id
            existing.save(update_fields=["status", "withdrawn_at", "updated_by", "updated_at"])
            pub = existing
        else:
            name = params.display_name or f"{spec.label}: {campaign.code}"
            reg = register(
                RegisterIn(
                    display_name=name,
                    metric_code=params.metric_code,
                    description=params.description,
                    direction=spec.direction,
                    aggregation=spec.aggregation,
                    unit=spec.unit,
                    is_percentage=spec.is_percentage,
                    target_scope=spec.target_scope,
                    collection_method="feed",
                    computation_note=(
                        f"Derived from campaign {campaign.code} ({params.result_kind}); "
                        "see the campaign for its lineage."
                    ),
                    products=params.products,
                    status="active",
                    effective_from=params.effective_from,
                    # System-generated names share the campaign suffix; the publish itself
                    # is the explicit, approved choice, so the similar-name panel is moot.
                    acknowledge_similar=True,
                ),
                ctx,
            )
            metric = Metric.objects.get(metric_id=reg.metrics[0].metric_id)
            pub = CampaignPublishedMetric.objects.create(
                org_id=ctx.org_id,
                campaign=campaign,
                metric=metric,
                result_kind=params.result_kind,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
        ctx.audit(
            "campaign.metric_published.detail",
            campaign_id=str(campaign.campaign_id),
            result_kind=params.result_kind,
            metric_code=metric.metric_code,
            products=sorted(params.products),
        )
    return PublishedMetricOut.of(_loaded(pub.published_id))


class WithdrawMetricIn(BaseModel):
    published_id: uuid.UUID


@action(
    name="campaign.metric.withdraw",
    summary="Withdraw a published campaign metric; it goes inactive in the registry.",
    schema=WithdrawMetricIn,
    output=PublishedMetricOut,
    permission="campaign.metric.publish",
    read_only=False,
    module="campaign",
    requires_approval="metric_change",
    audit="campaign.metric_withdrawn",
    config_change=True,
    example={"published_id": EXAMPLE_CAMPAIGN},
)
def withdraw_metric(params: WithdrawMetricIn, ctx: ActionContext) -> PublishedMetricOut:
    with transaction.atomic():
        pub = (
            CampaignPublishedMetric.objects.select_related("campaign", "metric")
            .filter(org_id=ctx.org_id, published_id=params.published_id)
            .first()
        )
        if pub is None:
            raise Conflict("No such published metric.")
        get_visible(ctx, str(pub.campaign_id))  # scope check
        if pub.status != "active":
            raise Conflict("That metric has already been withdrawn.")
        pub.status = "withdrawn"
        pub.withdrawn_at = timezone.now()
        pub.updated_by = ctx.user_id
        pub.save(update_fields=["status", "withdrawn_at", "updated_by", "updated_at"])
        metric = pub.metric
        metric.status = "inactive"
        metric.updated_by = ctx.user_id
        metric.save(update_fields=["status", "updated_by", "updated_at"])
        ctx.audit(
            "campaign.metric_withdrawn.detail",
            campaign_id=str(pub.campaign_id),
            metric_code=metric.metric_code,
        )
    return PublishedMetricOut.of(_loaded(pub.published_id))


class CampaignMetricsIn(BaseModel):
    campaign_id: uuid.UUID


class CampaignMetricsOut(BaseModel):
    campaign_id: str
    published: list[PublishedMetricOut]


@action(
    name="campaign.metrics",
    summary="The registry metrics published from a campaign, active and withdrawn.",
    schema=CampaignMetricsIn,
    output=CampaignMetricsOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={"campaign_id": EXAMPLE_CAMPAIGN},
)
def campaign_metrics(params: CampaignMetricsIn, ctx: ActionContext) -> CampaignMetricsOut:
    campaign = get_visible(ctx, str(params.campaign_id))
    rows = (
        CampaignPublishedMetric.objects.select_related("campaign", "metric")
        .prefetch_related("metric__bindings")
        .filter(org_id=ctx.org_id, campaign=campaign)
        .order_by("-created_at")
    )
    return CampaignMetricsOut(
        campaign_id=str(campaign.campaign_id),
        published=[PublishedMetricOut.of(p) for p in rows],
    )
