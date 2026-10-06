"""R4 exit: an executive figure entered by hand reaches the dashboard (PRD MI-4, EX-1).

The Engineering Plan's Executive-Dashboard exit criterion for the independent
path: a metric that lives only at executive level (here NPS, a manual-input
metric) is entered by hand — org-wide and by a region member — conforms to
``fact_actual_dimensional`` with manual provenance, and is read back through the
same ``widget.data`` an Admin's placed widget uses. A correction before the
deadline edits in place; the dimensional actual drives both org-level and
broken-down widgets.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kpigo.action import ActionContext
from kpigo.action.context import DataScopeGrant
from kpigo.hierarchy.scope import current_period_key
from tests.conftest import ORG_ID, role_ctx, run
from tests.executive_support import place, world

pytestmark = pytest.mark.django_db

EXEC_ALL = (DataScopeGrant(module="executive", dimension_type="region", member_code="*"),)


@pytest.fixture(autouse=True)
def org() -> None:
    world()


def exec_ctx() -> ActionContext:
    return role_ctx("executive", data_scopes=EXEC_ALL)


def period() -> str:
    # The current month: open for input, so its deadline (next month) has not passed.
    return current_period_key(ORG_ID)


def org_series(metric: Any) -> dict[str, Decimal | None]:
    return {s.series_type: s.value for s in metric.org}


def test_hand_entered_org_value_reaches_the_widget() -> None:
    p = period()
    out = run("executive.input.set", exec_ctx(), metric_code="ex_nps", period_key=p, value="63")
    assert out.value == Decimal("63") and out.version == 1 and out.dimension_type == ""

    place(widget_key="nps", widget_type="kpi_card", metrics=[{"metric_code": "ex_nps"}])
    read = run("widget.data", exec_ctx(), widget_key="nps", period_key=p)
    assert read.empty is None
    metric = next(m for m in read.metrics if m.metric_code == "ex_nps")
    assert metric.source == "independent"
    assert org_series(metric)["actual"] == Decimal("63")


def test_the_value_conforms_with_manual_provenance() -> None:
    from kpigo.ingestion.models import FactActualDimensional

    p = period()
    run("executive.input.set", exec_ctx(), metric_code="ex_nps", period_key=p, value="63")
    row = FactActualDimensional.objects.get(
        org_id=ORG_ID, metric__metric_code="ex_nps", dimension_type="", member_code="", period_key=p
    )
    assert row.actual_value == Decimal("63")
    assert row.run_id is None  # manual input carries no feed run (MI-11)


def test_a_correction_before_the_deadline_edits_in_place() -> None:
    p = period()
    run("executive.input.set", exec_ctx(), metric_code="ex_nps", period_key=p, value="63")
    again = run(
        "executive.input.set",
        exec_ctx(),
        metric_code="ex_nps",
        period_key=p,
        value="71",
        note="revised after the survey closed",
    )
    assert again.version == 1 and again.value == Decimal("71")

    listed = run("executive.input.list", exec_ctx(), period_key=p)
    assert [(i.metric_code, i.value) for i in listed.inputs] == [("ex_nps", Decimal("71"))]


def test_a_broken_down_widget_reads_the_member_value() -> None:
    p = period()
    run(
        "executive.input.set",
        exec_ctx(),
        metric_code="ex_nps",
        period_key=p,
        dimension_type="region",
        member_code="GA",
        value="58",
    )
    place(
        widget_key="nps_by_region",
        widget_type="bar",
        metrics=[{"metric_code": "ex_nps"}],
        dimension="region",
    )
    read = run(
        "widget.data", exec_ctx(), widget_key="nps_by_region", period_key=p, drill_to="south"
    )
    metric = next(m for m in read.metrics if m.metric_code == "ex_nps")
    ga = next(m for m in metric.members if m.member_code == "GA")
    assert {s.series_type: s.value for s in ga.series}["actual"] == Decimal("58")


def test_a_feed_metric_is_refused_for_manual_input() -> None:
    from kpigo.action import InvalidInput

    with pytest.raises(InvalidInput, match="collected by a feed"):
        run(
            "executive.input.set",
            exec_ctx(),
            metric_code="ex_revenue",
            period_key=period(),
            value="1",
        )


def test_a_scorecards_only_metric_is_not_an_executive_input() -> None:
    from kpigo.action import InvalidInput

    # sc_only is manual-eligible by binding but not bound to Executive.
    run(
        "metric.register",
        display_name="Manual scorecards only",
        metric_code="sc_manual",
        direction="higher_is_better",
        aggregation="average",
        unit="score",
        is_percentage=False,
        products=["scorecards"],
        status="active",
        collection_method="manual_input",
        effective_from="2026-01-01",
        acknowledge_similar=True,
    )
    with pytest.raises(InvalidInput, match="not bound to Executive"):
        run(
            "executive.input.set",
            exec_ctx(),
            metric_code="sc_manual",
            period_key=period(),
            value="1",
        )


def test_an_org_value_does_not_leak_into_a_member_slot() -> None:
    p = period()
    run("executive.input.set", exec_ctx(), metric_code="ex_nps", period_key=p, value="63")
    place(
        widget_key="nps_by_region",
        widget_type="bar",
        metrics=[{"metric_code": "ex_nps"}],
        dimension="region",
    )
    read = run(
        "widget.data", exec_ctx(), widget_key="nps_by_region", period_key=p, drill_to="south"
    )
    metric = next(m for m in read.metrics if m.metric_code == "ex_nps")
    assert all({s.series_type: s.value for s in m.series}["actual"] is None for m in metric.members)
