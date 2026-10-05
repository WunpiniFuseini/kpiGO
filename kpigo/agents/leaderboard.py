"""Leaderboards: ranked within a peer cohort, with a declared tie-break (PRD AP-3; Scope §8.1).

A leaderboard ranks the agents of one cohort (everyone, a profile, a branch, a
region, or a custom cohort) by one metric's value to date, or by the composite:
the mean of each metric's pace, capped so one runaway metric cannot carry an
agent. A tie on the ranking key is broken by the declared second metric's
value; agents still level share the rank (1, 2, 2, 4).

Absent is not zero: an agent with nothing reported on the ranking key is listed
below the ranked agents without a rank, never ranked last on a zero. An agent
whose profile does not carry the ranking metric is not on that board at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Q

from kpigo.agents.daily import Agent, AgentPace, MetricPace
from kpigo.agents.models import AgentCohortMember
from kpigo.agents.pace import q
from kpigo.hierarchy.models import DimMember

# A cohort with no branch or region recorded still has a name to pick.
UNASSIGNED = ""


@dataclass(frozen=True)
class Cohort:
    code: str
    name: str
    subject_ids: frozenset[str]


def cohorts(
    org_id: str, product: str, cohort_type: str, agents: Sequence[Agent], day: date
) -> list[Cohort]:
    """Every cohort of the type that has agents, by name."""
    groups: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    if cohort_type == "all":
        groups["all"] = {a.subject_id for a in agents}
        names["all"] = "Everyone"
    elif cohort_type == "cohort":
        rows = (
            AgentCohortMember.objects.filter(
                cohort__org_id=org_id,
                cohort__product=product,
                cohort__status="active",
                subject_id__in=[a.subject_id for a in agents],
                effective_from__lte=day,
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=day))
            .select_related("cohort")
        )
        for m in rows:
            groups.setdefault(m.cohort.code, set()).add(str(m.subject_id))
            names[m.cohort.code] = m.cohort.name
    else:
        field = f"{cohort_type}_code"
        for a in agents:
            code = getattr(a, field) or UNASSIGNED
            groups.setdefault(code, set()).add(a.subject_id)
        if cohort_type in ("branch", "region"):
            names = dict(
                DimMember.objects.filter(
                    org_id=org_id, dimension_type=cohort_type, member_code__in=list(groups)
                ).values_list("member_code", "member_name")
            )
        label = {"profile": "No profile", "branch": "No branch", "region": "No region"}
        for code in groups:
            names.setdefault(code, code or label[cohort_type])
    return sorted(
        (Cohort(code, names[code], frozenset(ids)) for code, ids in groups.items()),
        key=lambda c: (c.code == UNASSIGNED, c.name.lower(), c.code),
    )


def cohort_of(cohort_type: str, agent: Agent, found: Iterable[Cohort]) -> Cohort | None:
    for c in found:
        if agent.subject_id in c.subject_ids:
            return c
    return None


@dataclass(frozen=True)
class Row:
    agent: AgentPace
    # None: nothing reported on the ranking key, listed below the ranked.
    rank: int | None
    # The ranking key: the metric's value to date, or the composite pace.
    value: Decimal | None
    # What the pace bar shows: the ranking metric's pace, or the composite.
    pace: Decimal | None
    tiebreak: Decimal | None
    primary: MetricPace | None


def _metric(a: AgentPace, code: str | None) -> MetricPace | None:
    if code is None:
        return None
    return next((m for m in a.metrics if m.metric.metric_code == code), None)


def composite(a: AgentPace, cap: Decimal) -> Decimal | None:
    paces = [min(m.pace.pace, cap) for m in a.metrics if m.pace.pace is not None]
    return q(sum(paces, Decimal(0)) / len(paces)) if paces else None


def _key(value: Decimal | None, direction: str) -> tuple[int, Decimal]:
    """Sorts best first; a missing value after every present one."""
    if value is None:
        return (1, Decimal(0))
    return (0, value if direction == "lower_is_better" else -value)


def rank(
    agents: Sequence[AgentPace],
    *,
    rank_metric_code: str | None,
    tiebreak_metric_code: str | None,
    cap: Decimal,
) -> list[Row]:
    rows: list[tuple[tuple[tuple[int, Decimal], tuple[int, Decimal]], Row]] = []
    for a in agents:
        tie = _metric(a, tiebreak_metric_code)
        tie_value = tie.pace.actual if tie is not None else None
        tie_key = _key(tie_value, tie.metric.direction if tie else "higher_is_better")
        if rank_metric_code is None:
            value = composite(a, cap)
            row = Row(a, None, value, value, tie_value, None)
            rows.append(((_key(value, "higher_is_better"), tie_key), row))
            continue
        primary = _metric(a, rank_metric_code)
        if primary is None:
            continue  # not measured on the ranking metric: not on this board
        value = primary.pace.actual
        row = Row(a, None, value, primary.pace.pace, tie_value, primary)
        rows.append(((_key(value, primary.metric.direction), tie_key), row))

    rows.sort(key=lambda kr: (kr[0], kr[1].agent.agent.full_name, kr[1].agent.agent.staff_no))
    out: list[Row] = []
    previous: object = None
    current = 0
    for i, (key, row) in enumerate(rows):
        if row.value is None:
            out.append(row)
            continue
        if key != previous:
            current, previous = i + 1, key
        out.append(Row(row.agent, current, row.value, row.pace, row.tiebreak, row.primary))
    return out
