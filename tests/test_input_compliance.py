# ruff: noqa: F811 (the manual-input fixtures are imported, then requested by name)
"""Contributor compliance (PRD MI-12): who was asked, who submitted, and when."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from kpigo.access.models import AppUser
from kpigo.action.context import ExplicitSubjects
from kpigo.metrics.models import Metric
from kpigo.scorecards import inputs
from kpigo.scorecards.models import InputAssignment, InputSubmission
from tests.conftest import ORG_ID, run
from tests.scorecard_support import shift
from tests.test_manual_input import (  # noqa: F401 (fixtures)
    P,
    assign,
    ids,
    open_window,
    people,
)

pytestmark = pytest.mark.django_db

DUE = datetime(2020, 1, 8, tzinfo=UTC)
MONTHS = [shift(P, -n) for n in range(5, -1, -1)]


@pytest.fixture
def all_due(monkeypatch: pytest.MonkeyPatch, open_window: Any) -> None:
    """Every month's deadline has passed, at the same moment, so arrivals read plainly."""
    monkeypatch.setattr(inputs, "due_at", lambda org_id, period_key: DUE)


def arrive(
    a: Any, period_key: str, when: datetime, state: str = "submitted", version: int = 1
) -> None:
    ia = InputAssignment.objects.get(assignment_id=a.assignment_id)
    InputSubmission.objects.create(
        org_id=ORG_ID,
        input_assignment=ia,
        metric=Metric.objects.get(org_id=ORG_ID, metric_code=ia.metric_code),
        metric_code=ia.metric_code,
        scope_type=ia.scope_type,
        scope_code=ia.scope_code,
        period_key=period_key,
        value=Decimal(4),
        state=state,
        submitted_at=when,
        version=version,
        is_current=True,
    )


@pytest.fixture
def history(people: dict[str, Any], ids: dict[str, Any], all_due: None) -> dict[str, Any]:
    e3 = AppUser.objects.get(email="e3@bank.example")
    late_lead = assign(people, assignee_user_id=str(e3.user_id))
    punctual = assign(people, scope_type="subject", scope_code="E2")
    for month in MONTHS:
        arrive(punctual, month, DUE - timedelta(days=2))
    # E3's contributor: on time once, late twice, never in the other three months.
    arrive(late_lead, MONTHS[0], DUE - timedelta(hours=1))
    arrive(late_lead, MONTHS[1], DUE + timedelta(days=3))
    arrive(late_lead, MONTHS[2], DUE + timedelta(days=1))
    return {"e3": e3, "late_lead": late_lead}


def test_the_chronically_late_contributor_comes_first(history: dict[str, Any]) -> None:
    out = run("input.compliance", period_key=P, months=6)
    assert out.periods == MONTHS and out.scope == "all"
    late, punctual = out.rows
    assert late.name == history["e3"].display_name and late.chronic
    assert (late.asked, late.on_time, late.late, late.missing) == (6, 1, 2, 3)
    assert late.late_months == 5 and late.on_time_rate == Decimal("0.167")
    assert [(m.on_time, m.late, m.missing) for m in late.months[:4]] == [
        (1, 0, 0),
        (0, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
    ]
    assert punctual.name == "kofi" and not punctual.chronic
    assert punctual.on_time_rate == Decimal("1.000")


def test_a_resubmission_does_not_make_an_input_late(history: dict[str, Any]) -> None:
    InputSubmission.objects.filter(period_key=MONTHS[0]).update(is_current=False)
    arrive(history["late_lead"], MONTHS[0], DUE + timedelta(days=9), state="restated", version=2)
    late = run("input.compliance", period_key=P, months=6).rows[0]
    assert late.months[0].on_time == 1 and late.months[0].late == 0


def test_before_the_deadline_an_unsubmitted_input_is_open_not_missing(
    people: dict[str, Any], open_window: Any
) -> None:
    assign(people)
    (row,) = run("input.compliance", period_key=P, months=1).rows
    assert (row.months[0].open, row.months[0].missing, row.on_time_rate) == (1, 0, None)


def test_a_manager_sees_only_their_team(
    history: dict[str, Any], people: dict[str, Any], ids: dict[str, Any]
) -> None:
    run(
        "input.assignment.create",
        metric_code="csat",
        scope_type="subject",
        scope_code="E1",
        assignee_type="role_relative",
        assignee_role="line_manager_of",
        effective_from=MONTHS[0][:4] + "-" + MONTHS[0][4:] + "-01",
    )
    # An Admin also sees a slice nobody could be asked for.
    names = [r.name for r in run("input.compliance", period_key=P).rows]
    assert "Nobody (no line manager)" in names

    e1 = people["e1"]
    assert run("input.compliance", e1, period_key=P).rows == []
    team = replace(e1, visible_subjects=ExplicitSubjects.of([ids["E1"], ids["E2"], ids["E3"]]))
    out = run("input.compliance", team, period_key=P)
    assert out.scope == "team" and [r.name for r in out.rows] == [history["e3"].display_name]
