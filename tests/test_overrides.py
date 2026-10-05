"""Overrides (PRD SC-5, Scope §6.4) and the live scorecard actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from kpigo.action import ActionContext, Conflict, InvalidInput, OutOfScope
from kpigo.action.context import AllSubjects, ExplicitSubjects
from kpigo.hierarchy.models import Assignment
from kpigo.ingestion.models import FactActualMonthly
from kpigo.metrics.models import Metric
from kpigo.periods.models import PeriodStatus
from kpigo.platform.config import config_version
from kpigo.platform.models import AuditLog
from kpigo.scorecards.models import Override, Target
from tests.conftest import ORG_ID, role_ctx, run
from tests.scorecard_support import PROFILE, TODAY, world

pytestmark = pytest.mark.django_db

P = f"{TODAY.year:04d}06"


def as_user(user: User, role: str, *visible: str) -> ActionContext:
    scope = ExplicitSubjects.of(visible) if visible else AllSubjects()
    return replace(role_ctx(role), user=user, visible_subjects=scope)


@pytest.fixture
def ids(make_user: Callable[..., User]) -> dict[str, Any]:
    ids = world()
    for code, value, weight, cap in (
        ("casa_growth", "100", "40", "60"),
        ("ntb_accounts", "10", "20", "30"),
        ("service_tat", "5", "15", "22"),
    ):
        Target.objects.create(
            org_id=ORG_ID,
            metric=Metric.objects.get(metric_code=code),
            scope_type="profile",
            scope_code=PROFILE,
            period_key=P,
            target_value=Decimal(value),
            weight=Decimal(weight),
            cap=Decimal(cap),
            version=1,
            state="published",
        )
    for code, value in (("casa_growth", "90"), ("ntb_accounts", "10"), ("service_tat", "5")):
        FactActualMonthly.objects.create(
            org_id=ORG_ID,
            metric=Metric.objects.get(metric_code=code),
            subject_id=ids["E1"],
            assignment=Assignment.objects.get(subject_id=ids["E1"]),
            period_key=P,
            actual_value=Decimal(value),
            loaded_at=timezone.now(),
        )
    ids["manager"] = as_user(make_user("line_manager"), "line_manager", ids["E1"])
    ids["admin"] = as_user(make_user("admin"), "admin")
    ids["owner"] = as_user(make_user("metric_owner"), "metric_owner")
    return ids


def everyone() -> ActionContext:
    return replace(role_ctx(), visible_subjects=AllSubjects())


def card(ids: dict[str, Any], who: str = "E1") -> Any:
    return run("scorecard.compute", everyone(), subject_id=ids[who], period_key=P)


def request(ctx: ActionContext, **kw: Any) -> Any:
    payload = {
        "scope_type": "subject",
        "scope_code": "E1",
        "metric_code": "casa_growth",
        "period_from": P,
        "change_type": "target",
        "override_value": "80",
        "reason": "Branch refurbished for two weeks.",
        **kw,
    }
    return run("override.request", ctx, **payload)


def test_a_request_changes_nothing_until_a_second_person_approves(ids: dict[str, Any]) -> None:
    before = card(ids)
    casa = next(m for m in before.metrics if m.metric_code == "casa_growth")
    assert casa.pct_achieved == Decimal("0.9") and casa.overrides == []

    o = request(ids["manager"])
    assert o.status == "pending" and o.scope_code == ids["E1"] and o.scope_label == "E1 · Person E1"
    assert o.mine and o.requested_by_name == ids["manager"].user.username
    assert card(ids).total_score == before.total_score

    with pytest.raises(Conflict, match="second person"):
        run(
            "override.approve",
            replace(ids["admin"], user=ids["manager"].user),
            override_id=o.override_id,
        )

    version = config_version(ORG_ID)
    done = run("override.approve", ids["owner"], override_id=o.override_id, note="Agreed")
    assert done.status == "approved" and done.approved_by == ids["owner"].user_id
    assert not done.mine and done.approved_by_name == ids["owner"].user.username
    assert config_version(ORG_ID) == version + 1
    after = next(m for m in card(ids).metrics if m.metric_code == "casa_growth")
    assert after.target_value == 80 and after.pct_achieved == Decimal("1.125")
    assert [x.override_id for x in after.overrides] == [o.override_id]
    assert AuditLog.objects.filter(event="override.approved").exists()

    gone = run("override.revoke", ids["admin"], override_id=o.override_id, note="Raised in error")
    assert gone.status == "revoked"
    assert next(m for m in card(ids).metrics if m.metric_code == "casa_growth").target_value == 100


def test_line_managers_request_only_for_people_they_can_see(ids: dict[str, Any]) -> None:
    with pytest.raises(OutOfScope):
        request(ids["manager"], scope_code="E2")
    with pytest.raises(OutOfScope, match="configuration rights"):
        request(ids["manager"], scope_type="profile", scope_code=PROFILE)
    o = request(ids["owner"], scope_type="profile", scope_code=PROFILE, change_type="weight")
    assert o.scope_type == "profile"
    with pytest.raises(InvalidInput, match="dimension"):
        request(ids["owner"], scope_type="dimension", scope_code="desk:FX")
    assert (
        request(ids["owner"], scope_type="dimension", scope_code="branch:ACC").status == "pending"
    )
    with pytest.raises(InvalidInput, match="No profile"):
        request(ids["owner"], scope_type="profile", scope_code="nobody")

    # The manager sees only overrides for subjects in their scope.
    listed = run("override.list", ids["manager"])
    assert {x.scope_type for x in listed.overrides} == {"profile", "dimension"}
    request(ids["owner"], scope_code="E2")
    assert all(x.scope_code != ids["E2"] for x in run("override.list", ids["manager"]).overrides)
    assert run("override.list", ids["admin"], status="pending").pending == 3


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"override_value": "0"}, "greater than zero"),
        ({"change_type": "weight", "override_value": "-1"}, "negative"),
        ({"change_type": "target_type", "override_value": None}, "needs target_type"),
        ({"change_type": "cap", "override_value": None}, "needs override_value"),
        ({"period_to": "202001"}, "before period_from"),
        ({"reason": "  "}, "reason"),
        ({"metric_code": "no_such"}, "No Scorecards metric"),
    ],
)
def test_request_validation(ids: dict[str, Any], kw: dict[str, Any], match: str) -> None:
    with pytest.raises(InvalidInput) as e:
        request(ids["admin"], **kw)
    assert match in f"{e.value.message} {e.value.detail}"


def test_locked_periods_take_no_overrides(ids: dict[str, Any]) -> None:
    o = request(ids["manager"])
    PeriodStatus.objects.create(org_id=ORG_ID, product="scorecards", period_key=P, status="closed")
    with pytest.raises(Conflict, match="restatement"):
        request(ids["manager"])
    with pytest.raises(Conflict, match="restatement"):
        run("override.approve", ids["owner"], override_id=o.override_id)


def test_reject_withdraw_and_states(ids: dict[str, Any]) -> None:
    a = request(ids["manager"])
    with pytest.raises(Conflict, match="Only the person"):
        run("override.withdraw", ids["owner"], override_id=a.override_id)
    assert run("override.withdraw", ids["manager"], override_id=a.override_id).status == "withdrawn"
    with pytest.raises(Conflict, match="not pending"):
        run("override.approve", ids["owner"], override_id=a.override_id)

    b = request(
        ids["manager"], change_type="target_type", override_value=None, target_type="yearly"
    )
    with pytest.raises(InvalidInput):
        run("override.reject", ids["owner"], override_id=b.override_id, note="")
    r = run("override.reject", ids["owner"], override_id=b.override_id, note="Use a monthly target")
    assert r.status == "rejected" and r.decision_note == "Use a monthly target"
    with pytest.raises(Conflict, match="only an approved"):
        run("override.revoke", ids["owner"], override_id=b.override_id, note="x" * 5)


def test_the_database_refuses_self_approval(ids: dict[str, Any]) -> None:
    o = request(ids["manager"])
    with pytest.raises(IntegrityError), transaction.atomic():
        Override.objects.filter(override_id=o.override_id).update(
            status="approved", approved_by=ids["manager"].user_id, approved_at=timezone.now()
        )


def test_scorecard_compute_and_period_list(ids: dict[str, Any]) -> None:
    c = card(ids)
    assert c.assigned and c.profile_code == PROFILE and c.source == "live"
    assert c.metrics_total == 4 and c.metrics_scored == 3 and c.not_reported == 1
    assert c.statement == "3 of 4 metrics scored · 1 awaiting data"
    # casa 0.9 × 40 = 36, ntb 20, service 15 → 0.71.
    assert c.total_score == Decimal("0.71") and c.band is not None
    assert c.band.label == "Needs Focus" and c.next_band is not None
    assert c.next_band.label == "Gaining Momentum"

    with pytest.raises(OutOfScope):
        run("scorecard.compute", ids["manager"], subject_id=ids["E2"], period_key=P)
    early = run("scorecard.compute", everyone(), subject_id=ids["E1"], period_key="199901")
    assert not early.assigned and early.metrics == [] and "No role in force" in early.statement

    listed = run("scorecard.period.list", ids["manager"], period_key=P)
    assert [r.staff_no for r in listed.rows] == ["E1"]
    all_rows = run("scorecard.period.list", ids["admin"], period_key=P)
    assert [r.staff_no for r in all_rows.rows] == ["E1", "E2", "E3"]
    assert all_rows.rows[1].graded_score is None and all_rows.rows[1].not_reported == 4
