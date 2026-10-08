"""R7 guardrails: what the assistant may run, propose, and never touch (TDD §13, PRD AG-4–AG-6).

The assistant runs as its user with ``caller="agent"``. Read actions execute;
every mutating action becomes a proposal; a fixed set of actions is refused by
metadata whatever the user holds. None of this needs a model connected.
"""

from collections.abc import Callable

import pytest
from django.contrib.auth.models import Group, User
from pydantic import BaseModel

from kpigo.action import (
    ActionContext,
    Conflict,
    NotFound,
    PermissionDenied,
    Proposal,
    RegistryError,
    action,
    invoke,
    registry,
)
from kpigo.action.definition import ActionDefinition
from kpigo.action.identity import build_context
from kpigo.platform.models import ApprovalRequest, AuditLog

pytestmark = pytest.mark.django_db

# The assistant's hard prohibitions. Adding or removing one is a reviewed
# decision: change this list in the same change, where a reviewer will see it.
AGENT_FORBIDDEN = {
    # Period close (AG-6)
    "period.transition",
    "scorecard.period.close",
    "scorecard.period.restate",
    # Target publish (AG-6)
    "target.publish",
    "target.batch.revert",
    # Override decisions (AG-6)
    "override.approve",
    "override.reject",
    "override.revoke",
    # Permission and access changes (AG-6)
    "role.clone",
    "role.update",
    "scope.grant.create",
    "scope.grant.end",
    "user.invite",
    "user.invite.resend",
    "user.update",
    "user.disable",
    "user.enable",
    "user.reassign",
    "approval.policy.set",
    "apitoken.issue",
    "apitoken.revoke",
    "directory.import.apply",
    "agent.visibility.set",
    "agent.visibility.clear",
    # Deciding approvals: a proposal is never approved by the thing that made it.
    "platform.approval.approve",
    "platform.approval.reject",
    "platform.approval.confirm",
    # Credentials, the licence and the installed version.
    "auth.logout",
    "auth.password.change",
    "licence.activate",
    "system.update.apply",
    # The assistant's own controls: it cannot ask itself, delete its record or lift its budget.
    "assistant.ask",
    "assistant.conversation.delete",
    "assistant.budget.set",
}


class NoteIn(BaseModel):
    name: str


class NoteOut(BaseModel):
    name: str


def _create(params: NoteIn, ctx: ActionContext) -> NoteOut:
    Group.objects.create(name=params.name)
    return NoteOut(name=params.name)


# Permissions everyone holds, so a replay as the maker passes its own check.
@action(
    name="test.assistant.note",
    schema=NoteIn,
    output=NoteOut,
    permission="subject.view",
    read_only=False,
)
def make_note(params: NoteIn, ctx: ActionContext) -> NoteOut:
    return _create(params, ctx)


@action(
    name="test.assistant.guarded",
    schema=NoteIn,
    output=NoteOut,
    permission="subject.view",
    read_only=False,
    requires_approval="config_change",
)
def make_guarded(params: NoteIn, ctx: ActionContext) -> NoteOut:
    return _create(params, ctx)


@action(
    name="test.assistant.banned",
    schema=NoteIn,
    output=NoteOut,
    permission="subject.view",
    read_only=False,
    agent_forbidden=True,
)
def make_banned(params: NoteIn, ctx: ActionContext) -> NoteOut:
    return _create(params, ctx)


@action(
    name="test.assistant.read",
    schema=NoteIn,
    output=NoteOut,
    permission="subject.view",
    read_only=True,
)
def read_note(params: NoteIn, ctx: ActionContext) -> NoteOut:
    return NoteOut(name=params.name)


@pytest.fixture(autouse=True)
def _actions(register: Callable[..., ActionDefinition]) -> None:
    for fn in (make_note, make_guarded, make_banned, read_note):
        register(fn)


def agent_ctx(user: User) -> ActionContext:
    """The assistant's context: the human's own, with caller="agent" (AG-4)."""
    return build_context(user, caller="agent")


def propose(user: User, name: str = "test.assistant.note", value: str = "x") -> Proposal:
    out = invoke(registry.get(name), {"name": value}, agent_ctx(user))
    assert isinstance(out, Proposal)
    return out


def run(action_name: str, user: User, **params: object) -> BaseModel:
    return invoke(registry.get(action_name), params, build_context(user, caller="http"))


# --- metadata ---------------------------------------------------------------


def test_forbidden_actions_are_exactly_the_reviewed_list() -> None:
    declared = {d.name for d in registry if d.agent_forbidden and not d.name.startswith("test.")}
    assert declared == AGENT_FORBIDDEN


def test_access_and_close_classes_are_always_forbidden() -> None:
    for d in registry:
        if d.requires_approval in ("access_change", "period_close"):
            assert d.agent_forbidden, f"'{d.name}' changes access or closes a period"


def test_public_actions_are_never_offered() -> None:
    assert all(not d.agent_allowed for d in registry if d.public)


def test_read_only_action_cannot_be_forbidden() -> None:
    with pytest.raises(RegistryError, match="forbidden"):
        action(
            name="test.bad.forbid",
            schema=NoteIn,
            output=NoteOut,
            permission="x.y",
            read_only=True,
            agent_forbidden=True,
        )


# --- the pipeline -----------------------------------------------------------


def test_read_actions_execute(make_user: Callable[..., User]) -> None:
    out = invoke(registry.get("test.assistant.read"), {"name": "r"}, agent_ctx(make_user("staff")))
    assert out == NoteOut(name="r")


def test_mutating_actions_always_propose(make_user: Callable[..., User]) -> None:
    user = make_user("staff")
    proposal = propose(user, value="central")
    assert "approves" in proposal.message
    assert not Group.objects.filter(name="central").exists()
    request = ApprovalRequest.objects.get()
    assert (request.caller, request.requested_by_id, request.payload) == (
        "agent",
        user.pk,
        {"name": "central"},
    )
    row = AuditLog.objects.get(event="approval.requested")
    assert (row.caller, row.actor_user_id) == ("agent", user.pk)


def test_dry_runs_propose_too(make_user: Callable[..., User]) -> None:
    ctx = build_context(make_user("staff"), caller="agent", dry_run=True)
    assert isinstance(invoke(registry.get("test.assistant.note"), {"name": "d"}, ctx), Proposal)


def test_forbidden_action_is_refused_even_when_permitted(make_user: Callable[..., User]) -> None:
    user = make_user("admin")
    with pytest.raises(PermissionDenied, match="never run by the assistant"):
        invoke(registry.get("test.assistant.banned"), {"name": "b"}, agent_ctx(user))
    assert not ApprovalRequest.objects.exists()
    assert AuditLog.objects.filter(event="action.denied", caller="agent").exists()
    # The same user, acting directly, is unaffected.
    assert run("test.assistant.banned", user, name="b") == NoteOut(name="b")


def test_a_real_prohibition_holds_for_an_admin(make_user: Callable[..., User]) -> None:
    with pytest.raises(PermissionDenied, match="never run by the assistant"):
        invoke(
            registry.get("target.publish"),
            registry.get("target.publish").example or {},
            agent_ctx(make_user("admin")),
        )


def test_assistant_cannot_lack_the_users_permission(make_user: Callable[..., User]) -> None:
    # No elevation (AG-4): a contributor's assistant cannot propose an Admin action.
    with pytest.raises(PermissionDenied):
        invoke(
            registry.get("metric.register"),
            registry.get("metric.register").example or {},
            agent_ctx(make_user("contributor")),
        )
    assert not ApprovalRequest.objects.exists()


# --- confirming, withdrawing and the queue ----------------------------------


def test_requester_confirms_own_proposal(make_user: Callable[..., User]) -> None:
    user = make_user("staff")
    proposal = propose(user, value="mine")
    out = run("platform.approval.confirm", user, approval_request_id=proposal.approval_request_id)
    assert out.status == "approved"  # type: ignore[attr-defined]
    assert Group.objects.filter(name="mine").exists()
    replay = AuditLog.objects.get(action_name="test.assistant.note", event__endswith="invoked")
    assert (replay.caller, replay.actor_user_id) == ("agent", user.pk)
    assert replay.payload["approval_request_id"] == proposal.approval_request_id


def test_maker_checker_class_needs_a_second_person(
    make_user: Callable[..., User], settings: object
) -> None:
    settings.KPIGO_APPROVAL_CLASSES_ENABLED = ["config_change"]  # type: ignore[attr-defined]
    user = make_user("staff")
    proposal = propose(user, "test.assistant.guarded", "guarded")
    with pytest.raises(Conflict, match="second person"):
        run("platform.approval.confirm", user, approval_request_id=proposal.approval_request_id)
    listed = run("platform.approval.list", user).requests  # type: ignore[attr-defined]
    assert [(r.can_confirm, r.can_withdraw, r.can_decide) for r in listed] == [(False, True, False)]
    run(
        "platform.approval.approve",
        make_user("admin"),
        approval_request_id=listed[0].approval_request_id,
    )
    assert Group.objects.filter(name="guarded").exists()


def test_only_assistant_proposals_can_be_confirmed(
    make_user: Callable[..., User], settings: object
) -> None:
    settings.KPIGO_APPROVAL_CLASSES_ENABLED = ["config_change"]  # type: ignore[attr-defined]
    user = make_user("staff")
    proposal = invoke(
        registry.get("test.assistant.guarded"), {"name": "h"}, build_context(user, caller="http")
    )
    assert isinstance(proposal, Proposal)
    with pytest.raises(Conflict, match="second person"):
        run("platform.approval.confirm", user, approval_request_id=proposal.approval_request_id)


def test_someone_elses_request_is_not_found(make_user: Callable[..., User]) -> None:
    proposal = propose(make_user("staff"))
    other = make_user("staff")
    for name in (
        "platform.approval.confirm",
        "platform.approval.withdraw",
        "platform.approval.get",
    ):
        with pytest.raises(NotFound):
            run(name, other, approval_request_id=proposal.approval_request_id)
    with pytest.raises(NotFound):
        run("platform.approval.get", other, approval_request_id="not-a-uuid")


def test_withdraw_runs_nothing(make_user: Callable[..., User]) -> None:
    user = make_user("staff")
    proposal = propose(user, value="gone")
    out = run("platform.approval.withdraw", user, approval_request_id=proposal.approval_request_id)
    assert out.status == "rejected"  # type: ignore[attr-defined]
    assert not Group.objects.filter(name="gone").exists()
    request = ApprovalRequest.objects.get()
    assert request.rejection_reason == "Withdrawn by the requester."
    with pytest.raises(Conflict, match="already rejected"):
        run("platform.approval.confirm", user, approval_request_id=proposal.approval_request_id)


def test_queue_is_the_orgs_for_checkers_and_own_for_everyone_else(
    make_user: Callable[..., User],
) -> None:
    alice, bob, admin = make_user("staff"), make_user("staff"), make_user("admin")
    propose(alice, value="a")
    propose(bob, value="b")

    own = run("platform.approval.list", alice)
    assert own.scope == "own"  # type: ignore[attr-defined]
    assert [r.payload for r in own.requests] == [{"name": "a"}]  # type: ignore[attr-defined]
    assert own.requests[0].can_confirm and not own.requests[0].can_decide  # type: ignore[attr-defined]
    assert own.requests[0].action_summary == ""  # type: ignore[attr-defined]

    queue = run("platform.approval.list", admin)
    assert queue.scope == "org"  # type: ignore[attr-defined]
    assert {r.payload["name"] for r in queue.requests} == {"a", "b"}  # type: ignore[attr-defined]
    assert all(r.can_decide and not r.mine for r in queue.requests)  # type: ignore[attr-defined]
    assert run("platform.approval.list", admin, mine=True).requests == []  # type: ignore[attr-defined]

    one = queue.requests[0]  # type: ignore[attr-defined]
    got = run("platform.approval.get", admin, approval_request_id=one.approval_request_id)
    assert got.payload == one.payload  # type: ignore[attr-defined]
    assert got.requested_by_name  # type: ignore[attr-defined]
