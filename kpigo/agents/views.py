"""The preset sections beyond the leaderboard and matrix (Scope §8.2; Starter Packs §4.2).

- **Trend**: each day of the window to date, across the agents in view. For a
  sum or count, the day's total and the running total against where the targets
  expect it; for an average, the mean of the agents who reported that day
  against the mean of their targets.
- **Heatmap**: entities (region, branch or RM) × days, each cell that day's
  figure against its target with a RAG: the SLA view.
- **Distribution**: how agents spread on their to-date figure, in equal-width
  bins: the TAT view.

As in the matrix, everything is built from agents. An agent who reported
nothing in the window adds neither figure nor target (absent is not zero), and
a mix of currencies yields no sum.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from kpigo.agents.config import Settings, rag
from kpigo.agents.daily import Agent, Pacer, Plan, Window, daily_totals, month_targets
from kpigo.agents.pace import ADDITIVE, q, ratio
from kpigo.metrics.models import Metric

Rag = Literal["green", "amber", "red"] | None
Level = Literal["region", "branch", "rm"]
MAX_BINS = 8


@dataclass(frozen=True)
class Series:
    agent: Agent
    plan: Plan

    @property
    def reported(self) -> bool:
        return bool(self.plan.actuals)


def series(
    org_id: str,
    product: str,
    window: Window,
    settings: Settings,
    metric: Metric,
    agents: Sequence[Agent],
) -> tuple[Pacer, list[Series]]:
    """Every agent's day plan and figures on one metric, in the window to date."""
    pacer = Pacer(org_id, product, window, settings)
    last = min(window.as_of, window.end - timedelta(days=1))
    code = metric.metric_code
    ids = [a.subject_id for a in agents]
    totals = daily_totals(org_id, [code], window.start, last, ids)
    targets = month_targets(org_id, [code], window.months)
    out = [
        Series(
            a,
            pacer.plan(
                a,
                metric,
                pacer.targets_for(a, metric, targets),
                totals.get((a.subject_id, code), {}),
            ),
        )
        for a in agents
    ]
    return pacer, out


def to_date_days(window: Window) -> list[date]:
    return [d for d in window.days if d <= window.as_of]


def _currency(found: Sequence[Series]) -> tuple[str | None, bool]:
    codes = {s.plan.currency_code for s in found if s.reported and s.plan.currency_code}
    return (next(iter(codes)) if len(codes) == 1 else None), len(codes) > 1


# ── trend ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrendPoint:
    day: date
    working: bool
    value: Decimal | None
    reported: int
    # Sums and counts only: the running total, and where the targets expect it.
    cumulative: Decimal | None
    expected: Decimal | None
    # Averages only: the mean of the reporting agents' targets.
    target: Decimal | None


@dataclass(frozen=True)
class Trend:
    points: list[TrendPoint]
    agents: int
    reported: int
    currency_code: str | None
    mixed_currency: bool


def trend(pacer: Pacer, metric: Metric, found: Sequence[Series]) -> Trend:
    additive = metric.aggregation in ADDITIVE
    currency, mixed = _currency(found)
    reporting = [s for s in found if s.reported]
    points: list[TrendPoint] = []
    running = Decimal(0)
    expected = Decimal(0)
    any_target = any(p.share is not None for s in reporting for p in s.plan.days)
    for i, day in enumerate(to_date_days(pacer.window)):
        values = [s.plan.actuals[day] for s in reporting if day in s.plan.actuals]
        value: Decimal | None = None
        target: Decimal | None = None
        if values and not mixed:
            value = sum(values, Decimal(0)) if additive else sum(values, Decimal(0)) / len(values)
        if additive:
            running += value or 0
            expected += sum((s.plan.days[i].share or Decimal(0) for s in reporting), Decimal(0))
        else:
            targets = [
                s.plan.month_target
                for s in reporting
                if day in s.plan.actuals and s.plan.month_target is not None
            ]
            target = sum(targets, Decimal(0)) / len(targets) if targets else None
        points.append(
            TrendPoint(
                day=day,
                working=pacer.calendar.is_working(day, None),
                value=q(value),
                reported=len(values),
                cumulative=q(running) if additive and reporting and not mixed else None,
                expected=q(expected) if additive and any_target and not mixed else None,
                target=q(target) if not mixed else None,
            )
        )
    return Trend(
        points=points,
        agents=len(found),
        reported=len(reporting),
        currency_code=currency,
        mixed_currency=mixed,
    )


# ── heatmap ──────────────────────────────────────────────────────────────────


@dataclass
class _Acc:
    actual: Decimal = Decimal(0)
    targeted_actual: Decimal = Decimal(0)
    target: Decimal = Decimal(0)
    reported: int = 0
    targeted: int = 0


@dataclass(frozen=True)
class HeatCell:
    value: Decimal | None
    achieved: Decimal | None
    rag: Rag
    reported: int


@dataclass(frozen=True)
class HeatRow:
    key: str
    name: str
    region_code: str | None
    branch_code: str | None
    agents: int
    cells: list[HeatCell]


@dataclass(frozen=True)
class Heatmap:
    days: list[date]
    working: list[bool]
    rows: list[HeatRow]
    currency_code: str | None
    mixed_currency: bool


def row_key(agent: Agent, level: Level) -> str:
    if level == "region":
        return agent.region_code or ""
    if level == "branch":
        return agent.branch_code or ""
    return agent.subject_id


def heatmap(
    pacer: Pacer,
    metric: Metric,
    found: Sequence[Series],
    level: Level,
    names: dict[str, str],
) -> Heatmap:
    additive = metric.aggregation in ADDITIVE
    currency, mixed = _currency(found)
    # A day off nobody worked is no column; a day off someone reported on is.
    days = [
        d
        for d in to_date_days(pacer.window)
        if pacer.calendar.is_working(d, None) or any(d in s.plan.actuals for s in found)
    ]
    plan_index = {d: i for i, d in enumerate(pacer.window.days)}
    by_row: dict[str, list[Series]] = defaultdict(list)
    for s in found:
        by_row[row_key(s.agent, level)].append(s)
    rows: list[HeatRow] = []
    for key, members in by_row.items():
        cells: list[HeatCell] = []
        for day in days:
            i = plan_index[day]
            acc = _Acc()
            for s in members:
                if day not in s.plan.actuals:
                    continue
                v = s.plan.actuals[day]
                acc.reported += 1
                acc.actual += v
                expected = s.plan.days[i].share if additive else s.plan.month_target
                if expected is not None and (expected > 0 or not additive):
                    acc.targeted += 1
                    acc.targeted_actual += v
                    acc.target += expected
            cells.append(_heat_cell(acc, additive, metric, pacer.settings, mixed))
        first = members[0].agent
        rows.append(
            HeatRow(
                key=key,
                name=names.get(key)
                or key
                or {"region": "No region", "branch": "No branch"}.get(level, "Unnamed"),
                region_code=first.region_code,
                branch_code=None if level == "region" else first.branch_code,
                agents=len(members),
                cells=cells,
            )
        )
    rows.sort(key=lambda r: (r.key == "", r.name.lower(), r.key))
    return Heatmap(
        days=days,
        working=[pacer.calendar.is_working(d, None) for d in days],
        rows=rows,
        currency_code=currency,
        mixed_currency=mixed,
    )


def _heat_cell(
    acc: _Acc, additive: bool, metric: Metric, settings: Settings, mixed: bool
) -> HeatCell:
    if not acc.reported or mixed:
        return HeatCell(value=None, achieved=None, rag=None, reported=acc.reported)
    value = acc.actual if additive else acc.actual / acc.reported
    achieved = None
    if acc.targeted and acc.target > 0:
        actual = acc.targeted_actual if additive else acc.targeted_actual / acc.targeted
        expected = acc.target if additive else acc.target / acc.targeted
        achieved = q(ratio(actual, expected, metric.direction, settings.pace_cap))
    return HeatCell(
        value=q(value), achieved=achieved, rag=rag(settings, achieved), reported=acc.reported
    )


# ── distribution ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Bin:
    low: Decimal
    high: Decimal
    agents: int
    # Of them, how many are at or better than their target to date.
    on_target: int


@dataclass(frozen=True)
class Distribution:
    bins: list[Bin]
    agents: int
    reported: int
    # The target to date, when every reporting agent shares one.
    target: Decimal | None
    median: Decimal | None
    currency_code: str | None
    mixed_currency: bool
    values: list[Decimal] = field(default_factory=list)


def distribution(pacer: Pacer, metric: Metric, found: Sequence[Series]) -> Distribution:
    currency, mixed = _currency(found)
    figures: list[tuple[Decimal, Decimal | None, bool]] = []
    for s in found:
        if not s.reported:
            continue
        p = pacer.paced(metric, s.plan).pace
        if p.actual is None:
            continue
        figures.append((p.actual, p.target_to_date, p.pace is not None and p.pace >= 1))
    if not figures or mixed:
        return Distribution(
            bins=[],
            agents=len(found),
            reported=len(figures),
            target=None,
            median=None,
            currency_code=currency,
            mixed_currency=mixed,
        )
    values = sorted(v for v, _, _ in figures)
    lo, hi = values[0], values[-1]
    count = 1 if lo == hi else min(MAX_BINS, len(set(values)))
    width = (hi - lo) / count if count > 1 else Decimal(0)
    bins = []
    for i in range(count):
        low = lo + width * i
        high = hi if i == count - 1 else lo + width * (i + 1)
        inside = [f for f in figures if (low <= f[0] < high) or (i == count - 1 and f[0] == high)]
        bins.append(
            Bin(
                low=q(low) or low,
                high=q(high) or high,
                agents=len(inside),
                on_target=sum(1 for f in inside if f[2]),
            )
        )
    targets = {t for _, t, _ in figures if t is not None}
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    return Distribution(
        bins=bins,
        agents=len(found),
        reported=len(figures),
        target=q(next(iter(targets))) if len(targets) == 1 else None,
        median=q(median),
        currency_code=currency,
        mixed_currency=mixed,
        values=values,
    )
