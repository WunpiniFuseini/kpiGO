"""Pace to target, the arithmetic alone (PRD AP-2; Scope §8.1). No database."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from kpigo.agents.pace import DayPlan, Pace, PaceIn, pace

# June 2025: starts on a Sunday, 21 working days Monday to Friday.
JUNE = [date(2025, 6, 1) + timedelta(days=i) for i in range(30)]


def plans(
    target: Decimal | None, days: list[date] = JUNE, holidays: frozenset[date] = frozenset()
) -> list[DayPlan]:
    worked = [d for d in days if d.weekday() < 5 and d not in holidays]
    return [
        DayPlan(
            day=d,
            working=d in worked,
            share=None if target is None else (target / len(worked) if d in worked else Decimal(0)),
        )
        for d in days
    ]


def run(
    target: Decimal | None,
    as_of: date,
    actuals: dict[date, Decimal],
    *,
    direction: str = "higher_is_better",
    aggregation: str = "sum",
    days: list[DayPlan] | None = None,
    missing_fx: bool = False,
) -> Pace:
    return pace(
        PaceIn(
            direction=direction,
            aggregation=aggregation,
            days=days if days is not None else plans(target),
            as_of=as_of,
            actuals=actuals,
            month_target=target,
            cap=Decimal(2),
            missing_fx=missing_fx,
        )
    )


def test_pace_is_actual_against_the_target_expected_by_now() -> None:
    # 17 June is working day 12 of 21: 2100 expects 1200 by then.
    p = run(Decimal(2100), date(2025, 6, 17), {date(2025, 6, 2): Decimal(1320)})
    assert (p.working_days_elapsed, p.working_days_total) == (12, 21)
    assert p.state == "paced"
    assert p.target_to_date == Decimal("1200.0000")
    assert p.window_target == Decimal("2100.0000")
    assert p.pace == Decimal("1.1000")
    assert p.short_of_pace == Decimal("0.0000")
    # At this run rate the month ends at 1320 / 12 × 21.
    assert p.projected == Decimal("2310.0000")


def test_behind_pace_reports_what_is_short() -> None:
    p = run(Decimal(2100), date(2025, 6, 17), {date(2025, 6, 2): Decimal(900)})
    assert p.pace == Decimal("0.7500")
    assert p.short_of_pace == Decimal("300.0000")


def test_a_weekend_as_of_day_counts_the_week_already_worked() -> None:
    saturday = run(Decimal(2100), date(2025, 6, 7), {date(2025, 6, 3): Decimal(500)})
    friday = run(Decimal(2100), date(2025, 6, 6), {date(2025, 6, 3): Decimal(500)})
    assert saturday.working_days_elapsed == friday.working_days_elapsed == 5
    assert saturday.pace == friday.pace == Decimal(1)


def test_a_holiday_moves_the_daily_share() -> None:
    holiday = date(2025, 6, 4)
    days = plans(Decimal(2000), holidays=frozenset({holiday}))
    p = run(Decimal(2000), date(2025, 6, 6), {date(2025, 6, 2): Decimal(400)}, days=days)
    # 20 working days: 100 a day, and 4 of them by Friday the 6th.
    assert (p.working_days_elapsed, p.working_days_total) == (4, 20)
    assert p.target_to_date == Decimal(400)
    assert p.pace == Decimal(1)


def test_lower_is_better_reads_the_other_way_and_zero_earns_the_cap() -> None:
    # Complaints: 21 allowed in the month, so 12 by the 17th.
    p = run(
        Decimal(21),
        date(2025, 6, 17),
        {date(2025, 6, 2): Decimal(8)},
        direction="lower_is_better",
    )
    assert p.pace == Decimal("1.5000")
    zero = run(
        Decimal(21), date(2025, 6, 17), {date(2025, 6, 2): Decimal(0)}, direction="lower_is_better"
    )
    assert zero.state == "paced" and zero.pace == Decimal(2)
    over = run(
        Decimal(21), date(2025, 6, 17), {date(2025, 6, 2): Decimal(16)}, direction="lower_is_better"
    )
    assert over.short_of_pace == Decimal(4)


def test_an_average_compares_with_the_month_target_without_accruing() -> None:
    actuals = {date(2025, 6, 2): Decimal(3), date(2025, 6, 3): Decimal(5)}
    p = run(
        Decimal(4), date(2025, 6, 3), actuals, direction="lower_is_better", aggregation="average"
    )
    assert p.actual == Decimal(4)
    assert p.target_to_date == p.window_target == Decimal(4)
    assert p.pace == Decimal(1)
    assert p.projected is None


def test_latest_takes_the_last_reported_day() -> None:
    actuals = {date(2025, 6, 2): Decimal(90), date(2025, 6, 9): Decimal(110)}
    p = run(Decimal(100), date(2025, 6, 17), actuals, aggregation="latest")
    assert p.actual == Decimal(110) and p.pace == Decimal("1.1000")


def test_days_after_the_as_of_day_are_ignored() -> None:
    actuals = {date(2025, 6, 2): Decimal(100), date(2025, 6, 20): Decimal(999)}
    p = run(Decimal(2100), date(2025, 6, 2), actuals)
    assert p.actual == Decimal(100) and p.days_reported == 1


def test_absent_is_not_zero() -> None:
    p = run(Decimal(2100), date(2025, 6, 17), {})
    assert p.state == "not_reported"
    assert p.pace is None and p.actual is None
    assert p.target_to_date == Decimal(1200)


def test_no_target_and_no_rate_are_states_not_zeros() -> None:
    assert run(None, date(2025, 6, 17), {date(2025, 6, 2): Decimal(1)}).state == "no_target"
    p = run(Decimal(2100), date(2025, 6, 17), {date(2025, 6, 2): Decimal(1)}, missing_fx=True)
    assert p.state == "no_fx_rate" and p.pace is None


def test_nothing_is_expected_before_the_first_working_day() -> None:
    p = run(Decimal(2100), date(2025, 6, 1), {date(2025, 6, 1): Decimal(50)})
    assert p.state == "not_started" and p.pace is None


def test_a_week_straddling_two_months_takes_a_share_of_each() -> None:
    # Mon 30 June to Sun 6 July 2025. June: 2100 over 21 working days, 100 a day;
    # July: 4600 over 23, 200 a day.
    week = [date(2025, 6, 30) + timedelta(days=i) for i in range(7)]
    per_day = {6: Decimal(2100) / 21, 7: Decimal(4600) / 23}
    days = [
        DayPlan(
            day=d,
            working=d.weekday() < 5,
            share=per_day[d.month] if d.weekday() < 5 else Decimal(0),
        )
        for d in week
    ]
    p = run(Decimal(4600), date(2025, 7, 2), {date(2025, 6, 30): Decimal(550)}, days=days)
    assert (p.working_days_elapsed, p.working_days_total) == (3, 5)
    # 100 for Monday 30 June, then 200 each for 1 and 2 July.
    assert p.target_to_date == Decimal(500) and p.window_target == Decimal(900)
    assert p.pace == Decimal("1.1000")
