"""Baseline suppression: what a campaign caused, not just what followed it (Scope §9.2).

For each customer an event was credited for, the baseline is the same
customer's value of the same metric over an equal-length window immediately
before the event's span: the span is ``(period_start, window_end]``, so the
baseline window is ``(period_start - L, period_start]`` with ``L`` its length.
Incremental is gross minus baseline, and may be negative: a bad month reads as
one. The baseline is scaled by the share of the customer's in-span value this
event was credited, so a split or a lost collision subtracts only its part.

Two cases are shown rather than folded in:

- **New customers** have no outcome on or before the end of the baseline window.
  Their baseline is absent, not zero, and their gross counts as incremental.
- **Contaminated baselines**: another published event contacted the customer
  (or had them in its audience) during the baseline window. The baseline no
  longer measures "without a campaign", so the incremental figure is withheld.

Baselines are recomputed whenever attribution runs over days that touch an
event's span or baseline window.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db.models import Sum
from django.utils import timezone

from kpigo.campaigns.models import (
    CampaignAttribution,
    CampaignBaseline,
    CampaignContact,
    CampaignOutcome,
)

if TYPE_CHECKING:
    from kpigo.campaigns.attribution import EventSpec

QUANTUM = Decimal("0.0001")
Key = tuple[str, str, str]  # customer_ref, metric_id, currency ("" when none)


def window(start: date, end: date) -> tuple[date, date]:
    """The baseline window for the span ``(start, end]``: ``(start - L, start]``."""
    return start - timedelta(days=(end - start).days), start


def refresh(
    org_id: str, events: list[EventSpec], lo: date, hi: date, *, now: datetime | None = None
) -> None:
    """Measure again every event whose baseline window or span touches ``[lo, hi]``."""
    now = now or timezone.now()
    for spec in events:
        first, _ = window(spec.period_start, spec.window_end)
        if spec.window_end < lo or first >= hi:
            continue
        measure(org_id, spec, events, now)


def _totals(query: object) -> dict[Key, Decimal]:
    from django.db.models import QuerySet

    assert isinstance(query, QuerySet)
    return {
        (r["customer_ref"], str(r["metric_id"]), r["currency_code"] or ""): r["value"]
        for r in query.values("customer_ref", "metric_id", "currency_code").annotate(
            value=Sum("activity_value")
        )
    }


def measure(org_id: str, spec: EventSpec, events: list[EventSpec], now: datetime) -> None:
    CampaignBaseline.objects.filter(event_id=spec.event_id).delete()
    gross: dict[Key, Decimal] = defaultdict(Decimal)
    sample: dict[str, CampaignOutcome] = {}
    for a in CampaignAttribution.objects.filter(
        event_id=spec.event_id, credited=True
    ).select_related("outcome"):
        o = a.outcome
        gross[(o.customer_ref, str(o.metric_id), o.currency_code or "")] += a.attributed_value
        if o.customer_ref not in sample or o.activity_date > sample[o.customer_ref].activity_date:
            sample[o.customer_ref] = o
    if not gross:
        return
    customers = sorted(sample)
    metrics = sorted({k[1] for k in gross})
    first, last = window(spec.period_start, spec.window_end)
    theirs = CampaignOutcome.objects.filter(
        org_id=org_id, customer_ref__in=customers, metric_id__in=metrics
    )
    in_span = _totals(
        theirs.filter(activity_date__gt=spec.period_start, activity_date__lte=spec.window_end)
    )
    before = _totals(theirs.filter(activity_date__gt=first, activity_date__lte=last))
    known = set(
        CampaignOutcome.objects.filter(
            org_id=org_id, customer_ref__in=customers, activity_date__lte=last
        )
        .values_list("customer_ref", flat=True)
        .distinct()
    )
    spoiled = contaminated(org_id, spec, events, sample, first, last)
    rows = []
    for key, value in gross.items():
        customer = key[0]
        row = CampaignBaseline(
            org_id=org_id,
            event_id=spec.event_id,
            customer_ref=customer,
            metric_id=key[1],
            currency_code=key[2],
            gross=value,
            computed_at=now,
        )
        total = in_span.get(key) or value
        share = value / total if total else Decimal(1)
        scaled = (before.get(key, Decimal(0)) * share).quantize(QUANTUM)
        if customer in spoiled:
            row.confidence = "low_contaminated_baseline"
            row.baseline = scaled
            row.contaminated_by_id = spoiled[customer]
        elif customer not in known:
            row.confidence = "new_customer"
            row.incremental = value
        else:
            row.confidence = "high"
            row.baseline = scaled
            row.incremental = value - scaled
        rows.append(row)
    CampaignBaseline.objects.bulk_create(rows)


def contaminated(
    org_id: str,
    spec: EventSpec,
    events: list[EventSpec],
    sample: dict[str, CampaignOutcome],
    first: date,
    last: date,
) -> dict[str, uuid.UUID]:
    """Customers another event reached during the baseline window, and which event."""
    rivals = [
        e
        for e in events
        if e.event_id != spec.event_id and e.period_start < last and e.period_end > first
    ]
    if not rivals:
        return {}
    contacts: dict[uuid.UUID, set[str]] = defaultdict(set)
    held: dict[uuid.UUID, set[str]] = defaultdict(set)
    for event_id, customer, holdout in CampaignContact.objects.filter(
        event_id__in=[e.event_id for e in rivals], customer_ref__in=sorted(sample)
    ).values_list("event_id", "customer_ref", "holdout"):
        (held if holdout else contacts)[event_id].add(customer)
    found: dict[str, uuid.UUID] = {}
    for customer, outcome in sample.items():
        for e in rivals:
            if customer in held[e.event_id]:
                continue
            reached = (
                customer in contacts[e.event_id]
                or outcome.campaign_id == e.campaign_id
                or e.in_audience(outcome)
            )
            if reached:
                found[customer] = e.event_id
                break
    return found
