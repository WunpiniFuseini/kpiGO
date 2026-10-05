"""The product-line registry: the handshake, groups, moves, retirement, line targets (Scope §8.5)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kpigo.action import Conflict, InvalidInput, NotFound, PermissionDenied
from tests.agent_support import PRODUCT, day, load, targets, world
from tests.conftest import role_ctx, run

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> dict[str, str]:
    ids = world()
    targets()
    load(
        [
            ["value_booked", "A1", day(2), "CARDS", "600", "GHS"],
            ["value_booked", "A1", day(3), "LOANS", "300", "GHS"],
            ["value_booked", "A2", day(5), "MORTGAGE", "200", "GHS"],
        ]
    )
    return ids


def registry(as_of: str = day(17), ctx: Any = None) -> Any:
    return run("product_line.registry", ctx or role_ctx(), as_of=as_of)


def codes(lines: Any) -> list[str]:
    return [line.code for line in lines]


def test_the_feed_makes_lines_available_and_nothing_shows_until_activated(
    org: dict[str, str],
) -> None:
    out = registry()
    assert codes(out.available) == ["CARDS", "LOANS", "MORTGAGE"]
    assert all(line.first_detected_at is not None for line in out.available)
    assert out.available[0].effective_from.isoformat() == day(2)
    assert out.in_matrix == [] and out.groups == []
    # There is no way to type a line in: only a code the feed carried activates.
    with pytest.raises(NotFound):
        run("product_line.activate", code="BANCASSURANCE", display_name="Bancassurance")


def test_the_first_line_needs_no_group_and_gets_a_default_one(org: dict[str, str]) -> None:
    line = run("product_line.activate", code="CARDS", display_name="Cards")
    assert (line.status, line.group_code) == ("active", "products")
    # Shown from the first day the feed carried it.
    assert line.effective_from.isoformat() == day(2)
    out = registry()
    assert codes(out.in_matrix) == ["CARDS"] and out.in_matrix[0].display_name == "Cards"
    assert [(g.code, g.display_name, g.line_codes) for g in out.groups] == [
        ("products", "Products", ["CARDS"])
    ]
    # The only active group is the default for the next line too.
    assert run("product_line.activate", code="LOANS", display_name="Loans").group_code == (
        "products"
    )
    with pytest.raises(Conflict):
        run("product_line.activate", code="CARDS", display_name="Cards")


def test_with_several_groups_activation_names_one(org: dict[str, str]) -> None:
    run("product_group.set", code="lending", display_name="Lending", sort_order=10)
    run("product_group.set", code="cards", display_name="Cards and payments", sort_order=20)
    with pytest.raises(InvalidInput):
        run("product_line.activate", code="CARDS", display_name="Cards")
    with pytest.raises(NotFound):
        run("product_line.activate", code="CARDS", display_name="Cards", group_code="nope")
    run("product_line.activate", code="CARDS", display_name="Cards", group_code="cards")
    run("product_line.activate", code="LOANS", display_name="Loans", group_code="lending")
    out = registry()
    # Groups first by their order, then lines in theirs.
    assert [(g.code, g.line_codes) for g in out.groups] == [
        ("lending", ["LOANS"]),
        ("cards", ["CARDS"]),
    ]
    assert codes(out.available) == ["MORTGAGE"]
    with pytest.raises(Conflict):
        run("product_group.set", code="cards", display_name="Cards", status="retired")


def test_a_move_is_effective_dated_and_history_keeps_its_grouping(org: dict[str, str]) -> None:
    run("product_group.set", code="lending", display_name="Lending")
    run("product_group.set", code="retail", display_name="Retail")
    run("product_line.activate", code="CARDS", display_name="Cards", group_code="lending")
    run("product_line.move", code="CARDS", group_code="retail", effective_from=day(16))
    before = {g.code: g.line_codes for g in registry(day(15)).groups}
    after = {g.code: g.line_codes for g in registry(day(16)).groups}
    assert before == {"lending": ["CARDS"], "retail": []}
    assert after == {"lending": [], "retail": ["CARDS"]}
    # A move before one already scheduled would rewrite it: refused.
    with pytest.raises(Conflict):
        run("product_line.move", code="CARDS", group_code="lending", effective_from=day(10))
    with pytest.raises(Conflict):
        run("product_line.move", code="CARDS", group_code="retail", effective_from=day(20))
    with pytest.raises(Conflict):
        run("product_line.move", code="LOANS", group_code="retail", effective_from=day(20))


def test_retirement_is_effective_dated_and_never_destructive(org: dict[str, str]) -> None:
    run("product_line.activate", code="CARDS", display_name="Cards")
    line = run("product_line.retire", code="CARDS", effective_to=day(20))
    assert (line.status, line.effective_to.isoformat()) == ("retired", day(20))
    # June's matrix still has it; from the 20th it is gone.
    assert codes(registry(day(19)).in_matrix) == ["CARDS"]
    later = registry(day(20))
    assert later.in_matrix == [] and codes(later.retired) == ["CARDS"]
    # The facts stay where they are.
    pace = run("agent.pace", product=PRODUCT, subject_id=org["A1"], as_of=day(17))
    value = next(m for m in pace.metrics if m.metric_code == "value_booked")
    assert value.actual == Decimal("900.0000")
    with pytest.raises(Conflict):
        run("product_line.retire", code="CARDS")
    with pytest.raises(Conflict):
        run("product_line.retire", code="LOANS")  # never switched on
    run("product_line.activate", code="LOANS", display_name="Loans")
    with pytest.raises(InvalidInput):
        run("product_line.retire", code="LOANS", effective_to=day(1))


def test_quick_settings_reorder_and_line_thresholds(org: dict[str, str]) -> None:
    for code in ("CARDS", "LOANS", "MORTGAGE"):
        run("product_line.activate", code=code, display_name=code.title())
    assert run("product_line.reorder", codes=["MORTGAGE", "CARDS"]).codes == [
        "MORTGAGE",
        "CARDS",
        "LOANS",
    ]
    assert codes(registry().in_matrix) == ["MORTGAGE", "CARDS", "LOANS"]
    line = run("product_line.update", code="CARDS", rag_green="1.1", rag_amber="0.9")
    assert (line.rag_green, line.rag_amber) == (Decimal("1.100"), Decimal("0.900"))
    assert run("product_line.update", code="CARDS", clear_rag=True).rag_green is None
    with pytest.raises(InvalidInput):
        run("product_line.update", code="CARDS", rag_green="0.8", rag_amber="0.9")
    with pytest.raises(InvalidInput):
        run("product_line.update", code="CARDS", rag_green="1.1")
    with pytest.raises(NotFound):
        run("product_line.reorder", codes=["NOPE"])


def test_past_eight_lines_the_grouped_view_is_suggested(org: dict[str, str]) -> None:
    rows: list[list[Any]] = [["value_booked", "A3", day(4), f"L{i}", "10", "GHS"] for i in range(9)]
    load(rows, feed="many")
    for i in range(9):
        run("product_line.activate", code=f"L{i}", display_name=f"Line {i}")
    assert registry().suggest_grouped is True


def line_row(line: str | None, metric: str = "value_booked", **extra: Any) -> dict[str, Any]:
    row = {
        "metric_code": metric,
        "scope_type": "profile",
        "scope_code": "sme_rm",
        "period_key": "202507",
        "target_value": "500",
        "currency_code": "GHS",
        "product_line_code": line,
    }
    return row | extra


def test_line_targets_go_through_the_workbench(org: dict[str, str]) -> None:
    run("product_line.activate", code="CARDS", display_name="Cards")
    out = run("target.upload", rows=[line_row("CARDS"), line_row("LOANS"), line_row(None)])
    assert out.accepted, out.findings
    run("target.publish", period_keys=["202507"])
    live = run("target.list", period_key="202507", state="published").targets
    assert sorted(t.product_line_code for t in live) == ["", "CARDS", "LOANS"]
    only = run("target.list", period_key="202507", product_line_code="CARDS").targets
    assert [t.target_value for t in only] == [Decimal("500.0000")]
    # Pacing reads the metric's own target, not a line's.
    pace = run("agent.pace", product=PRODUCT, subject_id=org["A1"], as_of="2025-07-31")
    value = next(m for m in pace.metrics if m.metric_code == "value_booked")
    assert value.window_target == Decimal("500.0000")


def test_line_targets_are_checked(org: dict[str, str]) -> None:
    run(
        "metric.register",
        display_name="Deposits",
        metric_code="deposits",
        direction="higher_is_better",
        aggregation="sum",
        unit="currency",
        target_scope="profile",
        products=["scorecards"],
        effective_from="2024-01-01",
        acknowledge_similar=True,
    )
    run("product_line.activate", code="CARDS", display_name="Cards")
    run("product_line.retire", code="CARDS", effective_to="2025-07-01")
    out = run(
        "target.upload",
        check_only=True,
        rows=[
            line_row("NOPE"),
            line_row("CARDS"),
            line_row("LOANS", metric="deposits", weight="10", cap="12"),
        ],
    )
    found = {(f.row_no, f.code) for f in out.findings if f.severity == "error"}
    assert found == {
        (1, "unknown_product_line"),
        (2, "product_line_retired"),
        (3, "line_target_not_agent"),
    }


def test_admins_and_data_stewards_keep_the_registry(org: dict[str, str]) -> None:
    steward = role_ctx("data_steward")
    assert codes(registry(ctx=steward).available) == ["CARDS", "LOANS", "MORTGAGE"]
    run("product_line.activate", steward, code="CARDS", display_name="Cards")
    with pytest.raises(PermissionDenied):
        run("product_line.activate", role_ctx("staff"), code="LOANS", display_name="Loans")
    with pytest.raises(PermissionDenied):
        registry(ctx=role_ctx("staff"))
