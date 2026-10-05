"""The product-line matrix: lines × entities, drilling region → branch → RM (PRD AP-5, AP-9, AP-11)."""

from __future__ import annotations

from typing import Any

import pytest

from kpigo.action import Conflict, InvalidInput, NotFound, PermissionDenied
from kpigo.action.identity import build_context
from tests.agent_support import PRODUCT, day, load, targets, world
from tests.conftest import role_ctx, run

pytestmark = pytest.mark.django_db


def line_targets(**values: str) -> None:
    rows = [
        {
            "metric_code": "value_booked",
            "scope_type": "profile",
            "scope_code": "sme_rm",
            "period_key": "202506",
            "target_value": value,
            "currency_code": "GHS",
            "product_line_code": line,
        }
        for line, value in values.items()
    ]
    out = run("target.upload", rows=rows)
    assert out.accepted, out.findings
    run("target.publish", period_keys=["202506"])


@pytest.fixture
def org() -> dict[str, str]:
    ids = world()
    targets()
    load(
        [
            ["value_booked", "A1", day(2), "CARDS", "600", "GHS"],
            ["value_booked", "A1", day(3), "LOANS", "300", "GHS"],
            ["value_booked", "A2", day(5), "CARDS", "200", "GHS"],
            ["value_booked", "A3", day(4), "MORTGAGE", "400", "GHS"],
            ["value_booked", "A3", day(6), None, "100", "GHS"],
        ]
    )
    run("product_group.set", code="retail", display_name="Retail", sort_order=10)
    run("product_group.set", code="home", display_name="Home loans", sort_order=20)
    run("product_line.activate", code="CARDS", display_name="Cards", group_code="retail")
    run("product_line.activate", code="LOANS", display_name="Loans", group_code="retail")
    run("product_line.activate", code="MORTGAGE", display_name="Mortgage", group_code="home")
    line_targets(CARDS="1050", LOANS="525", MORTGAGE="2100")
    return ids


def matrix(**params: Any) -> Any:
    return run("agent.matrix", product=PRODUCT, as_of=day(17), **params)


def figures(row: Any) -> list[tuple[Any, ...]]:
    return [
        (
            c.actual and str(c.actual),
            c.target and str(c.target),
            c.achieved and str(c.achieved),
            c.reported,
        )
        for c in row.cells
    ]


def test_regions_sum_their_agents_on_each_line_against_line_targets(org: dict[str, str]) -> None:
    out = matrix(metric_code="value_booked")
    assert out.view == "expanded" and out.level == "region"
    assert [c.key for c in out.columns] == ["CARDS", "LOANS", "MORTGAGE", ""]
    assert out.columns[-1].name == "All products"
    assert [(r.key, r.agents, r.drill_level) for r in out.rows] == [
        ("AS", 1, "branch"),
        ("GA", 2, "branch"),
    ]
    ga = out.rows[1]
    # Cards: 1050 a month is 600 by working day 12 of 21, for each of GA's two agents.
    # Loans: only A1 sold any; A2 adds neither actual nor target (absent is not zero).
    # All products: the metric's own target, and every figure, lined or not.
    assert figures(ga) == [
        ("800.0000", "1200.0000", "0.6667", 2),
        ("300.0000", "300.0000", "1.0000", 1),
        (None, None, None, 0),
        ("1100.0000", "2400.0000", "0.4583", 2),
    ]
    assert [c.rag for c in ga.cells] == ["red", "green", None, "red"]
    assert all(c.agents == 2 for c in ga.cells)
    # A3's 100 with no line is in All products only.
    assert figures(out.rows[0])[-1] == ("500.0000", "1200.0000", "0.4167", 1)
    # The total is of distinct agents, not of the regions' cells' percentages.
    assert out.total is not None and out.total.agents == 3
    assert figures(out.total)[0] == ("800.0000", "1200.0000", "0.6667", 2)
    assert figures(out.total)[-1] == ("1600.0000", "3600.0000", "0.4444", 3)


def test_drilling_to_branches_then_rms_keeps_a_breadcrumb(org: dict[str, str]) -> None:
    branches = matrix(metric_code="value_booked", level="branch", region_code="GA")
    assert [(r.key, r.drill_level, r.region_code) for r in branches.rows] == [("GA-01", "rm", "GA")]
    assert [(c.level, c.code) for c in branches.breadcrumb] == [("all", None), ("region", "GA")]
    rms = matrix(metric_code="value_booked", level="rm", region_code="GA", branch_code="GA-01")
    assert [(r.name, r.drill_level) for r in rms.rows] == [("Agent A1", None), ("Agent A2", None)]
    assert [c.level for c in rms.breadcrumb] == ["all", "region", "branch"]
    assert figures(rms.rows[1])[:2] == [
        ("200.0000", "600.0000", "0.3333", 1),
        (None, None, None, 0),
    ]
    # A branch alone still finds its region for the breadcrumb.
    alone = matrix(metric_code="value_booked", level="rm", branch_code="AS-01")
    assert [(c.level, c.code) for c in alone.breadcrumb] == [
        ("all", None),
        ("region", "AS"),
        ("branch", "AS-01"),
    ]
    with pytest.raises(NotFound):
        matrix(level="branch", region_code="NOWHERE")
    with pytest.raises(InvalidInput):
        matrix(level="rm")
    with pytest.raises(InvalidInput):
        matrix(level="branch")


def test_the_grouped_view_sums_lines_and_recomputes_percent(org: dict[str, str]) -> None:
    out = matrix(metric_code="value_booked", view="grouped")
    assert [(c.key, c.kind, c.name) for c in out.columns] == [
        ("retail", "group", "Retail"),
        ("home", "group", "Home loans"),
        ("", "all", "All products"),
    ]
    ga = out.rows[1]
    # Retail = Cards + Loans: (600 + 300 + 200) over (600 + 300 + 600), not the mean of 67% and 100%.
    assert figures(ga)[0] == ("1100.0000", "1500.0000", "0.7333", 2)
    assert figures(ga)[1] == (None, None, None, 0)


def test_a_line_can_carry_its_own_thresholds(org: dict[str, str]) -> None:
    run("product_line.update", code="CARDS", rag_green="0.6", rag_amber="0.5")
    ga = matrix(metric_code="value_booked").rows[1]
    assert [c.rag for c in ga.cells] == ["green", "green", None, "red"]


def test_a_line_retired_mid_month_keeps_its_column_for_that_month(org: dict[str, str]) -> None:
    run("product_line.retire", code="LOANS", effective_to=day(10))
    assert [c.key for c in matrix(metric_code="value_booked").columns] == [
        "CARDS",
        "LOANS",
        "MORTGAGE",
        "",
    ]
    july = run("agent.matrix", product=PRODUCT, as_of="2025-07-15", metric_code="value_booked")
    assert [c.key for c in july.columns] == ["CARDS", "MORTGAGE", ""]


def test_only_additive_metrics_are_offered(org: dict[str, str]) -> None:
    out = matrix()
    assert [m.key for m in out.metric_options] == ["accounts_opened", "value_booked"]
    assert out.metric is not None and out.metric.key == "accounts_opened"
    with pytest.raises(InvalidInput):
        matrix(metric_code="service_tat")
    run("agent.settings.set", product=PRODUCT, rank_metric_code="value_booked")
    assert (matrix().metric or pytest.fail()).key == "value_booked"


def test_with_no_line_switched_on_only_all_products_shows() -> None:
    world()
    targets()
    out = matrix(metric_code="value_booked")
    assert out.no_lines and [c.key for c in out.columns] == [""]
    # Nothing reported: no figure and no target, not zeros.
    assert figures(out.rows[0]) == [(None, None, None, 0)]


def test_the_view_is_each_users_own_preference(org: dict[str, str], make_user: Any) -> None:
    user = make_user("agent_supervisor")
    ctx = build_context(user, caller="cli")
    assert run("agent.matrix", ctx, product=PRODUCT, as_of=day(17)).view == "expanded"
    run("preference.set", ctx, key="agent.matrix.view", value="grouped")
    assert run("agent.matrix", ctx, product=PRODUCT, as_of=day(17)).view == "grouped"
    # The call can still ask for the other view; nobody else's default moved.
    assert run("agent.matrix", ctx, product=PRODUCT, as_of=day(17), view="expanded").view == (
        "expanded"
    )
    assert matrix().view == "expanded"
    prefs = run("preference.list", ctx).preferences
    assert [(p.key, p.value, p.allowed) for p in prefs] == [
        ("agent.matrix.view", "grouped", ["expanded", "grouped"])
    ]
    with pytest.raises(InvalidInput):
        run("preference.set", ctx, key="agent.matrix.view", value="sideways")
    with pytest.raises(InvalidInput):
        run("preference.set", ctx, key="nope", value="grouped")
    with pytest.raises(Conflict):
        run("preference.set", key="agent.matrix.view", value="grouped")


def test_the_matrix_is_open_to_agent_readers_only(org: dict[str, str]) -> None:
    assert run("agent.matrix", role_ctx("agent_supervisor"), product=PRODUCT, as_of=day(17)).rows
    with pytest.raises(PermissionDenied):
        run("agent.matrix", role_ctx("contributor"), product=PRODUCT, as_of=day(17))
