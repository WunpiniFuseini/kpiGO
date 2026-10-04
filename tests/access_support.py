"""Shared set-up for the access, SSO, directory and licence tests."""

from __future__ import annotations

import json
from typing import Any

from django.test import Client

PASSWORD = "a long enough pass phrase"


def post(client: Client, path: str, body: dict[str, Any] | None = None) -> Any:
    return client.post(f"/api/v1{path}", json.dumps(body or {}), content_type="application/json")


def get(client: Client, path: str, params: dict[str, Any] | None = None) -> Any:
    return client.get(f"/api/v1{path}", params or {})


def action_path(name: str) -> str:
    return f"/actions/{name}"
