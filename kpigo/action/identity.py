"""Builds an ActionContext from a user. Every adapter goes through here.

R0 Workstream A resolves roles from Django groups named after system role codes;
Workstream D replaces this with ``app_user`` / ``role``, behind the same function.
Subject scope comes from the visibility closure (Workstream B).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from kpigo.action.context import ActionContext, ApprovalGrant, Caller, SubjectScope
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
    org_id = str(settings.KPIGO_ORG_ID)
    return ActionContext(
        caller=caller,
        user=user,
        org_id=org_id,
        permissions=permissions_for_roles(role_codes(user)),
        visible_subjects=subject_scope(user, org_id),
        dry_run=dry_run,
        ip_address=ip_address,
        approval=approval,
        request_id=request_id or uuid.uuid4().hex,
    )


def subject_scope(user: AbstractBaseUser, org_id: str) -> SubjectScope:
    """The subjects the user may see this period, from the visibility closure.

    No linked subject, or no closure for the period, is an empty scope: no grant
    means no data.
    """
    from kpigo.hierarchy.scope import scope_for_user

    return scope_for_user(user, org_id)


def resolve_user(username: str) -> AbstractBaseUser:
    """Look up the user a CLI invocation or job runs as."""
    model = get_user_model()
    try:
        return model._default_manager.get(**{model.USERNAME_FIELD: username})
    except model.DoesNotExist:
        raise NotAuthenticated(f"No user '{username}'.") from None
