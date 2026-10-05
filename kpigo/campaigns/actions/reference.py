"""What the campaign builder needs to offer, in one read (Design Brief §5.6, ``CampaignBuilder``).

Currencies, the dimensions an audience can be built from with their active
members, each objective's default window, the channels, and whether a live
budget change will wait for approval, so the builder can say so before saving.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel

from kpigo.action import ActionContext, action
from kpigo.action.pipeline import approval_enabled
from kpigo.campaigns.actions.objectives import objectives_for
from kpigo.campaigns.models import CHANNELS
from kpigo.hierarchy.models import Dimension, DimMember
from kpigo.platform.models import Currency, OrgSettings


class CampaignMemberOptionOut(BaseModel):
    member_code: str
    member_name: str
    parent_code: str | None


class CampaignDimensionOptionOut(BaseModel):
    dimension_type: str
    display_name: str
    members: list[CampaignMemberOptionOut]


class CampaignWindowOut(BaseModel):
    objective: str
    default_window_days: int


class CampaignBuilderIn(BaseModel):
    pass


class CampaignBuilderOut(BaseModel):
    currencies: list[str]
    default_currency: str | None
    dimensions: list[CampaignDimensionOptionOut]
    objectives: list[CampaignWindowOut]
    channels: list[str]
    budget_needs_approval: bool


@action(
    name="campaign.builder.reference",
    summary="Currencies, audience dimensions, objective windows and channels for the builder.",
    schema=CampaignBuilderIn,
    output=CampaignBuilderOut,
    permission="campaign.manage",
    read_only=True,
    module="campaign",
    example={},
)
def builder_reference(params: CampaignBuilderIn, ctx: ActionContext) -> CampaignBuilderOut:
    currencies = list(
        Currency.objects.filter(org_id=ctx.org_id, is_active=True)
        .order_by("code")
        .values_list("code", flat=True)
    )
    settings = OrgSettings.objects.filter(org_id=ctx.org_id).first()
    reporting = settings.reporting_currency if settings else None
    members: dict[str, list[CampaignMemberOptionOut]] = defaultdict(list)
    for m in DimMember.objects.filter(org_id=ctx.org_id, status="active").order_by(
        "dimension_type", "sort_order", "member_name"
    ):
        members[m.dimension_type].append(
            CampaignMemberOptionOut(
                member_code=m.member_code, member_name=m.member_name, parent_code=m.parent_code
            )
        )
    return CampaignBuilderOut(
        currencies=currencies,
        default_currency=reporting
        if reporting in currencies
        else (currencies[0] if currencies else None),
        dimensions=[
            CampaignDimensionOptionOut(
                dimension_type=d.dimension_type,
                display_name=d.display_name,
                members=members[d.dimension_type],
            )
            for d in Dimension.objects.filter(org_id=ctx.org_id).order_by(
                "sort_order", "display_name"
            )
            if members[d.dimension_type]
        ],
        objectives=[
            CampaignWindowOut(objective=o.objective, default_window_days=o.default_window_days)
            for o in objectives_for(ctx.org_id)
        ],
        channels=list(CHANNELS),
        budget_needs_approval=approval_enabled("budget", ctx.org_id),
    )
