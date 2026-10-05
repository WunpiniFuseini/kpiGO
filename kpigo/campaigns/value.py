"""What an event was worth: gross, incremental, the control group and ROI (Scope §9.2, §9.1b).

Everything here is read from what attribution and the baseline already stored,
so it is computed when asked, never materialised.

- **Gross** is the value credited to the event. **Incremental** is gross less
  each customer's pre-period baseline (``kpigo.campaigns.baseline``); it is
  withheld, with the reason, when any baseline is contaminated or the outcome
  history does not reach back over the whole baseline window.
- **Money** is only the value carried in the event's budget currency; other
  currencies are listed beside it and never converted.
- **Control group**: customers the contact feed marks as held out. Lift
  compares the share of contacted and held-out customers with an outcome the
  event matched, whoever won the credit, so it measures the contact and not
  the collision rule. Its incremental value is the gap in value per customer
  times the contacted count.
- **ROI** = (value - budget) / budget on the org's basis, with gross ROI beside
  it. Cost per outcome is budget over converted customers; utilisation is
  spend over budget.

Absent is not zero: every figure nothing stands behind is ``None``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Min, QuerySet, Sum

from kpigo.campaigns import authoring as au
from kpigo.campaigns import baseline as bl
from kpigo.campaigns.models import (
    CampaignAttribution,
    CampaignBaseline,
    CampaignContact,
    CampaignEvent,
    CampaignOutcome,
)
from kpigo.platform.models import OrgSettings

QUANTUM = Decimal("0.0001")
# A control group smaller than this is shown, but flagged as too small to lean on.
SMALL_CONTROL = 30

NOT_PUBLISHED = "not_published"
NO_OUTCOMES = "no_outcomes_fed"
CONTAMINATED = "contaminated_baseline"
SHORT_HISTORY = "history_too_short"
NO_MONEY = "no_value_in_budget_currency"
NO_BUDGET = "no_budget"
NO_CONTACTS = "no_contacts_fed"
NO_CONTROL = "no_control_group"


def basis_for(org_id: str) -> tuple[str, datetime | None]:
    found = (
        OrgSettings.objects.filter(org_id=org_id)
        .values_list("campaign_value_basis", "value_basis_changed_at")
        .first()
    )
    if found is None:
        return "incremental", None
    return str(found[0]), found[1]


def ratio(top: Decimal | None, bottom: Decimal | None) -> Decimal | None:
    if top is None or bottom is None or bottom == 0:
        return None
    return (top / bottom).quantize(QUANTUM)


@dataclass
class Control:
    planned_pct: int | None
    treated: int | None = None
    control: int | None = None
    actual_pct: Decimal | None = None
    treated_rate: Decimal | None = None
    control_rate: Decimal | None = None
    # Percentage points: treated rate less control rate.
    lift_points: Decimal | None = None
    incremental: Decimal | None = None
    small: bool = False
    reason: str | None = None


@dataclass
class EventValue:
    event_id: str
    currency: str
    budget: Decimal
    spend: Decimal | None
    baseline_window: tuple[date, date]
    gross: Decimal | None = None
    other_currencies: list[tuple[str | None, Decimal]] = field(default_factory=list)
    baseline: Decimal | None = None
    incremental: Decimal | None = None
    # Why incremental is withheld; gross still stands.
    withheld: str | None = None
    converted_customers: int | None = None
    new_customers: int | None = None
    contaminated_customers: int | None = None
    roi: Decimal | None = None
    gross_roi: Decimal | None = None
    roi_reason: str | None = None
    cost_per_outcome: Decimal | None = None
    utilisation: Decimal | None = None
    control: Control = field(default_factory=lambda: Control(None))


def event_value(
    event: CampaignEvent, basis: str, outcomes_fed: bool, contacts_fed: bool
) -> EventValue:
    window = bl.window(event.period_start, au.window_end(event))
    found = EventValue(
        event_id=str(event.event_id),
        currency=event.budget_currency,
        budget=au.money(event.budget_amount),
        spend=event.spend_to_date,
        baseline_window=window,
        control=Control(event.holdout_pct),
    )
    found.utilisation = ratio(found.spend, found.budget)
    if event.state == "draft":
        found.withheld = found.roi_reason = found.control.reason = NOT_PUBLISHED
        return found
    found.control = control(event, contacts_fed and outcomes_fed, contacts_fed)
    if not outcomes_fed:
        found.withheld = found.roi_reason = NO_OUTCOMES
        return found
    rows = CampaignBaseline.objects.filter(event=event)
    money = rows.filter(currency_code=event.budget_currency)
    found.gross = money.aggregate(v=Sum("gross"))["v"] or Decimal(0)
    found.other_currencies = [
        (r["currency_code"] or None, r["v"])
        for r in rows.exclude(currency_code=event.budget_currency)
        .values("currency_code")
        .annotate(v=Sum("gross"))
        .order_by("currency_code")
    ]
    found.converted_customers = rows.values("customer_ref").distinct().count()
    found.new_customers = (
        rows.filter(confidence="new_customer").values("customer_ref").distinct().count()
    )
    found.contaminated_customers = (
        rows.filter(confidence="low_contaminated_baseline")
        .values("customer_ref")
        .distinct()
        .count()
    )
    found.cost_per_outcome = (
        (found.budget / found.converted_customers).quantize(Decimal("0.01"))
        if found.converted_customers
        else None
    )
    if found.contaminated_customers:
        found.withheld = CONTAMINATED
    elif _history_short(event, rows, window[0]):
        found.withheld = SHORT_HISTORY
    else:
        totals = money.aggregate(b=Sum("baseline"), i=Sum("incremental"))
        found.baseline = totals["b"] or Decimal(0)
        found.incremental = totals["i"] or Decimal(0)
    _roi(found, basis, has_money=money.exists() or not rows.exists())
    return found


def _history_short(event: CampaignEvent, rows: QuerySet[CampaignBaseline], first: date) -> bool:
    """True when the outcome feed starts after the baseline window does: the baseline is partial."""
    metrics = list(rows.values_list("metric_id", flat=True).distinct())
    if not metrics:
        return False
    earliest = CampaignOutcome.objects.filter(org_id=event.org_id, metric_id__in=metrics).aggregate(
        d=Min("activity_date")
    )["d"]
    return earliest is None or earliest > first + timedelta(days=1)


def _roi(found: EventValue, basis: str, *, has_money: bool) -> None:
    if not found.budget:
        found.roi_reason = NO_BUDGET
        return
    if not has_money:
        found.roi_reason = NO_MONEY
        return
    found.gross_roi = ratio((found.gross or Decimal(0)) - found.budget, found.budget)
    if basis == "gross":
        found.roi = found.gross_roi
    elif found.incremental is not None:
        found.roi = ratio(found.incremental - found.budget, found.budget)
    else:
        found.roi_reason = found.withheld


def control(event: CampaignEvent, both_fed: bool, contacts_fed: bool) -> Control:
    found = Control(event.holdout_pct)
    if not contacts_fed:
        found.reason = NO_CONTACTS
        return found
    groups: dict[bool, set[str]] = defaultdict(set)
    for customer, holdout in CampaignContact.objects.filter(event=event).values_list(
        "customer_ref", "holdout"
    ):
        groups[holdout].add(customer)
    # A customer both held out and contacted was not a clean control.
    held = groups[True] - groups[False]
    treated = groups[False]
    found.treated, found.control = len(treated), len(held)
    if not held:
        found.reason = NO_CONTROL
        return found
    found.actual_pct = (Decimal(100) * len(held) / (len(held) + len(treated))).quantize(
        Decimal("0.1")
    )
    found.small = len(held) < SMALL_CONTROL
    if not both_fed:
        found.reason = NO_OUTCOMES
        return found
    acted: dict[str, Decimal] = defaultdict(Decimal)
    for customer, currency, value in CampaignAttribution.objects.filter(event=event).values_list(
        "outcome__customer_ref", "outcome__currency_code", "outcome__activity_value"
    ):
        acted[customer] += value if currency == event.budget_currency else Decimal(0)
    rates: dict[bool, tuple[Decimal | None, Decimal]] = {}
    for held_out, members in ((False, treated), (True, held)):
        if not members:
            rates[held_out] = (None, Decimal(0))
            continue
        converted = [c for c in members if c in acted]
        value = sum((acted[c] for c in converted), Decimal(0))
        rates[held_out] = (Decimal(len(converted)) / len(members), value / len(members))
    (t_rate, t_value), (c_rate, c_value) = rates[False], rates[True]
    found.treated_rate = None if t_rate is None else t_rate.quantize(QUANTUM)
    found.control_rate = None if c_rate is None else c_rate.quantize(QUANTUM)
    if t_rate is not None and c_rate is not None:
        found.lift_points = ((t_rate - c_rate) * 100).quantize(Decimal("0.01"))
        found.incremental = ((t_value - c_value) * len(treated)).quantize(Decimal("0.01"))
    return found
