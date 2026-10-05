"""Value and ROI: what each event was worth, and which figure leads (Scope §9.1b, §9.2).

``campaign.value`` reads each event's gross, baseline, incremental, control
group and ROI. Incremental leads unless the org's value basis says gross; the
basis is fixed at onboarding and changed only through approval, with an audit
entry and a banner on every value screen from then on.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, action
from kpigo.campaigns import reach as rc
from kpigo.campaigns import value as vl
from kpigo.campaigns.actions.reach import CampaignValueOut
from kpigo.campaigns.models import VALUE_BASES, CampaignEvent
from kpigo.campaigns.scope import get_visible
from kpigo.platform.models import OrgSettings

Basis = Literal["incremental", "gross"]
assert set(Basis.__args__) == set(VALUE_BASES)  # type: ignore[attr-defined]
Withheld = Literal["not_published", "no_outcomes_fed", "contaminated_baseline", "history_too_short"]
RoiReason = Literal[
    "not_published",
    "no_outcomes_fed",
    "contaminated_baseline",
    "history_too_short",
    "no_value_in_budget_currency",
    "no_budget",
]
ControlReason = Literal["not_published", "no_contacts_fed", "no_control_group", "no_outcomes_fed"]


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class CampaignControlOut(BaseModel):
    # The share planned in the builder; the rest is what the contact feed says.
    planned_pct: int | None
    treated: int | None
    control: int | None
    actual_pct: str | None
    # Share of each group with an outcome the event matched, 0..1.
    treated_rate: str | None
    control_rate: str | None
    lift_points: str | None
    # (value per contacted - value per held-out customer) x contacted, budget currency.
    incremental: str | None
    # Fewer held-out customers than the lift can lean on.
    small: bool
    reason: ControlReason | None


class CampaignEventValueOut(BaseModel):
    event_id: str
    currency: str
    budget: str
    spend_to_date: str | None
    # (start, end]: the equal-length window before the event that each baseline covers.
    baseline_start: date
    baseline_end: date
    # Budget currency only. None: nothing fed says.
    gross: str | None
    other_currencies: list[CampaignValueOut]
    baseline: str | None
    incremental: str | None
    withheld: Withheld | None
    converted_customers: int | None
    new_customers: int | None
    contaminated_customers: int | None
    # Ratios: 0.25 is a 25% return.
    roi: str | None
    gross_roi: str | None
    roi_reason: RoiReason | None
    cost_per_outcome: str | None
    utilisation: str | None
    control: CampaignControlOut


class CampaignValueIn(BaseModel):
    campaign_id: uuid.UUID


class CampaignValueReportOut(BaseModel):
    campaign_id: str
    basis: Basis
    # Set once the basis has been changed after onboarding: screens show a banner.
    basis_changed_at: datetime | None
    events: list[CampaignEventValueOut]


def _event_out(v: vl.EventValue) -> CampaignEventValueOut:
    c = v.control
    return CampaignEventValueOut(
        event_id=v.event_id,
        currency=v.currency,
        budget=str(v.budget),
        spend_to_date=_s(v.spend),
        baseline_start=v.baseline_window[0],
        baseline_end=v.baseline_window[1],
        gross=_s(v.gross),
        other_currencies=[
            CampaignValueOut(currency=cur, amount=str(amount)) for cur, amount in v.other_currencies
        ],
        baseline=_s(v.baseline),
        incremental=_s(v.incremental),
        withheld=v.withheld,
        converted_customers=v.converted_customers,
        new_customers=v.new_customers,
        contaminated_customers=v.contaminated_customers,
        roi=_s(v.roi),
        gross_roi=_s(v.gross_roi),
        roi_reason=v.roi_reason,
        cost_per_outcome=_s(v.cost_per_outcome),
        utilisation=_s(v.utilisation),
        control=CampaignControlOut(
            planned_pct=c.planned_pct,
            treated=c.treated,
            control=c.control,
            actual_pct=_s(c.actual_pct),
            treated_rate=_s(c.treated_rate),
            control_rate=_s(c.control_rate),
            lift_points=_s(c.lift_points),
            incremental=_s(c.incremental),
            small=c.small,
            reason=c.reason,
        ),
    )


@action(
    name="campaign.value",
    summary="Each event's gross and incremental value, control-group lift and ROI.",
    schema=CampaignValueIn,
    output=CampaignValueReportOut,
    permission="campaign.view",
    read_only=True,
    module="campaign",
    example={"campaign_id": "00000000-0000-0000-0000-000000000000"},
)
def campaign_value(params: CampaignValueIn, ctx: ActionContext) -> CampaignValueReportOut:
    campaign = get_visible(ctx, str(params.campaign_id))
    basis, changed = vl.basis_for(ctx.org_id)
    fed = rc.Fed.of(ctx.org_id)
    return CampaignValueReportOut(
        campaign_id=str(campaign.campaign_id),
        basis=basis,
        basis_changed_at=changed,
        events=[
            _event_out(vl.event_value(e, basis, fed.outcomes, fed.contacts))
            for e in CampaignEvent.objects.filter(campaign=campaign).order_by("sequence_no")
        ],
    )


class CampaignValueBasisIn(BaseModel):
    basis: Basis
    reason: str = Field(min_length=1, max_length=2000)


class CampaignValueBasisOut(BaseModel):
    basis: Basis
    basis_changed_at: datetime | None


@action(
    name="campaign.value_basis.set",
    summary="Change whether campaign value and ROI lead with incremental or gross.",
    schema=CampaignValueBasisIn,
    output=CampaignValueBasisOut,
    permission="campaign.config.manage",
    read_only=False,
    module="campaign",
    requires_approval="config_change",
    audit="campaign.value_basis.set",
    config_change=True,
    example={"basis": "gross", "reason": "Board reporting is on gross value this year"},
)
def set_value_basis(params: CampaignValueBasisIn, ctx: ActionContext) -> CampaignValueBasisOut:
    with transaction.atomic():
        before, changed = vl.basis_for(ctx.org_id)
        if before != params.basis:
            changed = timezone.now()
            OrgSettings.objects.update_or_create(
                org_id=ctx.org_id,
                defaults={
                    "campaign_value_basis": params.basis,
                    "value_basis_changed_at": changed,
                    "updated_by": ctx.user_id,
                    "updated_at": changed,
                },
                create_defaults={
                    "campaign_value_basis": params.basis,
                    "value_basis_changed_at": changed,
                    "created_by": ctx.user_id,
                    "updated_by": ctx.user_id,
                },
            )
            ctx.audit(
                "campaign.value_basis.detail",
                before=before,
                after=params.basis,
                reason=params.reason,
            )
    return CampaignValueBasisOut(basis=params.basis, basis_changed_at=changed)
