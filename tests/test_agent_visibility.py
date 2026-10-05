"""Who sees whom in Agent Performance: open by default, narrowed per role or profile (AP-7)."""

from __future__ import annotations

from typing import Any

import pytest

from kpigo.action import InvalidInput, NotFound, PermissionDenied
from kpigo.action.identity import build_context
from tests.agent_support import PRODUCT, day, load, targets, world
from tests.conftest import role_ctx, run

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> dict[str, str]:
    ids = world()
    targets()
    load(
        [
            ["value_booked", "A1", day(2), None, "150", "GHS"],
            ["value_booked", "A2", day(3), None, "120", "GHS"],
            ["value_booked", "A3", day(4), None, "90", "GHS"],
        ]
    )
    return ids


@pytest.fixture
def a1(org: dict[str, str], make_user: Any) -> Any:
    """Agent A1 (Greater Accra, GA-01), signed in with the Staff role."""
    return build_context(make_user("staff", email="a1@bank.example"), caller="cli")


def board(ctx: Any) -> list[str]:
    out = run("agent.leaderboard", ctx, product=PRODUCT, as_of=day(17), cohort_type="all")
    return sorted(r.agent.staff_no for r in out.rows)


def test_open_by_default(a1: Any) -> None:
    assert board(a1) == ["A1", "A2", "A3"]
    out = run("agent.leaderboard", a1, product=PRODUCT, as_of=day(17))
    assert out.visibility.restricted is False and out.visibility.scopes == []


def test_a_role_narrowed_to_its_branch(a1: Any, org: dict[str, str]) -> None:
    rule = run(
        "agent.visibility.set",
        product=PRODUCT,
        applies_to="role",
        applies_code="staff",
        scope="branch",
    )
    assert (rule.applies_code, rule.scope) == ("staff", "branch")
    assert board(a1) == ["A1", "A2"]
    out = run("agent.leaderboard", a1, product=PRODUCT, as_of=day(17), cohort_type="all")
    assert out.visibility.restricted and out.visibility.scopes == ["branch"]
    assert [(b.applies_to, b.applies_code) for b in out.visibility.because] == [("role", "staff")]
    # The matrix, the sections and an agent's own pace all read the same view.
    m = run("agent.matrix", a1, product=PRODUCT, as_of=day(17), metric_code="value_booked")
    assert [r.key for r in m.rows] == ["GA"] and m.visibility.restricted
    t = run("agent.trend", a1, product=PRODUCT, as_of=day(17), metric_code="value_booked")
    assert t.agents == 2
    run("agent.pace", a1, product=PRODUCT, subject_id=org["A2"], as_of=day(17))
    with pytest.raises(PermissionDenied):
        run("agent.pace", a1, product=PRODUCT, subject_id=org["A3"], as_of=day(17))
    with pytest.raises(NotFound):
        run("agent.matrix", a1, product=PRODUCT, as_of=day(17), level="branch", region_code="AS")
    # The other module is untouched.
    assert run("agent.visibility.list", product="agent_service").rules == []


def test_a_profile_narrowed_to_self_sees_only_their_own(a1: Any, org: dict[str, str]) -> None:
    run(
        "agent.visibility.set",
        product=PRODUCT,
        applies_to="profile",
        applies_code="sme_rm",
        scope="self",
    )
    assert board(a1) == ["A1"]
    assert run("agent.pace", a1, product=PRODUCT, as_of=day(17)).agent.staff_no == "A1"


def test_rules_add_up_and_all_lifts_them(a1: Any) -> None:
    run(
        "agent.visibility.set",
        product=PRODUCT,
        applies_to="profile",
        applies_code="sme_rm",
        scope="self",
    )
    run(
        "agent.visibility.set",
        product=PRODUCT,
        applies_to="role",
        applies_code="staff",
        scope="region",
    )
    # The widest of the viewer's rules wins: their region, Greater Accra.
    assert board(a1) == ["A1", "A2"]
    run(
        "agent.visibility.set",
        product=PRODUCT,
        applies_to="role",
        applies_code="staff",
        scope="all",
    )
    assert board(a1) == ["A1", "A2", "A3"]
    run("agent.visibility.clear", product=PRODUCT, applies_to="role", applies_code="staff")
    assert board(a1) == ["A1"]
    rules = run("agent.visibility.list", product=PRODUCT).rules
    assert [(r.applies_to, r.applies_code, r.scope) for r in rules] == [
        ("profile", "sme_rm", "self")
    ]


def test_a_subtree_scope_reads_the_hierarchy_and_no_closure_means_only_yourself(a1: Any) -> None:
    run(
        "agent.visibility.set",
        product=PRODUCT,
        applies_to="role",
        applies_code="staff",
        scope="subtree",
    )
    # Nobody reports to A1: they see themselves, never everyone.
    assert board(a1) == ["A1"]


def test_rules_are_checked_and_kept_by_admins(a1: Any) -> None:
    with pytest.raises(NotFound):
        run(
            "agent.visibility.set",
            product=PRODUCT,
            applies_to="role",
            applies_code="nope",
            scope="self",
        )
    with pytest.raises(InvalidInput):
        run(
            "agent.visibility.set",
            product=PRODUCT,
            applies_to="profile",
            applies_code="nope",
            scope="self",
        )
    with pytest.raises(NotFound):
        run("agent.visibility.clear", product=PRODUCT, applies_to="role", applies_code="staff")
    with pytest.raises(PermissionDenied):
        run(
            "agent.visibility.set",
            role_ctx("agent_supervisor"),
            product=PRODUCT,
            applies_to="role",
            applies_code="staff",
            scope="self",
        )
    with pytest.raises(PermissionDenied):
        run("agent.visibility.list", a1, product=PRODUCT)
