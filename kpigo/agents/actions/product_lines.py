"""The product-line registry as actions: groups, the handshake, moves, retirement.

Lines are never typed in (Scope §8.5): ingestion registers a code it sees with
data as ``available``, and an Admin names it, groups it and activates it here,
from quick settings on the Agent Performance page or the full settings surface.
Every change applies to everyone, bumps ``config_version`` and is audited.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints, model_validator

from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.agents import lines as reg
from kpigo.hierarchy.models import ProductGroup, ProductLine, ProductLineGroup
from kpigo.ingestion.reference import org_today
from kpigo.platform.vocab import Code

Module = Literal["agent_performance", "agent_sales", "agent_service"]
Name = Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]
LineCode = Annotated[str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)]


# ── reads ────────────────────────────────────────────────────────────────────


class ProductGroupOut(BaseModel):
    code: str
    display_name: str
    sort_order: int
    module: str
    status: str
    # Lines in this group on the day asked about.
    line_codes: list[str]


class RegistryLineOut(BaseModel):
    line_id: uuid.UUID
    code: str
    display_name: str
    sort_order: int
    module: str
    # available: detected in a feed, awaiting an Admin. active, or retired.
    status: str
    # The group on the day asked about (the registry), or the latest one (a change);
    # null while available.
    group_code: str | None
    first_detected_at: datetime | None
    effective_from: date
    effective_to: date | None
    # Per-line RAG on % achieved; null takes the module's thresholds.
    rag_green: Decimal | None
    rag_amber: Decimal | None


class ProductLineRegistryIn(BaseModel):
    as_of: date | None = None


class ProductLineRegistryOut(BaseModel):
    as_of: date
    groups: list[ProductGroupOut]
    # In the matrix on the day: active, or retired after it.
    in_matrix: list[RegistryLineOut]
    # Detected in a feed and waiting to be named, grouped and switched on.
    available: list[RegistryLineOut]
    retired: list[RegistryLineOut]
    # Past eight lines in the matrix, the grouped view is the readable one.
    suggest_grouped: bool


def _line_out(line: ProductLine, group_code: str | None) -> RegistryLineOut:
    return RegistryLineOut(
        line_id=line.line_id,
        code=line.code,
        display_name=line.display_name,
        sort_order=line.sort_order,
        module=line.module,
        status=line.status,
        group_code=group_code,
        first_detected_at=line.first_detected_at,
        effective_from=line.effective_from,
        effective_to=line.effective_to,
        rag_green=line.rag_green,
        rag_amber=line.rag_amber,
    )


@action(
    name="product_line.registry",
    summary="Product groups and lines on a day: in the matrix, available from the feed, retired.",
    schema=ProductLineRegistryIn,
    output=ProductLineRegistryOut,
    permission="dimension.view",
    read_only=True,
    module="agent_performance",
    example={},
)
def registry(params: ProductLineRegistryIn, ctx: ActionContext) -> ProductLineRegistryOut:
    day = params.as_of or org_today(ctx.org_id)
    grouped = dict(
        ProductLineGroup.objects.filter(line__org_id=ctx.org_id)
        .filter(reg.in_force(day))
        .values_list("line_id", "group__code")
    )
    lines = list(ProductLine.objects.filter(org_id=ctx.org_id).order_by("sort_order", "code"))
    by_group: dict[str, list[str]] = {}
    in_matrix: list[RegistryLineOut] = []
    available: list[RegistryLineOut] = []
    retired: list[RegistryLineOut] = []
    for line in lines:
        group_code = grouped.get(line.line_id)
        if line.status == "available":
            available.append(_line_out(line, None))
            continue
        showing = line.effective_from <= day and (
            line.effective_to is None or line.effective_to > day
        )
        if showing and group_code is not None:
            in_matrix.append(_line_out(line, group_code))
            by_group.setdefault(group_code, []).append(line.code)
        elif line.status == "retired":
            retired.append(_line_out(line, group_code))
    available.sort(key=lambda x: (x.first_detected_at is None, x.first_detected_at, x.code))
    groups = [
        ProductGroupOut(
            code=g.code,
            display_name=g.display_name,
            sort_order=g.sort_order,
            module=g.module,
            status=g.status,
            line_codes=by_group.get(g.code, []),
        )
        for g in ProductGroup.objects.filter(org_id=ctx.org_id).order_by("sort_order", "code")
    ]
    return ProductLineRegistryOut(
        as_of=day,
        groups=groups,
        in_matrix=in_matrix,
        available=available,
        retired=retired,
        suggest_grouped=len(in_matrix) > reg.GROUPING_SUGGESTED_ABOVE,
    )


# ── groups ───────────────────────────────────────────────────────────────────


class ProductGroupSetIn(BaseModel):
    code: Code
    display_name: Name
    sort_order: int = Field(default=0, ge=0, le=100_000)
    module: Module = "agent_performance"
    status: Literal["active", "retired"] = "active"


@action(
    name="product_group.set",
    summary="Create or change a product group, the column group lines collapse into.",
    schema=ProductGroupSetIn,
    output=ProductGroupOut,
    permission="product_line.manage",
    read_only=False,
    module="agent_performance",
    requires_approval="config_change",
    audit="product_group.set",
    config_change=True,
    example={"code": "lending", "display_name": "Lending", "sort_order": 10},
)
def set_group(params: ProductGroupSetIn, ctx: ActionContext) -> ProductGroupOut:
    group, created = ProductGroup.objects.select_for_update().get_or_create(
        org_id=ctx.org_id,
        code=params.code,
        defaults={
            "display_name": params.display_name,
            "sort_order": params.sort_order,
            "module": params.module,
            "status": params.status,
            "created_by": ctx.user_id,
            "updated_by": ctx.user_id,
        },
    )
    day = org_today(ctx.org_id)
    members = list(
        ProductLineGroup.objects.filter(group=group)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=day))
        .values_list("line__code", flat=True)
    )
    if params.status == "retired" and members:
        raise Conflict(
            f"'{params.code}' still has lines: {', '.join(sorted(members))}. "
            "Move or retire them first.",
            detail={"line_codes": sorted(members)},
        )
    if not created:
        group.display_name = params.display_name
        group.sort_order = params.sort_order
        group.module = params.module
        group.status = params.status
        group.updated_by = ctx.user_id
        group.updated_at = timezone.now()
        group.save()
    current = list(
        ProductLineGroup.objects.filter(group=group)
        .filter(reg.in_force(day))
        .values_list("line__code", flat=True)
    )
    return ProductGroupOut(
        code=group.code,
        display_name=group.display_name,
        sort_order=group.sort_order,
        module=group.module,
        status=group.status,
        line_codes=sorted(current),
    )


# ── the handshake ────────────────────────────────────────────────────────────


def _line(ctx: ActionContext, code: str) -> ProductLine:
    line = ProductLine.objects.select_for_update().filter(org_id=ctx.org_id, code=code).first()
    if line is None:
        raise NotFound(
            f"No product line '{code}'. Lines come from the actuals feed: once a load carries "
            "data for a new product_line_code, it appears here as available."
        )
    return line


def _group(ctx: ActionContext, code: str | None, module: str) -> ProductGroup:
    if code is None:
        group = reg.default_group(ctx.org_id, module)
        if group is not None:
            return group
        if ProductGroup.objects.filter(org_id=ctx.org_id, status="active").exists():
            raise InvalidInput(
                "Name the group this line belongs to.", detail={"field": "group_code"}
            )
        # A client with no meaningful grouping gets one group and never notices it.
        group, _ = ProductGroup.objects.get_or_create(
            org_id=ctx.org_id,
            code="products",
            defaults={
                "display_name": "Products",
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
            },
        )
        return group
    group = ProductGroup.objects.filter(org_id=ctx.org_id, code=code).first()
    if group is None:
        raise NotFound(f"No product group '{code}'.")
    if group.status != "active":
        raise Conflict(f"'{code}' is retired; it takes no lines.")
    if group.module not in ("agent_performance", module) and module != "agent_performance":
        raise InvalidInput(f"'{code}' belongs to {group.module}, this line to {module}.")
    return group


class LineActivateIn(BaseModel):
    code: LineCode
    display_name: Name
    # Defaults to the only active group, or creates "Products" when there is none.
    group_code: Code | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    module: Module = "agent_performance"
    # From when it shows; defaults to the first day the feed carried it.
    effective_from: date | None = None


@action(
    name="product_line.activate",
    summary="Name, group and switch on a product line the feed made available.",
    schema=LineActivateIn,
    output=RegistryLineOut,
    permission="product_line.manage",
    read_only=False,
    module="agent_performance",
    requires_approval="config_change",
    audit="product_line.activated",
    config_change=True,
    example={"code": "CARDS", "display_name": "Cards", "group_code": "lending"},
)
def activate(params: LineActivateIn, ctx: ActionContext) -> RegistryLineOut:
    line = _line(ctx, params.code)
    if line.status != "available":
        raise Conflict(f"'{params.code}' is already {line.status}.")
    group = _group(ctx, params.group_code, params.module)
    start = params.effective_from or line.effective_from
    sort_order = params.sort_order
    if sort_order is None:
        top = (
            ProductLine.objects.filter(org_id=ctx.org_id)
            .exclude(status="available")
            .order_by("-sort_order")
            .values_list("sort_order", flat=True)
            .first()
        )
        sort_order = (top or 0) + 10
    line.display_name = params.display_name
    line.group = group
    line.module = params.module
    line.sort_order = sort_order
    line.status = "active"
    line.effective_from = start
    line.updated_by = ctx.user_id
    line.updated_at = timezone.now()
    line.save()
    ProductLineGroup.objects.create(
        line=line,
        group=group,
        effective_from=start,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    return _line_out(line, group.code)


class LineUpdateIn(BaseModel):
    code: LineCode
    display_name: Name | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    module: Module | None = None
    # Set both to override the module's RAG for this line; clear_rag to inherit again.
    rag_green: Decimal | None = Field(default=None, gt=0, le=5)
    rag_amber: Decimal | None = Field(default=None, gt=0, le=5)
    clear_rag: bool = False

    @model_validator(mode="after")
    def _rag(self) -> LineUpdateIn:
        if (self.rag_green is None) != (self.rag_amber is None):
            raise ValueError("set rag_green and rag_amber together")
        if self.rag_green is not None and self.rag_amber is not None:
            if self.rag_amber > self.rag_green:
                raise ValueError("rag_amber must not be above rag_green")
            if self.clear_rag:
                raise ValueError("clear_rag with new thresholds contradicts itself")
        return self


@action(
    name="product_line.update",
    summary="Rename, reorder or rebind a product line, or override its RAG thresholds.",
    schema=LineUpdateIn,
    output=RegistryLineOut,
    permission="product_line.manage",
    read_only=False,
    module="agent_performance",
    audit="product_line.updated",
    config_change=True,
    example={"code": "CARDS", "display_name": "Credit cards"},
)
def update(params: LineUpdateIn, ctx: ActionContext) -> RegistryLineOut:
    line = _line(ctx, params.code)
    if params.display_name is not None:
        line.display_name = params.display_name
    if params.sort_order is not None:
        line.sort_order = params.sort_order
    if params.module is not None:
        line.module = params.module
    if params.clear_rag:
        line.rag_green = line.rag_amber = None
    elif params.rag_green is not None:
        line.rag_green, line.rag_amber = params.rag_green, params.rag_amber
    line.updated_by = ctx.user_id
    line.updated_at = timezone.now()
    line.save()
    return _line_out(line, line.group.code if line.group else None)


class LineReorderIn(BaseModel):
    # Line codes in the order the matrix shows them; lines not named keep theirs, after.
    codes: list[LineCode] = Field(min_length=1, max_length=500)


class LineReorderOut(BaseModel):
    codes: list[str]


@action(
    name="product_line.reorder",
    summary="Put product lines in the order the matrix shows them (quick settings).",
    schema=LineReorderIn,
    output=LineReorderOut,
    permission="product_line.manage",
    read_only=False,
    module="agent_performance",
    audit="product_line.reordered",
    config_change=True,
    example={"codes": ["LOANS", "CARDS"]},
)
def reorder(params: LineReorderIn, ctx: ActionContext) -> LineReorderOut:
    if len(set(params.codes)) != len(params.codes):
        raise InvalidInput("A line is named twice.")
    found = {
        line.code: line
        for line in ProductLine.objects.select_for_update().filter(
            org_id=ctx.org_id, code__in=params.codes
        )
    }
    unknown = sorted(set(params.codes) - set(found))
    if unknown:
        raise NotFound("Unknown product lines.", detail={"unknown": unknown})
    rest = (
        ProductLine.objects.filter(org_id=ctx.org_id)
        .exclude(code__in=params.codes)
        .order_by("sort_order", "code")
    )
    ordered = [found[c] for c in params.codes] + list(rest)
    now = timezone.now()
    for i, line in enumerate(ordered):
        if line.sort_order != (i + 1) * 10:
            line.sort_order = (i + 1) * 10
            line.updated_by = ctx.user_id
            line.updated_at = now
            line.save(update_fields=["sort_order", "updated_by", "updated_at"])
    return LineReorderOut(codes=[line.code for line in ordered])


class LineMoveIn(BaseModel):
    code: LineCode
    group_code: Code
    # Periods before this keep the old grouping.
    effective_from: date


@action(
    name="product_line.move",
    summary="Move a product line to another group from a date; earlier periods keep the old one.",
    schema=LineMoveIn,
    output=RegistryLineOut,
    permission="product_line.manage",
    read_only=False,
    module="agent_performance",
    requires_approval="config_change",
    audit="product_line.moved",
    config_change=True,
    example={"code": "CARDS", "group_code": "lending", "effective_from": "2026-11-01"},
)
def move(params: LineMoveIn, ctx: ActionContext) -> RegistryLineOut:
    line = _line(ctx, params.code)
    if line.status == "available":
        raise Conflict(f"'{params.code}' is not active yet; group it when you activate it.")
    group = _group(ctx, params.group_code, line.module)
    day = params.effective_from
    if day < line.effective_from:
        raise InvalidInput(f"'{params.code}' starts on {line.effective_from}.")
    if line.effective_to is not None and day >= line.effective_to:
        raise Conflict(f"'{params.code}' is retired from {line.effective_to}.")
    rows = list(
        ProductLineGroup.objects.select_for_update()
        .filter(line=line)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=day))
        .order_by("effective_from")
    )
    if any(r.effective_from > day for r in rows):
        raise Conflict(
            f"'{params.code}' already has a move scheduled after {day}; move it from then or later."
        )
    current = rows[0] if rows else None
    if current is not None and current.group_id == group.group_id:
        raise Conflict(f"'{params.code}' is already in '{group.code}' on {day}.")
    now = timezone.now()
    end = line.effective_to
    if current is not None:
        end = current.effective_to
        if current.effective_from == day:
            current.delete()
        else:
            current.effective_to = day
            current.updated_by = ctx.user_id
            current.updated_at = now
            current.save()
    ProductLineGroup.objects.create(
        line=line,
        group=group,
        effective_from=day,
        effective_to=end,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    line.group = group  # the latest grouping; history answers any earlier day
    line.updated_by = ctx.user_id
    line.updated_at = now
    line.save()
    return _line_out(line, group.code)


class LineRetireIn(BaseModel):
    code: LineCode
    # The first day it no longer shows; earlier periods still render it.
    effective_to: date | None = None


@action(
    name="product_line.retire",
    summary="Retire a product line from a date; closed periods keep its columns and facts.",
    schema=LineRetireIn,
    output=RegistryLineOut,
    permission="product_line.manage",
    read_only=False,
    module="agent_performance",
    requires_approval="config_change",
    audit="product_line.retired",
    config_change=True,
    example={"code": "CARDS", "effective_to": "2026-11-01"},
)
def retire(params: LineRetireIn, ctx: ActionContext) -> RegistryLineOut:
    line = _line(ctx, params.code)
    if line.status != "active":
        raise Conflict(
            f"'{params.code}' is {line.status}; only an active line retires."
            if line.status == "retired"
            else f"'{params.code}' was never switched on; it stays available."
        )
    end = params.effective_to or org_today(ctx.org_id)
    if end <= line.effective_from:
        raise InvalidInput(f"'{params.code}' starts on {line.effective_from}; retire it after.")
    now = timezone.now()
    later = ProductLineGroup.objects.filter(line=line, effective_from__gte=end)
    if later.exists():
        raise Conflict(f"'{params.code}' has a group move from {end} or later; retire it before.")
    for g in (
        ProductLineGroup.objects.select_for_update()
        .filter(line=line)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=end))
    ):
        g.effective_to = end
        g.updated_by = ctx.user_id
        g.updated_at = now
        g.save()
    line.status = "retired"
    line.effective_to = end
    line.updated_by = ctx.user_id
    line.updated_at = now
    line.save()
    return _line_out(line, line.group.code if line.group else None)
