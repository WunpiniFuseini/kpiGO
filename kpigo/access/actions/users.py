"""User lifecycle: invite, update, disable, enable, reassign. Never hard-deleted (PRD AD-4)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints

from kpigo.access.accounts import (
    create_account,
    issue_invite,
    normalise_email,
    set_roles,
    subject_by_id,
    subject_for_email,
)
from kpigo.access.identity import app_user_for
from kpigo.access.me import UserOut, user_out
from kpigo.access.models import AppUser
from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.ingestion.models import Feed
from kpigo.metrics.models import MetricFamily

Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Provider = Literal["local", "ldap", "oidc", "saml"]
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"


def _account(org_id: str, user_id: uuid.UUID, *, lock: bool = False) -> AppUser:
    query = AppUser.objects.select_related("auth_user").filter(org_id=org_id, user_id=user_id)
    found = (query.select_for_update() if lock else query).first()
    if found is None:
        raise NotFound("No such user.")
    return found


def default_provider() -> str:
    return str(getattr(settings, "KPIGO_DEFAULT_AUTH_PROVIDER", "local"))


# ── user.invite ─────────────────────────────────────────────────────────────


class InviteIn(BaseModel):
    email: Email
    display_name: Name
    role_codes: list[str] = Field(default_factory=list, max_length=20)
    subject_id: uuid.UUID | None = Field(
        default=None, description="Link to a measured person; matched by email when omitted."
    )
    auth_provider: Provider | None = Field(
        default=None, description="Defaults to the install's KPIGO_DEFAULT_AUTH_PROVIDER."
    )


class InviteOut(BaseModel):
    user: UserOut
    invite_token: str | None = Field(
        default=None,
        description="Local accounts only: hand this link token to the user. It is shown once.",
    )
    invite_expires_at: str | None = None
    next_step: str


@action(
    name="user.invite",
    summary="Invite a user: identity, roles and an optional link to their subject.",
    schema=InviteIn,
    output=InviteOut,
    permission="user.manage",
    read_only=False,
    requires_approval="access_change",
    audit="user.invited",
    http={"method": "POST", "path": "/users/invite"},
    example={
        "email": "kofi.boateng@bank.example",
        "display_name": "Kofi Boateng",
        "role_codes": ["line_manager"],
    },
)
def invite(params: InviteIn, ctx: ActionContext) -> InviteOut:
    email = normalise_email(params.email)
    subject = (
        subject_by_id(ctx.org_id, params.subject_id)
        if params.subject_id
        else subject_for_email(ctx.org_id, email)
    )
    provider = params.auth_provider or default_provider()
    account = create_account(
        ctx.org_id,
        email=email,
        display_name=params.display_name,
        auth_provider=provider,
        role_codes=params.role_codes,
        subject=subject,
        created_by=ctx.user_id,
    )
    token = None
    if provider == "local":
        token = issue_invite(account)
        step = "Send the user the invitation link; they set a password on first visit."
    else:
        step = f"The user signs in through {provider.upper()}; the first sign-in activates them."
    return InviteOut(
        user=user_out(account),
        invite_token=token,
        invite_expires_at=account.invite_expires_at.isoformat()
        if account.invite_expires_at
        else None,
        next_step=step,
    )


class ResendIn(BaseModel):
    user_id: uuid.UUID


@action(
    name="user.invite.resend",
    summary="Issue a fresh invitation link for a local user who has not accepted yet.",
    schema=ResendIn,
    output=InviteOut,
    permission="user.manage",
    read_only=False,
    audit="user.invite_resent",
    example={"user_id": EXAMPLE_ID},
)
def resend(params: ResendIn, ctx: ActionContext) -> InviteOut:
    account = _account(ctx.org_id, params.user_id, lock=True)
    if account.status != "invited" or account.auth_provider != "local":
        raise Conflict("Only a local user who has not accepted yet can be re-invited.")
    token = issue_invite(account)
    return InviteOut(
        user=user_out(account),
        invite_token=token,
        invite_expires_at=account.invite_expires_at.isoformat()
        if account.invite_expires_at
        else None,
        next_step="Send the user the new invitation link; the previous one no longer works.",
    )


# ── user.update ─────────────────────────────────────────────────────────────


class UserUpdateIn(BaseModel):
    user_id: uuid.UUID
    display_name: Name | None = None
    role_codes: list[str] | None = Field(default=None, max_length=20)
    subject_id: uuid.UUID | None = None
    unlink_subject: bool = False
    auth_provider: Provider | None = None
    unlock: bool = False


@action(
    name="user.update",
    summary="Change a user's name, roles, subject link or sign-in method, or unlock them.",
    schema=UserUpdateIn,
    output=UserOut,
    permission="user.manage",
    read_only=False,
    requires_approval="access_change",
    audit="user.updated",
    example={"user_id": EXAMPLE_ID, "role_codes": ["line_manager", "metric_owner"]},
)
def update(params: UserUpdateIn, ctx: ActionContext) -> UserOut:
    account = _account(ctx.org_id, params.user_id, lock=True)
    if params.subject_id and params.unlink_subject:
        raise InvalidInput("Give a subject to link, or unlink; not both.")
    if params.role_codes is not None:
        _keep_an_admin(ctx, account, params.role_codes)
        set_roles(account, params.role_codes, ctx.user_id)
    if params.display_name is not None:
        account.display_name = params.display_name
    if params.subject_id is not None:
        subject = subject_by_id(ctx.org_id, params.subject_id)
        clash = AppUser.objects.filter(org_id=ctx.org_id, subject=subject).exclude(pk=account.pk)
        if clash.exists():
            raise Conflict("That subject is already linked to another user.")
        account.subject = subject
    if params.unlink_subject:
        account.subject = None
    if params.auth_provider is not None and params.auth_provider != account.auth_provider:
        account.auth_provider = params.auth_provider
        account.external_id = None
        if params.auth_provider != "local":
            account.auth_user.set_unusable_password()
            account.auth_user.save(update_fields=["password"])
    if params.unlock:
        account.failed_logins = 0
        account.locked_until = None
    account.updated_by = ctx.user_id
    account.updated_at = timezone.now()
    account.save()
    return user_out(account)


def _admins(org_id: str) -> int:
    return AppUser.objects.filter(org_id=org_id, status="active", roles__role_code="admin").count()


def _keep_an_admin(ctx: ActionContext, account: AppUser, new_roles: list[str] | None) -> None:
    """Never leave the install without an active Admin: nobody could provision anyone."""
    holds = account.roles.filter(role_code="admin").exists() and account.status == "active"
    keeps = new_roles is not None and "admin" in new_roles
    if holds and not keeps and _admins(ctx.org_id) <= 1:
        raise Conflict("This is the only active Admin. Make someone else an Admin first.")


# ── user.disable / user.enable ──────────────────────────────────────────────


class DisableIn(BaseModel):
    user_id: uuid.UUID
    reason: str = Field(default="", max_length=500)


@action(
    name="user.disable",
    summary="Disable a user. Their sessions stop working; their history is kept.",
    schema=DisableIn,
    output=UserOut,
    permission="user.manage",
    read_only=False,
    requires_approval="access_change",
    audit="user.disabled",
    example={"user_id": EXAMPLE_ID, "reason": "Left the bank"},
)
def disable(params: DisableIn, ctx: ActionContext) -> UserOut:
    account = _account(ctx.org_id, params.user_id, lock=True)
    if account.status == "disabled":
        raise Conflict("This user is already disabled.")
    if account.user_id == getattr(app_user_for(ctx.user, ctx.org_id), "user_id", None):
        raise Conflict("You cannot disable yourself.")
    _keep_an_admin(ctx, account, None)
    now = timezone.now()
    account.status = "disabled"
    account.disabled_at = now
    account.disabled_reason = params.reason
    account.invite_token_hash = None
    account.updated_by = ctx.user_id
    account.updated_at = now
    account.save()
    account.auth_user.is_active = False
    account.auth_user.save(update_fields=["is_active"])
    return user_out(account)


class EnableIn(BaseModel):
    user_id: uuid.UUID


@action(
    name="user.enable",
    summary="Re-enable a disabled user.",
    schema=EnableIn,
    output=UserOut,
    permission="user.manage",
    read_only=False,
    requires_approval="access_change",
    audit="user.enabled",
    example={"user_id": EXAMPLE_ID},
)
def enable(params: EnableIn, ctx: ActionContext) -> UserOut:
    account = _account(ctx.org_id, params.user_id, lock=True)
    if account.status != "disabled":
        raise Conflict("This user is not disabled.")
    # A local account without a password goes back to needing an invitation.
    needs_invite = account.auth_provider == "local" and not account.auth_user.has_usable_password()
    account.status = "invited" if needs_invite else "active"
    account.disabled_at = None
    account.disabled_reason = ""
    account.failed_logins = 0
    account.locked_until = None
    account.updated_by = ctx.user_id
    account.updated_at = timezone.now()
    account.save()
    account.auth_user.is_active = True
    account.auth_user.save(update_fields=["is_active"])
    return user_out(account)


# ── user.reassign ──────────────────────────────────────────────────────────


class ReassignIn(BaseModel):
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID


class ReassignOut(BaseModel):
    feeds: int
    metric_families: int


@action(
    name="user.reassign",
    summary="Move a user's ownerships (feeds, metric families) to another user.",
    schema=ReassignIn,
    output=ReassignOut,
    permission="user.manage",
    read_only=False,
    requires_approval="access_change",
    audit="user.reassigned",
    example={"from_user_id": EXAMPLE_ID, "to_user_id": "00000000-0000-0000-0000-000000000001"},
)
def reassign(params: ReassignIn, ctx: ActionContext) -> ReassignOut:
    if params.from_user_id == params.to_user_id:
        raise InvalidInput("Choose two different users.")
    source = _account(ctx.org_id, params.from_user_id)
    target = _account(ctx.org_id, params.to_user_id)
    if target.status == "disabled":
        raise Conflict("Ownership cannot pass to a disabled user.")
    now = timezone.now()
    feeds = Feed.objects.filter(org_id=ctx.org_id, owner_user_id=source.auth_user.pk).update(
        owner_user_id=target.auth_user.pk, updated_by=ctx.user_id, updated_at=now
    )
    families = MetricFamily.objects.filter(
        org_id=ctx.org_id, owner_user_id=source.auth_user.pk
    ).update(owner_user_id=target.auth_user.pk, updated_by=ctx.user_id, updated_at=now)
    return ReassignOut(feeds=feeds, metric_families=families)


# ── user.list / user.get ───────────────────────────────────────────────────


class UserListIn(BaseModel):
    status: Literal["invited", "active", "disabled"] | None = None
    role_code: str | None = Field(default=None, max_length=60)
    search: str | None = Field(default=None, max_length=100)


class UserListOut(BaseModel):
    users: list[UserOut]


@action(
    name="user.list",
    summary="List users, by status, role or a search on name and email.",
    schema=UserListIn,
    output=UserListOut,
    permission="user.view",
    read_only=True,
    http={"method": "GET", "path": "/users"},
    example={"status": "active"},
)
def list_users(params: UserListIn, ctx: ActionContext) -> UserListOut:
    query = AppUser.objects.filter(org_id=ctx.org_id).prefetch_related("roles")
    if params.status:
        query = query.filter(status=params.status)
    if params.role_code:
        query = query.filter(roles__role_code=params.role_code)
    if params.search:
        query = query.filter(
            Q(email__icontains=params.search) | Q(display_name__icontains=params.search)
        )
    return UserListOut(users=[user_out(a) for a in query.order_by("display_name", "email")])


class UserGetIn(BaseModel):
    user_id: uuid.UUID


@action(
    name="user.get",
    summary="One user.",
    schema=UserGetIn,
    output=UserOut,
    permission="user.view",
    read_only=True,
    example={"user_id": EXAMPLE_ID},
)
def get_user(params: UserGetIn, ctx: ActionContext) -> UserOut:
    return user_out(_account(ctx.org_id, params.user_id))
