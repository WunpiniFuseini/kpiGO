"""What an Admin needs to connect a client's own AI assistant over MCP (R6).

``mcp.status`` reports whether the MCP endpoint is on, where it is, and which tools a
token issued by the caller would offer — the read-only actions the caller's own account
holds the permission for, since a token carries exactly its account's permissions.
Tokens themselves are issued and revoked with the ``apitoken.*`` actions.
"""

from __future__ import annotations

from django.conf import settings
from pydantic import BaseModel

from kpigo.action import ActionContext, action, registry
from kpigo.action.adapters.mcp import PROTOCOL_VERSIONS, mcp_enabled, tools_for

MCP_PATH = "/api/mcp"


class McpStatusIn(BaseModel):
    pass


class McpToolOut(BaseModel):
    name: str
    action: str
    summary: str


class McpStatusOut(BaseModel):
    enabled: bool
    endpoint_path: str
    # The full address when the install's public URL is configured; else the UI joins the
    # path to the address it was opened at.
    endpoint_url: str | None
    protocol_versions: list[str]
    tools: list[McpToolOut]
    message: str


@action(
    name="mcp.status",
    summary="Whether the MCP endpoint is on, its address, and the tools your token would offer.",
    schema=McpStatusIn,
    output=McpStatusOut,
    permission="apitoken.view",
    read_only=True,
    http={"method": "GET", "path": "/mcp/status"},
    example={},
)
def mcp_status(params: McpStatusIn, ctx: ActionContext) -> McpStatusOut:
    enabled = mcp_enabled()
    public = str(getattr(settings, "KPIGO_PUBLIC_URL", "") or "")
    tools = [
        McpToolOut(name=name, action=d.name, summary=d.summary or d.name)
        for name, d in sorted(tools_for(registry, ctx).items())
    ]
    if enabled:
        message = (
            "Point the assistant at this address with an API token issued here. It sees "
            "exactly what you see in kpiGo, through read-only tools, and every call is audited."
        )
    else:
        message = (
            "The MCP endpoint is off for this install (KPIGO_MCP_ENABLED=0). Your infrastructure "
            "team turns it on in the install's settings."
        )
    return McpStatusOut(
        enabled=enabled,
        endpoint_path=MCP_PATH,
        endpoint_url=f"{public}{MCP_PATH}" if public else None,
        protocol_versions=list(PROTOCOL_VERSIONS),
        tools=tools,
        message=message,
    )
