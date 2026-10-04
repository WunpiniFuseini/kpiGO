"""Registry introspection: the basis of the R6 MCP surface and the permission matrix."""

from typing import Any

from pydantic import BaseModel

from kpigo.action import ActionContext, action
from kpigo.action import registry as action_registry


class RegistryListIn(BaseModel):
    module: str | None = None


class ActionInfo(BaseModel):
    name: str
    summary: str
    module: str
    permission: str
    read_only: bool
    scope: str | None
    requires_approval: str | None
    config_change: bool
    http_method: str
    http_path: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class RegistryListOut(BaseModel):
    actions: list[ActionInfo]
    unlicensed: list[str]


@action(
    name="platform.registry.list",
    summary="List every registered action with its schemas and permission.",
    schema=RegistryListIn,
    output=RegistryListOut,
    permission="platform.registry.view",
    read_only=True,
    http={"method": "GET", "path": "/registry"},
    example={},
)
def list_actions(params: RegistryListIn, ctx: ActionContext) -> RegistryListOut:
    infos = [
        ActionInfo(
            name=d.name,
            summary=d.summary,
            module=d.module,
            permission=d.permission,
            read_only=d.read_only,
            scope=d.scope,
            requires_approval=d.requires_approval,
            config_change=d.config_change,
            http_method=d.http_spec.method,
            http_path=d.http_spec.path,
            input_schema=d.schema.model_json_schema(),
            output_schema=d.output.model_json_schema(),
        )
        for d in action_registry
        if params.module is None or d.module == params.module
    ]
    return RegistryListOut(actions=infos, unlicensed=action_registry.skipped)
