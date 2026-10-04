"""Builds an ActionContext from a user. Every adapter goes through here.

R0 Workstream A resolves roles from Django groups named after system role codes;
Workstream D replaces this with ``app_user`` / ``role`` and the visibility
closure, behind the same function.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from kpigo.action.context import ActionContext, ApprovalGrant, Caller, NoSubjects
from kpigo.action.errors import NotAuthenticated
from kpigo.action.roles import permissions_for_roles

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


def role_codes(user: AbstractBaseUser) -> frozenset[str]:
    groups = getattr(user, "groups", None)
    if groups is None:
        return frozenset()
    return frozenset(str(name) for name in groups.values_list("name", flat=True))


def build_context(
    user: AbstractBaseUser | AnonymousUser | None,
    *,
    caller: Caller,
    request_id: str | None = None,
    dry_run: bool = False,
    ip_address: str | None = None,
    approval: ApprovalGrant | None = None,
) -> ActionContext:
    if user is None or not user.is_authenticated or not user.is_active:
        raise NotAuthenticated("An active, authenticated user is required.")
    assert not isinstance(user, AnonymousUser)
    return ActionContext(
        caller=caller,
        user=user,
        org_id=str(settings.KPIGO_ORG_ID),
        permissions=permissions_for_roles(role_codes(user)),
        # Deny-all until the visibility closure exists (Workstream B).
        visible_subjects=NoSubjects(),
        dry_run=dry_run,
        ip_address=ip_address,
        approval=approval,
        request_id=request_id or uuid.uuid4().hex,
    )


def resolve_user(username: str) -> AbstractBaseUser:
    """Look up the user a CLI invocation or job runs as."""
    model = get_user_model()
    try:
        return model._default_manager.get(**{model.USERNAME_FIELD: username})
    except model.DoesNotExist:
        raise NotAuthenticated(f"No user '{username}'.") from None
