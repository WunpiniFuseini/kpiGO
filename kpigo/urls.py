"""The only URLs are the action API and the MCP adapter. Every route serves a registered action."""

from django.urls import path

from kpigo.action import registry
from kpigo.action.adapters.http import build_api
from kpigo.action.adapters.mcp import build_view as build_mcp_view

api = build_api(registry)
mcp = build_mcp_view(registry)

urlpatterns = [
    # Read-only actions as MCP tools for a client's own assistant (R6); under /api/ so
    # the web front proxies it like the action API.
    path("api/mcp", mcp, name="kpigo-mcp"),
    path("api/v1/", api.urls),
]
