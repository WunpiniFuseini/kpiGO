"""Account lifecycle helpers shared by the user, auth, setup and directory actions."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from kpigo.access.models import AppUser, Role, UserRole
from kpigo.action.errors import Conflict, InvalidInput, NotFound
from kpigo.action.roles import SYSTEM_ROLES
from kpigo.hierarchy.models import Subject


def normalise_email(email: str) -> str:
    return email.strip().lower()


def known_role_codes(org_id: str) -> set[str]:
    return set(SYSTEM_ROLES) | set(
        Role.objects.filter(org_id=org_id).values_list("code", flat=True)
    )


def check_role_codes(org_id: str, codes: Iterable[str]) -> list[str]:
    wanted = sorted(set(codes))
    unknown = sorted(set(wanted) - known_role_codes(org_id))
    if unknown:
        raise InvalidInput(f"Unknown roles: {', '.join(unknown)}.", detail={"unknown": unknown})
    return wanted


def subject_by_id(org_id: str, subject_id: Any) -> Subject:
    found = Subject.objects.filter(org_id=org_id, subject_id=subject_id).first()
    if found is None:
        raise NotFound("No such subject.")
    return found


def subject_for_email(org_id: str, email: str) -> Subject | None:
    """The active subject with this email, if that subject has no account yet."""
    subject = Subject.objects.filter(org_id=org_id, email=email, status="active").first()
    if subject is None or AppUser.objects.filter(org_id=org_id, subject=subject).exists():
        return None
    return subject


def create_account(
    org_id: str,
    *,
    email: str,
    display_name: str,
    auth_provider: str,
    role_codes: Iterable[str],
    subject: Subject | None,
    created_by: int | None,
    status: str = "invited",
    external_id: str | None = None,
) -> AppUser:
    email = normalise_email(email)
    if AppUser.objects.filter(org_id=org_id, email=email).exists():
        raise Conflict("A user with this email already exists.")
    if subject is not None and AppUser.objects.filter(org_id=org_id, subject=subject).exists():
        raise Conflict("That subject is already linked to another user.")
    model = get_user_model()
    if model._default_manager.filter(username=email).exists():
        raise Conflict("A sign-in with this email already exists.")
    user = model._default_manager.create_user(username=email, email=email)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    account = AppUser.objects.create(
        org_id=org_id,
        auth_user=user,
        email=email,
        display_name=display_name,
        subject=subject,
        auth_provider=auth_provider,
        external_id=external_id,
        status=status,
        created_by=created_by,
        updated_by=created_by,
    )
    set_roles(account, role_codes, created_by)
    return account


def set_roles(account: AppUser, codes: Iterable[str], by: int | None) -> list[str]:
    wanted = set(check_role_codes(str(account.org_id), codes))
    current = set(account.roles.values_list("role_code", flat=True))
    UserRole.objects.filter(app_user=account, role_code__in=current - wanted).delete()
    UserRole.objects.bulk_create(
        [
            UserRole(org_id=account.org_id, app_user=account, role_code=code, created_by=by)
            for code in sorted(wanted - current)
        ]
    )
    return sorted(wanted)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_invite(account: AppUser) -> str:
    """A one-time token for a local account to set its password. Only the hash is kept."""
    token = secrets.token_urlsafe(32)
    account.invite_token_hash = hash_token(token)
    days = int(getattr(settings, "KPIGO_INVITE_DAYS", 7))
    account.invite_expires_at = timezone.now() + timedelta(days=days)
    account.save(update_fields=["invite_token_hash", "invite_expires_at"])
    return token
