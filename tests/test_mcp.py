"""The MCP surface (R6): read-only registry actions as tools for a client's own assistant.

A client authenticates with a kpiGo API token (the REST read API's, PRD OP-8) and sees
exactly what that account sees in the UI: only read-only actions, only those it holds the
permission for, every call through the same pipeline and audited with ``caller="mcp"``.
"""

import asyncio
import json
import queue
import threading
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from kpigo.access.accounts import hash_token
from kpigo.access.identity import app_user_for
from kpigo.access.models import ApiToken
from kpigo.action import invoke, registry
from kpigo.action.adapters.mcp import PROTOCOL_VERSIONS, tool_definitions
from kpigo.action.identity import build_context
from kpigo.platform.models import AuditLog
from tests.conftest import ORG_ID

pytestmark = pytest.mark.django_db

URL = "/api/mcp"


def issue_token(user: User) -> str:
    ctx = build_context(user, caller="http")
    result: Any = invoke(registry.get("apitoken.issue"), {"name": "assistant"}, ctx)
    return str(result.secret)


def token_for(user: User) -> str:
    """A token row for any account (only an Admin can mint one through the action)."""
    account = app_user_for(user, ORG_ID)
    assert account is not None
    raw = f"kpigo_{user.username}-mcp"
    ApiToken.objects.create(
        org_id=ORG_ID, app_user=account, name="mcp", token_hash=hash_token(raw), prefix=raw[:12]
    )
    return raw


def rpc(
    token: str | None, method: str, params: dict[str, Any] | None = None, **headers: str
) -> Any:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        message["params"] = params
    return post(token, message, **headers)


def post(token: str | None, body: Any, **headers: str) -> Any:
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return Client().post(
        URL,
        data=json.dumps(body),
        content_type="application/json",
        headers={"Accept": "application/json, text/event-stream", **headers},
    )


def tool_names(token: str) -> set[str]:
    response = rpc(token, "tools/list")
    assert response.status_code == 200, response.content
    return {t["name"] for t in response.json()["result"]["tools"]}


def call(token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = rpc(token, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.content
    body: dict[str, Any] = response.json()
    return body


def test_a_token_is_required(make_user: Callable[..., User]) -> None:
    make_user("admin")
    response = rpc(None, "initialize", {"protocolVersion": "2025-06-18"})
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")
    assert rpc("kpigo_unknown", "tools/list").status_code == 401


def test_a_revoked_token_stops_at_once(make_user: Callable[..., User]) -> None:
    token = issue_token(make_user("admin"))
    assert rpc(token, "ping").status_code == 200
    ApiToken.objects.filter(token_hash=hash_token(token)).update(status="revoked")
    assert rpc(token, "ping").status_code == 401


def test_initialize_negotiates_a_version_and_offers_tools(make_user: Callable[..., User]) -> None:
    token = issue_token(make_user("admin"))
    result = rpc(token, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "kpigo"
    # An unknown version is answered with the newest this server speaks.
    other = rpc(token, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert other["protocolVersion"] == PROTOCOL_VERSIONS[0]


def test_notifications_are_accepted_without_a_body(make_user: Callable[..., User]) -> None:
    token = issue_token(make_user("admin"))
    response = post(token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 202
    assert response.content == b""


def test_tools_are_read_only_actions_with_their_own_schemas(
    make_user: Callable[..., User],
) -> None:
    token = issue_token(make_user("admin"))
    tools = {t["name"]: t for t in rpc(token, "tools/list").json()["result"]["tools"]}
    hello = tools["platform_hello"]
    assert hello["inputSchema"] == registry.get("platform.hello").schema.model_json_schema()
    assert hello["annotations"]["readOnlyHint"] is True
    assert "webhook_list" in tools  # an admin read
    # No mutating action is ever a tool, however privileged the account.
    mutating = {d.name.replace(".", "_") for d in registry if not d.read_only}
    assert not mutating & set(tools)
    assert "webhook_register" not in tools and "apitoken_issue" not in tools
    # Public actions (sign-in, setup) are not tools either.
    assert not {d.name.replace(".", "_") for d in registry if d.public} & set(tools)


def test_tools_follow_the_accounts_permissions(make_user: Callable[..., User]) -> None:
    admin_tools = tool_names(issue_token(make_user("admin")))
    staff = make_user("staff")
    staff_token = token_for(staff)
    staff_tools = tool_names(staff_token)
    assert "platform_hello" in staff_tools
    assert "webhook_list" not in staff_tools
    assert staff_tools < admin_tools
    # Calling a tool outside the list is refused as unknown, not run.
    refused = call(staff_token, "webhook_list", {})
    assert refused["error"]["code"] == -32602
    assert not AuditLog.objects.filter(action_name="webhook.list").exists()


def test_a_call_runs_through_the_pipeline_as_the_tokens_account(
    make_user: Callable[..., User],
) -> None:
    admin = make_user("admin")
    token = issue_token(admin)
    result = call(token, "platform_hello", {"name": "assistant"})["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["greeting"] == "Hello, assistant!"
    assert result["structuredContent"]["caller"] == "mcp"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    row = AuditLog.objects.filter(action_name="platform.hello").latest("occurred_at")
    assert row.caller == "mcp"


def test_a_refused_call_is_a_tool_error_the_model_can_read(
    make_user: Callable[..., User],
) -> None:
    token = issue_token(make_user("admin"))
    result = call(token, "platform_hello", {"name": ""})["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "invalid_input"


def test_unknown_methods_and_bad_bodies_are_json_rpc_errors(
    make_user: Callable[..., User],
) -> None:
    token = issue_token(make_user("admin"))
    # A client probing the stateless 2026 revision falls back to initialize on this.
    assert rpc(token, "server/discover").json()["error"]["code"] == -32601
    bad = Client().post(
        URL,
        data="{nope",
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == -32700
    batch = post(
        token,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "nope"},
        ],
    ).json()
    assert [m["id"] for m in batch] == [1, 2]
    assert batch[1]["error"]["code"] == -32601


def test_only_post_is_served(make_user: Callable[..., User]) -> None:
    token = issue_token(make_user("admin"))
    response = Client().get(URL, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 405
    assert response["Allow"] == "POST"


def test_a_foreign_browser_origin_is_refused(make_user: Callable[..., User], settings: Any) -> None:
    token = issue_token(make_user("admin"))
    assert rpc(token, "ping", Origin="https://evil.example").status_code == 403
    assert rpc(token, "ping", Origin="http://testserver").status_code == 200
    settings.KPIGO_MCP_ALLOWED_ORIGINS = ["https://evil.example"]
    assert rpc(token, "ping", Origin="https://evil.example").status_code == 200


def test_the_endpoint_can_be_switched_off(make_user: Callable[..., User], settings: Any) -> None:
    token = issue_token(make_user("admin"))
    settings.KPIGO_MCP_ENABLED = False
    assert rpc(token, "ping").status_code == 404


def test_tool_names_are_unique_and_client_safe() -> None:
    tools = tool_definitions(registry)
    for name in tools:
        # Claude and other clients accept only this alphabet, up to 64 characters.
        assert len(name) <= 64 and name.replace("_", "").replace("-", "").isalnum(), name


def test_the_official_mcp_client_reads_through_kpigo(make_user: Callable[..., User]) -> None:
    """Interop: the reference SDK client connects, lists tools and calls one (R6 exit).

    The client runs its event loop on a worker thread; each HTTP request it makes is handed
    back to this thread and served by Django's test client, inside this test's transaction
    (Django keeps database connections per thread, and apart from any event loop).
    """
    import httpx2
    from mcp import Client as McpClient
    from mcp.client.streamable_http import streamable_http_client

    token = issue_token(make_user("admin"))
    inbox: queue.Queue[Any] = queue.Queue()
    outcome: dict[str, Any] = {}

    async def via_django(request: httpx2.Request) -> httpx2.Response:
        reply: queue.Queue[tuple[int, list[tuple[str, str]], bytes]] = queue.Queue(maxsize=1)
        inbox.put((request, await request.aread(), reply))
        status, headers, content = await asyncio.to_thread(reply.get, timeout=30)
        return httpx2.Response(status, headers=headers, content=content)

    async def session() -> tuple[set[str], Any]:
        http = httpx2.AsyncClient(
            transport=httpx2.MockTransport(via_django),
            headers={"Authorization": f"Bearer {token}"},
        )
        async with (
            http,
            McpClient(
                streamable_http_client(f"http://testserver{URL}", http_client=http)
            ) as client,
        ):
            listed = await client.list_tools()
            result = await client.call_tool("platform_hello", {"name": "sdk"})
            return {t.name for t in listed.tools}, result

    def run_client() -> None:
        try:
            outcome["value"] = asyncio.run(session())
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            inbox.put(None)

    worker = threading.Thread(target=run_client)
    worker.start()
    skip = {"host", "content-length", "content-type"}
    while (item := inbox.get(timeout=60)) is not None:
        request, body, reply = item
        response = Client().generic(
            request.method,
            request.url.path,
            data=body,
            content_type=request.headers.get("content-type", ""),
            headers={k: v for k, v in request.headers.items() if k.lower() not in skip},
        )
        reply.put((response.status_code, list(response.items()), response.content))
    worker.join(timeout=30)

    assert "error" not in outcome, outcome.get("error")
    names, result = outcome["value"]
    assert "platform_hello" in names
    assert result.is_error is False
    assert result.structured_content["greeting"] == "Hello, sdk!"
    assert result.structured_content["caller"] == "mcp"
