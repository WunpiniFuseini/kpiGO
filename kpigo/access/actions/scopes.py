"""Data scope grants for Executive and Campaign data (PRD AD-2). No grant means no data."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator

from kpigo.access.accounts import known_role_codes
from kpigo.access.identity import in_force
from kpigo.access.models import AppUser, DataScopeGrant
from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.hierarchy.models import Dimension, DimMember

EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    grant_id: uuid.UUID
    role_code: str | None
    user_id: uuid.UUID | None
    module: str
    dimension_type: str
    member_code: str
    effective_from: date
    effective_to: date | None


def grant_out(g: DataScopeGrant) -> GrantOut:
    return GrantOut(
        grant_id=g.grant_id,
        role_code=g.role_code,
        user_id=g.app_user_id,
        module=g.module,
        dimension_type=g.dimension_type,
        member_code=g.member_code,
        effective_from=g.effective_from,
        effective_to=g.effective_to,
    )


class CreateIn(BaseModel):
    role_code: str | None = Field(default=None, max_length=60)
    user_id: uuid.UUID | None = None
    module: Literal["executive", "campaign"]
    dimension_type: str = Field(min_length=1, max_length=60)
    member_code: str = Field(min_length=1, max_length=100, description="'*' for every member.")
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def _one_holder(self) -> CreateIn:
        if (self.role_code is None) == (self.user_id is None):
            raise ValueError("Grant to a role or to a user: exactly one.")
        if (
            self.effective_to is not None
            and self.effective_from is not None
            and self.effective_to <= self.effective_from
        ):
            raise ValueError("effective_to must be after effective_from")
        return self


@action(
    name="scope.grant.create",
    summary="Grant a role or a user Executive or Campaign data for a dimension member.",
    schema=CreateIn,
    output=GrantOut,
    permission="scope.manage",
    read_only=False,
    requires_approval="access_change",
    audit="scope.granted",
    example={
        "role_code": "executive",
        "module": "executive",
        "dimension_type": "region",
        "member_code": "*",
    },
)
def create(params: CreateIn, ctx: ActionContext) -> GrantOut:
    if not Dimension.objects.filter(
        org_id=ctx.org_id, dimension_type=params.dimension_type
    ).exists():
        raise InvalidInput(f"No dimension '{params.dimension_type}' is defined.")
    if (
        params.member_code != "*"
        and not DimMember.objects.filter(
            org_id=ctx.org_id, dimension_type=params.dimension_type, member_code=params.member_code
        ).exists()
    ):
        raise InvalidInput(f"'{params.member_code}' is not a member of {params.dimension_type}.")
    holder: AppUser | None = None
    if params.user_id is not None:
        holder = AppUser.objects.filter(org_id=ctx.org_id, user_id=params.user_id).first()
        if holder is None:
            raise NotFound("No such user.")
    elif params.role_code not in known_role_codes(ctx.org_id):
        raise InvalidInput(f"Unknown role '{params.role_code}'.")
    start = params.effective_from or timezone.localdate()
    duplicate = DataScopeGrant.objects.filter(
        in_force(start),
        org_id=ctx.org_id,
        role_code=params.role_code,
        app_user=holder,
        module=params.module,
        dimension_type=params.dimension_type,
        member_code=params.member_code,
    )
    if duplicate.exists():
        raise Conflict("This grant already exists.")
    grant = DataScopeGrant.objects.create(
        org_id=ctx.org_id,
        role_code=params.role_code,
        app_user=holder,
        module=params.module,
        dimension_type=params.dimension_type,
        member_code=params.member_code,
        effective_from=start,
        effective_to=params.effective_to,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    return grant_out(grant)


class EndIn(BaseModel):
    grant_id: uuid.UUID
    effective_to: date | None = Field(default=None, description="Defaults to today: ends now.")


@action(
    name="scope.grant.end",
    summary="End a grant. Grants are ended, never deleted, so history stays explainable.",
    schema=EndIn,
    output=GrantOut,
    permission="scope.manage",
    read_only=False,
    requires_approval="access_change",
    audit="scope.ended",
    example={"grant_id": EXAMPLE_ID},
)
def end(params: EndIn, ctx: ActionContext) -> GrantOut:
    grant = (
        DataScopeGrant.objects.select_for_update()
        .filter(org_id=ctx.org_id, grant_id=params.grant_id)
        .first()
    )
    if grant is None:
        raise NotFound("No such grant.")
    when = params.effective_to or timezone.localdate()
    if grant.effective_to is not None and when >= grant.effective_to:
        raise Conflict(f"The grant already ends on {grant.effective_to}.")
    if when <= grant.effective_from:
        raise InvalidInput("A grant must end after it starts.")
    grant.effective_to = when
    grant.updated_by = ctx.user_id
    grant.updated_at = timezone.now()
    grant.save(update_fields=["effective_to", "updated_by", "updated_at"])
    return grant_out(grant)


class ListIn(BaseModel):
    module: Literal["executive", "campaign"] | None = None
    role_code: str | None = None
    user_id: uuid.UUID | None = None
    in_force_only: bool = True


class ListOut(BaseModel):
    grants: list[GrantOut]


@action(
    name="scope.grant.list",
    summary="List data scope grants.",
    schema=ListIn,
    output=ListOut,
    permission="scope.view",
    read_only=True,
    http={"method": "GET", "path": "/scope-grants"},
    example={"module": "executive"},
)
def list_grants(params: ListIn, ctx: ActionContext) -> ListOut:
    query = DataScopeGrant.objects.filter(org_id=ctx.org_id)
    if params.module:
        query = query.filter(module=params.module)
    if params.role_code:
        query = query.filter(role_code=params.role_code)
    if params.user_id:
        query = query.filter(app_user_id=params.user_id)
    if params.in_force_only:
        query = query.filter(in_force(timezone.localdate()))
    rows = query.order_by("module", "dimension_type", "member_code")
    return ListOut(grants=[grant_out(g) for g in rows])
