"""The daily read model: who is measured, on what, and how they are pacing.

Agent Performance is daily grain (AP-1). Everything here reads
``fact_actual_daily`` for a *window* (the month, or the ISO week for the weekly
opt-in) up to an *as-of* day, and paces it with ``kpigo.agents.pace``.

Who is an agent comes from the same rule Scorecards uses, on the as-of day
rather than the month's last: the assignment in force then gives the profile,
and the profile's metric assignments for the product (``metric_profile_assignment``
with ``product = agent_sales | agent_service``) give the metric set.

Facts are read by ``metric_code``, not ``metric_id``: a metric that opened a new
effective period mid-month (MR-7) keeps one line. Product lines partition a
day's activity, so a day's figure is the sum across its lines.

Targets are the workbench's published monthly targets. A stored target becomes
one month's share by its type (yearly and prorated over the cycle's months,
quarterly over three); a ``cumulative`` target already states a month's step.
Actuals convert into the target's currency at the month's average rate, as
Scorecards does; a missing rate is ``no_fx_rate``, never an assumed 1.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from django.db import connection
from django.db.models import Q

from kpigo.agents.config import Settings, settings_for
from kpigo.agents.pace import DayPlan, Pace, PaceIn, pace
from kpigo.hierarchy.models import Assignment
from kpigo.ingestion.reference import org_today
from kpigo.metrics.models import Metric, MetricProfileAssignment
from kpigo.periods.business import WorkingCalendar
from kpigo.platform.config import MissingRate, fx_rate
from kpigo.platform.vocab import month_bounds, period_key_for
from kpigo.scorecards.cycles import cycle_for
from kpigo.scorecards.models import Target

WindowKind = Literal["month", "week"]


def in_force(day: date) -> Q:
    return Q(effective_from__lte=day) & (Q(effective_to__isnull=True) | Q(effective_to__gt=day))


@dataclass(frozen=True)
class Window:
    kind: WindowKind
    start: date
    # First day after the window (half-open).
    end: date
    as_of: date

    @property
    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days)]

    @property
    def months(self) -> list[str]:
        return sorted({period_key_for(d) for d in self.days})


def window_for(kind: WindowKind, as_of: date) -> Window:
    """The month, or the Monday-to-Sunday week, containing ``as_of``."""
    if kind == "week":
        start = as_of - timedelta(days=as_of.weekday())
        return Window(kind, start, start + timedelta(days=7), as_of)
    start, end = month_bounds(period_key_for(as_of))
    return Window(kind, start, end, as_of)


def latest_day(org_id: str, product: str) -> date | None:
    """The last day with daily facts for the product's metrics, up to today."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT max(f.activity_date) FROM mv_leaderboard_daily f
            JOIN metric m ON m.metric_code = f.metric_code AND m.org_id = f.org_id
            WHERE f.org_id = %s AND f.activity_date <= %s AND EXISTS (
                SELECT 1 FROM metric_binding b
                WHERE b.metric_id = m.metric_id AND b.product = %s AND b.is_active)
            """,
            [org_id, org_today(org_id), product],
        )
        row = cur.fetchone()
    return row[0] if row else None


def default_as_of(org_id: str, product: str) -> date:
    """The latest loaded day; today when nothing has loaded yet."""
    return latest_day(org_id, product) or org_today(org_id)


# ── who, and on what ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Agent:
    subject_id: str
    staff_no: str
    full_name: str
    assignment_id: str
    role_code: str
    profile_code: str
    branch_code: str | None
    region_code: str | None
    portfolio_code: str | None
    segment_code: str | None
    cycle_id: str | None


def product_metrics(org_id: str, product: str, day: date) -> dict[str, list[Metric]]:
    """Active metrics per profile for an Agent Performance product, in force on ``day``."""
    rows = (
        MetricProfileAssignment.objects.filter(
            product=product,
            metric__org_id=org_id,
            metric__status="active",
            metric__bindings__product=product,
            metric__bindings__is_active=True,
        )
        .filter(in_force(day))
        .select_related("metric")
        .order_by("profile_code", "metric__metric_code")
    )
    out: dict[str, list[Metric]] = defaultdict(list)
    current = {m.metric_code: m for m in Metric.objects.filter(org_id=org_id).filter(in_force(day))}
    for pm in rows:
        # The assignment names one effective period; score the row in force today.
        m = current.get(pm.metric.metric_code)
        if m is not None and m.status == "active" and m not in out[pm.profile_code]:
            out[pm.profile_code].append(m)
    return dict(out)


def agents_on(
    org_id: str, product: str, day: date, subject_ids: Iterable[str] | None = None
) -> tuple[list[Agent], dict[str, list[Metric]]]:
    """Everyone whose profile carries the product's metrics on ``day``."""
    by_profile = product_metrics(org_id, product, day)
    if not by_profile:
        return [], {}
    rows = (
        Assignment.objects.filter(org_id=org_id, profile_code__in=sorted(by_profile))
        .filter(in_force(day))
        .filter(subject__status="active")
        .select_related("subject")
        .order_by("subject__full_name", "subject__staff_no")
    )
    if subject_ids is not None:
        rows = rows.filter(subject_id__in=list(subject_ids))
    agents = [
        Agent(
            subject_id=str(a.subject_id),
            staff_no=a.subject.staff_no,
            full_name=a.subject.full_name,
            assignment_id=str(a.assignment_id),
            role_code=a.role_code,
            profile_code=a.profile_code,
            branch_code=a.branch_code,
            region_code=a.region_code,
            portfolio_code=a.portfolio_code,
            segment_code=a.segment_code,
            cycle_id=str(a.cycle_id) if a.cycle_id else None,
        )
        for a in rows
    ]
    return agents, by_profile


# ── facts and targets ────────────────────────────────────────────────────────

# (subject_id, metric_code) → day → currency → total across product lines
Totals = dict[tuple[str, str], dict[date, dict[str | None, Decimal]]]


def daily_totals(
    org_id: str,
    codes: Sequence[str],
    first: date,
    last: date,
    subject_ids: Sequence[str] | None = None,
) -> Totals:
    """Each agent's daily figure per metric over ``[first, last]``, summed across lines.

    Read from ``mv_leaderboard_daily``, which ingestion refreshes after every
    daily load, archive and restore.
    """
    out: Totals = defaultdict(lambda: defaultdict(dict))
    if not codes or last < first:
        return out
    sql = """
        SELECT subject_id::text, metric_code, activity_date, nullif(currency_code, ''),
               actual_value
        FROM mv_leaderboard_daily
        WHERE org_id = %s AND activity_date >= %s AND activity_date <= %s
          AND metric_code = ANY(%s)
    """
    args: list[Any] = [org_id, first, last, list(codes)]
    if subject_ids is not None:
        sql += " AND subject_id = ANY(%s::uuid[])"
        args.append(list(subject_ids))
    with connection.cursor() as cur:
        cur.execute(sql, args)
        for subject_id, code, day, currency, value in cur.fetchall():
            out[(subject_id, code)][day][currency] = Decimal(value)
    return out


@dataclass(frozen=True)
class MonthTarget:
    target_id: str
    version: int
    scope_type: str
    stored: Decimal
    target_type: str
    currency_code: str | None


# (metric_code, scope_type, scope_code, period_key) → target
Targets = dict[tuple[str, str, str, str], MonthTarget]


def month_targets(org_id: str, codes: Sequence[str], months: Sequence[str]) -> Targets:
    out: Targets = {}
    rows = Target.objects.filter(
        org_id=org_id,
        metric__metric_code__in=list(codes),
        period_key__in=list(months),
        series_type="target",
        product_line_code="",
        state="published",
    ).select_related("metric")
    for t in rows:
        out[(t.metric.metric_code, t.scope_type, t.scope_code, t.period_key)] = MonthTarget(
            target_id=str(t.target_id),
            version=t.version,
            scope_type=t.scope_type,
            stored=Decimal(t.target_value),
            target_type=t.target_type,
            currency_code=t.currency_code,
        )
    return out


def month_share(stored: Decimal, target_type: str, cycle_months: int) -> Decimal:
    """One month's part of a stored target."""
    if target_type in ("yearly", "prorated"):
        return stored / cycle_months
    if target_type == "quarterly":
        return stored / 3
    return stored  # monthly, and cumulative (a month's step of a running total)


# ── pacing a board ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricPace:
    metric: Metric
    target: MonthTarget | None
    currency_code: str | None
    pace: Pace
    # The as-of day's own figure, in the target's currency (None when not reported).
    day_value: Decimal | None = None


@dataclass(frozen=True)
class AgentPace:
    agent: Agent
    metrics: list[MetricPace]


@dataclass
class Board:
    product: str
    window: Window
    settings: Settings
    agents: list[AgentPace] = field(default_factory=list)
    # Working day N of M on the org-wide calendar, for the page's caption.
    working_days_elapsed: int = 0
    working_days_total: int = 0


class _Fx:
    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        self.cache: dict[tuple[str, str, str], Decimal | None] = {}

    def rate(self, src: str | None, dst: str | None, period_key: str) -> Decimal | None:
        if not src or not dst or src == dst:
            return Decimal(1)
        key = (src, dst, period_key)
        if key not in self.cache:
            try:
                self.cache[key] = fx_rate(self.org_id, src, dst, period_key, "average")
            except MissingRate:
                self.cache[key] = None
        return self.cache[key]


def board(
    org_id: str,
    product: str,
    window: Window,
    *,
    subject_ids: Iterable[str] | None = None,
) -> Board:
    """Every agent's pace on every metric of their profile, for the window."""
    settings = settings_for(org_id, product)
    agents, by_profile = agents_on(org_id, product, window.as_of, subject_ids)
    # Shares need each month's working days in full, even where the week cuts it.
    span_start = month_bounds(window.months[0])[0]
    span_end = month_bounds(window.months[-1])[1]
    calendar = WorkingCalendar(org_id, span_start, span_end)
    out = Board(
        product=product,
        window=window,
        settings=settings,
        working_days_elapsed=len(
            calendar.working_days(window.start, window.as_of + timedelta(days=1))
        ),
        working_days_total=len(calendar.working_days(window.start, window.end)),
    )
    if not agents:
        return out

    codes = sorted({m.metric_code for ms in by_profile.values() for m in ms})
    totals = daily_totals(
        org_id,
        codes,
        window.start,
        min(window.as_of, window.end - timedelta(days=1)),
        [a.subject_id for a in agents] if subject_ids is not None else None,
    )
    targets = month_targets(org_id, codes, window.months)
    fx = _Fx(org_id)
    as_of_month = period_key_for(window.as_of)
    month_working: dict[tuple[str | None, str], int] = {}
    cycle_months: dict[str | None, int] = {}

    def working_in(region: str | None, month: str) -> int:
        if (region, month) not in month_working:
            first, end = month_bounds(month)
            month_working[(region, month)] = len(calendar.working_days(first, end, region))
        return month_working[(region, month)]

    for agent in agents:
        if agent.cycle_id not in cycle_months:
            cycle_months[agent.cycle_id] = cycle_for(
                org_id, product, as_of_month, agent.cycle_id
            ).months
        months_in_cycle = cycle_months[agent.cycle_id]
        paced: list[MetricPace] = []
        for metric in by_profile.get(agent.profile_code, []):
            scope_code = (
                agent.subject_id if metric.target_scope == "subject" else agent.profile_code
            )
            found = {
                month: targets.get((metric.metric_code, metric.target_scope, scope_code, month))
                for month in window.months
            }
            mine = found.get(as_of_month)
            currency = next(
                (t.currency_code for t in found.values() if t is not None and t.currency_code),
                None,
            )
            plans: list[DayPlan] = []
            for day in window.days:
                month = period_key_for(day)
                working = calendar.is_working(day, agent.region_code)
                t = found.get(month)
                share: Decimal | None = None
                if t is not None:
                    n = working_in(agent.region_code, month)
                    month_value = month_share(t.stored, t.target_type, months_in_cycle)
                    share = month_value / n if working and n else Decimal(0)
                plans.append(DayPlan(day=day, working=working, share=share))
            actuals: dict[date, Decimal] = {}
            missing_fx = False
            for day, by_currency in totals.get((agent.subject_id, metric.metric_code), {}).items():
                value = Decimal(0)
                for src, amount in by_currency.items():
                    rate = fx.rate(src, currency, period_key_for(day))
                    if rate is None:
                        missing_fx = True
                        continue
                    value += amount * rate
                actuals[day] = value
            paced.append(
                MetricPace(
                    metric=metric,
                    target=mine,
                    currency_code=currency,
                    day_value=actuals.get(window.as_of),
                    pace=pace(
                        PaceIn(
                            direction=metric.direction,
                            aggregation=metric.aggregation,
                            days=plans,
                            as_of=window.as_of,
                            actuals=actuals,
                            month_target=(
                                month_share(mine.stored, mine.target_type, months_in_cycle)
                                if mine is not None
                                else None
                            ),
                            cap=settings.pace_cap,
                            missing_fx=missing_fx,
                        )
                    ),
                )
            )
        out.agents.append(AgentPace(agent=agent, metrics=paced))
    return out
