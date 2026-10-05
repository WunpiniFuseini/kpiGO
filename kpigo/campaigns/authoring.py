"""The rules behind authoring and managing campaigns (Scope §9.1a, App Flow §5.1–5.2).

- An event is ``draft`` until published. A draft is edited freely, budget included.
- Publishing makes it ``live``. From then on every change writes a version row,
  a budget change goes through ``campaign.event.budget.set`` (maker-checker,
  class ``budget``, on by default), and a change to its period or audience marks
  the window attribution must redo (``reattribute_from``).
- ``scheduled``/``running``/``closed`` are read from a live event's dates: it is
  running from its first contact day until its attribution window ends.
  Pausing holds it; closing is final.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from django.utils import timezone

from kpigo.action import ActionContext, Conflict, InvalidInput
from kpigo.campaigns.models import (
    Campaign,
    CampaignAudience,
    CampaignEvent,
    CampaignEventVersion,
    CampaignObjective,
)
from kpigo.hierarchy.models import Dimension, DimMember
from kpigo.platform.models import ApprovalRequest, Currency

EventStatus = Literal["draft", "scheduled", "running", "paused", "closed"]
CampaignStatus = Literal["draft", "scheduled", "running", "paused", "closed"]

# The Banking pack's windows (Starter Packs §6.1); objectives it leaves out take 30.
DEFAULT_WINDOWS: dict[str, int] = {
    "acquisition": 45,
    "deposit_growth": 30,
    "activation": 30,
    "cross_sell": 60,
    "attrition_winback": 90,
    "collections": 30,
    "awareness": 30,
}

BUDGET_ACTION = "campaign.event.budget.set"


def today() -> date:
    return timezone.localdate()


def window_end(event: CampaignEvent) -> date:
    """The last day an outcome can still attribute to the event."""
    return event.period_end + timedelta(days=event.attribution_window_days)


def event_status(event: CampaignEvent, on: date | None = None) -> EventStatus:
    on = on or today()
    if event.state in ("draft", "paused", "closed"):
        return event.state  # type: ignore[return-value]
    if on < event.period_start:
        return "scheduled"
    if on <= window_end(event):
        return "running"
    return "closed"


def campaign_status(campaign: Campaign, events: list[CampaignEvent], on: date | None = None) -> str:
    """The board's tab: running beats scheduled beats paused; closed once nothing is left."""
    if campaign.status == "closed":
        return "closed"
    statuses = {event_status(e, on) for e in events}
    for status in ("running", "scheduled", "paused"):
        if status in statuses:
            return status
    if statuses - {"draft"}:
        return "closed"
    return "draft"


def default_window(org_id: str, objective: str) -> int:
    row = CampaignObjective.objects.filter(org_id=org_id, objective=objective).first()
    return row.default_window_days if row else DEFAULT_WINDOWS.get(objective, 30)


# ── validation ───────────────────────────────────────────────────────────────


def check_currency(org_id: str, code: str) -> None:
    if not Currency.objects.filter(org_id=org_id, code=code, is_active=True).exists():
        known = sorted(
            Currency.objects.filter(org_id=org_id, is_active=True).values_list("code", flat=True)
        )
        raise InvalidInput(
            f"'{code}' is not an active currency. A Data Steward adds currencies.",
            detail={"field": "budget_currency", "currencies": known},
        )


def check_product(org_id: str, product_code: str | None) -> None:
    """Where the install defines a ``product`` dimension, the product must be one of it."""
    if product_code is None:
        return
    if not Dimension.objects.filter(org_id=org_id, dimension_type="product").exists():
        return
    if not DimMember.objects.filter(
        org_id=org_id, dimension_type="product", member_code=product_code, status="active"
    ).exists():
        raise InvalidInput(
            f"'{product_code}' is not an active member of the product dimension.",
            detail={"field": "product_code"},
        )


def check_audience(org_id: str, criteria: list[tuple[str, str]]) -> None:
    """Every criterion names an active member of a defined dimension (CM-3)."""
    if not criteria:
        return
    types = {d for d, _ in criteria}
    defined = set(
        Dimension.objects.filter(org_id=org_id, dimension_type__in=types).values_list(
            "dimension_type", flat=True
        )
    )
    unknown_types = sorted(types - defined)
    if unknown_types:
        raise InvalidInput(
            f"No dimension called {', '.join(unknown_types)}.",
            detail={"field": "audience", "dimension_types": unknown_types},
        )
    active = set(
        DimMember.objects.filter(org_id=org_id, dimension_type__in=types, status="active")
        .values_list("dimension_type", "member_code")
        .iterator()
    )
    missing = sorted(f"{d}={m}" for d, m in criteria if (d, m) not in active)
    if missing:
        raise InvalidInput(
            f"Not active dimension members: {', '.join(missing)}.",
            detail={"field": "audience", "members": missing},
        )


def check_owner(org_id: str, owner_user_id: int | None) -> None:
    from kpigo.access.models import AppUser

    if owner_user_id is None:
        return
    if not AppUser.objects.filter(
        org_id=org_id, auth_user_id=owner_user_id, status="active"
    ).exists():
        raise InvalidInput("The owner must be an active user.", detail={"field": "owner_user_id"})


def check_period(start: date, end: date, window: int) -> None:
    if end < start:
        raise InvalidInput(
            "An event ends on or after the day it starts.", detail={"field": "period_end"}
        )
    if window < 0:
        raise InvalidInput(
            "The attribution window is zero days or more.",
            detail={"field": "attribution_window_days"},
        )


# ── audience and versions ────────────────────────────────────────────────────


def criteria_of(event: CampaignEvent) -> list[tuple[str, str]]:
    return sorted(
        CampaignAudience.objects.filter(event=event).values_list("dimension_type", "member_code")
    )


def replace_audience(
    event: CampaignEvent, criteria: list[tuple[str, str]], ctx: ActionContext
) -> bool:
    """Store the event's criteria; whether they changed."""
    wanted = sorted(set(criteria))
    if wanted == criteria_of(event):
        return False
    CampaignAudience.objects.filter(event=event).delete()
    CampaignAudience.objects.bulk_create(
        CampaignAudience(
            org_id=event.org_id,
            event=event,
            dimension_type=d,
            member_code=m,
            created_by=ctx.user_id,
        )
        for d, m in wanted
    )
    return True


def snapshot(event: CampaignEvent) -> dict[str, Any]:
    return {
        "event_name": event.event_name,
        "period_start": event.period_start.isoformat(),
        "period_end": event.period_end.isoformat(),
        "attribution_window_days": event.attribution_window_days,
        "budget_amount": str(event.budget_amount),
        "budget_currency": event.budget_currency,
        "channels": list(event.channels),
        "holdout_pct": event.holdout_pct,
        "state": event.state,
        "audience": [{"dimension_type": d, "member_code": m} for d, m in criteria_of(event)],
    }


def record_version(event: CampaignEvent, change: str, ctx: ActionContext) -> None:
    """Write the event as it now stands; a draft's edits are not versions until it publishes."""
    latest = CampaignEventVersion.objects.filter(event=event).order_by("-version_no").first()
    number = latest.version_no + 1 if latest else 1
    event.version = number
    event.save(update_fields=["version"])
    CampaignEventVersion.objects.create(
        org_id=event.org_id,
        event=event,
        version_no=number,
        change=change,
        snapshot=snapshot(event),
        approval_request_id=ctx.approval.approval_request_id if ctx.approval else None,
        created_by=ctx.user_id,
    )


def reopen(event: CampaignEvent, from_day: date) -> None:
    """Mark attribution to redo from ``from_day`` on the next outcome load."""
    current = event.reattribute_from
    event.reattribute_from = from_day if current is None else min(current, from_day)


def require_editable(event: CampaignEvent) -> None:
    if event.state == "closed":
        raise Conflict("The event is closed. A closed event is not changed.")
    if event.campaign.status == "closed":
        raise Conflict("The campaign is closed.")


def pending_budget(event: CampaignEvent) -> ApprovalRequest | None:
    return (
        ApprovalRequest.objects.filter(
            org_id=event.org_id,
            action_name=BUDGET_ACTION,
            status=ApprovalRequest.Status.PENDING,
            payload__event_id=str(event.event_id),
        )
        .order_by("-requested_at")
        .first()
    )


def next_sequence(campaign: Campaign) -> int:
    last = CampaignEvent.objects.filter(campaign=campaign).order_by("-sequence_no").first()
    return last.sequence_no + 1 if last else 1


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))
