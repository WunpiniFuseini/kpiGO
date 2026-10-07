"""Bearer-token authentication for the read API (PRD OP-8).

An integration presents ``Authorization: Bearer <token>``. The token is matched by its
SHA-256 hash to an active, unexpired :class:`~kpigo.access.models.ApiToken`; the matching
token is stashed on the request so the HTTP adapter can build a context from its account
and refuse anything that is not read-only. Only the hash is ever compared — the raw token
is never stored. A session cookie, when present, takes precedence (this auth runs second),
so the UI is unaffected.
"""

from __future__ import annotations

import contextlib
from typing import Any

from django.conf import settings
from django.utils import timezone
from ninja.security import HttpBearer

REQUEST_TOKEN_ATTR = "_kpigo_api_token"


def resolve_api_token(raw: str) -> Any | None:
    """The active, unexpired ApiToken matching this raw token, or None."""
    from kpigo.access.accounts import hash_token
    from kpigo.access.models import ApiToken

    token = (
        ApiToken.objects.select_related("app_user")
        .filter(
            org_id=str(settings.KPIGO_ORG_ID),
            token_hash=hash_token(raw),
            status="active",
        )
        .first()
    )
    if token is None:
        return None
    if token.expires_at is not None and token.expires_at <= timezone.now():
        return None
    return token


def touch_token(token: Any) -> None:
    """Best-effort last-used stamp; a failure here must not block the read."""
    with contextlib.suppress(Exception):  # pragma: no cover - bookkeeping only
        type(token).objects.filter(pk=token.pk).update(last_used_at=timezone.now())


class ApiTokenAuth(HttpBearer):
    """Ninja auth: resolve a bearer token and remember it on the request."""

    def authenticate(self, request: Any, token: str) -> Any | None:
        found = resolve_api_token(token)
        if found is None:
            return None
        setattr(request, REQUEST_TOKEN_ATTR, found)
        touch_token(found)
        return found.token_id
