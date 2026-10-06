"""A published campaign result read as an Executive figure (Scope §9.5, §10.1).

A campaign result published as a metric (``campaign.metric.publish``) is shown on
the Executive dashboard like any other metric, but it is never materialised: each
figure is aggregated on demand from the rows the campaign surfaces already read,
filtered to the reporting month by the date the activity is booked on.

Three result kinds map cleanly onto a month and a customer dimension:

- **attributed_value** — credited attribution for outcomes dated in the month, in
  the outcome's currency, converted to the reporting currency.
- **conversions** — distinct customers whose outcome was credited in the month.
- **winbacks_confirmed** — win-backs that qualified in the month and are confirmed.

``incremental_value`` (a baseline over a whole span) and ``conversion_rate`` (an
event-level control measure, and contacts carry no customer dimensions) have no
honest month-by-dimension figure, so they report themselves pending.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import Q
from django.utils import timezone

from kpigo.action import ActionContext
from kpigo.action.context import DataScopeGrant
from kpigo.campaigns import attribution, authoring
from kpigo.campaigns.models import Campaign, CampaignOutcome, CampaignWinback
from kpigo.metrics.models import Metric
from tests.campaign_support import admin as campaign_admin
from tests.campaign_support import campaign, event, world
from tests.conftest import ORG_ID, role_ctx, run
from tests.executive_support import place

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 5)
MONTH = "202609"
PRIOR = "202608"

EXEC_ALL = tuple(
    DataScopeGrant(module="executive", dimension_type=d, member_code="*")
    for d in ("segment", "product", "region")
)


@pytest.fixture(autouse=True)
def org(monkeypatch: pytest.MonkeyPatch) -> None:
    world()  # GHS, and the segment / product / region dimensions
    monkeypatch.setattr(authoring, "today", lambda: TODAY)
    run("settings.update", reporting_currency="GHS")
    run(
        "metric.register",
        display_name="Campaign deposits",
        metric_code="cmp_deposit_value",
        direction="higher_is_better",
        aggregation="sum",
        unit="currency",
        products=["campaign"],
        effective_from="2024-01-01",
        acknowledge_similar=True,
    )
    run(
        "campaign.objective.set",
        campaign_admin(),
        objective="deposit_growth",
        default_window_days=30,
        outcome_metric_codes=["cmp_deposit_value"],
    )


def exec_ctx() -> ActionContext:
    return role_ctx("executive", data_scopes=EXEC_ALL)


def a_campaign() -> Campaign:
    """A published seasonal campaign whose event spans September."""
    out = campaign(
        campaign_admin(),
        events=[event(period_start="2026-09-01", period_end="2026-09-30")],
    )
    run("campaign.event.publish", campaign_admin(), event_id=out.events[0].event_id)
    return Campaign.objects.get(campaign_id=out.campaign_id)


def outcome(
    customer: str,
    value: str = "100",
    *,
    day: date = date(2026, 9, 15),
    segment: str = "mass",
    product: str = "savings",
    region: str = "GA",
    currency: str = "GHS",
) -> CampaignOutcome:
    """An outcome tagged to the campaign, so it is credited whatever the audience."""
    return CampaignOutcome.objects.create(
        org_id=ORG_ID,
        customer_ref=customer,
        metric_id=Metric.objects.get(metric_code="cmp_deposit_value").metric_id,
        campaign_code="SAVE-Q4",
        campaign=Campaign.objects.get(code="SAVE-Q4"),
        activity_date=day,
        activity_value=Decimal(value),
        currency_code=currency,
        segment_code=segment,
        product_code=product,
        region_code=region,
        run_id=uuid.uuid4(),
        loaded_at=timezone.now(),
    )


def winback(
    customer: str,
    *,
    day: date = date(2026, 9, 15),
    confirmed: date | None = date(2026, 9, 20),
    flag: bool = True,
    region: str = "GA",
) -> CampaignWinback:
    c = Campaign.objects.get(code="SAVE-Q4")
    return CampaignWinback.objects.create(
        org_id=ORG_ID,
        customer_ref=customer,
        campaign=c,
        qualified_at=day,
        winback_flag=flag,
        retention_confirmed_at=confirmed,
        event=c.events.first(),
        via="tag",
        rule_applied="single",
        region_code=region,
        run_id=uuid.uuid4(),
        loaded_at=timezone.now(),
    )


def attribute() -> None:
    attribution.attribute(ORG_ID, Q())


def publish(result_kind: str, c: Campaign) -> Any:
    return run(
        "campaign.metric.publish",
        campaign_admin(),
        campaign_id=str(c.campaign_id),
        result_kind=result_kind,
        products=["executive"],
    )


def read(metric_code: str, *, dimension: str | None = None, drill_to: str | None = None) -> Any:
    kind = "bar" if dimension else "kpi_card"
    spec: dict[str, Any] = {
        "widget_key": "camp",
        "widget_type": kind,
        "metrics": [{"metric_code": metric_code}],
    }
    if dimension:
        spec["dimension"] = dimension
    place(**spec)
    over = {"drill_to": drill_to} if drill_to else {}
    return run("widget.data", exec_ctx(), widget_key="camp", period_key=MONTH, **over)


def one(out: Any) -> Any:
    assert out.empty is None
    return out.metrics[0]


def org_actual(metric: Any) -> Decimal | None:
    value: Decimal | None = {s.series_type: s.value for s in metric.org}["actual"]
    return value


# ── attributed value ───────────────────────────────────────────────────────


def test_attributed_value_sums_credited_outcomes_in_the_month() -> None:
    c = a_campaign()
    outcome("C1", "100")
    outcome("C2", "50")
    outcome("C3", "999", day=date(2026, 8, 20))  # a prior month: out of the window too
    attribute()
    pub = publish("attributed_value", c)

    metric = one(read(pub.metric_code))
    assert metric.source == "campaign" and metric.pending is None
    s = metric.org[0]
    assert s.value == Decimal("150") and s.currency == "GHS"


def test_attributed_value_breaks_down_by_a_customer_dimension() -> None:
    c = a_campaign()
    outcome("C1", "100", region="GA")
    outcome("C2", "40", region="GA")
    outcome("C3", "70", region="AS")
    attribute()
    pub = publish("attributed_value", c)

    metric = one(read(pub.metric_code, dimension="region"))
    by = {
        m.member_code: {s.series_type: s.value for s in m.series}["actual"] for m in metric.members
    }
    assert by == {"GA": Decimal("140"), "AS": Decimal("70")}


def test_attributed_value_converts_to_the_reporting_currency() -> None:
    c = a_campaign()
    run("currency.upsert", code="USD", name="US dollar")
    run(
        "fx.set",
        rates=[
            {
                "from_currency": "USD",
                "to_currency": "GHS",
                "period_key": MONTH,
                "rate": "15",
                "rate_type": "average",
            }
        ],
    )
    outcome("C1", "100", currency="GHS")
    outcome("C2", "10", currency="USD")  # 10 USD -> 150 GHS
    attribute()
    pub = publish("attributed_value", c)

    assert org_actual(one(read(pub.metric_code))) == Decimal("250")


def test_a_bucket_with_no_rate_is_dropped_rather_than_counted_wrong() -> None:
    c = a_campaign()
    run("currency.upsert", code="USD", name="US dollar")  # no rate for the month
    outcome("C1", "100", currency="GHS")
    outcome("C2", "10", currency="USD")
    attribute()
    pub = publish("attributed_value", c)

    assert org_actual(one(read(pub.metric_code))) == Decimal("100")


def test_a_month_with_nothing_credited_is_blank_not_zero() -> None:
    c = a_campaign()
    pub = publish("attributed_value", c)
    assert org_actual(one(read(pub.metric_code))) is None


# ── conversions (a count of distinct customers) ─────────────────────────────


def test_conversions_count_distinct_credited_customers() -> None:
    c = a_campaign()
    outcome("C1", "100")
    outcome("C1", "20", day=date(2026, 9, 20))  # same customer, twice
    outcome("C2", "30")
    attribute()
    pub = publish("conversions", c)

    metric = one(read(pub.metric_code))
    s = metric.org[0]
    assert s.value == Decimal("2") and s.currency is None  # a count carries no currency


def test_conversions_zero_is_a_real_figure_once_a_customer_converts() -> None:
    c = a_campaign()
    outcome("C1", "100", region="GA")
    attribute()
    pub = publish("conversions", c)

    metric = one(read(pub.metric_code, dimension="region"))
    by = {
        m.member_code: {s.series_type: s.value for s in m.series}["actual"] for m in metric.members
    }
    assert by["GA"] == Decimal("1") and by["AS"] is None


# ── confirmed win-backs ─────────────────────────────────────────────────────


def test_winbacks_confirmed_counts_confirmed_qualifiers_in_the_month() -> None:
    c = a_campaign()
    winback("C1")
    winback("C2")
    winback("C3", flag=False)  # no longer qualifies: not counted
    winback("C4", day=date(2026, 8, 15))  # a prior month
    pub = publish("winbacks_confirmed", c)

    metric = one(read(pub.metric_code))
    s = metric.org[0]
    assert s.value == Decimal("2") and s.currency is None


def test_winbacks_confirmed_break_down_by_dimension() -> None:
    c = a_campaign()
    winback("C1", region="GA")
    winback("C2", region="AS")
    winback("C3", region="GA")
    pub = publish("winbacks_confirmed", c)

    metric = one(read(pub.metric_code, dimension="region"))
    by = {
        m.member_code: {s.series_type: s.value for s in m.series}["actual"] for m in metric.members
    }
    assert by == {"GA": Decimal("2"), "AS": Decimal("1")}


# ── prior series and deferred kinds ─────────────────────────────────────────


def test_prior_series_reads_the_prior_month() -> None:
    out = campaign(
        campaign_admin(),
        events=[event(period_start="2026-08-01", period_end="2026-09-30")],
    )
    run("campaign.event.publish", campaign_admin(), event_id=out.events[0].event_id)
    c = Campaign.objects.get(campaign_id=out.campaign_id)
    outcome("C1", "100", day=date(2026, 9, 15))
    outcome("C2", "60", day=date(2026, 8, 15))
    attribute()
    pub = publish("attributed_value", c)

    place(
        widget_key="camp",
        widget_type="line",
        metrics=[{"metric_code": pub.metric_code}],
        series=["actual", "prior"],
    )
    metric = run("widget.data", exec_ctx(), widget_key="camp", period_key=MONTH).metrics[0]
    by = {s.series_type: s.value for s in metric.org}
    assert by["actual"] == Decimal("100") and by["prior"] == Decimal("60")


@pytest.mark.parametrize("kind", ["incremental_value", "conversion_rate"])
def test_a_deferred_result_kind_reports_pending(kind: str) -> None:
    c = a_campaign()
    pub = publish(kind, c)
    metric = read(pub.metric_code).metrics[0]
    assert metric.source == "campaign"
    assert metric.pending is not None and metric.org is None
