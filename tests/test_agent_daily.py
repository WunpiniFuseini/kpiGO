"""Agent Performance's daily read model through its actions (PRD AP-1, AP-2, AP-4, AP-7)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kpigo.action import Conflict, InvalidInput, NotFound, PermissionDenied
from tests.agent_support import MONTH, PRODUCT, day, load, targets, world
from tests.conftest import role_ctx, run

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> dict[str, str]:
    ids = world()
    targets()
    # Ashanti closes on Wednesday 4 June; Greater Accra works it.
    run(
        "calendar.set_days",
        days=[{"date": day(4), "is_working_day": False, "region_code": "AS"}],
    )
    load(
        [
            # Two product lines and one unlined row make one day's figure.
            ["value_booked", "A1", day(2), "CARDS", "600", "GHS"],
            ["value_booked", "A1", day(2), "LOANS", "300", "GHS"],
            ["value_booked", "A1", day(2), None, "100", "GHS"],
            ["value_booked", "A1", day(17), None, "320", "GHS"],
            ["accounts_opened", "A1", day(2), None, "15", None],
            ["service_tat", "A1", day(2), None, "3", None],
            ["service_tat", "A1", day(3), None, "5", None],
            ["value_booked", "A3", day(17), None, "1155", "GHS"],
        ]
    )
    return ids


def metrics(out: Any) -> dict[str, Any]:
    return {m.metric_code: m for m in out.metrics}


def test_pace_against_working_days_elapsed(org: dict[str, str]) -> None:
    out = run("agent.pace", product=PRODUCT, subject_id=org["A1"])
    # The latest loaded day is the default as-of: 17 June, working day 12 of 21.
    assert out.window.kind == "month"
    assert (out.window.start.isoformat(), out.window.end.isoformat()) == (day(1), day(30))
    assert out.window.as_of.isoformat() == day(17)
    assert (out.window.working_day, out.window.working_days) == (12, 21)
    m = metrics(out)
    value = m["value_booked"]
    assert value.state == "paced"
    assert value.actual == Decimal("1320.0000")
    assert value.target_to_date == Decimal("1200.0000")
    assert value.pace == Decimal("1.1000")
    assert value.currency_code == "GHS"
    assert value.band is not None and value.band.label == "On Target"
    assert value.rag == "green"
    assert m["accounts_opened"].pace == Decimal("1.2500")
    # An average is compared with the month's target as it stands: 4 hours against 4.
    tat = m["service_tat"]
    assert (tat.actual, tat.pace, tat.projected) == (Decimal("4.0000"), Decimal(1), None)


def test_a_regional_holiday_changes_that_regions_pace(org: dict[str, str]) -> None:
    out = run("agent.pace", product=PRODUCT, subject_id=org["A3"], as_of=day(17))
    value = metrics(out)["value_booked"]
    # 20 working days in Ashanti: 105 a day, 11 of them by the 17th.
    assert value.target_to_date == Decimal("1155.0000")
    assert value.pace == Decimal(1)


def test_absent_is_not_zero_and_targets_missing_say_so(org: dict[str, str]) -> None:
    out = run("agent.pace", product=PRODUCT, subject_id=org["A2"], as_of=day(17))
    assert {m.state for m in out.metrics} == {"not_reported"}
    assert all(m.pace is None for m in out.metrics)
    later = run("agent.pace", product=PRODUCT, subject_id=org["A1"], as_of="2025-07-01")
    # July has no targets yet, and nothing loaded.
    assert {m.state for m in later.metrics} == {"not_reported"}
    load([["value_booked", "A1", "2025-07-01", None, "50", "GHS"]], feed="july")
    later = run("agent.pace", product=PRODUCT, subject_id=org["A1"], as_of="2025-07-01")
    assert metrics(later)["value_booked"].state == "no_target"


def test_the_weekly_opt_in_paces_the_week_to_date(org: dict[str, str]) -> None:
    run("agent.settings.set", product=PRODUCT, grain="weekly")
    out = run("agent.pace", product=PRODUCT, subject_id=org["A1"], as_of=day(17))
    assert out.window.kind == "week"
    assert (out.window.start.isoformat(), out.window.end.isoformat()) == (day(16), day(22))
    assert (out.window.working_day, out.window.working_days) == (2, 5)
    value = metrics(out)["value_booked"]
    # Only the 17th's 320 falls in the week; two days at 100 were expected.
    assert value.actual == Decimal("320.0000")
    assert value.target_to_date == Decimal("200.0000")
    assert value.window_target == Decimal("500.0000")
    assert value.pace == Decimal("1.6000")
    # A caller can still ask for the month.
    month = run("agent.pace", product=PRODUCT, subject_id=org["A1"], as_of=day(17), window="month")
    assert month.window.kind == "month"


def test_actuals_convert_into_the_targets_currency(org: dict[str, str]) -> None:
    load([["value_booked", "A2", day(2), None, "10", "USD"]], feed="usd")
    out = run("agent.pace", product=PRODUCT, subject_id=org["A2"], as_of=day(17))
    assert metrics(out)["value_booked"].state == "no_fx_rate"
    run(
        "fx.set",
        rates=[{"from_currency": "USD", "to_currency": "GHS", "period_key": MONTH, "rate": "12"}],
    )
    out = run("agent.pace", product=PRODUCT, subject_id=org["A2"], as_of=day(17))
    value = metrics(out)["value_booked"]
    assert value.actual == Decimal("120.0000") and value.state == "paced"


def test_open_by_default_and_your_own_pace(org: dict[str, str], make_user: Any) -> None:
    # A relationship manager reads a colleague's pace: the comparison is the point (AP-7).
    out = run("agent.pace", role_ctx("staff"), product=PRODUCT, subject_id=org["A3"])
    assert out.agent.staff_no == "A3"
    me = make_user("staff", email="a1@bank.example")
    from kpigo.action.identity import build_context

    ctx = build_context(me, caller="cli")
    assert run("agent.pace", ctx, product=PRODUCT).agent.staff_no == "A1"
    with pytest.raises(Conflict):
        run("agent.pace", product=PRODUCT)  # the CLI admin is nobody in the hierarchy
    with pytest.raises(PermissionDenied):
        run("agent.pace", role_ctx("contributor"), product=PRODUCT, subject_id=org["A1"])


def test_someone_not_measured_in_the_product_is_not_found(org: dict[str, str]) -> None:
    with pytest.raises(NotFound):
        run("agent.pace", product="agent_service", subject_id=org["A1"], as_of=day(17))


def test_targets_for_agent_metrics_need_no_weight(org: dict[str, str]) -> None:
    run(
        "metric.register",
        display_name="Deposits",
        metric_code="deposits",
        direction="higher_is_better",
        aggregation="sum",
        unit="currency",
        products=["scorecards"],
        effective_from="2024-01-01",
        acknowledge_similar=True,
    )
    out = run(
        "target.upload",
        rows=[
            {
                "metric_code": "value_booked",
                "scope_type": "profile",
                "scope_code": "sme_rm",
                "period_key": "202508",
                "target_value": "10",
            },
            {
                "metric_code": "deposits",
                "scope_type": "profile",
                "scope_code": "sme_rm",
                "period_key": "202508",
                "target_value": "10",
            },
        ],
    )
    codes = {(f.row_no, f.code) for f in out.findings if f.severity == "error"}
    # The Scorecards metric still needs its weight and cap; the agent metric does not.
    assert codes == {(2, "weight_required"), (2, "cap_required")}


def test_settings_validate_and_default(org: dict[str, str]) -> None:
    got = run("agent.settings.get", product="agent_service")
    assert (got.grain, got.pace_cap, got.rag_green, got.rag_amber) == (
        "daily",
        Decimal(2),
        Decimal(1),
        Decimal("0.85"),
    )
    with pytest.raises(InvalidInput):
        run("agent.settings.set", product=PRODUCT, rag_green="0.8", rag_amber="0.9")
    with pytest.raises(PermissionDenied):
        run("agent.settings.set", role_ctx("staff"), product=PRODUCT, grain="weekly")
