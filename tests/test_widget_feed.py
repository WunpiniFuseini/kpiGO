"""The ``tmpl_widget_data`` contract (PRD EX-9, Scope §10.5, App Flow §6.1).

One template feeds every widget, keyed by ``widget_key``. A key kpiGo has not seen
is registered ``available`` so the DE team can feed first and the Admin places the
widget afterwards; a placed widget's rows are checked against what it shows and how
it breaks down.
"""

from __future__ import annotations

from typing import Any

import pytest

from kpigo.executive.models import WidgetDefinition
from kpigo.ingestion import validator as v
from kpigo.ingestion.models import FactWidgetData
from kpigo.ingestion.reference import build_reference
from tests.conftest import ORG_ID, run
from tests.executive_support import place, world
from tests.ingestion_support import PERIOD, PRIOR_PERIOD, dry, live, register_feed

pytestmark = pytest.mark.django_db

HEADER = [
    "widget_key",
    "metric_code",
    "period_key",
    "dimension_type",
    "member_code",
    "series_type",
    "value",
    "currency_code",
]


@pytest.fixture(autouse=True)
def org() -> None:
    world()
    register_feed("widgets", "widget_data")


def revenue_rows(period: str = PERIOD, value: str = "42600000") -> list[list[Any]]:
    return [
        ["revenue", "ex_revenue", period, None, None, "actual", value, "GHS"],
        ["revenue", "ex_revenue", period, None, None, "target", "39300000", "GHS"],
        ["revenue", "ex_revenue", period, "region", "GA", "actual", "18900000", "GHS"],
    ]


def rules(out: Any) -> dict[str, str]:
    return {f.rule: f.severity for f in out.findings}


def load(rows: list[list[Any]]) -> Any:
    assert dry("widgets", HEADER, rows).passed
    return live("widgets", HEADER, rows)


def test_an_unknown_key_loads_and_becomes_available_for_an_admin_to_place() -> None:
    out = load(revenue_rows())
    assert out.passed and rules(out) == {"unknown_widget_key": "warning"}
    assert out.registered_widget_keys == ["revenue"]
    available = WidgetDefinition.objects.get(widget_key="revenue")
    assert (available.state, available.widget_type, available.change) == (
        "available",
        None,
        "detected",
    )
    assert available.first_detected_at is not None
    listed = run("widget.list").widgets
    assert [(w.widget_key, w.state) for w in listed] == [("revenue", "available")]

    facts = {
        (f.dimension_type, f.member_code, f.series_type): f.value
        for f in FactWidgetData.objects.all()
    }
    assert facts[("", "", "actual")] == 42600000
    assert facts[("region", "GA", "actual")] == 18900000
    assert FactWidgetData.objects.filter(currency_code="GHS").count() == 3

    # The Admin places the key the feed brought (App Flow §6.1 step 4).
    placed = place()
    assert (placed.version, placed.state) == (2, "placed")
    assert placed.first_detected_at == available.first_detected_at


def test_placed_widgets_check_their_metric_and_breakdown() -> None:
    place()
    place(
        widget_key="casa_by_region",
        widget_type="bar",
        metrics=[{"metric_code": "ex_casa", "source": "independent"}],
        dimension="region",
    )
    out = load(
        [
            *revenue_rows(),
            ["revenue", "ex_accounts", PERIOD, None, None, "actual", "120", None],
            ["casa_by_region", "ex_casa", PERIOD, None, None, "actual", "318000000", "GHS"],
        ]
    )
    assert out.passed
    assert rules(out) == {"metric_not_on_widget": "warning", "breakdown_missing": "warning"}
    missing = next(f for f in out.findings if f.rule == "breakdown_missing")
    assert "casa_by_region" in missing.message and "region" in missing.message


def test_a_removed_widget_keeps_receiving_rows_with_a_warning() -> None:
    place()
    run("widget.remove", widget_key="revenue")
    out = load(revenue_rows())
    assert out.passed and rules(out) == {"widget_removed": "warning"}
    assert FactWidgetData.objects.count() == 3


@pytest.mark.parametrize(
    ("row", "rule"),
    [
        (["Revenue", "ex_revenue", PERIOD, None, None, "actual", "1", None], "bad_widget_key"),
        (["revenue", "ex_revenue", PERIOD, None, None, "plan", "1", None], "bad_series_type"),
        (
            ["revenue", "ex_revenue", PERIOD, "region", None, "actual", "1", None],
            "partial_dimension",
        ),
        (["revenue", "ex_revenue", PERIOD, None, "GA", "actual", "1", None], "partial_dimension"),
        (["revenue", "sc_only", PERIOD, None, None, "actual", "1", None], "metric_not_bound"),
        (["revenue", "ex_nps", PERIOD, None, None, "actual", "41", None], "metric_not_feed"),
        (
            ["revenue", "ex_revenue", PERIOD, "galaxy", "x", "actual", "1", None],
            "unknown_dimension",
        ),
    ],
)
def test_bad_rows_are_rejected(row: list[Any], rule: str) -> None:
    out = dry("widgets", HEADER, [row])
    assert not out.passed
    assert rules(out)[rule] == "error"


def test_an_unknown_member_is_registered_available_like_any_dimensional_feed() -> None:
    out = load([["revenue", "ex_revenue", PERIOD, "region", "WR", "actual", "5", None]])
    assert out.passed and out.registered_members == ["region:WR"]


def test_a_changed_reload_replaces_the_earlier_figures_for_that_widget_and_month() -> None:
    load([*revenue_rows(), *revenue_rows(PRIOR_PERIOD, "40000000")[:1]])
    later = load(revenue_rows(value="43000000")[:2])
    assert later.passed
    figures = {
        (f.period_key, f.dimension_type, f.series_type): f.value
        for f in FactWidgetData.objects.all()
    }
    # The month reloaded holds only what the new run sent; the other month is untouched.
    assert figures == {
        (PERIOD, "", "actual"): 43000000,
        (PERIOD, "", "target"): 39300000,
        (PRIOR_PERIOD, "", "actual"): 40000000,
    }


def test_the_contract_carries_widget_keys_for_the_standalone_validator() -> None:
    place(
        widget_key="casa_by_region",
        widget_type="bar",
        metrics=[{"metric_code": "ex_casa", "source": "independent"}],
        dimension="region",
    )
    out = run("feed.contract.export", name="widgets", period_from=PERIOD, period_to=PERIOD)
    widgets = out.contract["reference"]["widgets"]
    assert widgets["casa_by_region"] == {
        "state": "placed",
        "metric_codes": ["ex_casa"],
        "dimension": "region",
    }
    ref = build_reference(ORG_ID, v.TEMPLATES["widget_data"], [PERIOD])
    assert v.reference_from_json(v.reference_to_json(ref)).widgets == ref.widgets
