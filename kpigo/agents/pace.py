"""Pace to target (PRD AP-2; Scope §8.1). Pure arithmetic, no database.

On working day 16 of 22, "73% of target" reads as failure when the agent is on
track. Pace compares what an agent has done with what the target expected *by
now*:

    pace = actual_to_date ÷ target_to_date

``target_to_date`` accrues one share of the month's target per elapsed working
day: a month target ``T`` over ``N`` working days is ``T / N`` a working day and
nothing on a weekend or holiday. Over a month window that is exactly
``T × elapsed ÷ total`` (Scope §8.1); over a week it sums the shares of the days
in the week, which is how a week straddling two months takes a part of each
month's target. The as-of day counts as elapsed: a load for Tuesday is Tuesday's
work done.

Only additive metrics accrue (``sum``, ``count``). An average, a ratio or a
latest value is not "built up" over the month, so it is compared with the month
target directly and pace is its achievement so far.

Lower is better reads the other way (``target ÷ actual``), as Scorecards does;
an actual of zero against a positive target earns the cap, since nothing is
better than none. Absent is not zero: no row in the window is ``not_reported``,
never a pace of zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

ADDITIVE = frozenset({"sum", "count"})

PaceState = Literal["paced", "not_reported", "no_target", "no_fx_rate", "not_started"]

_VALUE = Decimal("0.0001")


def q(x: Decimal | None) -> Decimal | None:
    return None if x is None else x.quantize(_VALUE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class DayPlan:
    """One calendar day of the window: whether it is worked, and its share of target.

    ``share`` is the day's part of its own month's target (``T / N`` on a working
    day, zero otherwise); None when that month has no target.
    """

    day: date
    working: bool
    share: Decimal | None


@dataclass(frozen=True)
class PaceIn:
    direction: str
    aggregation: str
    days: Sequence[DayPlan]
    as_of: date
    # Daily totals in the target's currency, for days on or before ``as_of``.
    actuals: Mapping[date, Decimal]
    # The target of the as-of day's month, for non-additive metrics.
    month_target: Decimal | None
    cap: Decimal
    # A reported day's currency had no FX rate into the target's.
    missing_fx: bool = False


@dataclass(frozen=True)
class Pace:
    state: PaceState
    actual: Decimal | None
    # What the target expects by the as-of day; for non-additive metrics, the target.
    target_to_date: Decimal | None
    # The target for the whole window.
    window_target: Decimal | None
    # Out of 1.0; None unless ``paced``.
    pace: Decimal | None
    # How far the agent is from being on pace (zero when on or ahead of it).
    short_of_pace: Decimal | None
    # Where the window ends at the current run rate (additive metrics only).
    projected: Decimal | None
    working_days_elapsed: int
    working_days_total: int
    days_reported: int


def ratio(actual: Decimal, expected: Decimal, direction: str, cap: Decimal) -> Decimal:
    """Achievement of ``actual`` against ``expected``, direction-aware."""
    if direction == "lower_is_better":
        return cap if actual == 0 else expected / actual
    return actual / expected


def pace(p: PaceIn) -> Pace:
    elapsed = [d for d in p.days if d.day <= p.as_of]
    worked_total = sum(1 for d in p.days if d.working)
    worked_elapsed = sum(1 for d in elapsed if d.working)
    reported = {day: v for day, v in p.actuals.items() if day <= p.as_of}

    def result(state: PaceState, **kw: Decimal | None) -> Pace:
        return Pace(
            state=state,
            actual=q(kw.get("actual")),
            target_to_date=q(kw.get("target_to_date")),
            window_target=q(kw.get("window_target")),
            pace=q(kw.get("pace")),
            short_of_pace=q(kw.get("short_of_pace")),
            projected=q(kw.get("projected")),
            working_days_elapsed=worked_elapsed,
            working_days_total=worked_total,
            days_reported=len(reported),
        )

    additive = p.aggregation in ADDITIVE
    actual: Decimal | None = None
    if reported:
        values = [reported[d] for d in sorted(reported)]
        if additive:
            actual = sum(values, Decimal(0))
        elif p.aggregation == "latest":
            actual = values[-1]
        else:  # average, ratio: the mean of the reported days
            actual = sum(values, Decimal(0)) / len(values)

    if additive:
        shares = [d.share for d in p.days]
        known = all(s is not None for s in shares)
        window_target = sum((s for s in shares if s is not None), Decimal(0)) if known else None
        elapsed_shares = [d.share for d in elapsed]
        to_date = (
            sum((s for s in elapsed_shares if s is not None), Decimal(0))
            if all(s is not None for s in elapsed_shares)
            else None
        )
    else:
        window_target = to_date = p.month_target

    if actual is None:
        return result("not_reported", target_to_date=to_date, window_target=window_target)
    if p.missing_fx:
        return result("no_fx_rate", actual=actual)
    if to_date is None or window_target is None or window_target <= 0:
        return result("no_target", actual=actual)
    projected = actual / worked_elapsed * worked_total if additive and worked_elapsed > 0 else None
    if to_date <= 0:
        # Nothing was expected yet: the window has had no working day.
        return result(
            "not_started", actual=actual, target_to_date=to_date, window_target=window_target
        )
    achieved = ratio(actual, to_date, p.direction, p.cap)
    gap = to_date - actual if p.direction != "lower_is_better" else actual - to_date
    return result(
        "paced",
        actual=actual,
        target_to_date=to_date,
        window_target=window_target,
        pace=achieved,
        short_of_pace=max(gap, Decimal(0)),
        projected=projected,
    )
