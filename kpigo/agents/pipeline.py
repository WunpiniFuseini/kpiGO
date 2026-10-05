"""The sales pipeline: what sits in each stage, from the daily feed (Scope §8.2).

Each stage reads two snapshot metrics (aggregation ``latest``) the feed carries
in ``actual_daily``: the value and the number of deals in the stage on a day.
An agent's position is their latest snapshot on or before the as-of day in the
window; their opening position is their first snapshot in the window. Rows sum
distinct agents, and an agent with no snapshot adds nothing (absent is not
zero). Conversion is each stage's count over the stage before it, or its value
where the stages carry no counts.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from kpigo.agents.daily import Agent, Window, daily_totals
from kpigo.agents.models import PipelineStage
from kpigo.agents.pace import q

Level = Literal["region", "branch", "rm"]


@dataclass(frozen=True)
class Snapshot:
    start: Decimal
    now: Decimal
    currency: str | None


def snapshots(
    org_id: str, codes: Sequence[str], window: Window, agents: Sequence[Agent]
) -> dict[tuple[str, str], Snapshot]:
    """(subject, metric) → first and latest snapshot in the window to date."""
    last = min(window.as_of, window.end - timedelta(days=1))
    totals = daily_totals(org_id, codes, window.start, last, [a.subject_id for a in agents])
    out: dict[tuple[str, str], Snapshot] = {}
    for key, by_day in totals.items():
        days = sorted(by_day)
        if not days:
            continue
        first, latest = by_day[days[0]], by_day[days[-1]]
        currencies = set(first) | set(latest)
        if len(currencies) > 1:
            continue  # one agent in two currencies: no figure rather than a wrong one
        currency = next(iter(currencies))
        out[key] = Snapshot(start=first[currency], now=latest[currency], currency=currency)
    return out


@dataclass
class StageSums:
    value: Decimal = Decimal(0)
    count: Decimal = Decimal(0)
    value_change: Decimal = Decimal(0)
    count_change: Decimal = Decimal(0)
    reported: int = 0
    currencies: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class StageCell:
    value: Decimal | None
    count: Decimal | None
    value_change: Decimal | None
    count_change: Decimal | None
    reported: int
    # Out of 1.0: this stage over the one before it; null on the first stage.
    conversion: Decimal | None
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
    cells: list[StageCell]


@dataclass(frozen=True)
class Pipeline:
    rows: list[Row]
    total: Row | None


def _row_key(agent: Agent, level: Level) -> str:
    if level == "region":
        return agent.region_code or ""
    if level == "branch":
        return agent.branch_code or ""
    return agent.subject_id


def build(
    org_id: str,
    window: Window,
    stages: Sequence[PipelineStage],
    agents: Sequence[Agent],
    level: Level,
    names: dict[str, str],
) -> Pipeline:
    codes = sorted(
        {c for s in stages for c in (s.value_metric_code, s.count_metric_code) if c is not None}
    )
    snaps = snapshots(org_id, codes, window, agents)
    per_row: dict[str, list[StageSums]] = defaultdict(lambda: [StageSums() for _ in stages])
    total = [StageSums() for _ in stages]
    members: dict[str, list[Agent]] = defaultdict(list)
    for agent in agents:
        key = _row_key(agent, level)
        members[key].append(agent)
        for i, stage in enumerate(stages):
            v = snaps.get((agent.subject_id, stage.value_metric_code or ""))
            c = snaps.get((agent.subject_id, stage.count_metric_code or ""))
            if v is None and c is None:
                continue
            for sums in (per_row[key][i], total[i]):
                sums.reported += 1
                if v is not None:
                    sums.value += v.now
                    sums.value_change += v.now - v.start
                    if v.currency:
                        sums.currencies.add(v.currency)
                if c is not None:
                    sums.count += c.now
                    sums.count_change += c.now - c.start

    def cells(sums: list[StageSums]) -> list[StageCell]:
        out: list[StageCell] = []
        for i, (stage, s) in enumerate(zip(stages, sums, strict=True)):
            mixed = len(s.currencies) > 1
            has_value = stage.value_metric_code is not None and s.reported and not mixed
            has_count = stage.count_metric_code is not None and s.reported
            out.append(
                StageCell(
                    value=q(s.value) if has_value else None,
                    count=q(s.count) if has_count else None,
                    value_change=q(s.value_change) if has_value else None,
                    count_change=q(s.count_change) if has_count else None,
                    reported=s.reported,
                    conversion=_conversion(stages, sums, i) if i else None,
                    currency_code=next(iter(s.currencies)) if len(s.currencies) == 1 else None,
                    mixed_currency=mixed,
                )
            )
        return out

    rows = []
    for key, group in members.items():
        first = group[0]
        rows.append(
            Row(
                key=key,
                name=names.get(key)
                or key
                or {"region": "No region", "branch": "No branch"}.get(level, "Unnamed"),
                level=level,
                region_code=first.region_code,
                branch_code=None if level == "region" else first.branch_code,
                agents=len(group),
                cells=cells(per_row[key]),
            )
        )
    rows.sort(key=lambda r: (r.key == "", r.name.lower(), r.key))
    total_row = (
        Row("total", "Total", "total", None, None, len(agents), cells(total)) if agents else None
    )
    return Pipeline(rows=rows, total=total_row)


def _conversion(stages: Sequence[PipelineStage], sums: list[StageSums], i: int) -> Decimal | None:
    here, before = sums[i], sums[i - 1]
    if not here.reported or not before.reported:
        return None
    if stages[i].count_metric_code and stages[i - 1].count_metric_code and before.count > 0:
        return q(here.count / before.count)
    both_valued = stages[i].value_metric_code and stages[i - 1].value_metric_code
    if both_valued and before.value > 0 and len(here.currencies | before.currencies) <= 1:
        return q(here.value / before.value)
    return None
