"""The HTTP client SSO and directory calls use. Tests swap in an httpx.MockTransport."""

from __future__ import annotations

import httpx

TRANSPORT: httpx.BaseTransport | None = None
TIMEOUT_SECONDS = 10.0


def client() -> httpx.Client:
    return httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_SECONDS)
