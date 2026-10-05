"""The product-line matrix (PRD AP-5, AP-9, AP-11; Scope §8.1, §8.5).

Entity × product line, each cell the month (or week) to date: actual, the target
expected by now, and % achieved with a RAG. Rows drill region → branch → RM.

Every figure is built from agents, never from subordinate totals: each agent is
paced on each line against their line target (the same arithmetic as their own
pace), and a row sums its distinct agents' figures. Absent is not zero: an agent
with nothing reported on a line adds neither actual nor target to that cell, and
the cell says how many of its agents reported.

In the grouped view a group's actual and target are the sums of its lines', and
its % is recomputed from those sums, never averaged from line percentages.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from django.db import connection

from kpigo.agents.config import Settings, rag
from kpigo.agents.daily import Agent, Pacer, Totals, Window, daily_totals, month_targets
from kpigo.agents.lines import Line
from kpigo.agents.pace import q, ratio
from kpigo.metrics.models import Metric

Level = Literal["region", "branch", "rm"]
View = Literal["expanded", "grouped"]
ALL = ""  # the All products column


def line_totals(
    org_id: str, metric_code: str, first: date, last: date, subject_ids: Sequence[str]
) -> dict[str, Totals]:
    """line_code → (subject, metric) → day → currency → figure, from ``mv_product_line_matrix``."""
    out: dict[str, Totals] = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    if not subject_ids or last < first:
        return out
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT line_code, subject_id::text, activity_date, nullif(currency_code, ''),
                   actual_value
            FROM mv_product_line_matrix
            WHERE org_id = %s AND metric_code = %s AND activity_date >= %s
              AND activity_date <= %s AND subject_id = ANY(%s::uuid[]) AND line_code <> ''
            """,
            [org_id, metric_code, first, last, list(subject_ids)],
        )
        for line_code, subject_id, day, currency, value in cur.fetchall():
            out[line_code][(subject_id, metric_code)][day][currency] = Decimal(value)
    return out


@dataclass
class Sums:
    """What a cell adds up: agents' figures, never their percentages."""

    agents: int = 0
    reported: int = 0
    actual: Decimal = Decimal(0)
    # Only agents with a target to date count towards %: like against like.
    targeted_actual: Decimal = Decimal(0)
    target: Decimal = Decimal(0)
    targeted: int = 0
    currencies: set[str] = field(default_factory=set)

    def add(self, other: Sums) -> None:
        self.reported += other.reported
        self.actual += other.actual
        self.targeted_actual += other.targeted_actual
        self.target += other.target
        self.targeted += other.targeted
        self.currencies |= other.currencies


@dataclass(frozen=True)
class Column:
    key: str  # a line code, a group code, or "" for All products
    kind: Literal["line", "group", "all"]
    name: str
    group_code: str | None
    rag_green: Decimal | None = None
    rag_amber: Decimal | None = None


@dataclass(frozen=True)
class Cell:
    actual: Decimal | None
    target: Decimal | None
    achieved: Decimal | None
    rag: Literal["green", "amber", "red"] | None
    agents: int
    reported: int
    currency_code: str | None
    mixed_currency: bool


@dataclass(frozen=True)
class Row:
    key: str
    name: str
    level: Level | Literal["total"]
    region_code: str | None
    branch_code: str | None
    agents: int
    cells: list[Cell]


@dataclass
class Matrix:
    columns: list[Column]
    rows: list[Row]
    total: Row | None


def columns_for(lines: Sequence[Line], view: View, line_rag: dict[str, Any]) -> list[Column]:
    out: list[Column] = []
    if view == "grouped":
        seen: set[str] = set()
        for line in lines:
            if line.group_code not in seen:
                seen.add(line.group_code)
                out.append(Column(line.group_code, "group", line.group_name, line.group_code))
    else:
        for line in lines:
            green, amber = line_rag.get(line.code, (None, None))
            out.append(Column(line.code, "line", line.display_name, line.group_code, green, amber))
    out.append(Column(ALL, "all", "All products", None))
    return out


def _agent_sums(p: Any) -> Sums:
    """One agent's contribution on one line (or all of them)."""
    s = Sums(agents=1)
    pace = p.pace
    if pace.actual is None:
        return s
    s.reported = 1
    s.actual = pace.actual
    if p.currency_code:
        s.currencies.add(p.currency_code)
    if pace.state == "paced" and pace.target_to_date is not None:
        s.targeted = 1
        s.targeted_actual = pace.actual
        s.target = pace.target_to_date
    return s


def build(
    *,
    org_id: str,
    product: str,
    window: Window,
    settings: Settings,
    metric: Metric,
    agents: Sequence[Agent],
    lines: Sequence[Line],
    line_rag: dict[str, Any],
    view: View,
    level: Level,
    row_names: dict[str, str],
) -> Matrix:
    columns = columns_for(lines, view, line_rag)
    pacer = Pacer(org_id, product, window, settings)
    last = min(window.as_of, window.end - timedelta(days=1))
    ids = [a.subject_id for a in agents]
    code = metric.metric_code
    all_series = daily_totals(org_id, [code], window.start, last, ids)
    all_targets = month_targets(org_id, [code], window.months)
    by_line = line_totals(org_id, code, window.start, last, ids)
    line_targets = {
        line.code: month_targets(org_id, [code], window.months, line.code) for line in lines
    }
    group_of = {line.code: line.group_code for line in lines}

    # Each agent's sums per column, then each row's.
    per_row: dict[str, dict[str, Sums]] = defaultdict(lambda: defaultdict(Sums))
    row_agents: dict[str, int] = defaultdict(int)
    row_place: dict[str, tuple[str | None, str | None]] = {}
    total: dict[str, Sums] = defaultdict(Sums)

    for agent in agents:
        key = _row_key(agent, level)
        row_agents[key] += 1
        row_place[key] = (agent.region_code, None if level == "region" else agent.branch_code)
        mine: dict[str, Sums] = {c.key: Sums(agents=1) for c in columns}
        for line in lines:
            p = pacer.pace(
                agent,
                metric,
                pacer.targets_for(agent, metric, line_targets[line.code]),
                by_line.get(line.code, {}).get((agent.subject_id, code), {}),
            )
            contribution = _agent_sums(p)
            column = line.code if view == "expanded" else group_of[line.code]
            if view == "grouped":
                # A group's figure is its lines' sums: reported once if any line was.
                merged = mine[column]
                if contribution.reported:
                    merged.reported = 1
                merged.actual += contribution.actual
                merged.targeted_actual += contribution.targeted_actual
                merged.target += contribution.target
                merged.targeted = max(merged.targeted, contribution.targeted)
                merged.currencies |= contribution.currencies
            else:
                mine[column] = contribution
        whole = pacer.pace(
            agent,
            metric,
            pacer.targets_for(agent, metric, all_targets),
            all_series.get((agent.subject_id, code), {}),
        )
        mine[ALL] = _agent_sums(whole)
        for column_key, sums in mine.items():
            per_row[key][column_key].add(sums)
            total[column_key].add(sums)

    def cells(sums_by: dict[str, Sums], n: int) -> list[Cell]:
        return [_cell(sums_by.get(c.key, Sums()), n, c, metric, settings) for c in columns]

    rows = [
        Row(
            key=key,
            name=row_names.get(key) or key or _unassigned(level),
            level=level,
            region_code=row_place[key][0],
            branch_code=row_place[key][1],
            agents=row_agents[key],
            cells=cells(per_row[key], row_agents[key]),
        )
        for key in per_row
    ]
    rows.sort(key=lambda r: (r.key == "", r.name.lower(), r.key))
    total_row = (
        Row("total", "Total", "total", None, None, len(agents), cells(total, len(agents)))
        if agents
        else None
    )
    return Matrix(columns=columns, rows=rows, total=total_row)


def _row_key(agent: Agent, level: Level) -> str:
    if level == "region":
        return agent.region_code or ""
    if level == "branch":
        return agent.branch_code or ""
    return agent.subject_id


def _unassigned(level: Level) -> str:
    return {"region": "No region", "branch": "No branch", "rm": "Unnamed"}[level]


def _cell(s: Sums, agents: int, column: Column, metric: Metric, settings: Settings) -> Cell:
    mixed = len(s.currencies) > 1
    achieved = None
    if s.targeted and s.target > 0 and not mixed:
        achieved = q(ratio(s.targeted_actual, s.target, metric.direction, settings.pace_cap))
    thresholds = settings
    if column.rag_green is not None and column.rag_amber is not None:
        thresholds = Settings(
            product=settings.product,
            rag_green=Decimal(column.rag_green),
            rag_amber=Decimal(column.rag_amber),
        )
    return Cell(
        actual=None if not s.reported or mixed else q(s.actual),
        target=None if not s.targeted or mixed else q(s.target),
        achieved=achieved,
        rag=rag(thresholds, achieved),
        agents=agents,
        reported=s.reported,
        currency_code=next(iter(s.currencies)) if len(s.currencies) == 1 else None,
        mixed_currency=mixed,
    )
