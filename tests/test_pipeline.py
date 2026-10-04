"""The wrapper pipeline: every cross-cutting behaviour is the pipeline's, not the action's."""

from collections.abc import Callable

import pytest
from django.contrib.auth.models import Group, User
from django.db import DatabaseError, connection, transaction
from pydantic import BaseModel

from kpigo.action import (
    ActionContext,
    Conflict,
    ExplicitSubjects,
    InvalidInput,
    MaintenanceMode,
    OutOfScope,
    PermissionDenied,
    Proposal,
    action,
    invoke,
    registry,
)
from kpigo.action.definition import ActionDefinition
from kpigo.action.identity import build_context
from kpigo.platform.models import ActionIdempotency, ApprovalRequest, AuditLog

pytestmark = pytest.mark.django_db

ORG = "00000000-0000-0000-0000-000000000001"


class GroupIn(BaseModel):
    name: str


class GroupOut(BaseModel):
    group_id: int
    name: str


class SubjectIn(BaseModel):
    subject_id: str


class SubjectOut(BaseModel):
    subject_id: str


calls: list[str] = []


@action(
    name="test.group.create",
    schema=GroupIn,
    output=GroupOut,
    permission="test.group.create",
    read_only=False,
    requires_approval="maker_checker",
    audit="test.group.created",
    idempotency_key=lambda p: f"group:{p.name}",
)
def create_group(params: GroupIn, ctx: ActionContext) -> GroupOut:
    calls.append(params.name)
    if params.name == "boom":
        raise Conflict("Refused.")
    group = Group.objects.create(name=params.name)
    ctx.audit("test.group.side_note", group=params.name)
    return GroupOut(group_id=group.pk, name=group.name)


@action(
    name="test.subject.view",
    schema=SubjectIn,
    output=SubjectOut,
    permission="test.subject.view",
    read_only=True,
    scope="subject",
)
def view_subject(params: SubjectIn, ctx: ActionContext) -> SubjectOut:
    return SubjectOut(subject_id=params.subject_id)


@action(
    name="test.broken",
    schema=SubjectIn,
    output=SubjectOut,
    permission="test.broken",
    read_only=True,
)
def broken(params: SubjectIn, ctx: ActionContext) -> SubjectOut:
    return GroupOut(group_id=1, name="wrong type")  # type: ignore[return-value]


def ctx_for(user: User | None = None, *perms: str, **kw: object) -> ActionContext:
    return ActionContext(
        caller="cli",
        user=user,
        org_id=ORG,
        permissions=frozenset(perms),
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    calls.clear()


@pytest.fixture
def group_action(register: Callable[..., ActionDefinition]) -> ActionDefinition:
    return register(create_group)


def defn(fn: object) -> ActionDefinition:
    return fn.__kpigo_action__  # type: ignore[attr-defined,no-any-return]


def test_success_writes_one_audit_row_plus_action_events(
    group_action: ActionDefinition, make_user: Callable[..., User]
) -> None:
    user = make_user()
    out = invoke(group_action, {"name": "east"}, ctx_for(user, "test.group.create"))
    assert isinstance(out, GroupOut) and out.name == "east"
    rows = list(
        AuditLog.objects.order_by("event").values("event", "actor_user_id", "caller", "payload")
    )
    assert [r["event"] for r in rows] == ["test.group.created", "test.group.side_note"]
    assert all(r["actor_user_id"] == user.pk and r["caller"] == "cli" for r in rows)
    assert rows[0]["payload"]["params"] == {"name": "east"}
    assert rows[0]["payload"]["outcome"] == "ok"


def test_validation_runs_before_anything_else(group_action: ActionDefinition) -> None:
    with pytest.raises(InvalidInput):
        invoke(group_action, {"nope": 1}, ctx_for(None))
    assert calls == []
    assert AuditLog.objects.get().event == "action.failed"


def test_permission_denied_never_runs_and_is_audited(group_action: ActionDefinition) -> None:
    with pytest.raises(PermissionDenied):
        invoke(group_action, {"name": "x"}, ctx_for(None, "something.else"))
    assert calls == []
    row = AuditLog.objects.get()
    assert row.event == "action.denied"
    assert row.payload["outcome"] == "permission_denied"


def test_subject_scope_is_narrowed() -> None:
    d = defn(view_subject)
    ctx = ctx_for(None, "test.subject.view", visible_subjects=ExplicitSubjects.of(["S1"]))
    assert invoke(d, {"subject_id": "S1"}, ctx) == SubjectOut(subject_id="S1")
    with pytest.raises(OutOfScope):
        invoke(d, {"subject_id": "S2"}, ctx)
    # The default scope is deny-all: no grant means no data.
    with pytest.raises(OutOfScope):
        invoke(d, {"subject_id": "S1"}, ctx_for(None, "test.subject.view"))


def test_maintenance_mode_blocks_writes_not_reads(
    group_action: ActionDefinition, settings: object
) -> None:
    settings.KPIGO_MAINTENANCE_MODE = True  # type: ignore[attr-defined]
    with pytest.raises(MaintenanceMode):
        invoke(group_action, {"name": "x"}, ctx_for(None, "test.group.create"))
    assert calls == []
    ctx = ctx_for(None, "test.subject.view", visible_subjects=ExplicitSubjects.of(["S1"]))
    assert invoke(defn(view_subject), {"subject_id": "S1"}, ctx).subject_id == "S1"  # type: ignore[attr-defined]


def test_idempotency_returns_stored_result_without_rerunning(
    group_action: ActionDefinition,
) -> None:
    ctx = ctx_for(None, "test.group.create")
    first = invoke(group_action, {"name": "west"}, ctx)
    second = invoke(group_action, {"name": "west"}, ctx_for(None, "test.group.create"))
    assert first == second
    assert calls == ["west"]
    assert ActionIdempotency.objects.count() == 1
    outcomes = sorted(
        r.payload["outcome"] for r in AuditLog.objects.filter(event="test.group.created")
    )
    assert outcomes == ["idempotent_replay", "ok"]


def test_dry_run_discards_writes_but_is_audited(group_action: ActionDefinition) -> None:
    out = invoke(group_action, {"name": "north"}, ctx_for(None, "test.group.create", dry_run=True))
    assert isinstance(out, GroupOut) and out.name == "north"
    assert not Group.objects.filter(name="north").exists()
    assert ActionIdempotency.objects.count() == 0
    row = AuditLog.objects.get()
    assert row.payload["outcome"] == "dry_run" and row.payload["dry_run"] is True


def test_handler_error_rolls_back_and_audits_failure(group_action: ActionDefinition) -> None:
    with pytest.raises(Conflict):
        invoke(group_action, {"name": "boom"}, ctx_for(None, "test.group.create"))
    row = AuditLog.objects.get()
    assert row.event == "action.failed" and row.payload["outcome"] == "conflict"


def test_wrong_output_type_is_a_failure() -> None:
    with pytest.raises(TypeError):
        invoke(defn(broken), {"subject_id": "S"}, ctx_for(None, "test.broken"))
    assert AuditLog.objects.get().payload["outcome"] == "error"


def test_audit_log_is_append_only(group_action: ActionDefinition) -> None:
    invoke(group_action, {"name": "south"}, ctx_for(None, "test.group.create"))
    row = AuditLog.objects.first()
    assert row is not None
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        AuditLog.objects.filter(pk=row.pk).update(event="tampered")
    with (
        pytest.raises(DatabaseError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cur,
    ):
        cur.execute("DELETE FROM audit_log")


class TestMakerChecker:
    @pytest.fixture(autouse=True)
    def _enable(self, settings: object) -> None:
        settings.KPIGO_APPROVAL_CLASSES_ENABLED = ["maker_checker"]  # type: ignore[attr-defined]

    def test_off_by_default(self, group_action: ActionDefinition, settings: object) -> None:
        settings.KPIGO_APPROVAL_CLASSES_ENABLED = []  # type: ignore[attr-defined]
        out = invoke(group_action, {"name": "direct"}, ctx_for(None, "test.group.create"))
        assert isinstance(out, GroupOut)

    def test_intercept_then_approve_replays_identical_payload(
        self, group_action: ActionDefinition, make_user: Callable[..., User]
    ) -> None:
        maker = make_user("staff")
        checker = make_user("admin")
        proposal = invoke(group_action, {"name": "central"}, ctx_for(maker, "test.group.create"))
        assert isinstance(proposal, Proposal)
        assert calls == []
        request = ApprovalRequest.objects.get()
        assert request.payload == {"name": "central"}
        assert request.status == "pending"

        with _maker_holds("test.group.create"):
            result = invoke(
                registry.get("platform.approval.approve"),
                {"approval_request_id": proposal.approval_request_id},
                build_context(checker, caller="http"),
            )
        assert result.status == "approved"  # type: ignore[attr-defined]
        assert calls == ["central"]
        assert Group.objects.filter(name="central").exists()
        replay = AuditLog.objects.get(event="test.group.created")
        assert replay.actor_user_id == maker.pk
        assert replay.payload["approved_by_id"] == checker.pk
        assert replay.payload["approval_request_id"] == proposal.approval_request_id

    def test_maker_cannot_check_own_request(
        self, group_action: ActionDefinition, make_user: Callable[..., User]
    ) -> None:
        maker = make_user("admin")
        proposal = invoke(group_action, {"name": "self"}, ctx_for(maker, "test.group.create"))
        assert isinstance(proposal, Proposal)
        with pytest.raises(Conflict, match="maker"):
            invoke(
                registry.get("platform.approval.approve"),
                {"approval_request_id": proposal.approval_request_id},
                build_context(maker, caller="http"),
            )
        assert calls == []

    def test_reject_executes_nothing(
        self, group_action: ActionDefinition, make_user: Callable[..., User]
    ) -> None:
        proposal = invoke(group_action, {"name": "nope"}, ctx_for(make_user(), "test.group.create"))
        assert isinstance(proposal, Proposal)
        out = invoke(
            registry.get("platform.approval.reject"),
            {"approval_request_id": proposal.approval_request_id, "reason": "not now"},
            build_context(make_user("admin"), caller="http"),
        )
        assert out.status == "rejected"  # type: ignore[attr-defined]
        assert calls == []
        assert ApprovalRequest.objects.get().rejection_reason == "not now"


class _maker_holds:
    """Temporarily grant a permission to every role, so the replayed maker context holds it."""

    def __init__(self, permission: str) -> None:
        self.permission = permission

    def __enter__(self) -> None:
        from kpigo.action import roles

        self.saved = dict(roles.SYSTEM_ROLES)
        for code, spec in self.saved.items():
            roles.SYSTEM_ROLES[code] = roles.RoleSpec(
                code=spec.code, name=spec.name, permissions=spec.permissions | {self.permission}
            )

    def __exit__(self, *exc: object) -> None:
        from kpigo.action import roles

        roles.SYSTEM_ROLES.clear()
        roles.SYSTEM_ROLES.update(self.saved)
