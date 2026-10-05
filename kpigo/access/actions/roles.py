"""Roles and the page access matrix (PRD AD-1): system roles are fixed; clone to customise."""

from __future__ import annotations

from typing import Annotated, Literal

from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints

from kpigo.access.models import Role, RolePageAccess, RolePermission, UserRole
from kpigo.access.pages import PAGE_KEYS, SYSTEM_PAGE_ACCESS
from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.action.roles import SYSTEM_ROLES, all_granted_permissions

RoleCode = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{1,59}$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
AccessLevel = Literal["none", "view", "edit"]


class RoleOut(BaseModel):
    code: str
    name: str
    description: str
    is_system: bool
    cloned_from: str | None
    permissions: list[str]
    pages: dict[str, str]
    users: int


def _system_out(code: str, org_id: str) -> RoleOut:
    spec = SYSTEM_ROLES[code]
    return RoleOut(
        code=code,
        name=spec.name,
        description="",
        is_system=True,
        cloned_from=None,
        permissions=sorted(spec.permissions),
        pages={k: SYSTEM_PAGE_ACCESS.get(code, {}).get(k, "none") for k in PAGE_KEYS},
        users=UserRole.objects.filter(org_id=org_id, role_code=code).count(),
    )


def _custom_out(role: Role) -> RoleOut:
    pages = dict(RolePageAccess.objects.filter(role=role).values_list("page_key", "access"))
    return RoleOut(
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=False,
        cloned_from=role.cloned_from,
        permissions=sorted(
            RolePermission.objects.filter(role=role).values_list("permission", flat=True)
        ),
        pages={k: pages.get(k, "none") for k in PAGE_KEYS},
        users=UserRole.objects.filter(org_id=role.org_id, role_code=role.code).count(),
    )


def _custom(org_id: str, code: str, *, lock: bool = False) -> Role:
    query = Role.objects.filter(org_id=org_id, code=code)
    found = (query.select_for_update() if lock else query).first()
    if found is None:
        if code in SYSTEM_ROLES:
            raise Conflict("System roles are fixed. Clone one to change it.")
        raise NotFound("No such role.")
    return found


def role_out(org_id: str, code: str) -> RoleOut:
    return _system_out(code, org_id) if code in SYSTEM_ROLES else _custom_out(_custom(org_id, code))


class RoleListIn(BaseModel):
    pass


class RoleListOut(BaseModel):
    roles: list[RoleOut]
    page_keys: list[str]
    assignable_permissions: list[str]


@action(
    name="role.list",
    summary="Every role with its permissions and page access matrix.",
    schema=RoleListIn,
    output=RoleListOut,
    permission="role.view",
    read_only=True,
    http={"method": "GET", "path": "/roles"},
    example={},
)
def list_roles(params: RoleListIn, ctx: ActionContext) -> RoleListOut:
    roles = [_system_out(code, ctx.org_id) for code in SYSTEM_ROLES]
    roles += [_custom_out(r) for r in Role.objects.filter(org_id=ctx.org_id).order_by("code")]
    return RoleListOut(
        roles=roles,
        page_keys=list(PAGE_KEYS),
        assignable_permissions=sorted(all_granted_permissions()),
    )


class CloneIn(BaseModel):
    source_code: str = Field(min_length=1, max_length=60)
    code: RoleCode
    name: Name
    description: str = Field(default="", max_length=500)


@action(
    name="role.clone",
    summary="Clone a role, its permissions and page access, under a new code.",
    schema=CloneIn,
    output=RoleOut,
    permission="role.manage",
    read_only=False,
    requires_approval="access_change",
    audit="role.cloned",
    example={"source_code": "line_manager", "code": "branch_manager", "name": "Branch Manager"},
)
def clone(params: CloneIn, ctx: ActionContext) -> RoleOut:
    if (
        params.code in SYSTEM_ROLES
        or Role.objects.filter(org_id=ctx.org_id, code=params.code).exists()
    ):
        raise Conflict(f"A role with code '{params.code}' already exists.")
    if params.source_code in SYSTEM_ROLES:
        permissions = set(SYSTEM_ROLES[params.source_code].permissions)
        pages: dict[str, str] = dict(SYSTEM_PAGE_ACCESS.get(params.source_code, {}))
    else:
        source = _custom(ctx.org_id, params.source_code)
        permissions = set(
            RolePermission.objects.filter(role=source).values_list("permission", flat=True)
        )
        pages = dict(RolePageAccess.objects.filter(role=source).values_list("page_key", "access"))
    role = Role.objects.create(
        org_id=ctx.org_id,
        code=params.code,
        name=params.name,
        description=params.description,
        cloned_from=params.source_code,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    _set_permissions(role, permissions, ctx.user_id)
    _set_pages(role, pages, ctx.user_id)
    return _custom_out(role)


def _set_permissions(role: Role, permissions: set[str], by: int | None) -> None:
    unknown = sorted(permissions - all_granted_permissions())
    if unknown:
        raise InvalidInput(
            f"Unknown permissions: {', '.join(unknown)}.", detail={"unknown": unknown}
        )
    RolePermission.objects.filter(role=role).exclude(permission__in=permissions).delete()
    have = set(RolePermission.objects.filter(role=role).values_list("permission", flat=True))
    RolePermission.objects.bulk_create(
        [RolePermission(role=role, permission=p, created_by=by) for p in sorted(permissions - have)]
    )


def _set_pages(role: Role, pages: dict[str, str], by: int | None) -> None:
    unknown = sorted(set(pages) - set(PAGE_KEYS))
    if unknown:
        raise InvalidInput(f"Unknown pages: {', '.join(unknown)}.", detail={"unknown": unknown})
    for key, access in pages.items():
        RolePageAccess.objects.update_or_create(
            role=role,
            page_key=key,
            defaults={"access": access, "updated_by": by, "updated_at": timezone.now()},
            create_defaults={"access": access, "created_by": by, "updated_by": by},
        )


class RoleUpdateIn(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    name: Name | None = None
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = Field(default=None, max_length=500)
    pages: dict[str, AccessLevel] | None = None


@action(
    name="role.update",
    summary="Edit a cloned role: name, permissions, or its page access matrix.",
    schema=RoleUpdateIn,
    output=RoleOut,
    permission="role.manage",
    read_only=False,
    requires_approval="access_change",
    audit="role.updated",
    example={"code": "branch_manager", "pages": {"admin.targets": "view"}},
)
def update(params: RoleUpdateIn, ctx: ActionContext) -> RoleOut:
    role = _custom(ctx.org_id, params.code, lock=True)
    if params.name is not None:
        role.name = params.name
    if params.description is not None:
        role.description = params.description
    if params.permissions is not None:
        _set_permissions(role, set(params.permissions), ctx.user_id)
    if params.pages is not None:
        _set_pages(role, dict(params.pages), ctx.user_id)
    role.updated_by = ctx.user_id
    role.updated_at = timezone.now()
    role.save()
    return _custom_out(role)


class RoleGetIn(BaseModel):
    code: str = Field(min_length=1, max_length=60)


@action(
    name="role.get",
    summary="One role with its permissions and page access.",
    schema=RoleGetIn,
    output=RoleOut,
    permission="role.view",
    read_only=True,
    example={"code": "admin"},
)
def get_role(params: RoleGetIn, ctx: ActionContext) -> RoleOut:
    return role_out(ctx.org_id, params.code)
