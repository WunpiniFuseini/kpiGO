"""Dimensions and their members (Schema §5: dim_dimension, dim_member)."""

from __future__ import annotations

from typing import Annotated, Literal

from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from kpigo.action import ActionContext, InvalidInput, NotFound, action
from kpigo.hierarchy.models import DIMENSION_TYPE_PATTERN, Dimension, DimMember
from kpigo.platform.vocab import Code

DimensionType = Annotated[str, StringConstraints(pattern=DIMENSION_TYPE_PATTERN, max_length=40)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class DimensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dimension_type: str
    display_name: str
    is_custom: bool
    sort_order: int


class DimensionDefineIn(BaseModel):
    dimension_type: DimensionType
    display_name: Name
    is_custom: bool = False
    sort_order: int = 0


@action(
    name="dimension.define",
    summary="Define a dimension (branch, region, segment, or a custom one), or rename it.",
    schema=DimensionDefineIn,
    output=DimensionOut,
    permission="dimension.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="dimension.defined",
    config_change=True,
    example={"dimension_type": "region", "display_name": "Region"},
)
def define(params: DimensionDefineIn, ctx: ActionContext) -> DimensionOut:
    dimension, _ = Dimension.objects.update_or_create(
        org_id=ctx.org_id,
        dimension_type=params.dimension_type,
        defaults={
            "display_name": params.display_name,
            "is_custom": params.is_custom,
            "sort_order": params.sort_order,
            "updated_by": ctx.user_id,
            "updated_at": timezone.now(),
        },
        create_defaults={
            "display_name": params.display_name,
            "is_custom": params.is_custom,
            "sort_order": params.sort_order,
            "created_by": ctx.user_id,
            "updated_by": ctx.user_id,
        },
    )
    return DimensionOut.model_validate(dimension)


class DimensionListIn(BaseModel):
    pass


class DimensionListOut(BaseModel):
    dimensions: list[DimensionOut]


@action(
    name="dimension.list",
    summary="The org's dimensions.",
    schema=DimensionListIn,
    output=DimensionListOut,
    permission="dimension.view",
    read_only=True,
    example={},
)
def list_dimensions(params: DimensionListIn, ctx: ActionContext) -> DimensionListOut:
    rows = Dimension.objects.filter(org_id=ctx.org_id).order_by("sort_order", "dimension_type")
    return DimensionListOut(dimensions=[DimensionOut.model_validate(d) for d in rows])


class MemberIn(BaseModel):
    member_code: Code
    member_name: Name
    parent_code: Code | None = None
    sort_order: int = 0
    status: Literal["active", "inactive"] = "active"


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dimension_type: str
    member_code: str
    member_name: str
    parent_code: str | None
    sort_order: int
    status: str


class MembersUpsertIn(BaseModel):
    dimension_type: DimensionType
    members: list[MemberIn] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def _distinct(self) -> MembersUpsertIn:
        codes = [m.member_code for m in self.members]
        if len(set(codes)) != len(codes):
            raise ValueError("member codes must not repeat")
        return self


class MembersOut(BaseModel):
    dimension_type: str
    created: int
    updated: int


def _parent_cycle(parents: dict[str, str | None]) -> list[str] | None:
    for start in parents:
        seen: list[str] = []
        node: str | None = start
        while node is not None and node in parents:
            if node in seen:
                return [*seen[seen.index(node) :], node]
            seen.append(node)
            node = parents[node]
    return None


@action(
    name="dimension.member.upsert",
    summary="Add or update a dimension's members. Parents must exist and never form a loop.",
    schema=MembersUpsertIn,
    output=MembersOut,
    permission="dimension.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="dimension.members_upserted",
    config_change=True,
    example={
        "dimension_type": "region",
        "members": [{"member_code": "GA", "member_name": "Greater Accra"}],
    },
)
def upsert_members(params: MembersUpsertIn, ctx: ActionContext) -> MembersOut:
    if not Dimension.objects.filter(
        org_id=ctx.org_id, dimension_type=params.dimension_type
    ).exists():
        raise NotFound(f"No dimension '{params.dimension_type}'; define it first.")
    existing = {
        m.member_code: m
        for m in DimMember.objects.select_for_update().filter(
            org_id=ctx.org_id, dimension_type=params.dimension_type
        )
    }
    parents: dict[str, str | None] = {code: m.parent_code for code, m in existing.items()}
    parents.update({m.member_code: m.parent_code for m in params.members})
    missing = sorted({p for p in parents.values() if p is not None and p not in parents})
    if missing:
        raise InvalidInput(
            "Parent codes are not members of this dimension.", detail={"missing": missing}
        )
    cycle = _parent_cycle(parents)
    if cycle is not None:
        raise InvalidInput("Member parents form a loop.", detail={"cycle": cycle})

    now = timezone.now()
    created = updated = 0
    for member in params.members:
        row = existing.get(member.member_code)
        if row is None:
            DimMember.objects.create(
                org_id=ctx.org_id,
                dimension_type=params.dimension_type,
                **member.model_dump(),
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
            created += 1
            continue
        for field, value in member.model_dump().items():
            setattr(row, field, value)
        row.updated_by = ctx.user_id
        row.updated_at = now
        row.save()
        updated += 1
    return MembersOut(dimension_type=params.dimension_type, created=created, updated=updated)


class MemberListIn(BaseModel):
    dimension_type: DimensionType
    status: Literal["available", "active", "inactive"] | None = None


class MemberListOut(BaseModel):
    dimension_type: str
    members: list[MemberOut]


@action(
    name="dimension.member.list",
    summary="A dimension's members.",
    schema=MemberListIn,
    output=MemberListOut,
    permission="dimension.view",
    read_only=True,
    example={"dimension_type": "region"},
)
def list_members(params: MemberListIn, ctx: ActionContext) -> MemberListOut:
    rows = DimMember.objects.filter(org_id=ctx.org_id, dimension_type=params.dimension_type)
    if params.status is not None:
        rows = rows.filter(status=params.status)
    return MemberListOut(
        dimension_type=params.dimension_type,
        members=[MemberOut.model_validate(m) for m in rows.order_by("sort_order", "member_code")],
    )
