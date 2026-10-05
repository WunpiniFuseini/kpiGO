"""Manual metric input (PRD MI-1–MI-13; App Flow §7.6; TDD §5.5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import User

from kpigo.access.models import AppUser
from kpigo.action import Conflict, InvalidInput, OutOfScope
from kpigo.action.context import AllSubjects, ExplicitSubjects
from kpigo.ingestion.models import FactActualMonthly
from kpigo.metrics.models import Metric
from kpigo.platform.models import AuditLog
from kpigo.scorecards import inputs
from kpigo.scorecards.models import InputSubmission, Target
from tests.conftest import ORG_ID, role_ctx, run
from tests.scorecard_support import METRICS, PREVIOUS, PROFILE, SINCE, world

pytestmark = pytest.mark.django_db

P = PREVIOUS
FUTURE = datetime(2999, 1, 1, tzinfo=UTC)
PAST = datetime(2000, 1, 1, tzinfo=UTC)


def metric(code: str) -> Metric:
    return Metric.objects.get(org_id=ORG_ID, metric_code=code)


@pytest.fixture
def open_window(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    """Pin the input deadline, so tests do not depend on today's date."""

    def pin(when: datetime) -> None:
        monkeypatch.setattr(inputs, "due_at", lambda org_id, period_key: when)

    pin(FUTURE)
    return pin


@pytest.fixture
def ids(open_window: Callable[[datetime], None]) -> dict[str, Any]:
    ids = world()
    run(
        "metric.register",
        display_name="Customer satisfaction",
        metric_code="csat",
        direction="higher_is_better",
        aggregation="average",
        unit="count",
        target_scope="profile",
        products=["scorecards"],
        collection_method="manual_input",
        effective_from=SINCE,
        acknowledge_similar=True,
    )
    run(
        "scorecard.profile.set_metrics",
        profile_code=PROFILE,
        metric_codes=[*METRICS, "csat"],
        effective_from=SINCE,
    )
    weights = {"casa_growth": 30, "csat": 10}
    for code, (_, _, _, _, scope, weight, cap) in [
        *METRICS.items(),
        ("csat", ("", "", "", "", "profile", 10, 15)),
    ]:
        w = weights.get(code, weight)
        for scope_code in [PROFILE] if scope == "profile" else [ids[s] for s in ("E1", "E2", "E3")]:
            Target.objects.create(
                org_id=ORG_ID,
                metric=metric(code),
                scope_type=scope,
                scope_code=scope_code,
                period_key=P,
                target_value=Decimal(100 if code != "csat" else 4),
                weight=Decimal(w),
                cap=Decimal(max(cap, w)),
                version=1,
                state="published",
            )
    return ids


@pytest.fixture
def people(ids: dict[str, Any], make_user: Callable[..., User]) -> dict[str, Any]:
    run(
        "reporting_edge.create",
        subject_id=ids["E2"],
        manager_id=ids["E1"],
        relationship_type="solid",
        effective_from=SINCE,
    )
    kofi = make_user("contributor", username="kofi")
    e1 = make_user("line_manager", username="person_e1", email="e1@bank.example")
    e3 = make_user("staff", "contributor", email="e3@bank.example")
    return {
        "kofi": replace(role_ctx("contributor"), user=kofi),
        "kofi_id": str(AppUser.objects.get(auth_user=kofi).user_id),
        "e1": replace(
            role_ctx("line_manager"),
            user=e1,
            visible_subjects=ExplicitSubjects.of([ids["E1"], ids["E2"]]),
        ),
        "e3": replace(role_ctx("contributor"), user=e3),
        "staff": replace(
            role_ctx("staff"), user=make_user("staff"), visible_subjects=AllSubjects()
        ),
        "admin": replace(
            role_ctx("admin"), user=make_user("admin"), visible_subjects=AllSubjects()
        ),
        "noone": str(AppUser.objects.get(auth_user=make_user("staff")).user_id),
    }


def assign(people: dict[str, Any], **kw: Any) -> Any:
    body = {
        "metric_code": "csat",
        "scope_type": "profile",
        "scope_code": PROFILE,
        "assignee_user_id": people["kofi_id"],
        "effective_from": SINCE,
    }
    return run("input.assignment.create", **(body | kw))


def test_assignment_checks_the_metric_the_person_and_overlaps(people: dict[str, Any]) -> None:
    with pytest.raises(InvalidInput, match="collected by a feed"):
        assign(people, metric_code="casa_growth")
    with pytest.raises(InvalidInput, match="Contributor role"):
        assign(people, assignee_user_id=people["noone"])
    with pytest.raises(InvalidInput) as e:
        assign(
            people,
            assignee_type="role_relative",
            assignee_user_id=None,
            assignee_role="line_manager_of",
        )
    assert "subject slice" in str(e.value.detail)
    a = assign(people)
    assert a.contributor_name == "kofi" and a.members == 3 and a.state == "pending"
    with pytest.raises(Conflict, match="overlapping"):
        assign(people)
    listed = run("input.assignment.list", period_key=P)
    assert [x.assignment_id for x in listed.assignments] == [
        a.assignment_id
    ] and listed.unassigned == []


def test_a_draft_reaches_no_scorecard_and_a_submission_reaches_everyone(
    people: dict[str, Any], ids: dict[str, Any]
) -> None:
    a = assign(people)
    kofi = people["kofi"]
    tasks = run("input.task.list", kofi, period_key=P)
    assert [t.metric_code for t in tasks.tasks] == ["csat"] and tasks.tasks[0].state == "pending"
    assert not tasks.locked

    entry = {"assignment_id": a.assignment_id, "value": "4.2", "note": "Survey closed on the 28th."}
    out = run("input.save", kofi, period_key=P, entries=[entry])
    assert (
        out.tasks[0].state == "draft"
        and not FactActualMonthly.objects.filter(metric__metric_code="csat").exists()
    )

    with pytest.raises(OutOfScope):
        run("input.submit", people["e3"], period_key=P, entries=[entry])
    with pytest.raises(InvalidInput, match="decimal places"):
        run("input.submit", kofi, period_key=P, entries=[entry | {"value": "4.123456"}])
    out = run("input.submit", kofi, period_key=P, entries=[entry])
    assert out.tasks[0].state == "submitted" and out.tasks[0].submitted_at is not None
    facts = FactActualMonthly.objects.filter(metric__metric_code="csat", period_key=P)
    assert facts.count() == 3 and {f.run_id for f in facts} == {None}
    assert {f.actual_value for f in facts} == {Decimal("4.2")}

    with pytest.raises(Conflict, match="already submitted"):
        run("input.save", kofi, period_key=P, entries=[entry])
    # Freely editable until the deadline.
    run("input.submit", kofi, period_key=P, entries=[entry | {"value": "4.4"}])
    assert {f.actual_value for f in facts.all()} == {Decimal("4.4")}
    assert InputSubmission.objects.get().version == 1


def test_values_stay_hidden_until_close(people: dict[str, Any], ids: dict[str, Any]) -> None:
    a = assign(people)
    run(
        "input.submit",
        people["kofi"],
        period_key=P,
        entries=[{"assignment_id": a.assignment_id, "value": "4.4", "note": "Quarterly survey."}],
    )

    staff = run("scorecard.compute", people["staff"], subject_id=ids["E1"], period_key=P)
    csat = next(m for m in staff.metrics if m.metric_code == "csat")
    assert csat.hidden_until_close and csat.state == "not_reported" and csat.actual_value is None
    assert csat.input_by is None
    admin = run("scorecard.compute", people["admin"], subject_id=ids["E1"], period_key=P)
    csat = next(m for m in admin.metrics if m.metric_code == "csat")
    assert csat.state == "scored" and csat.actual_value == Decimal("4.4")
    assert (csat.input_by, csat.input_note, csat.run_id) == ("kofi", "Quarterly survey.", None)
    listed = run("scorecard.period.list", people["staff"], period_key=P)
    assert all(r.not_reported >= 1 for r in listed.rows)

    for code in ("casa_growth", "ntb_accounts", "service_tat", "fee_income"):
        for s in ("E1", "E2", "E3"):
            FactActualMonthly.objects.create(
                org_id=ORG_ID,
                metric=metric(code),
                subject_id=ids[s],
                assignment_id=FactActualMonthly.objects.filter(subject_id=ids[s])
                .first()
                .assignment_id,  # type: ignore[union-attr]
                period_key=P,
                actual_value=Decimal(100),
                loaded_at=datetime.now(UTC),
            )
    run("scorecard.period.close", period_key=P)
    closed = run("scorecard.compute", people["staff"], subject_id=ids["E1"], period_key=P)
    csat = next(m for m in closed.metrics if m.metric_code == "csat")
    assert (
        not csat.hidden_until_close
        and csat.actual_value == Decimal("4.4")
        and csat.input_by == "kofi"
    )


def test_close_names_who_owes_the_input(people: dict[str, Any]) -> None:
    assign(people)
    check = run("scorecard.close.check", period_key=P)
    csat = next(b for b in check.blockers if b.metric_code == "csat")
    assert csat.owed_by == ["kofi"] and "awaiting manual input from kofi" in csat.message


def test_after_the_deadline_a_change_is_a_restatement(
    people: dict[str, Any], ids: dict[str, Any], open_window: Callable[[datetime], None]
) -> None:
    a = assign(people)
    entry = {"assignment_id": a.assignment_id, "value": "4.0"}
    run("input.submit", people["kofi"], period_key=P, entries=[entry])
    open_window(PAST)
    assert run("input.task.list", people["kofi"], period_key=P).locked
    with pytest.raises(Conflict, match="locked at the deadline"):
        run("input.submit", people["kofi"], period_key=P, entries=[entry | {"value": "4.5"}])

    from kpigo.periods.models import PeriodStatus

    PeriodStatus.objects.update_or_create(
        org_id=ORG_ID, product="scorecards", period_key=P, defaults={"status": "restating"}
    )
    out = run("input.submit", people["kofi"], period_key=P, entries=[entry | {"value": "4.5"}])
    assert out.tasks[0].state == "restated" and out.tasks[0].version == 2
    assert (
        InputSubmission.objects.count() == 2
        and InputSubmission.objects.filter(is_current=True).get().version == 2
    )
    assert {
        f.actual_value for f in FactActualMonthly.objects.filter(metric__metric_code="csat")
    } == {Decimal("4.5")}


def test_line_manager_of_resolves_from_the_reporting_line(
    people: dict[str, Any], ids: dict[str, Any]
) -> None:
    a = run(
        "input.assignment.create",
        metric_code="csat",
        scope_type="subject",
        scope_code="E2",
        assignee_type="role_relative",
        assignee_role="line_manager_of",
        effective_from=SINCE,
    )
    assert a.contributor_name == "person_e1"
    e1 = run("input.task.list", people["e1"], period_key=P)
    assert [(t.metric_code, t.members) for t in e1.tasks] == [("csat", 1)]
    assert run("input.task.list", people["kofi"], period_key=P).tasks == []
    run(
        "input.submit",
        people["e1"],
        period_key=P,
        entries=[{"assignment_id": a.assignment_id, "value": "3.9"}],
    )
    assert str(FactActualMonthly.objects.get(metric__metric_code="csat").subject_id) == ids["E2"]


def test_one_reminder_per_slice(people: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    assign(people)
    first_rung_only(monkeypatch)
    first = run("input.remind", people["admin"])
    # Last month's and this month's slice, once each.
    assert first.reminded == 2 and first.recipients == ["kofi"]
    assert run("input.remind", people["admin"]).reminded == 0
    assert run("input.task.list", people["kofi"], period_key=P).tasks[0].reminded_at is not None
    assert AuditLog.objects.filter(event="input.reminded").count() == 2


def first_rung_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The contributor's reminder is due for last month and this one; nothing climbs higher."""
    yesterday = date.today() - timedelta(days=1)
    monkeypatch.setattr(
        inputs,
        "ladder",
        lambda org_id, period_key: {1: yesterday if period_key >= P else None, 2: None, 3: None},
    )


@pytest.fixture
def due_now(monkeypatch: pytest.MonkeyPatch) -> None:
    first_rung_only(monkeypatch)


@pytest.fixture
def relay(settings: Any) -> None:
    settings.KPIGO_EMAIL_HOST = "relay.bank.example"
    settings.KPIGO_PUBLIC_URL = "https://kpigo.bank.example"


def test_without_a_relay_reminders_stay_in_app(
    people: dict[str, Any], due_now: None, mailoutbox: list[Any]
) -> None:
    assign(people)
    out = run("input.remind", people["admin"])
    assert out.reminded == 2 and out.emailed == 0 and out.not_emailed == []
    assert mailoutbox == []
    assert run("input.assignment.list", people["admin"]).email_reminders is False


def test_with_a_relay_each_contributor_gets_one_email(
    people: dict[str, Any], due_now: None, relay: None, mailoutbox: list[Any]
) -> None:
    assign(people)
    assert run("input.assignment.list", people["admin"]).email_reminders is True
    # A dry run reminds nobody and sends nothing.
    run("input.remind", replace(people["admin"], dry_run=True))
    assert mailoutbox == []
    out = run("input.remind", people["admin"])
    assert out.reminded == 2 and out.emailed == 1
    (message,) = mailoutbox
    assert message.to == ["kofi@test.example"]
    assert message.subject == "kpiGo: 2 input(s) due soon"
    # Both months' slices in one message, each naming the metric and who it is for.
    assert message.body.count(f"Customer satisfaction, for Everyone on {PROFILE}") == 2
    assert "https://kpigo.bank.example/my-inputs" in message.body
    assert AuditLog.objects.filter(event="input.reminder_emailed").count() == 1
    # Still one reminder: the next run sends nothing.
    assert run("input.remind", people["admin"]).emailed == 0
    assert len(mailoutbox) == 1


def test_a_relay_failure_still_reminds_in_app(
    people: dict[str, Any],
    due_now: None,
    relay: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kpigo.platform import mail

    def refuse(*args: Any, **kwargs: Any) -> int:
        raise ConnectionRefusedError

    monkeypatch.setattr(mail, "send_mail", refuse)
    assign(people)
    out = run("input.remind", people["admin"])
    assert out.reminded == 2 and out.emailed == 0 and out.not_emailed == ["kofi"]
    assert AuditLog.objects.filter(event="input.reminded").count() == 2
    assert AuditLog.objects.filter(event="input.reminder_email_failed").count() == 1
