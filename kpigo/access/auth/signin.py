"""Completing a sign-in, whichever provider vouched for the user."""

from __future__ import annotations

from django.utils import timezone

from kpigo.access.accounts import normalise_email
from kpigo.access.models import AppUser
from kpigo.action.context import ActionContext
from kpigo.action.errors import AuthenticationFailed

NO_ACCOUNT = "There is no kpiGo account for this identity. Ask an Admin to invite you."


def sso_account(org_id: str, provider: str, external_id: str, email: str) -> AppUser:
    """The account an SSO identity signs in as.

    Matched on the provider's stable id once known; the first sign-in matches the
    invited email and records that id. kpiGo never creates accounts on sign-in:
    an Admin provisions every user (App Flow §7.5).
    """
    account = (
        AppUser.objects.select_for_update()
        .filter(org_id=org_id, auth_provider=provider, external_id=external_id)
        .first()
    )
    if account is None and email:
        account = (
            AppUser.objects.select_for_update()
            .filter(org_id=org_id, auth_provider=provider, email=normalise_email(email))
            .first()
        )
        if account is not None and account.external_id not in (None, external_id):
            # The email moved to a different directory identity; refuse rather
            # than hand one person's account to another.
            raise AuthenticationFailed(NO_ACCOUNT)
    if account is None:
        raise AuthenticationFailed(NO_ACCOUNT)
    return account


def finish(account: AppUser, ctx: ActionContext, *, external_id: str | None = None) -> AppUser:
    """Activate on first SSO sign-in, record the login and ask for a session."""
    if account.status == "disabled":
        raise AuthenticationFailed("This account is disabled. Ask an Admin.")
    now = timezone.now()
    fields = ["last_login_at", "failed_logins", "locked_until", "updated_at"]
    if account.status == "invited":
        account.status = "active"
        fields.append("status")
    if external_id and account.external_id != external_id:
        account.external_id = external_id
        fields.append("external_id")
    account.last_login_at = now
    account.failed_logins = 0
    account.locked_until = None
    account.updated_at = now
    account.save(update_fields=fields)
    user = account.auth_user
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    ctx.session.login(user)
    ctx.audit("auth.signed_in", user_id=str(account.user_id), provider=account.auth_provider)
    return account
