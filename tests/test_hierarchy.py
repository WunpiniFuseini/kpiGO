"""Subjects, assignments, reporting lines, dimensions and the visibility closure."""

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction

from kpigo.action import Conflict, InvalidInput, NoSubjects, OutOfScope
from kpigo.action.identity import build_context
from kpigo.hierarchy.closure import find_cycle
from kpigo.hierarchy.models import Assignment, ReportingEdge, VisibilityClosure
from kpigo.hierarchy.scope import ClosureScope
from kpigo.platform.config import config_version
from tests.conftest import ORG_ID, role_ctx, run

pytestmark = pytest.mark.django_db

PERIOD = "202610"


def subject(staff_no: str, email: str | None = None) -> uuid.UUID:
    out = run(
        "subject.register",
        staff_no=staff_no,
        full_name=f"Person {staff_no}",
        email=email or f"{staff_no.lower()}@bank.example",
    )
    return out.subject_id  # type: ignore[no-any-return]


def assign(subject_id: uuid.UUID, **extra: Any) -> Any:
    payload = {
        "subject_id": str(subject_id),
        "role_code": "rm",
        "profile_code": "retail_rm",
        "effective_from": "2026-01-01",
        **extra,
    }
    return run("assignment.create", **payload)


def line(sub: uuid.UUID, mgr: uuid.UUID, kind: str = "solid", **extra: Any) -> Any:
    return run(
        "reporting_edge.create",
        subject_id=str(sub),
        manager_id=str(mgr),
        relationship_type=kind,
        effective_from=extra.pop("effective_from", "2026-01-01"),
        **extra,
    )


def closure_rows(period: str = PERIOD) -> set[tuple[uuid.UUID, uuid.UUID, str, bool]]:
    return {
        (r.viewer_subject_id, r.visible_subject_id, r.via, r.counts_toward_rollup)
        for r in VisibilityClosure.objects.filter(org_id=ORG_ID, period_key=period)
    }


# ── subjects ────────────────────────────────────────────────────────────────


def test_subject_identity_is_unique_per_org_case_insensitively() -> None:
    subject("E1", "Ama@Bank.example")
    with pytest.raises(Conflict, match="email"):
        subject("E2", "ama@bank.EXAMPLE")
    with pytest.raises(Conflict, match="staff number"):
        subject("E1", "other@bank.example")


def test_subject_update_and_list() -> None:
    sid = subject("E1")
    run("subject.update", subject_id=str(sid), status="inactive", left_at="2026-12-31")
    listed = run("subject.list", status="inactive")
    assert [s.staff_no for s in listed.subjects] == ["E1"]
    assert listed.subjects[0].left_at == date(2026, 12, 31)


# ── assignment: the overlap constraint is load-bearing ────────────────────


def test_overlapping_assignments_are_refused() -> None:
    sid = subject("E1")
    assign(sid, effective_to="2026-07-01")
    with pytest.raises(Conflict, match="overlapping period"):
        assign(sid, role_code="bm", effective_from="2026-06-01")
    with pytest.raises(Conflict):
        assign(sid, effective_from="2025-06-01")  # open-ended into the existing one
    assert Assignment.objects.count() == 1


def test_database_exclusion_constraint_refuses_overlap_directly() -> None:
    sid = subject("E1")
    assign(sid)
    with pytest.raises(IntegrityError, match="assignment_no_overlap"), transaction.atomic():
        Assignment.objects.create(
            org_id=ORG_ID,
            subject_id=sid,
            role_code="bm",
            profile_code="branch_manager",
            effective_from=date(2027, 1, 1),
        )


def test_adjacent_assignments_do_not_overlap() -> None:
    sid = subject("E1")
    first = assign(sid)
    run("assignment.end", assignment_id=str(first.assignment_id), effective_to="2026-07-01")
    assign(sid, role_code="bm", profile_code="branch_manager", effective_from="2026-07-01")
    history = run("assignment.list", subject_id=str(sid)).assignments
    assert [(a.role_code, a.effective_from, a.effective_to) for a in history] == [
        ("rm", date(2026, 1, 1), date(2026, 7, 1)),
        ("bm", date(2026, 7, 1), None),
    ]


def test_assignments_for_different_subjects_may_overlap() -> None:
    assign(subject("E1"))
    assign(subject("E2"))
    assert Assignment.objects.count() == 2


def test_assignment_end_only_shortens() -> None:
    sid = subject("E1")
    a = assign(sid, effective_to="2026-06-01")
    with pytest.raises(Conflict, match="already ends"):
        run("assignment.end", assignment_id=str(a.assignment_id), effective_to="2026-09-01")
    with pytest.raises(Conflict, match="after effective_from"):
        run("assignment.end", assignment_id=str(a.assignment_id), effective_to="2025-12-01")


def test_assignment_dimension_codes_must_exist() -> None:
    sid = subject("E1")
    with pytest.raises(InvalidInput, match="Unknown dimension"):
        assign(sid, branch_code="ACC01")
    run("dimension.define", dimension_type="branch", display_name="Branch")
    run(
        "dimension.member.upsert",
        dimension_type="branch",
        members=[{"member_code": "ACC01", "member_name": "Accra Main"}],
    )
    assert assign(sid, branch_code="ACC01").branch_code == "ACC01"


# ── dimensions ──────────────────────────────────────────────────────────────


def test_dimension_members_need_existing_parents_and_no_loops() -> None:
    run("dimension.define", dimension_type="region", display_name="Region")
    with pytest.raises(InvalidInput, match="Parent codes"):
        run(
            "dimension.member.upsert",
            dimension_type="region",
            members=[{"member_code": "GA", "member_name": "Greater Accra", "parent_code": "SOUTH"}],
        )
    run(
        "dimension.member.upsert",
        dimension_type="region",
        members=[
            {"member_code": "SOUTH", "member_name": "South"},
            {"member_code": "GA", "member_name": "Greater Accra", "parent_code": "SOUTH"},
        ],
    )
    with pytest.raises(InvalidInput, match="loop"):
        run(
            "dimension.member.upsert",
            dimension_type="region",
            members=[{"member_code": "SOUTH", "member_name": "South", "parent_code": "GA"}],
        )
    out = run(
        "dimension.member.upsert",
        dimension_type="region",
        members=[{"member_code": "GA", "member_name": "Accra", "parent_code": "SOUTH"}],
    )
    assert (out.created, out.updated) == (0, 1)
    members = run("dimension.member.list", dimension_type="region").members
    assert {m.member_code: m.member_name for m in members} == {"GA": "Accra", "SOUTH": "South"}


def test_members_need_a_defined_dimension() -> None:
    with pytest.raises(Exception, match="define it first"):
        run(
            "dimension.member.upsert",
            dimension_type="segment",
            members=[{"member_code": "RET", "member_name": "Retail"}],
        )


# ── reporting lines ─────────────────────────────────────────────────────────


def test_reporting_line_guards() -> None:
    a, b, c = subject("A"), subject("B"), subject("C")
    with pytest.raises(Conflict, match="themself"):
        line(a, a)
    line(a, b)
    with pytest.raises(Conflict, match="already reports"):
        line(a, b, "dotted", effective_from="2026-03-01")
    with pytest.raises(Conflict, match="solid-line manager"):
        line(a, c)
    # A second line is fine as dotted.
    assert line(a, c, "dotted").relationship_type == "dotted"
    assert ReportingEdge.objects.count() == 2


# ── the closure ─────────────────────────────────────────────────────────────


@pytest.fixture
def org() -> dict[str, uuid.UUID]:
    """CEO ← M1 ← R1, R2; M2 ← R3; R1 also reports dotted to M2."""
    people = {k: subject(k) for k in ("CEO", "M1", "M2", "R1", "R2", "R3")}
    for name, sid in people.items():
        profile = "retail_rm" if name.startswith("R") else "manager"
        assign(sid, profile_code=profile)
    line(people["M1"], people["CEO"])
    line(people["M2"], people["CEO"])
    line(people["R1"], people["M1"])
    line(people["R2"], people["M1"])
    line(people["R3"], people["M2"])
    line(people["R1"], people["M2"], "dotted")
    return people


def test_closure_is_transitive_with_self_rows_and_dotted_lines(org: dict[str, uuid.UUID]) -> None:
    before = config_version(ORG_ID)
    out = run("visibility.rebuild", period_key=PERIOD)
    assert out.edges == 6
    assert out.subjects == 6
    assert config_version(ORG_ID) == before + 1
    rows = closure_rows()
    p = org
    for sid in p.values():
        assert (sid, sid, "self", True) in rows
    assert (p["CEO"], p["R1"], "solid", True) in rows  # two hops, all solid
    assert (p["M1"], p["R2"], "solid", True) in rows
    assert (p["M2"], p["R1"], "dotted", True) in rows
    # The CEO reaches R1 through M1 (solid) and M2 (dotted); the solid path wins.
    assert not any(r[0] == p["CEO"] and r[1] == p["R1"] and r[2] == "dotted" for r in rows)
    assert not any(r[0] == p["M1"] and r[1] == p["R3"] for r in rows)
    assert not any(r[0] == p["R1"] and r[1] == p["M1"] for r in rows)
    visible = run("visibility.list", period_key=PERIOD, viewer_subject_id=str(p["M2"]))
    assert {v.visible_subject_id for v in visible.visible} == {p["M2"], p["R3"], p["R1"]}


def test_rollup_policy_overrides_edge_by_profile(org: dict[str, uuid.UUID]) -> None:
    run(
        "rollup_policy.set",
        scope_type="profile",
        scope_code="retail_rm",
        relationship_type="dotted",
        counts_toward_rollup=False,
        effective_from="2026-01-01",
    )
    run("visibility.rebuild", period_key=PERIOD)
    rows = closure_rows()
    assert (org["M2"], org["R1"], "dotted", False) in rows
    assert (org["M1"], org["R1"], "solid", True) in rows
    with pytest.raises(Conflict, match="already covers"):
        run(
            "rollup_policy.set",
            scope_type="profile",
            scope_code="retail_rm",
            relationship_type="dotted",
            counts_toward_rollup=True,
            effective_from="2026-06-01",
        )


def test_edge_level_rollup_flag_is_used_without_a_policy() -> None:
    m, r = subject("M"), subject("R")
    line(r, m, "dotted", counts_toward_rollup=False)
    run("visibility.rebuild", period_key=PERIOD)
    assert (m, r, "dotted", False) in closure_rows()


def test_edges_outside_the_period_are_ignored() -> None:
    m, r = subject("M"), subject("R")
    line(r, m, effective_from="2026-11-01")
    run("visibility.rebuild", period_key=PERIOD)
    # No line and no assignment in October: nobody is in the period.
    assert closure_rows() == set()
    assign(r)
    run("visibility.rebuild", period_key=PERIOD)
    assert closure_rows() == {(r, r, "self", True)}


def test_cycle_refuses_to_commit_and_previous_closure_stands(org: dict[str, uuid.UUID]) -> None:
    run("visibility.rebuild", period_key=PERIOD)
    good = closure_rows()
    version = config_version(ORG_ID)
    # CEO dotted-reports to R2, who reports up to the CEO: a cycle.
    line(org["CEO"], org["R2"], "dotted")
    with pytest.raises(Conflict, match="contain a cycle") as exc:
        run("visibility.rebuild", period_key=PERIOD)
    cycle = exc.value.detail["cycle"]
    assert cycle[0] == cycle[-1]
    assert {str(org["CEO"]), str(org["R2"]), str(org["M1"])} <= set(cycle)
    assert closure_rows() == good
    # The refused build does not count as a configuration change.
    assert config_version(ORG_ID) == version + 1  # only the edge creation bumped it


def test_cycle_in_another_period_does_not_block() -> None:
    a, b = subject("A"), subject("B")
    line(a, b)
    line(b, a, effective_from="2027-01-01")
    assert find_cycle(ORG_ID, PERIOD) is None
    assert find_cycle(ORG_ID, "202701") is not None
    run("visibility.rebuild", period_key=PERIOD)
    with pytest.raises(Conflict):
        run("visibility.rebuild", period_key="202701")


def test_rebuild_replaces_the_period(org: dict[str, uuid.UUID]) -> None:
    run("visibility.rebuild", period_key=PERIOD)
    edge = ReportingEdge.objects.get(subject_id=org["R1"], relationship_type="dotted")
    run("reporting_edge.end", edge_id=str(edge.edge_id), effective_to="2026-10-01")
    run("visibility.rebuild", period_key=PERIOD)
    assert not any(r[0] == org["M2"] and r[1] == org["R1"] for r in closure_rows())
    # September still had the line.
    run("visibility.rebuild", period_key="202609")
    assert (org["M2"], org["R1"], "dotted", True) in closure_rows("202609")


# ── scope: the closure narrows what a user sees ─────────────────────────────


def test_build_context_scopes_a_user_to_their_subtree(
    org: dict[str, uuid.UUID], make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kpigo.hierarchy.scope.current_period_key", lambda org_id: PERIOD)
    run("visibility.rebuild", period_key=PERIOD)
    manager = make_user("line_manager", email="m1@bank.example")
    ctx = build_context(manager, caller="http")
    assert isinstance(ctx.visible_subjects, ClosureScope)
    assert run("subject.get", ctx, subject_id=str(org["R1"])).subject.staff_no == "R1"
    got = run("subject.get", ctx, subject_id=str(org["M1"]))
    assert got.assignment is not None and got.assignment.profile_code == "manager"
    with pytest.raises(OutOfScope):
        run("subject.get", ctx, subject_id=str(org["R3"]))
    with pytest.raises(OutOfScope):
        run("subject.get", ctx, subject_id=str(org["CEO"]))


def test_no_subject_or_no_closure_means_no_data(
    org: dict[str, uuid.UUID], make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kpigo.hierarchy.scope.current_period_key", lambda org_id: PERIOD)
    stranger = make_user("line_manager", email="nobody@bank.example")
    assert isinstance(build_context(stranger, caller="http").visible_subjects, NoSubjects)
    # A linked subject but no closure built for the period: still nothing.
    manager = build_context(make_user("line_manager", email="m1@bank.example"), caller="http")
    with pytest.raises(OutOfScope):
        run("subject.get", manager, subject_id=str(org["M1"]))


def test_closure_scope_fails_closed_on_bad_ids() -> None:
    scope = ClosureScope(org_id=ORG_ID, viewer_subject_id="not-a-uuid", period_key=PERIOD)
    assert scope.contains("also-not-a-uuid") is False


def test_hierarchy_admin_is_steward_work() -> None:
    from kpigo.action import PermissionDenied

    for role in ("line_manager", "staff", "metric_owner"):
        with pytest.raises(PermissionDenied):
            run(
                "subject.register",
                role_ctx(role),
                staff_no="X",
                full_name="X",
                email="x@bank.example",
            )
    run(
        "subject.register",
        role_ctx("data_steward"),
        staff_no="X",
        full_name="X",
        email="x@b.example",
    )
