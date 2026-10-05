"""Single-use, short-lived records of SSO sign-ins in flight."""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.utils import timezone

from kpigo.access.models import AuthFlowState
from kpigo.action.errors import AuthenticationFailed, InvalidInput

FLOW_MINUTES = 10


def safe_next(path: str | None) -> str:
    """Only same-site relative paths: never an open redirect."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


def new_flow(
    org_id: str,
    provider: str,
    *,
    next_path: str | None,
    state: str | None = None,
    nonce: str = "",
    code_verifier: str = "",
) -> AuthFlowState:
    return AuthFlowState.objects.create(
        state=state or secrets.token_urlsafe(32),
        org_id=org_id,
        provider=provider,
        nonce=nonce,
        code_verifier=code_verifier,
        next_path=safe_next(next_path),
        expires_at=timezone.now() + timedelta(minutes=FLOW_MINUTES),
    )


def consume(org_id: str, provider: str, state: str) -> AuthFlowState:
    """Take a flow once. A replayed, expired or unknown state is refused."""
    if not state:
        raise InvalidInput("The sign-in response carries no state.")
    flow = (
        AuthFlowState.objects.select_for_update()
        .filter(org_id=org_id, provider=provider, state=state)
        .first()
    )
    if flow is None or flow.consumed_at is not None:
        raise AuthenticationFailed("This sign-in link is not valid. Start signing in again.")
    flow.consumed_at = timezone.now()
    flow.save(update_fields=["consumed_at"])
    if flow.expires_at <= timezone.now():
        raise AuthenticationFailed("This sign-in took too long. Start signing in again.")
    return flow
