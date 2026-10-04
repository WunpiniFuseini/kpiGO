"""R0 Workstream A exit criterion.

A ``hello`` action callable from HTTP, CLI and a scheduled job, with an audit
row written automatically and a permission enforced identically on all three.
"""

import io
import json
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client

from kpigo.action import PermissionDenied, registry
from kpigo.action.adapters.jobs import beat_schedule, task_name
from kpigo.celery import app as celery_app
from kpigo.platform.models import AuditLog

pytestmark = pytest.mark.django_db


def via_http(user: User, name: str) -> tuple[str, dict[str, Any]]:
    client = Client()
    client.force_login(user)
    response = client.get("/api/v1/hello", {"name": name}, headers={"X-Request-ID": "req-http"})
    if response.status_code == 403:
        return "denied", response.json()
    assert response.status_code == 200, response.content
    return "ok", response.json()


def via_cli(user: User, name: str) -> tuple[str, dict[str, Any]]:
    out, err = io.StringIO(), io.StringIO()
    try:
        call_command(
            "action",
            "platform.hello",
            "--user",
            user.username,
            "--name",
            name,
            stdout=out,
            stderr=err,
        )
    except SystemExit as exc:
        assert exc.code == 4, err.getvalue()
        return "denied", json.loads(err.getvalue())
    return "ok", json.loads(out.getvalue())


def via_job(user: User, name: str) -> tuple[str, dict[str, Any]]:
    task = celery_app.tasks[task_name("platform.hello")]
    try:
        # Eager mode propagates the task's exception, as a worker would report it.
        return "ok", task.apply(kwargs={"params": {"name": name}, "run_as": user.username}).get()
    except PermissionDenied as exc:
        return "denied", exc.as_dict()


SURFACES: dict[str, Callable[[User, str], tuple[str, dict[str, Any]]]] = {
    "http": via_http,
    "cli": via_cli,
    "job": via_job,
}


@pytest.mark.parametrize("surface", SURFACES)
def test_hello_runs_and_is_audited(surface: str, make_user: Callable[..., User]) -> None:
    user = make_user("staff")
    outcome, body = SURFACES[surface](user, "Wunpini")
    assert outcome == "ok"
    assert body["greeting"] == "Hello, Wunpini!"
    assert body["caller"] == surface
    row = AuditLog.objects.get()
    assert row.action_name == "platform.hello"
    assert row.event == "platform.hello.invoked"
    assert row.caller == surface
    assert row.actor_user_id == user.pk
    assert row.request_id == body["request_id"]
    assert row.payload["params"] == {"name": "Wunpini"}


@pytest.mark.parametrize("surface", SURFACES)
def test_hello_denied_identically(surface: str, make_user: Callable[..., User]) -> None:
    user = make_user()  # no roles
    outcome, body = SURFACES[surface](user, "x")
    assert outcome == "denied"
    assert body == {
        "error": "permission_denied",
        "message": "'platform.hello' requires permission 'platform.hello'.",
    }
    row = AuditLog.objects.get()
    assert (row.event, row.caller, row.actor_user_id) == ("action.denied", surface, user.pk)


def test_http_request_id_header_is_carried_into_audit(make_user: Callable[..., User]) -> None:
    _, body = via_http(make_user("staff"), "x")
    assert body["request_id"] == "req-http"
    assert AuditLog.objects.get().request_id == "req-http"


def test_http_requires_a_session() -> None:
    response = Client().get("/api/v1/hello")
    assert response.status_code == 401
    assert AuditLog.objects.count() == 0


def test_http_invalid_input_uses_the_action_error_shape(make_user: Callable[..., User]) -> None:
    client = Client()
    client.force_login(make_user("staff"))
    response = client.get("/api/v1/hello", {"name": ""})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"


def test_hello_is_schedulable(make_user: Callable[..., User]) -> None:
    user = make_user("staff", username="ops")
    entries = [
        {
            "action": "platform.hello",
            "run_as": "ops",
            "every_seconds": 60,
            "params": {"name": "beat"},
        }
    ]
    schedule = beat_schedule(entries, registry)
    (entry,) = schedule.values()
    assert entry["task"] == "kpigo.action.platform.hello"
    assert entry["schedule"].total_seconds() == 60
    # Run exactly what beat would enqueue.
    result = celery_app.tasks[entry["task"]].apply(kwargs=entry["kwargs"]).get()
    assert result["greeting"] == "Hello, beat!"
    row = AuditLog.objects.get()
    assert (row.caller, row.actor_user_id) == ("job", user.pk)


def test_schedule_naming_an_unknown_action_fails_loudly() -> None:
    with pytest.raises(Exception, match="No action named"):
        beat_schedule([{"action": "platform.nope", "run_as": "x", "every_seconds": 1}], registry)


def test_registry_endpoint_lists_actions_for_admins(make_user: Callable[..., User]) -> None:
    client = Client()
    client.force_login(make_user("admin"))
    body = client.get("/api/v1/registry").json()
    names = {a["name"] for a in body["actions"]}
    assert {"platform.hello", "platform.registry.list"} <= names
    hello = next(a for a in body["actions"] if a["name"] == "platform.hello")
    assert hello["permission"] == "platform.hello"
    assert hello["input_schema"]["properties"]["name"]["maxLength"] == 100


def test_cli_lists_actions() -> None:
    out = io.StringIO()
    call_command("action", "--list", stdout=out)
    assert "platform.hello" in out.getvalue()
