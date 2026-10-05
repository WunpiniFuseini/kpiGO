"""Reach: who an event was for, who it got to, and who acted (PRD CM-15).

- **Targeted** is an estimate: the customer population feed's counts for the
  members the audience names. kpiGo holds no customer list, so this is the
  only way to size an audience. It is unavailable, with the reason, when no
  population has been fed, or when the audience names a dimension the
  population is not broken down by.
- **Contacted, delivered, responded** come from the contact feed.
- **Outcome reach** is the customers in the audience who did something the
  objective counts within the window; **converted** is those whose outcome was
  credited to this event after the collision rule. The gap between the two is
  what other events won.

Absent is not zero: a figure with nothing fed behind it is ``None``, never 0.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Count, QuerySet, Sum

from kpigo.campaigns.attribution import Trees
from kpigo.campaigns.models import (
    CampaignAttribution,
    CampaignContact,
    CampaignEvent,
    CampaignOutcome,
    CampaignPopulation,
)
from kpigo.ingestion.validator import CUSTOMER_DIMENSIONS

NO_CRITERIA = "no_criteria"
NO_POPULATION = "no_population"
NOT_BROKEN_DOWN = "dimension_not_in_population"


@dataclass
class Estimate:
    targeted: int | None
    population: int | None
    as_of: date | None
    reason: str | None = None
    # Dimensions the population cannot size the audience by.
    dimensions: list[str] = field(default_factory=list)


def snapshot_on(org_id: str, on: date | None) -> date | None:
    """The population snapshot to size with: the latest on or before ``on``, else the earliest."""
    days = CampaignPopulation.objects.filter(org_id=org_id)
    if on is not None:
        before = days.filter(snapshot_date__lte=on).order_by("-snapshot_date")
        found = before.values_list("snapshot_date", flat=True).first()
        if found is not None:
            return found
        return days.order_by("snapshot_date").values_list("snapshot_date", flat=True).first()
    return days.order_by("-snapshot_date").values_list("snapshot_date", flat=True).first()


def estimate(
    org_id: str,
    criteria: list[tuple[str, str]],
    on: date | None = None,
    trees: Trees | None = None,
) -> Estimate:
    snapshot = snapshot_on(org_id, on)
    if snapshot is None:
        return Estimate(None, None, None, NO_POPULATION)
    rows = CampaignPopulation.objects.filter(org_id=org_id, snapshot_date=snapshot)
    total = rows.aggregate(n=Sum("customer_count"))["n"]
    if not criteria:
        return Estimate(None, total, snapshot, NO_CRITERIA)
    by_dim: dict[str, set[str]] = defaultdict(set)
    for dim, code in criteria:
        by_dim[dim].add(code)
    missing = sorted(
        dim
        for dim in by_dim
        if dim not in CUSTOMER_DIMENSIONS
        or not rows.filter(**{f"{CUSTOMER_DIMENSIONS[dim]}__isnull": False}).exists()
    )
    if missing:
        return Estimate(None, total, snapshot, NOT_BROKEN_DOWN, missing)
    trees = trees or Trees(org_id)
    for dim, codes in by_dim.items():
        rows = rows.filter(**{f"{CUSTOMER_DIMENSIONS[dim]}__in": sorted(trees.below(dim, codes))})
    targeted = rows.aggregate(n=Sum("customer_count"))["n"] or 0
    return Estimate(int(targeted), total, snapshot)


@dataclass
class EventReach:
    estimate: Estimate
    contacted: int | None = None
    delivered: int | None = None
    responded: int | None = None
    matched_customers: int | None = None
    converted_customers: int | None = None
    credited_outcomes: int | None = None
    attributed: list[tuple[str | None, Decimal]] = field(default_factory=list)


@dataclass
class Fed:
    """Which campaign feeds have loaded anything for the org: absent is not zero."""

    outcomes: bool
    contacts: bool

    @classmethod
    def of(cls, org_id: str) -> Fed:
        return cls(
            outcomes=CampaignOutcome.objects.filter(org_id=org_id).exists(),
            contacts=CampaignContact.objects.filter(org_id=org_id).exists(),
        )


def _distinct(query: QuerySet[CampaignContact], flag: str | None = None) -> int | None:
    """Distinct customers contacted; for a flag, None when no row says either way."""
    if flag is not None:
        if not query.filter(**{f"{flag}__isnull": False}).exists():
            return None
        query = query.filter(**{flag: True})
    return query.values("customer_ref").distinct().count()


def event_reach(
    event: CampaignEvent,
    criteria: list[tuple[str, str]],
    fed: Fed,
    trees: Trees | None = None,
) -> EventReach:
    reach = EventReach(estimate(str(event.org_id), criteria, event.period_start, trees))
    if event.state == "draft":
        return reach
    if fed.contacts:
        contacts = CampaignContact.objects.filter(event=event)
        reach.contacted = _distinct(contacts)
        reach.delivered = _distinct(contacts, "delivered")
        reach.responded = _distinct(contacts, "responded")
    if fed.outcomes:
        matched = CampaignAttribution.objects.filter(event=event)
        reach.matched_customers = matched.values("outcome__customer_ref").distinct().count()
        credited = matched.filter(credited=True)
        totals = credited.aggregate(
            customers=Count("outcome__customer_ref", distinct=True), outcomes=Count("outcome")
        )
        reach.converted_customers = totals["customers"]
        reach.credited_outcomes = totals["outcomes"]
        reach.attributed = [
            (row["outcome__currency_code"], row["value"])
            for row in credited.values("outcome__currency_code")
            .annotate(value=Sum("attributed_value"))
            .order_by("outcome__currency_code")
        ]
    return reach
