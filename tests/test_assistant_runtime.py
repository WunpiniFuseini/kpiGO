"""R7 runtime: the tool loop over the registry, conversations, metering and budgets.

A scripted provider stands in for the model, so every test is deterministic and no
model is contacted. What is asserted is kpiGo's side: which actions ran, as whom,
what became a proposal, and what was stored and metered.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.contrib.auth.models import Group, User
from pydantic import BaseModel

from kpigo.action import ActionContext, Conflict, NotFound, action, invoke, registry
from kpigo.action.definition import ActionDefinition
from kpigo.action.identity import build_context
from kpigo.assistant import inference, runtime
from kpigo.assistant.inference import Completion, InferenceError, Message, ToolCall, ToolSpec
from kpigo.assistant.models import AssistantMessage, AssistantUsage
from kpigo.platform.models import ApprovalRequest, AuditLog

pytestmark = pytest.mark.django_db


class NoteIn(BaseModel):
    name: str


class NoteOut(BaseModel):
    name: str
    size: int = 0


@action(
    name="test.helper.note_read",
    summary="Read a note by name.",
    schema=NoteIn,
    output=NoteOut,
    permission="subject.view",
    read_only=True,
)
def note_read(params: NoteIn, ctx: ActionContext) -> NoteOut:
    if params.name == "boom":
        raise RuntimeError("broken")
    return NoteOut(name=params.name, size=len(params.name))


@action(
    name="test.helper.note_write",
    summary="Write a note.",
    schema=NoteIn,
    output=NoteOut,
    permission="subject.view",
    read_only=False,
)
def note_write(params: NoteIn, ctx: ActionContext) -> NoteOut:
    Group.objects.create(name=params.name)
    return NoteOut(name=params.name)


@action(
    name="test.helper.note_banned",
    summary="A note the assistant may never touch.",
    schema=NoteIn,
    output=NoteOut,
    permission="subject.view",
    read_only=False,
    agent_forbidden=True,
)
def note_banned(params: NoteIn, ctx: ActionContext) -> NoteOut:
    return NoteOut(name=params.name)


@pytest.fixture(autouse=True)
def _actions(register: Callable[..., ActionDefinition]) -> None:
    for fn in (note_read, note_write, note_banned):
        register(fn)


class Scripted:
    """A model that answers from a script and records what it was sent."""

    name = "openai_compatible"
    model = "scripted"

    def __init__(self, *answers: Completion | Exception) -> None:
        self.answers = list(answers)
        self.seen: list[list[Message]] = []
        self.tools: list[ToolSpec] = []

    def complete(
        self, messages: list[Message], tools: list[ToolSpec], *, max_tokens: int
    ) -> Completion:
        self.seen.append(list(messages))
        self.tools = tools
        answer = self.answers.pop(0) if self.answers else Completion(text="done", input_tokens=1)
        if isinstance(answer, Exception):
            raise answer
        return answer


def call(name: str, i: int = 0, **arguments: Any) -> Completion:
    return Completion(
        text="",
        tool_calls=(ToolCall(id=f"c{i}", name=name, arguments=arguments),),
        input_tokens=100,
        output_tokens=10,
        stop_reason="tool_use",
    )


def say(text: str) -> Completion:
    return Completion(text=text, input_tokens=50, output_tokens=20)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Scripted]]:
    holder: dict[str, Scripted] = {}

    def use(*answers: Completion | Exception) -> Scripted:
        holder["p"] = Scripted(*answers)
        return holder["p"]

    monkeypatch.setattr(inference, "get_provider", lambda transport=None: holder["p"])
    yield use


def ask(user: User, message: str, **extra: Any) -> Any:
    return invoke(
        registry.get("assistant.ask"),
        {"message": message, **extra},
        build_context(user, caller="ui"),
    )


def run(name: str, user: User, **params: Any) -> Any:
    return invoke(registry.get(name), params, build_context(user, caller="ui"))


# --- the loop -----------------------------------------------------------------


def test_off_without_a_model(make_user: Callable[..., User], settings: Any) -> None:
    settings.KPIGO_INFERENCE_PROVIDER = None
    with pytest.raises(Conflict, match="assistant is off: No model"):
        ask(make_user("staff"), "hello")


def test_reads_run_and_changes_become_proposals(
    make_user: Callable[..., User], model: Callable[..., Scripted]
) -> None:
    user = make_user("staff")
    scripted = model(
        call("find_actions", 1, query="note"),
        call("describe_action", 2, action="test.helper.note_read"),
        call("run_action", 3, action="test.helper.note_read", params={"name": "ama"}),
        call("run_action", 4, action="test.helper.note_write", params={"name": "new-note"}),
        say("Ama's note has 3 characters. I proposed a new note for your approval."),
    )
    out = ask(user, "Tell me about Ama")

    assert out.stopped == "answered"
    assert out.reply.startswith("Ama's note")
    assert [(s.action_name, s.outcome) for s in out.steps] == [
        ("test.helper.note_read", "ok"),
        ("test.helper.note_write", "proposed"),
    ]
    assert out.steps[0].result == {"name": "ama", "size": 3}
    # The change did not happen; it waits for the user as an agent proposal.
    assert not Group.objects.filter(name="new-note").exists()
    request = ApprovalRequest.objects.get()
    assert (request.caller, request.requested_by_id, request.payload) == (
        "agent",
        user.pk,
        {"name": "new-note"},
    )
    assert out.steps[1].approval_request_id == str(request.request_id)

    # Every call ran as the user, labelled as the assistant, and was audited.
    read = AuditLog.objects.get(action_name="test.helper.note_read")
    assert (read.caller, read.actor_user_id) == ("agent", user.pk)
    turn = AuditLog.objects.get(event="assistant.turn")
    assert turn.payload["actions"] == ["test.helper.note_read", "test.helper.note_write"]

    # The model saw the catalogue through three generated tools, and the search found ours.
    assert [t.name for t in scripted.tools] == ["find_actions", "describe_action", "run_action"]
    found = scripted.seen[1][-1]
    assert found.role == "tool" and "test.helper.note_read" in found.content
    described = scripted.seen[2][-1].content
    assert '"example"' in described and '"properties"' in described
    assert "proposal" in scripted.seen[4][-1].content

    # Metered per call; 4 tool rounds and the answer.
    assert AssistantUsage.objects.count() == 5
    assert (out.input_tokens, out.output_tokens) == (450, 60)


def test_catalogue_is_the_users_own_minus_prohibitions(make_user: Callable[..., User]) -> None:
    staff = build_context(make_user("staff"), caller="ui")
    admin = build_context(make_user("admin"), caller="ui")
    staff_names, admin_names = set(runtime.catalogue(staff)), set(runtime.catalogue(admin))
    assert "test.helper.note_read" in staff_names
    assert "test.helper.note_banned" not in admin_names
    assert "target.publish" not in admin_names  # a hard prohibition
    assert "metric.register" in admin_names and "metric.register" not in staff_names
    assert not any(n.startswith("assistant.") for n in admin_names)
    assert staff_names <= admin_names | staff_names


def test_refusals_and_failures_are_told_to_the_model(
    make_user: Callable[..., User], model: Callable[..., Scripted]
) -> None:
    model(
        call("run_action", 1, action="test.helper.note_banned", params={"name": "x"}),
        call("run_action", 2, action="metric.register", params={}),
        call("run_action", 3, action="test.helper.note_read", params={"name": "boom"}),
        call("run_action", 4, action="test.helper.note_read", params="not-an-object"),
        call("describe_action", 5, action="nope"),
        call("mystery_tool", 6),
        say("I could not do those."),
    )
    out = ask(make_user("staff"), "Try things")
    assert [(s.outcome, s.error) for s in out.steps] == [
        ("refused", "'test.helper.note_banned' is never run by the assistant."),
        ("refused", "'metric.register' is not available to you."),
        ("error", "The action failed (RuntimeError)."),
        ("error", "Invalid input for 'test.helper.note_read'."),
    ]
    assert out.stopped == "answered"
    assert not ApprovalRequest.objects.exists()


def test_stops_after_max_steps(
    make_user: Callable[..., User], model: Callable[..., Scripted], settings: Any
) -> None:
    settings.KPIGO_ASSISTANT_MAX_STEPS = 3
    model(*(call("find_actions", i, query="x") for i in range(5)))
    out = ask(make_user("staff"), "loop")
    assert out.stopped == "max_steps"
    assert "3 steps" in out.reply


def test_a_model_failure_ends_the_turn_politely(
    make_user: Callable[..., User], model: Callable[..., Scripted]
) -> None:
    model(InferenceError("The model did not answer in time."))
    out = ask(make_user("staff"), "hello")
    assert out.stopped == "model_error"
    assert out.reply == "The model could not answer: The model did not answer in time."
    assert AssistantMessage.objects.filter(role="assistant", content=out.reply).exists()


# --- conversations ----------------------------------------------------------------


def test_continuing_a_conversation_replays_history(
    make_user: Callable[..., User], model: Callable[..., Scripted]
) -> None:
    user = make_user("staff")
    model(
        call("run_action", 1, action="test.helper.note_read", params={"name": "ama"}), say("First.")
    )
    first = ask(user, "Question one")
    scripted = model(say("Second."))
    second = ask(user, "Question two", conversation_id=str(first.conversation_id))
    assert second.conversation_id == first.conversation_id
    sent = scripted.seen[0]
    assert [m.role for m in sent] == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert sent[1].content == "Question one" and sent[-1].content == "Question two"

    with pytest.raises(NotFound):
        ask(make_user("staff"), "mine now?", conversation_id=str(first.conversation_id))

    listed = run("assistant.conversation.list", user).conversations
    assert [c.title for c in listed] == ["Question one"]
    got = run("assistant.conversation.get", user, conversation_id=str(first.conversation_id))
    roles = [(m.role, m.action_name, m.outcome) for m in got.messages]
    assert roles == [
        ("user", "", None),
        ("assistant", "", None),
        ("tool", "test.helper.note_read", "ok"),
        ("assistant", "", None),
        ("user", "", None),
        ("assistant", "", None),
    ]
    assert got.messages[2].result == {"name": "ama", "size": 3}
    assert got.messages[2].content == ""

    with pytest.raises(NotFound):
        run(
            "assistant.conversation.get",
            make_user("staff"),
            conversation_id=str(first.conversation_id),
        )
    run("assistant.conversation.delete", user, conversation_id=str(first.conversation_id))
    assert run("assistant.conversation.list", user).conversations == []
    assert AuditLog.objects.filter(event="assistant.asked").count() == 2


def test_search_and_schema_lookups_stay_out_of_the_read_back(
    make_user: Callable[..., User], model: Callable[..., Scripted]
) -> None:
    user = make_user("staff")
    model(call("find_actions", 1, query="note"), say("Found it."))
    out = ask(user, "find")
    got = run("assistant.conversation.get", user, conversation_id=str(out.conversation_id))
    assert [m.role for m in got.messages] == ["user", "assistant", "assistant"]


# --- budgets and metering (AG-7) ----------------------------------------------------


def test_budget_refuses_up_front_and_stops_mid_turn(
    make_user: Callable[..., User], model: Callable[..., Scripted]
) -> None:
    admin, staff = make_user("admin"), make_user("staff")
    run("assistant.budget.set", admin, monthly_tokens=200)

    model(call("find_actions", 1, query="a"), call("find_actions", 2, query="b"), say("never"))
    out = ask(staff, "spend")
    # 110 tokens, then 220 ≥ 200: the third call is never made.
    assert out.stopped == "budget"
    assert "monthly" in out.reply
    assert AssistantUsage.objects.count() == 2

    with pytest.raises(Conflict, match="monthly assistant budget"):
        ask(staff, "more")

    usage = run("assistant.usage", admin)
    assert (usage.month_tokens, usage.monthly_limit) == (220, 200)
    assert usage.exhausted
    assert [(u.tokens) for u in usage.by_user] == [220]
    assert run("assistant.usage", staff).by_user is None

    run("assistant.budget.set", admin, monthly_tokens=None, user_daily_tokens=100)
    with pytest.raises(Conflict, match="daily"):
        ask(staff, "again")
    model(say("Fresh."))
    assert ask(admin, "admin has not spent today").reply == "Fresh."


def test_usage_rows_never_hold_text(
    make_user: Callable[..., User], model: Callable[..., Scripted]
) -> None:
    model(say("secret figures 123"))
    ask(make_user("staff"), "secret question")
    row = AssistantUsage.objects.get()
    fields = {f.name for f in AssistantUsage._meta.get_fields()}
    assert not fields & {"content", "text", "prompt", "reply"}
    assert (row.provider, row.model, row.input_tokens, row.output_tokens) == (
        "openai_compatible",
        "scripted",
        50,
        20,
    )
