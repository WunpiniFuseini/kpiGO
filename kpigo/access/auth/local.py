"""Email and password sign-in: local accounts (Argon2id) and LDAP binds."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from kpigo.access.accounts import normalise_email
from kpigo.access.auth import ldap
from kpigo.access.models import AppUser
from kpigo.action.errors import AuthenticationFailed

INCORRECT = "Email or password is incorrect."


def authenticate(org_id: str, email: str, password: str) -> AppUser:
    """The account these credentials open, or ``AuthenticationFailed``.

    Every failure counts toward a temporary lockout, and the failure commits
    even though the action fails (``commit_writes``).
    """
    account = (
        AppUser.objects.select_for_update()
        .select_related("auth_user")
        .filter(org_id=org_id, email=normalise_email(email))
        .first()
    )
    allowed = ["ldap"] + (["local"] if getattr(settings, "KPIGO_LOCAL_LOGIN", True) else [])
    if account is None or account.auth_provider not in allowed:
        make_password(password)  # same work as a real check, so timing reveals nothing
        raise AuthenticationFailed(INCORRECT)
    now = timezone.now()
    if account.locked_until is not None and account.locked_until > now:
        raise AuthenticationFailed(
            "Too many failed sign-ins. Try again later, or ask an Admin to unlock you."
        )
    if account.auth_provider == "local":
        valid = account.auth_user.has_usable_password() and account.auth_user.check_password(
            password
        )
    else:
        valid = ldap.bind_as(account.email, password) is not None
    if not valid:
        account.failed_logins += 1
        limit = int(getattr(settings, "KPIGO_LOGIN_MAX_ATTEMPTS", 5))
        if account.failed_logins >= limit:
            minutes = int(getattr(settings, "KPIGO_LOGIN_LOCKOUT_MINUTES", 15))
            account.locked_until = now + timedelta(minutes=minutes)
            account.failed_logins = 0
        account.save(update_fields=["failed_logins", "locked_until"])
        raise AuthenticationFailed(INCORRECT)
    if account.status == "invited" and account.auth_provider == "local":
        raise AuthenticationFailed("Use your invitation link to set a password first.")
    return account
