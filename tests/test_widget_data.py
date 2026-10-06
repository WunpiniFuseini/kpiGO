"""Reading an Executive widget's figures (PRD EX-1–EX-4, Scope §10).

Two ways a widget is drawn: independent rows fed straight into the widget, and a
roll-up over distinct subjects up a dimension with FX conversion. Data scope is
applied member by member — no grant means no data — and a breakdown widget drills
down the dimension hierarchy with a breadcrumb. Thresholds and provenance come with
every read.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kpigo.action import ActionContext
from kpigo.action.context import DataScopeGrant
from tests.conftest import role_ctx, run
from tests.executive_support import actual, place, rm, target, world
from tests.ingestion_support import PERIOD, PRIOR_PERIOD, dry, live, register_feed

pytestmark = pytest.mark.django_db

WIDGET_HEADER = [
    "widget_key",
    "metric_code",
    "period_key",
    "dimension_type",
    "member_code",
    "series_type",
    "value",
    "currency_code",
]

EXEC_ALL = (DataScopeGrant(module="executive", dimension_type="region", member_code="*"),)


def exec_ctx(*grants: DataScopeGrant) -> ActionContext:
    return role_ctx("executive", data_scopes=grants or EXEC_ALL)


@pytest.fixture(autouse=True)
def org() -> None:
    world()


def fx(frm: str, to: str, rate: str, period: str) -> None:
    run(
        "fx.set",
        rates=[
            {
                "from_currency": frm,
                "to_currency": to,
                "period_key": period,
                "rate": rate,
                "rate_type": "average",
            }
        ],
    )


def feed_widget(rows: list[list[Any]], key: str = "revenue") -> Any:
    register_feed(name=key, template="widget_data")
    assert dry(key, WIDGET_HEADER, rows).passed
    return live(key, WIDGET_HEADER, rows)


def read(ctx: ActionContext | None = None, **over: Any) -> Any:
    payload = {"widget_key": "revenue", "period_key": PERIOD, **over}
    return run("widget.data", ctx or exec_ctx(), **payload)


def series(metric: Any) -> dict[str, Decimal | None]:
    return {s.series_type: s.value for s in metric.org}


# ── independent ──────────────────────────────────────────────────────────────


def test_an_independent_widget_reads_its_fed_figures_with_actual_and_target() -> None:
    feed_widget(
        [
            ["revenue", "ex_revenue", PERIOD, None, None, "actual", "42600000", "GHS"],
            ["revenue", "ex_revenue", PERIOD, None, None, "target", "39300000", "GHS"],
        ]
    )
    place()
    out = read()
    assert out.widget_type == "kpi_card" and out.dimension is None and out.empty is None
    metric = out.metrics[0]
    assert (metric.source, metric.unit, metric.run_id is not None) == (
        "independent",
        "currency",
        True,
    )
    assert series(metric) == {"actual": Decimal("42600000"), "target": Decimal("39300000")}


def test_prior_and_prior_year_are_the_actual_at_the_shifted_period() -> None:
    feed_widget(
        [
            ["revenue", "ex_revenue", PERIOD, None, None, "actual", "100", "GHS"],
            ["revenue", "ex_revenue", PRIOR_PERIOD, None, None, "actual", "90", "GHS"],
        ]
    )
    place(series=["actual", "prior"])
    metric = read().metrics[0]
    assert series(metric) == {"actual": Decimal("100"), "prior": Decimal("90")}


def test_an_independent_figure_is_converted_to_the_reporting_currency() -> None:
    run("settings.update", reporting_currency="GHS")
    run("currency.upsert", code="USD", name="US dollar")
    fx("USD", "GHS", "15", PERIOD)
    feed_widget([["revenue", "ex_revenue", PERIOD, None, None, "actual", "1000", "USD"]])
    place()
    metric = read().metrics[0]
    assert metric.org[0].value == Decimal("15000") and metric.org[0].currency == "GHS"


def test_a_missing_rate_leaves_the_figure_blank_rather_than_wrong() -> None:
    run("settings.update", reporting_currency="GHS")
    run("currency.upsert", code="USD", name="US dollar")
    feed_widget([["revenue", "ex_revenue", PERIOD, None, None, "actual", "1000", "USD"]])
    place()
    assert read().metrics[0].org[0].value is None


# ── breakdown and drill ──────────────────────────────────────────────────────


def test_a_breakdown_widget_lists_the_members_at_each_drill_level() -> None:
    feed_widget(
        [
            ["revenue", "ex_revenue", PERIOD, "region", "south", "actual", "30", "GHS"],
            ["revenue", "ex_revenue", PERIOD, "region", "GA", "actual", "19", "GHS"],
            ["revenue", "ex_revenue", PERIOD, "region", "AS", "actual", "9", "GHS"],
            ["revenue", "ex_revenue", PERIOD, "region", "north", "actual", "12", "GHS"],
        ]
    )
    place(widget_key="revenue", widget_type="bar", dimension="region")
    top = read()
    assert top.dimension == "region" and top.breadcrumb == []
    members = top.metrics[0].members
    assert [(m.member_code, m.has_children) for m in members] == [
        ("north", True),
        ("south", True),
    ]
    assert members[1].series[0].value == Decimal("30")

    drilled = read(drill_to="south")
    assert [m.member_code for m in drilled.metrics[0].members] == ["AS", "GA"]
    assert [c.member_code for c in drilled.breadcrumb] == ["south"]


# ── roll-up ──────────────────────────────────────────────────────────────────


def test_a_rollup_sums_distinct_subjects_up_the_dimension_with_fx() -> None:
    run("settings.update", reporting_currency="GHS")
    a = rm("R1", "GA")
    b = rm("R2", "GA")
    rm("R3", "AS")  # no actual: absent is not zero, contributes nothing
    actual("ex_casa", a, "100", PERIOD)
    actual("ex_casa", b, "50", PERIOD)
    place(
        widget_key="casa",
        widget_type="bar",
        metrics=[{"metric_code": "ex_casa", "source": "rollup"}],
        dimension="region",
    )
    out = run("widget.data", exec_ctx(), widget_key="casa", period_key=PERIOD, drill_to="south")
    members = {m.member_code: m for m in out.metrics[0].members}
    ga = members["GA"].series[0]
    assert ga.value == Decimal("150") and ga.subjects == 2
    assert members["AS"].series[0].value is None and members["AS"].series[0].subjects == 0


def test_a_rollup_targets_resolve_per_subject_profile() -> None:
    a = rm("R1", "GA")
    actual("ex_casa", a, "100", PERIOD)
    target("ex_casa", "retail_rm", "80", PERIOD)
    place(
        widget_key="casa",
        widget_type="bullet",
        metrics=[{"metric_code": "ex_casa", "source": "rollup"}],
        dimension="region",
        series=["actual", "target"],
    )
    out = run("widget.data", exec_ctx(), widget_key="casa", period_key=PERIOD, drill_to="south")
    ga = {
        s.series_type: s.value
        for s in next(m for m in out.metrics[0].members if m.member_code == "GA").series
    }
    assert ga == {"actual": Decimal("100"), "target": Decimal("80")}


def test_an_org_level_rollup_covers_the_whole_organisation() -> None:
    actual("ex_casa", rm("R1", "GA"), "100", PERIOD)
    actual("ex_casa", rm("R2", "AS"), "40", PERIOD)
    place(
        widget_key="casa_total",
        widget_type="kpi_card",
        metrics=[{"metric_code": "ex_casa", "source": "rollup"}],
    )
    out = run("widget.data", exec_ctx(), widget_key="casa_total", period_key=PERIOD)
    assert out.metrics[0].org[0].value == Decimal("140") and out.metrics[0].org[0].subjects == 2


# ── scope: no grant means no data ────────────────────────────────────────────


def test_a_member_scoped_viewer_sees_only_their_members_and_a_named_empty_state() -> None:
    feed_widget(
        [
            ["revenue", "ex_revenue", PERIOD, "region", "GA", "actual", "19", "GHS"],
            ["revenue", "ex_revenue", PERIOD, "region", "AS", "actual", "9", "GHS"],
        ]
    )
    place(widget_key="revenue", widget_type="bar", dimension="region")
    ga_only = exec_ctx(
        DataScopeGrant(module="executive", dimension_type="region", member_code="GA")
    )
    out = run("widget.data", ga_only, widget_key="revenue", period_key=PERIOD)
    assert [m.member_code for m in out.metrics[0].members] == ["GA"]

    # The same viewer opening an org-level widget is told which grant they lack.
    place(widget_key="rev_total", widget_type="kpi_card")
    blocked = run("widget.data", ga_only, widget_key="rev_total", period_key=PERIOD)
    assert blocked.empty is not None and "whole organisation" in blocked.empty
    assert blocked.metrics == [] and blocked.thresholds is None


def test_no_executive_grant_is_an_empty_state_not_an_error() -> None:
    place()
    out = run("widget.data", role_ctx("executive"), widget_key="revenue", period_key=PERIOD)
    assert out.empty is not None and out.metrics == []


def test_only_viewers_of_the_executive_page_read_widget_data() -> None:
    from kpigo.action import PermissionDenied

    place()
    with pytest.raises(PermissionDenied):
        run("widget.data", role_ctx("staff"), widget_key="revenue", period_key=PERIOD)


# ── thresholds and provenance ────────────────────────────────────────────────


def test_metric_thresholds_come_from_the_rating_bands_as_achievement() -> None:
    place()
    th = read().thresholds
    assert th.source == "metric" and th.basis == "achievement"
    assert next(b.label for b in th.bands) == "Needs Focus"


def test_an_override_is_returned_as_stored() -> None:
    place(widget_key="cti", widget_type="gauge", metrics=[{"metric_code": "ex_cti"}])
    run(
        "widget.thresholds.set",
        widget_key="cti",
        thresholds={
            "source": "override",
            "basis": "value",
            "bands": [
                {"label": "Within", "threshold": "0"},
                {"label": "Above", "threshold": "0.035"},
            ],
            "note": "Board tolerance 3.5%.",
        },
    )
    th = run("widget.data", exec_ctx(), widget_key="cti", period_key=PERIOD).thresholds
    assert (th.source, th.basis, th.note) == ("override", "value", "Board tolerance 3.5%.")


def test_a_deferred_campaign_result_kind_reports_pending() -> None:
    # incremental_value has no honest month-by-dimension figure; the supported
    # kinds (attributed_value, conversions, winbacks_confirmed) are exercised in
    # tests/test_executive_campaign.py.
    from kpigo.campaigns.models import Campaign
    from tests.campaign_support import admin as campaign_admin
    from tests.campaign_support import campaign

    for dim, members in (("segment", ["retail"]), ("product", ["savings"])):
        run("dimension.define", dimension_type=dim, display_name=dim.title())
        run(
            "dimension.member.upsert",
            dimension_type=dim,
            members=[{"member_code": m, "member_name": m.title()} for m in members],
        )
    c = Campaign.objects.get(campaign_id=campaign().campaign_id)
    pub = run(
        "campaign.metric.publish",
        campaign_admin(),
        campaign_id=str(c.campaign_id),
        result_kind="incremental_value",
        products=["executive"],
    )
    place(widget_key="camp", metrics=[{"metric_code": pub.metric_code}])
    out = run("widget.data", exec_ctx(), widget_key="camp", period_key=PERIOD)
    assert out.metrics[0].source == "campaign" and out.metrics[0].pending is not None
    assert out.metrics[0].org is None


def test_a_widget_not_on_the_dashboard_is_not_found() -> None:
    from kpigo.action import NotFound

    with pytest.raises(NotFound):
        read()
