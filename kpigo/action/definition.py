"""The ``@action`` decorator and the metadata it records (TDD §3.1)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, cast

from pydantic import BaseModel

from kpigo.action.context import ActionContext
from kpigo.action.errors import RegistryError

Module = Literal["platform", "scorecards", "agent_performance", "campaign", "executive"]
MODULES: tuple[Module, ...] = (
    "platform",
    "scorecards",
    "agent_performance",
    "campaign",
    "executive",
)

Scope = Literal["subject"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

Handler = Callable[[Any, ActionContext], BaseModel]
F = TypeVar("F", bound=Callable[..., BaseModel])


@dataclass(frozen=True)
class HttpSpec:
    method: HttpMethod
    path: str


@dataclass(frozen=True)
class ActionDefinition:
    name: str
    summary: str
    schema: type[BaseModel]
    output: type[BaseModel]
    handler: Handler
    permission: str
    read_only: bool
    module: Module
    scope: Scope | None = None
    requires_approval: str | None = None
    audit: str | None = None
    idempotency_key: Callable[[Any], str] | None = None
    http: HttpSpec | None = None
    example: dict[str, Any] | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    config_change: bool = False

    @property
    def audit_event(self) -> str:
        return self.audit or f"{self.name}.invoked"

    @property
    def http_spec(self) -> HttpSpec:
        """The declared route, or the default ``/actions/<name>`` one."""
        if self.http is not None:
            return self.http
        return HttpSpec(method="GET" if self.read_only else "POST", path=f"/actions/{self.name}")


def action(
    *,
    name: str,
    schema: type[BaseModel],
    output: type[BaseModel],
    permission: str,
    read_only: bool,
    summary: str = "",
    module: Module = "platform",
    scope: Scope | None = None,
    requires_approval: str | None = None,
    audit: str | None = None,
    idempotency_key: Callable[[Any], str] | None = None,
    http: dict[str, str] | None = None,
    example: dict[str, Any] | None = None,
    tags: tuple[str, ...] = (),
    config_change: bool = False,
) -> Callable[[F], F]:
    """Declare a function as a kpiGo action.

    The function is not callable as an action until it is registered; adapters
    (HTTP, CLI, job) only ever reach it through the pipeline, which applies
    validation, permission, scope, idempotency, approval, audit and tracing.

    ``config_change=True`` declares that a successful run changes configuration
    (metrics, hierarchy, calendar, money settings): the pipeline bumps the org's
    ``config_version`` in the same transaction, so cached figures invalidate.
    """
    if not _NAME_RE.match(name):
        raise RegistryError(
            f"Action name '{name}' must be dotted lower_snake, e.g. 'scorecard.compute'."
        )
    if not permission or not _PERMISSION_RE.match(permission):
        raise RegistryError(f"Action '{name}' must declare a dotted permission string.")
    if module not in MODULES:
        raise RegistryError(f"Action '{name}' declares unknown module '{module}'.")
    if read_only and requires_approval:
        raise RegistryError(f"Read-only action '{name}' cannot require approval.")
    if read_only and config_change:
        raise RegistryError(f"Read-only action '{name}' cannot change configuration.")
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        raise RegistryError(f"Action '{name}' schema must be a Pydantic model.")
    if not (isinstance(output, type) and issubclass(output, BaseModel)):
        raise RegistryError(f"Action '{name}' output must be a Pydantic model.")
    http_spec: HttpSpec | None = None
    if http is not None:
        method = http.get("method", "GET" if read_only else "POST").upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise RegistryError(f"Action '{name}' declares unsupported HTTP method '{method}'.")
        if read_only and method != "GET":
            raise RegistryError(f"Read-only action '{name}' must be served over GET.")
        if not read_only and method == "GET":
            raise RegistryError(f"Mutating action '{name}' cannot be served over GET.")
        path = http.get("path", f"/actions/{name}")
        if not path.startswith("/"):
            raise RegistryError(f"Action '{name}' HTTP path must start with '/'.")
        http_spec = HttpSpec(method=cast(HttpMethod, method), path=path)

    def decorate(fn: F) -> F:
        definition = ActionDefinition(
            name=name,
            summary=summary or (fn.__doc__ or "").strip().split("\n")[0],
            schema=schema,
            output=output,
            handler=cast(Handler, fn),
            permission=permission,
            read_only=read_only,
            module=module,
            scope=scope,
            requires_approval=requires_approval,
            audit=audit,
            idempotency_key=idempotency_key,
            http=http_spec,
            example=example,
            tags=tags,
            config_change=config_change,
        )
        fn.__kpigo_action__ = definition  # type: ignore[attr-defined]
        return fn

    return decorate


def definition_of(fn: Callable[..., Any]) -> ActionDefinition | None:
    found = getattr(fn, "__kpigo_action__", None)
    return found if isinstance(found, ActionDefinition) else None
