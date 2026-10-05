"""Reconciliation: attributed value against the source totals it came from (PRD CM-20).

For one campaign, over the days its published events can attribute, every
outcome of the objective's metrics is accounted for exactly once, per metric
and currency:

    source total = credited to this campaign
                 + credited to other campaigns
                 + attributed to no campaign

The three lines always sum to the source, because attribution never credits more
than an outcome's value (CM-13). Each event then shows what it was credited,
what it matched but lost to another event under the collision rule, and what
its control group did; customers whose baseline was contaminated are listed so
the withheld incremental figure can be traced.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum

from kpigo.campaigns import attribution
from kpigo.campaigns.models import (
    Campaign,
    CampaignAttribution,
    CampaignBaseline,
    CampaignEvent,
    CampaignObjective,
    CampaignOutcome,
)
from kpigo.metrics.models import Metric

# Contaminated customers listed by name; the count is always complete.
LIST_LIMIT = 200

Key = tuple[str, str]  # metric_code, currency ("" when none)
ZERO = Decimal("0.0000")


@dataclass
class Line:
    metric_code: str
    currency: str | None
    source_total: Decimal = ZERO
    source_outcomes: int = 0
    credited_here: Decimal = ZERO
    credited_elsewhere: Decimal = ZERO

    @property
    def unattributed(self) -> Decimal:
        return self.source_total - self.credited_here - self.credited_elsewhere


@dataclass
class EventLine:
    event_id: str
    credited: Decimal = ZERO
    credited_outcomes: int = 0
    # Outcomes this event matched that another event won under the collision rule.
    lost: Decimal = ZERO
    lost_outcomes: int = 0
    # Outcomes of customers held out as this event's control group.
    held_out_outcomes: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)


@dataclass
class Contaminated:
    customer_ref: str
    event_id: str
    contaminated_by: str | None


@dataclass
class Report:
    span: tuple[date, date] | None
    lines: list[Line] = field(default_factory=list)
    events: list[EventLine] = field(default_factory=list)
    contaminated: list[Contaminated] = field(default_factory=list)
    contaminated_total: int = 0
    invariant_holds: bool = True


def build(org_id: str, campaign: Campaign) -> Report:
    events = list(CampaignEvent.objects.filter(campaign=campaign).order_by("sequence_no"))
    span = attribution.span(events)
    report = Report(span)
    published = [e for e in events if e.state != "draft"]
    report.events = [EventLine(str(e.event_id)) for e in published]
    if span is None:
        return report
    ids = [e.event_id for e in published]
    codes = sorted(attribution_codes(org_id, campaign))
    metrics = dict(
        Metric.objects.filter(org_id=org_id, metric_code__in=codes).values_list(
            "metric_id", "metric_code"
        )
    )
    in_span = CampaignOutcome.objects.filter(
        org_id=org_id,
        metric_id__in=list(metrics),
        activity_date__gt=span[0],
        activity_date__lte=span[1],
    )
    lines: dict[Key, Line] = {}

    def line(metric_id: uuid.UUID, currency: str | None) -> Line:
        key = (metrics[metric_id], currency or "")
        if key not in lines:
            lines[key] = Line(key[0], currency or None)
        return lines[key]

    for r in in_span.values("metric_id", "currency_code").annotate(
        v=Sum("activity_value"), n=Count("outcome_id")
    ):
        found = line(r["metric_id"], r["currency_code"])
        found.source_total += r["v"]
        found.source_outcomes += r["n"]
    credited = CampaignAttribution.objects.filter(org_id=org_id, credited=True, outcome__in=in_span)
    for r in credited.values("outcome__metric_id", "outcome__currency_code").annotate(
        here=Sum("attributed_value", filter=Q(event_id__in=ids)),
        there=Sum("attributed_value", filter=~Q(event_id__in=ids)),
    ):
        found = line(r["outcome__metric_id"], r["outcome__currency_code"])
        found.credited_here += r["here"] or Decimal(0)
        found.credited_elsewhere += r["there"] or Decimal(0)
    report.lines = sorted(lines.values(), key=lambda x: (x.metric_code, x.currency or ""))
    report.invariant_holds = all(x.unattributed >= 0 for x in report.lines)

    by_event = {x.event_id: x for x in report.events}
    rows = CampaignAttribution.objects.filter(event_id__in=ids)
    for r in rows.values("event_id", "credited", "rule_applied").annotate(
        credit=Sum("attributed_value"), value=Sum("outcome__activity_value"), n=Count("outcome_id")
    ):
        ev = by_event[str(r["event_id"])]
        ev.by_rule[r["rule_applied"]] = ev.by_rule.get(r["rule_applied"], 0) + r["n"]
        if r["rule_applied"] == "holdout":
            ev.held_out_outcomes += r["n"]
        elif r["credited"]:
            ev.credited += r["credit"]
            ev.credited_outcomes += r["n"]
        else:
            ev.lost += r["value"]
            ev.lost_outcomes += r["n"]

    spoiled = CampaignBaseline.objects.filter(
        event_id__in=ids, confidence="low_contaminated_baseline"
    )
    seen: dict[tuple[str, str], Contaminated] = {}
    for customer, event_id, by in spoiled.values_list(
        "customer_ref", "event_id", "contaminated_by_id"
    ).order_by("event_id", "customer_ref"):
        seen.setdefault(
            (customer, str(event_id)),
            Contaminated(customer, str(event_id), None if by is None else str(by)),
        )
    report.contaminated_total = len(seen)
    report.contaminated = list(seen.values())[:LIST_LIMIT]
    return report


def attribution_codes(org_id: str, campaign: Campaign) -> set[str]:
    found = CampaignObjective.objects.filter(org_id=org_id, objective=campaign.objective).first()
    return set(found.outcome_metric_codes) if found else set()
