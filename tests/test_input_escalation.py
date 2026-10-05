# ruff: noqa: F811 (the manual-input fixtures are imported, then requested by name)
"""The input escalation ladder (PRD MI-8, NT-4, NT-5; TDD §5.5)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from kpigo.access.models import AppUser
from kpigo.action import InvalidInput
from kpigo.platform.models import AuditLog
from kpigo.scorecards import inputs
from kpigo.scorecards.models import InputEscalation
from tests.conftest import ORG_ID, run
from tests.scorecard_support import PROFILE, SINCE
from tests.test_manual_input import (  # noqa: F401 (fixtures)
    PAST,
    P,
    assign,
    ids,
    open_window,
    people,
    relay,
)

pytestmark = pytest.mark.django_db

TODAY = date.today()


@pytest.fixture
def rungs(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Pin which rungs are due for last month: ``rungs(1, 2)`` means the first two."""

    def pin(*due: int) -> None:
        days = {1: TODAY - timedelta(days=3), 2: TODAY - timedelta(days=1), 3: TODAY}
        monkeypatch.setattr(
            inputs,
            "ladder",
            lambda org_id, period_key: {
                step: (days[step] if step in due and period_key == P else None)
                for step in (1, 2, 3)
            },
        )

    pin()
    return pin


@pytest.fixture
def e3_reports_to_e1(ids: dict[str, Any], people: dict[str, Any]) -> Any:
    run(
        "reporting_edge.create",
        subject_id=ids["E3"],
        manager_id=ids["E1"],
        relationship_type="solid",
        effective_from=SINCE,
    )
    e3 = AppUser.objects.get(email="e3@bank.example")
    return assign(people, assignee_user_id=str(e3.user_id))


def name_of(ctx: Any) -> str:
    return AppUser.objects.get(auth_user=ctx.user).display_name


def test_the_ladder_climbs_one_rung_at_a_time(
    people: dict[str, Any], e3_reports_to_e1: Any, rungs: Callable[..., None]
) -> None:
    rungs(1)
    first = run("input.remind", people["admin"])
    assert first.reminded == 1 and first.escalated == 0
    assert run("input.task.list", people["e3"], period_key=P).tasks[0].escalation_step == 1
    assert run("input.escalation.list", people["e1"]).items == []

    # The line manager of the contributor, from the reporting line in force today.
    rungs(1, 2)
    second = run("input.remind", people["admin"])
    assert second.reminded == 0 and second.escalated == 1
    assert second.escalated_to == ["person_e1"]
    (item,) = run("input.escalation.list", people["e1"]).items
    assert (item.step, item.contributor_name, item.state) == (
        2,
        name_of(people["e3"]),
        "pending",
    )

    # With no stakeholders named, everyone who manages input is told.
    rungs(1, 2, 3)
    third = run("input.remind", people["admin"])
    assert third.escalated == 1 and third.escalated_to == [name_of(people["admin"])]
    assert run("input.task.list", people["e3"], period_key=P).tasks[0].escalation_step == 3
    assert run("input.remind", people["admin"]).escalated == 0
    assert AuditLog.objects.filter(event="input.escalated").count() == 2

    # Submitting takes it off everyone's list.
    run(
        "input.submit",
        people["e3"],
        period_key=P,
        entries=[{"assignment_id": e3_reports_to_e1.assignment_id, "value": "4.1"}],
    )
    assert run("input.escalation.list", people["e1"]).items == []


def test_a_late_run_catches_up_every_rung_due(
    people: dict[str, Any], e3_reports_to_e1: Any, rungs: Callable[..., None]
) -> None:
    rungs(1, 2, 3)
    out = run("input.remind", people["admin"])
    assert out.reminded == 1 and out.escalated == 1
    assert sorted(InputEscalation.objects.values_list("step", flat=True)) == [1, 2, 3]


def test_once_locked_only_the_stakeholders_hear(
    people: dict[str, Any],
    e3_reports_to_e1: Any,
    rungs: Callable[..., None],
    open_window: Callable[[datetime], None],
) -> None:
    open_window(PAST)
    rungs(1, 2, 3)
    out = run("input.remind", people["admin"])
    assert out.reminded == 0 and out.escalated == 1
    assert list(InputEscalation.objects.values_list("step", flat=True)) == [3]


def test_a_rung_that_reaches_nobody_is_recorded(
    people: dict[str, Any], rungs: Callable[..., None]
) -> None:
    # Kofi is not a tracked subject, so he has no line manager to tell.
    assign(people)
    rungs(1, 2)
    out = run("input.remind", people["admin"])
    assert out.reminded == 1 and out.unresolved == 1 and out.escalated == 0
    assert InputEscalation.objects.filter(step=2, recipient__isnull=True).count() == 1
    assert AuditLog.objects.filter(event="input.escalation_unresolved").count() == 1


def test_a_slice_can_name_its_own_stakeholders(
    people: dict[str, Any], rungs: Callable[..., None]
) -> None:
    a = assign(people)
    e1 = AppUser.objects.get(email="e1@bank.example")
    with pytest.raises(InvalidInput, match="cannot see escalated inputs"):
        run(
            "input.assignment.set_stakeholders",
            assignment_id=a.assignment_id,
            stakeholder_user_ids=[people["noone"]],
        )
    out = run(
        "input.assignment.set_stakeholders",
        assignment_id=a.assignment_id,
        stakeholder_user_ids=[str(e1.user_id)],
    )
    assert out.stakeholder_names == ["person_e1"]
    rungs(3)
    assert run("input.remind", people["admin"]).escalated_to == ["person_e1"]
    (item,) = run("input.escalation.list", people["e1"]).items
    assert item.step == 3 and item.contributor_name == "kofi"


def test_the_ladder_is_set_in_order_and_reads_as_dates(people: dict[str, Any]) -> None:
    with pytest.raises(InvalidInput) as e:
        run(
            "input.ladder.set",
            contributor_working_days_before=2,
            manager_working_days=-3,
            stakeholder_working_days=1,
        )
    assert "climbs in order" in str(e.value.detail)
    e1 = AppUser.objects.get(email="e1@bank.example")
    out = run(
        "input.ladder.set",
        contributor_working_days_before=3,
        manager_working_days=None,
        stakeholder_working_days=2,
        stakeholder_user_ids=[str(e1.user_id)],
    )
    assert out.manager_on is None
    assert out.contributor_on < out.due_on < out.stakeholders_on
    assert [s.display_name for s in out.stakeholders] == ["person_e1"]
    assert inputs.ladder(ORG_ID, P)[2] is None
    got = run("input.ladder.get", period_key=P)
    assert got.contributor_working_days_before == 3 and got.stakeholder_working_days == 2


def test_each_person_gets_one_digest(
    people: dict[str, Any],
    e3_reports_to_e1: Any,
    rungs: Callable[..., None],
    relay: None,
    mailoutbox: list[Any],
    ids: dict[str, Any],
) -> None:
    e3 = AppUser.objects.get(email="e3@bank.example")
    assign(people, scope_type="subject", scope_code="E2", assignee_user_id=str(e3.user_id))
    rungs(1, 2)
    out = run("input.remind", people["admin"])
    assert out.reminded == 2 and out.escalated == 2 and out.emailed == 2
    by_to = {m.to[0]: m for m in mailoutbox}
    assert set(by_to) == {"e3@bank.example", "e1@bank.example"}
    manager = by_to["e1@bank.example"]
    assert manager.subject == "kpiGo: 2 input(s) need chasing"
    assert "Your team has not submitted these inputs yet:" in manager.body
    assert f"Customer satisfaction, for Everyone on {PROFILE} (owed by" in manager.body
    assert "Escalated to you" in manager.body
    assert InputEscalation.objects.filter(emailed=True).count() == 4
