"""Builds an ActionContext from a user. Every adapter goes through here.

The account, roles, permissions and data scope come from ``kpigo.access``
(Schema §1: ``app_user``, ``user_role``, ``role``, ``data_scope_grant``) and are
read on every invocation. Subject scope comes from the visibility closure.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from kpigo.action.context import ActionContext, ApprovalGrant, Caller, SessionBridge
from kpigo.action.errors import NotAuthenticated

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


def build_context(
    user: AbstractBaseUser | AnonymousUser | None,
    *,
    caller: Caller,
    request_id: str | None = None,
    dry_run: bool = False,
    ip_address: str | None = None,
    approval: ApprovalGrant | None = None,
    session: SessionBridge | None = None,
) -> ActionContext:
    if user is None or not user.is_authenticated or not user.is_active:
        raise NotAuthenticated("An active, authenticated user is required.")
    assert not isinstance(user, AnonymousUser)
    from kpigo.access.identity import resolve

    org_id = str(settings.KPIGO_ORG_ID)
    principal = resolve(user, org_id)
    return ActionContext(
        caller=caller,
        user=user,
        org_id=org_id,
        permissions=principal.permissions,
        visible_subjects=principal.subjects,
        data_scopes=principal.data_scopes,
        dry_run=dry_run,
        ip_address=ip_address,
        approval=approval,
        request_id=request_id or uuid.uuid4().hex,
        session=session or SessionBridge(),
    )


def anonymous_context(
    *,
    caller: Caller,
    request_id: str | None = None,
    ip_address: str | None = None,
    session: SessionBridge | None = None,
) -> ActionContext:
    """The context a public action runs in: no user, no permissions, no data."""
    return ActionContext(
        caller=caller,
        user=None,
        org_id=str(settings.KPIGO_ORG_ID),
        permissions=frozenset(),
        ip_address=ip_address,
        request_id=request_id or uuid.uuid4().hex,
        session=session or SessionBridge(),
    )


def resolve_user(username: str) -> AbstractBaseUser:
    """Look up the user a CLI invocation or job runs as."""
    model = get_user_model()
    try:
        return model._default_manager.get(**{model.USERNAME_FIELD: username})
    except model.DoesNotExist:
        raise NotAuthenticated(f"No user '{username}'.") from None
