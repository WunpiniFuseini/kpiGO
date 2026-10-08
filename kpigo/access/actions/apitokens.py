"""API-token actions (PRD OP-8): issue, list and revoke the bearer tokens integrations
use to reach the read API.

A token is bound to the caller's own account, so it carries exactly that caller's
permissions and visibility — never more — and reaches read-only actions only (the HTTP
adapter refuses a write before the action runs). Issuing and revoking are mutating, so a
read token can never mint or revoke a token: that always takes a signed-in Admin. Only the
SHA-256 hash is stored; the raw token is returned once at issue and is never recoverable.
"""

from __future__ import annotations

import secrets

from pydantic import BaseModel, Field

from kpigo.access.accounts import hash_token
from kpigo.access.identity import app_user_for
from kpigo.access.models import ApiToken, AppUser
from kpigo.action import ActionContext, action
from kpigo.action.errors import InvalidInput, NotFound

# A recognisable, non-secret leader so an integration (and a reviewer) can tell a kpiGo
# token apart from any other bearer string.
TOKEN_PREFIX = "kpigo_"


class ApiTokenOut(BaseModel):
    name: str
    prefix: str
    status: str
    expires_at: str | None
    last_used_at: str | None
    created_at: str | None

    @classmethod
    def of(cls, t: ApiToken) -> ApiTokenOut:
        return cls(
            name=t.name,
            prefix=t.prefix,
            status=t.status,
            expires_at=t.expires_at.isoformat() if t.expires_at else None,
            last_used_at=t.last_used_at.isoformat() if t.last_used_at else None,
            created_at=t.created_at.isoformat() if t.created_at else None,
        )


def _caller_account(ctx: ActionContext) -> AppUser:
    account = app_user_for(ctx.user, ctx.org_id)
    if account is None:
        raise InvalidInput("Only a signed-in kpiGo account can hold API tokens.")
    return account


class ApiTokenIssueIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # Optional lifetime in days; omitted means the token does not expire.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiTokenIssueOut(BaseModel):
    token: ApiTokenOut
    # The raw bearer token, shown this once and never again.
    secret: str
    message: str


@action(
    name="apitoken.issue",
    agent_forbidden=True,
    summary="Issue a read-API bearer token bound to your account; returns the token once.",
    schema=ApiTokenIssueIn,
    output=ApiTokenIssueOut,
    permission="apitoken.manage",
    read_only=False,
    audit="apitoken.issued",
    http={"method": "POST", "path": "/api-tokens"},
    example={"name": "reporting-etl", "expires_in_days": 365},
)
def issue(params: ApiTokenIssueIn, ctx: ActionContext) -> ApiTokenIssueOut:
    from datetime import timedelta

    from django.utils import timezone

    from kpigo.platform.db import conflicts

    account = _caller_account(ctx)
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = None
    if params.expires_in_days is not None:
        expires_at = timezone.now() + timedelta(days=params.expires_in_days)
    with conflicts({"api_token_name_unique": f"A token named '{params.name}' exists."}):
        token = ApiToken.objects.create(
            org_id=ctx.org_id,
            app_user=account,
            name=params.name,
            token_hash=hash_token(raw),
            prefix=raw[: len(TOKEN_PREFIX) + 6],
            expires_at=expires_at,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    ctx.audit("apitoken.issued", name=token.name, prefix=token.prefix)
    return ApiTokenIssueOut(
        token=ApiTokenOut.of(token),
        secret=raw,
        message=(
            "Store this token now; it is not shown again. Send it as "
            "'Authorization: Bearer <token>'. It reaches read-only actions only and carries "
            "your own permissions and visibility."
        ),
    )


class ApiTokenListIn(BaseModel):
    pass


class ApiTokenListOut(BaseModel):
    tokens: list[ApiTokenOut]


@action(
    name="apitoken.list",
    summary="The API tokens you have issued.",
    schema=ApiTokenListIn,
    output=ApiTokenListOut,
    permission="apitoken.view",
    read_only=True,
    http={"method": "GET", "path": "/api-tokens"},
    example={},
)
def list_tokens(params: ApiTokenListIn, ctx: ActionContext) -> ApiTokenListOut:
    account = _caller_account(ctx)
    rows = ApiToken.objects.filter(org_id=ctx.org_id, app_user=account).order_by("name")
    return ApiTokenListOut(tokens=[ApiTokenOut.of(t) for t in rows])


class ApiTokenRevokeIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ApiTokenRevokeOut(BaseModel):
    name: str
    message: str


@action(
    name="apitoken.revoke",
    agent_forbidden=True,
    summary="Revoke one of your API tokens; it stops working at once.",
    schema=ApiTokenRevokeIn,
    output=ApiTokenRevokeOut,
    permission="apitoken.manage",
    read_only=False,
    audit="apitoken.revoked",
    http={"method": "POST", "path": "/api-tokens/revoke"},
    example={"name": "reporting-etl"},
)
def revoke(params: ApiTokenRevokeIn, ctx: ActionContext) -> ApiTokenRevokeOut:
    account = _caller_account(ctx)
    token = ApiToken.objects.filter(org_id=ctx.org_id, app_user=account, name=params.name).first()
    if token is None:
        raise NotFound(f"No API token named '{params.name}'.")
    # Revoke rather than delete, so the row remains auditable; resolution refuses it
    # at once because only an 'active' token resolves.
    ApiToken.objects.filter(pk=token.pk).update(status="revoked")
    ctx.audit("apitoken.revoked", name=params.name)
    return ApiTokenRevokeOut(name=params.name, message=f"Revoked API token '{params.name}'.")
