"""MCP adapter: the registry's read-only actions as Model Context Protocol tools (TDD §13, R6).

A client's own assistant (Claude, Copilot, a local model) connects to ``/api/mcp`` over
the Streamable HTTP transport and authenticates with a kpiGo API token, the same bearer
token the REST read API takes (PRD OP-8). Every tool is a registered action: its input
schema is the action's Pydantic schema, its output schema the action's output model, and
a call goes through :func:`~kpigo.action.pipeline.invoke` with ``caller="mcp"``, so the
permission check, subject scope, licence gate and audit row are the UI's own.

Only read-only actions become tools, and only those the token's account holds the
permission for. Mutating actions are never offered; proposing changes is the agent
runtime's job (R7), behind the maker-checker interception.

The server is stateless: no session id, no server-initiated messages, every request
answered with a single JSON body. That is all a tools-only server needs, and it keeps
the adapter synchronous like the HTTP one, inside Django's request handling.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from kpigo.action.adapters.tokens import resolve_api_token, touch_token
from kpigo.action.context import ActionContext
from kpigo.action.definition import ActionDefinition
from kpigo.action.errors import ActionError, RegistryError
from kpigo.action.identity import build_context_for_account
from kpigo.action.pipeline import invoke
from kpigo.action.registry import Registry

logger = logging.getLogger("kpigo.action.mcp")

SERVER_NAME = "kpigo"
# The handshake-era revisions (``initialize``), newest first. A client asking for one of
# these gets it; any other gets the newest. A client that first probes the stateless
# 2026-07-28 revision (``server/discover``) is told the method is unknown and falls back
# to ``initialize``, as that revision requires.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "kpiGo is the organisation's performance-tracking system. These tools read "
    "scorecards, metrics, targets, hierarchy and dashboards. Every tool runs as the "
    "account that issued the API token: it sees exactly what that person sees in the "
    "kpiGo UI, and every call is audited. Tools are read-only."
)

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def tool_name(definition: ActionDefinition) -> str:
    """``scorecard.compute`` → ``scorecard_compute``: some clients refuse dots in tool names."""
    return definition.name.replace(".", "_")


def exposable(definition: ActionDefinition) -> bool:
    """Whether an action can ever be an MCP tool: read-only and not a public action."""
    return definition.read_only and not definition.public


def tool_definitions(registry: Registry) -> dict[str, ActionDefinition]:
    """Every action that can be a tool, keyed by tool name. Names must not collide."""
    tools: dict[str, ActionDefinition] = {}
    for definition in registry:
        if not exposable(definition):
            continue
        name = tool_name(definition)
        if name in tools:
            raise RegistryError(
                f"Actions '{tools[name].name}' and '{definition.name}' share MCP tool name '{name}'."
            )
        tools[name] = definition
    return tools


def tools_for(registry: Registry, ctx: ActionContext) -> dict[str, ActionDefinition]:
    """The tools this caller may use: exposable actions whose permission it holds."""
    return {
        name: definition
        for name, definition in tool_definitions(registry).items()
        if definition.permission in ctx.permissions
    }


def describe_tool(definition: ActionDefinition) -> dict[str, Any]:
    return {
        "name": tool_name(definition),
        "title": definition.summary or definition.name,
        "description": f"{definition.summary or definition.name} (kpiGo action {definition.name})",
        "inputSchema": definition.schema.model_json_schema(),
        "outputSchema": definition.output.model_json_schema(mode="serialization"),
        "annotations": {
            "title": definition.summary or definition.name,
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_error(message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    text = json.dumps(detail) if detail is not None else message
    return {"content": [{"type": "text", "text": text}], "isError": True}


class McpServer:
    """Answers MCP JSON-RPC messages for one caller. Transport-agnostic."""

    def __init__(self, registry: Registry, ctx: ActionContext) -> None:
        self.registry = registry
        self.ctx = ctx

    def handle(self, message: Any) -> dict[str, Any] | None:
        """One JSON-RPC message in, its response out (None for a notification)."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message.")
        method = message.get("method")
        if "id" not in message:
            # Notifications (initialized, cancelled) and stray responses need no answer.
            return None
        request_id = message["id"]
        if not isinstance(method, str):
            return _error(request_id, INVALID_REQUEST, "Missing method.")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object.")
        handler = self._methods().get(method)
        if handler is None:
            return _error(request_id, METHOD_NOT_FOUND, f"Method '{method}' is not supported.")
        try:
            return _result(request_id, handler(params))
        except _InvalidParams as exc:
            return _error(request_id, INVALID_PARAMS, str(exc))

    def _methods(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {
            "initialize": self._initialize,
            "ping": lambda _params: {},
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
        }

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "kpiGo", "version": _version()},
            "instructions": INSTRUCTIONS,
        }

    def _tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        tools = tools_for(self.registry, self.ctx)
        return {"tools": [describe_tool(d) for _, d in sorted(tools.items())]}

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            raise _InvalidParams("Missing tool name.")
        if not isinstance(arguments, dict):
            raise _InvalidParams("Tool arguments must be an object.")
        definition = tools_for(self.registry, self.ctx).get(name)
        if definition is None:
            raise _InvalidParams(f"Unknown tool '{name}'.")
        try:
            output = invoke(definition, arguments, self.ctx)
        except ActionError as exc:
            # The model sees the refusal and can correct itself, as a UI user would.
            return _tool_error(exc.message, exc.as_dict())
        except Exception:
            logger.exception("MCP tool %s failed", definition.name)
            return _tool_error("kpiGo could not complete this call.")
        structured: dict[str, Any] = output.model_dump(mode="json")
        return {
            "content": [{"type": "text", "text": json.dumps(structured)}],
            "structuredContent": structured,
            "isError": False,
        }


class _InvalidParams(Exception):
    pass


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("kpigo")
    except PackageNotFoundError:  # pragma: no cover - running from a bare checkout
        return "0"


def mcp_enabled() -> bool:
    return bool(getattr(settings, "KPIGO_MCP_ENABLED", True))


def _origin_allowed(request: HttpRequest) -> bool:
    """Refuse cross-site browser requests (DNS rebinding); non-browser clients send none."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    allowed: Iterable[str] = getattr(settings, "KPIGO_MCP_ALLOWED_ORIGINS", ())
    if origin in set(allowed):
        return True
    return urlsplit(origin).netloc == request.get_host()


def _bearer(request: HttpRequest) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _unauthorised(message: str) -> HttpResponse:
    response = JsonResponse({"error": "not_authenticated", "message": message}, status=401)
    response["WWW-Authenticate"] = 'Bearer realm="kpigo"'
    return response


def build_view(registry: Registry) -> Callable[[HttpRequest], HttpResponse]:
    """The Django view serving MCP at one URL. Every tool it offers comes from ``registry``."""

    @csrf_exempt
    def mcp_endpoint(request: HttpRequest) -> HttpResponse:
        if not mcp_enabled():
            return HttpResponse(status=404)
        if not _origin_allowed(request):
            return HttpResponse("Origin not allowed.", status=403)
        if request.method != "POST":
            # Stateless server: no SSE stream to open (GET), no session to end (DELETE).
            response = HttpResponse(status=405)
            response["Allow"] = "POST"
            return response
        raw_token = _bearer(request)
        if raw_token is None:
            return _unauthorised("Send a kpiGo API token as 'Authorization: Bearer <token>'.")
        token = resolve_api_token(raw_token)
        if token is None:
            return _unauthorised("The API token is unknown, revoked or expired.")
        touch_token(token)

        try:
            body = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return JsonResponse(_error(None, PARSE_ERROR, "Body is not valid JSON."), status=400)

        try:
            ctx = build_context_for_account(
                token.app_user,
                caller="mcp",
                request_id=(request.headers.get("X-Request-ID") or "")[:64] or None,
                ip_address=request.META.get("REMOTE_ADDR") or None,
            )
        except ActionError as exc:
            return _unauthorised(exc.message)
        server = McpServer(registry, ctx)

        if isinstance(body, list):
            if not body:
                return JsonResponse(_error(None, INVALID_REQUEST, "Empty batch."), status=400)
            answers = [a for a in (server.handle(m) for m in body) if a is not None]
            if not answers:
                return HttpResponse(status=202)
            return JsonResponse(answers, safe=False)
        answer = server.handle(body)
        if answer is None:
            return HttpResponse(status=202)
        return JsonResponse(answer)

    mcp_endpoint.__kpigo_mcp__ = True  # type: ignore[attr-defined]
    return mcp_endpoint


def is_mcp_view(view: Callable[..., Any]) -> bool:
    return getattr(view, "__kpigo_mcp__", False) is True
