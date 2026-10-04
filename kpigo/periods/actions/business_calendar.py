"""Performance cycles, working days and holidays (PRD AD-5)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from django.db.models import Q
from django.utils import timezone
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from kpigo.action import ActionContext, NotFound, action
from kpigo.periods import business
from kpigo.periods.models import CalendarDay, CycleBinding, PerformanceCycle
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import Code, Product

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Month = Annotated[int, Field(ge=1, le=12)]
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"

_CONFLICTS = {
    "performance_cycle_name_unique": "A cycle with this name already exists.",
    "cycle_binding_no_overlap": (
        "This product already follows a cycle for an overlapping period. End that binding first."
    ),
}


class CycleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cycle_id: uuid.UUID
    name: str
    start_month: int
    end_month: int
    status: str


class CycleCreateIn(BaseModel):
    name: Name
    start_month: Month
    end_month: Month


@action(
    name="cycle.create",
    summary="Create a performance cycle, e.g. January to December.",
    schema=CycleCreateIn,
    output=CycleOut,
    permission="calendar.manage",
    read_only=False,
    requires_approval="calendar_change",
    audit="cycle.created",
    config_change=True,
    example={"name": "FY2027", "start_month": 1, "end_month": 12},
)
def create_cycle(params: CycleCreateIn, ctx: ActionContext) -> CycleOut:
    with conflicts(_CONFLICTS):
        cycle = PerformanceCycle.objects.create(
            org_id=ctx.org_id,
            **params.model_dump(),
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    return CycleOut.model_validate(cycle)


class BindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    binding_id: uuid.UUID
    product: str
    cycle_id: uuid.UUID
    effective_from: date
    effective_to: date | None


class CycleListIn(BaseModel):
    pass


class CycleListOut(BaseModel):
    cycles: list[CycleOut]
    bindings: list[BindingOut]


@action(
    name="cycle.list",
    summary="Performance cycles and which product follows which.",
    schema=CycleListIn,
    output=CycleListOut,
    permission="calendar.view",
    read_only=True,
    example={},
)
def list_cycles(params: CycleListIn, ctx: ActionContext) -> CycleListOut:
    return CycleListOut(
        cycles=[
            CycleOut.model_validate(c)
            for c in PerformanceCycle.objects.filter(org_id=ctx.org_id).order_by("name")
        ],
        bindings=[
            BindingOut.model_validate(b)
            for b in CycleBinding.objects.filter(org_id=ctx.org_id).order_by(
                "product", "effective_from"
            )
        ],
    )


class CycleBindIn(BaseModel):
    product: Product
    cycle_id: uuid.UUID
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def _range(self) -> CycleBindIn:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


@action(
    name="cycle.bind",
    summary="Make a product follow a cycle from a date.",
    schema=CycleBindIn,
    output=BindingOut,
    permission="calendar.manage",
    read_only=False,
    requires_approval="calendar_change",
    audit="cycle.bound",
    config_change=True,
    example={"product": "scorecards", "cycle_id": EXAMPLE_ID, "effective_from": "2027-01-01"},
)
def bind_cycle(params: CycleBindIn, ctx: ActionContext) -> BindingOut:
    cycle = PerformanceCycle.objects.filter(org_id=ctx.org_id, cycle_id=params.cycle_id).first()
    if cycle is None:
        raise NotFound("No such performance cycle.")
    with conflicts(_CONFLICTS):
        binding = CycleBinding.objects.create(
            org_id=ctx.org_id,
            product=params.product,
            cycle=cycle,
            effective_from=params.effective_from,
            effective_to=params.effective_to,
            created_by=ctx.user_id,
        )
    return BindingOut.model_validate(binding)


# ── calendar days ───────────────────────────────────────────────────────────


class DayIn(BaseModel):
    date: date
    is_working_day: bool
    holiday_name: Name | None = None
    region_code: Code | None = None


class DayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    is_working_day: bool
    holiday_name: str | None
    region_code: str | None


class DaysSetIn(BaseModel):
    days: list[DayIn] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _distinct(self) -> DaysSetIn:
        keys = [(d.date, d.region_code) for d in self.days]
        if len(set(keys)) != len(keys):
            raise ValueError("each (date, region_code) may appear once")
        return self


class DaysSetOut(BaseModel):
    created: int
    updated: int


@action(
    name="calendar.set_days",
    summary="Mark holidays and working weekends, org-wide or for a region.",
    schema=DaysSetIn,
    output=DaysSetOut,
    permission="calendar.manage",
    read_only=False,
    requires_approval="calendar_change",
    audit="calendar.days_set",
    config_change=True,
    example={
        "days": [{"date": "2026-12-25", "is_working_day": False, "holiday_name": "Christmas Day"}]
    },
)
def set_days(params: DaysSetIn, ctx: ActionContext) -> DaysSetOut:
    created = updated = 0
    now = timezone.now()
    for day in params.days:
        changed = CalendarDay.objects.filter(
            org_id=ctx.org_id, date=day.date, region_code=day.region_code
        ).update(
            is_working_day=day.is_working_day,
            holiday_name=day.holiday_name,
            updated_by=ctx.user_id,
            updated_at=now,
        )
        if changed:
            updated += 1
            continue
        CalendarDay.objects.create(
            org_id=ctx.org_id, **day.model_dump(), created_by=ctx.user_id, updated_by=ctx.user_id
        )
        created += 1
    return DaysSetOut(created=created, updated=updated)


class DaysListIn(BaseModel):
    date_from: date
    date_to: date
    region_code: Code | None = None

    @model_validator(mode="after")
    def _range(self) -> DaysListIn:
        if self.date_to < self.date_from or (self.date_to - self.date_from).days > 731:
            raise ValueError("date_to must be on or after date_from and within two years")
        return self


class DaysListOut(BaseModel):
    days: list[DayOut]
    working_days: int


@action(
    name="calendar.list",
    summary="Calendar exceptions in a date range, and the working-day count.",
    schema=DaysListIn,
    output=DaysListOut,
    permission="calendar.view",
    read_only=True,
    example={"date_from": "2026-12-01", "date_to": "2026-12-31"},
)
def list_days(params: DaysListIn, ctx: ActionContext) -> DaysListOut:
    rows = CalendarDay.objects.filter(
        org_id=ctx.org_id, date__gte=params.date_from, date__lte=params.date_to
    ).filter(Q(region_code__isnull=True) | Q(region_code=params.region_code))
    end = date.fromordinal(params.date_to.toordinal() + 1)
    return DaysListOut(
        days=[DayOut.model_validate(d) for d in rows.order_by("date", "region_code")],
        working_days=business.working_days_between(
            ctx.org_id, params.date_from, end, params.region_code
        ),
    )


class BusinessDateIn(BaseModel):
    at: AwareDatetime
    region_code: Code | None = None


class BusinessDateOut(BaseModel):
    at: datetime
    business_date: date


@action(
    name="calendar.business_date",
    summary="The business day an instant counts toward, after timezone, cutoff and holidays.",
    schema=BusinessDateIn,
    output=BusinessDateOut,
    permission="calendar.view",
    read_only=True,
    example={"at": "2026-12-24T18:30:00+00:00"},
)
def business_date(params: BusinessDateIn, ctx: ActionContext) -> BusinessDateOut:
    return BusinessDateOut(
        at=params.at,
        business_date=business.business_date(ctx.org_id, params.at, params.region_code),
    )
