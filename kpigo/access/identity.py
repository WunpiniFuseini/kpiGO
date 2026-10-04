"""Who a signed-in user is to kpiGo: account, roles, permissions and data scope.

``kpigo.action.identity.build_context`` calls ``resolve`` for every invocation,
so a disabled account, a removed role or an ended grant takes effect on the
user's next request, not their next login.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import Q
from django.utils import timezone

from kpigo.access.models import AppUser, DataScopeGrant, Role, RolePageAccess, RolePermission
from kpigo.access.pages import SYSTEM_PAGE_ACCESS, merge
from kpigo.action.context import DataScopeGrant as ScopeGrant
from kpigo.action.context import NoSubjects, SubjectScope
from kpigo.action.errors import NotAuthenticated
from kpigo.action.roles import SYSTEM_ROLES, permissions_for_roles

# Every signed-in user holds these, whatever their roles: enough to see who
# they are and why a page is empty, and to sign out.
BASE_PERMISSIONS = frozenset({"auth.session"})


@dataclass(frozen=True)
class Principal:
    app_user: AppUser
    role_codes: frozenset[str]
    permissions: frozenset[str]
    data_scopes: tuple[ScopeGrant, ...]
    subjects: SubjectScope


def app_user_for(user: Any, org_id: str) -> AppUser | None:
    if user is None or getattr(user, "pk", None) is None:
        return None
    return (
        AppUser.objects.select_related("subject").filter(org_id=org_id, auth_user=user.pk).first()
    )


def resolve(user: Any, org_id: str) -> Principal:
    """The principal for an authenticated Django user. No active account, no access."""
    account = app_user_for(user, org_id)
    if account is None:
        raise NotAuthenticated("This sign-in has no kpiGo account.")
    if account.status != "active":
        raise NotAuthenticated(f"This kpiGo account is {account.status}.")
    codes = role_codes(account)
    return Principal(
        app_user=account,
        role_codes=codes,
        permissions=BASE_PERMISSIONS | role_permissions(org_id, codes),
        data_scopes=grants_for(account, codes),
        subjects=subject_scope(account),
    )


def role_codes(account: AppUser) -> frozenset[str]:
    return frozenset(account.roles.values_list("role_code", flat=True))


def custom_roles(org_id: str, codes: frozenset[str] | set[str]) -> list[Role]:
    custom = [c for c in codes if c not in SYSTEM_ROLES]
    if not custom:
        return []
    return list(Role.objects.filter(org_id=org_id, code__in=custom))


def role_permissions(org_id: str, codes: frozenset[str] | set[str]) -> frozenset[str]:
    granted = set(permissions_for_roles(codes))
    roles = custom_roles(org_id, codes)
    if roles:
        granted |= set(
            RolePermission.objects.filter(role__in=roles).values_list("permission", flat=True)
        )
    return frozenset(granted)


def page_access(org_id: str, codes: frozenset[str] | set[str]) -> dict[str, str]:
    matrices: list[dict[str, str]] = [
        dict(SYSTEM_PAGE_ACCESS.get(code, {})) for code in codes if code in SYSTEM_ROLES
    ]
    roles = custom_roles(org_id, codes)
    if roles:
        for role in roles:
            matrices.append(
                dict(RolePageAccess.objects.filter(role=role).values_list("page_key", "access"))
            )
    return merge(*matrices)


def in_force(as_of: date) -> Q:
    return Q(effective_from__lte=as_of) & (Q(effective_to__isnull=True) | Q(effective_to__gt=as_of))


def grants_for(
    account: AppUser, codes: frozenset[str], as_of: date | None = None
) -> tuple[ScopeGrant, ...]:
    as_of = as_of or timezone.localdate()
    rows = DataScopeGrant.objects.filter(
        Q(app_user=account) | Q(role_code__in=list(codes)), in_force(as_of), org_id=account.org_id
    ).values_list("module", "dimension_type", "member_code")
    return tuple(
        sorted(
            {ScopeGrant(module=m, dimension_type=d, member_code=c) for m, d, c in rows},
            key=lambda g: (g.module, g.dimension_type, g.member_code),
        )
    )


def subject_scope(account: AppUser) -> SubjectScope:
    """The linked subject's visibility closure. No link, no subjects."""
    from kpigo.hierarchy.scope import scope_for_subject

    if account.subject_id is None:
        return NoSubjects()
    return scope_for_subject(str(account.subject_id), str(account.org_id))
