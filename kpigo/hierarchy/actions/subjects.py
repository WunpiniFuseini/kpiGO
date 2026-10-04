"""Subjects and their role periods (Schema §1: subject, assignment)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.hierarchy.models import Assignment, DimMember, ReportingEdge, Subject
from kpigo.periods.models import PerformanceCycle
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import Code

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"

_CONFLICTS = {
    "subject_email_unique": "Another subject already has this email.",
    "subject_staff_no_unique": "Another subject already has this staff number.",
    "assignment_no_overlap": (
        "This subject already holds an assignment for an overlapping period. End the "
        "current assignment first; one subject holds one role at a time."
    ),
    "assignment_range_valid": "effective_to must be after effective_from.",
}


def _today() -> date:
    return timezone.localdate()


def _in_force(as_of: date) -> Q:
    return Q(effective_from__lte=as_of) & (Q(effective_to__isnull=True) | Q(effective_to__gt=as_of))


class SubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    subject_id: uuid.UUID
    staff_no: str
    full_name: str
    email: str
    status: str
    joined_at: date | None
    left_at: date | None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assignment_id: uuid.UUID
    subject_id: uuid.UUID
    role_code: str
    profile_code: str
    portfolio_code: str | None
    staff_ref: str | None
    branch_code: str | None
    region_code: str | None
    segment_code: str | None
    cycle_id: uuid.UUID | None
    effective_from: date
    effective_to: date | None


def _subject(org_id: str, subject_id: uuid.UUID) -> Subject:
    found = Subject.objects.filter(org_id=org_id, subject_id=subject_id).first()
    if found is None:
        raise NotFound("No such subject.")
    return found


# ── subject.register / subject.update ───────────────────────────────────────


class SubjectRegisterIn(BaseModel):
    staff_no: Code
    full_name: Name
    email: Email
    status: Literal["active", "inactive"] = "active"
    joined_at: date | None = None


@action(
    name="subject.register",
    summary="Register a measured person: stable identity only.",
    schema=SubjectRegisterIn,
    output=SubjectOut,
    permission="hierarchy.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="subject.registered",
    config_change=True,
    example={"staff_no": "E1001", "full_name": "Ama Mensah", "email": "ama.mensah@bank.example"},
)
def register_subject(params: SubjectRegisterIn, ctx: ActionContext) -> SubjectOut:
    with conflicts(_CONFLICTS):
        subject = Subject.objects.create(
            org_id=ctx.org_id,
            staff_no=params.staff_no,
            full_name=params.full_name,
            email=params.email,
            status=params.status,
            joined_at=params.joined_at,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    return SubjectOut.model_validate(subject)


class SubjectUpdateIn(BaseModel):
    subject_id: uuid.UUID
    staff_no: Code | None = None
    full_name: Name | None = None
    email: Email | None = None
    status: Literal["active", "inactive"] | None = None
    joined_at: date | None = None
    left_at: date | None = None


@action(
    name="subject.update",
    summary="Correct a subject's identity details or mark them inactive.",
    schema=SubjectUpdateIn,
    output=SubjectOut,
    permission="hierarchy.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="subject.updated",
    config_change=True,
    example={"subject_id": EXAMPLE_ID, "status": "inactive", "left_at": "2026-12-31"},
)
def update_subject(params: SubjectUpdateIn, ctx: ActionContext) -> SubjectOut:
    subject = _subject(ctx.org_id, params.subject_id)
    changes: dict[str, Any] = params.model_dump(exclude={"subject_id"}, exclude_none=True)
    if not changes:
        raise InvalidInput("Nothing to change.")
    for field, value in changes.items():
        setattr(subject, field, value)
    subject.updated_by = ctx.user_id
    subject.updated_at = timezone.now()
    with conflicts(_CONFLICTS):
        subject.save()
    return SubjectOut.model_validate(subject)


# ── subject.list / subject.get ──────────────────────────────────────────────


class SubjectListIn(BaseModel):
    status: Literal["active", "inactive"] | None = None
    search: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class SubjectListOut(BaseModel):
    total: int
    subjects: list[SubjectOut]


@action(
    name="subject.list",
    summary="List subjects for hierarchy administration.",
    schema=SubjectListIn,
    output=SubjectListOut,
    permission="hierarchy.view",
    read_only=True,
    example={"status": "active", "limit": 50},
)
def list_subjects(params: SubjectListIn, ctx: ActionContext) -> SubjectListOut:
    rows = Subject.objects.filter(org_id=ctx.org_id)
    if params.status is not None:
        rows = rows.filter(status=params.status)
    if params.search:
        rows = rows.filter(
            Q(full_name__icontains=params.search)
            | Q(staff_no__icontains=params.search)
            | Q(email__icontains=params.search)
        )
    page = rows.order_by("full_name", "staff_no")[params.offset : params.offset + params.limit]
    return SubjectListOut(total=rows.count(), subjects=[SubjectOut.model_validate(s) for s in page])


class SubjectGetIn(BaseModel):
    subject_id: uuid.UUID
    as_of: date | None = None


class ManagerOut(BaseModel):
    manager_id: uuid.UUID
    relationship_type: str
    counts_toward_rollup: bool


class SubjectGetOut(BaseModel):
    subject: SubjectOut
    as_of: date
    assignment: AssignmentOut | None
    managers: list[ManagerOut]


@action(
    name="subject.get",
    summary="A subject you can see: identity, current role and reporting lines.",
    schema=SubjectGetIn,
    output=SubjectGetOut,
    permission="subject.view",
    read_only=True,
    scope="subject",
    example={"subject_id": EXAMPLE_ID},
)
def get_subject(params: SubjectGetIn, ctx: ActionContext) -> SubjectGetOut:
    subject = _subject(ctx.org_id, params.subject_id)
    as_of = params.as_of or _today()
    assignment = subject.assignments.filter(_in_force(as_of)).first()
    edges = ReportingEdge.objects.filter(subject=subject).filter(_in_force(as_of))
    return SubjectGetOut(
        subject=SubjectOut.model_validate(subject),
        as_of=as_of,
        assignment=AssignmentOut.model_validate(assignment) if assignment else None,
        managers=[
            ManagerOut(
                manager_id=e.manager_id,
                relationship_type=e.relationship_type,
                counts_toward_rollup=e.counts_toward_rollup,
            )
            for e in edges.order_by("relationship_type")
        ],
    )


# ── assignment.create / assignment.end / assignment.list ───────────────────


class AssignmentCreateIn(BaseModel):
    subject_id: uuid.UUID
    role_code: Code
    profile_code: Code
    portfolio_code: Code | None = None
    staff_ref: Code | None = None
    branch_code: Code | None = None
    region_code: Code | None = None
    segment_code: Code | None = None
    cycle_id: uuid.UUID | None = None
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def _range(self) -> AssignmentCreateIn:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


def _check_members(org_id: str, params: AssignmentCreateIn) -> None:
    unknown = [
        f"{dimension}:{code}"
        for dimension, code in (
            ("branch", params.branch_code),
            ("region", params.region_code),
            ("segment", params.segment_code),
        )
        if code is not None
        and not DimMember.objects.filter(
            org_id=org_id, dimension_type=dimension, member_code=code
        ).exists()
    ]
    if unknown:
        raise InvalidInput("Unknown dimension members.", detail={"unknown": unknown})


@action(
    name="assignment.create",
    summary="Start a role period for a subject. Overlapping periods are refused.",
    schema=AssignmentCreateIn,
    output=AssignmentOut,
    permission="hierarchy.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="assignment.created",
    config_change=True,
    example={
        "subject_id": EXAMPLE_ID,
        "role_code": "rm",
        "profile_code": "retail_rm",
        "effective_from": "2026-10-01",
    },
)
def create_assignment(params: AssignmentCreateIn, ctx: ActionContext) -> AssignmentOut:
    subject = _subject(ctx.org_id, params.subject_id)
    _check_members(ctx.org_id, params)
    if (
        params.cycle_id is not None
        and not PerformanceCycle.objects.filter(
            org_id=ctx.org_id, cycle_id=params.cycle_id
        ).exists()
    ):
        raise NotFound("No such performance cycle.")
    with conflicts(_CONFLICTS):
        assignment = Assignment.objects.create(
            org_id=ctx.org_id,
            subject=subject,
            **params.model_dump(exclude={"subject_id"}),
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    return AssignmentOut.model_validate(assignment)


class AssignmentEndIn(BaseModel):
    assignment_id: uuid.UUID
    # First day the assignment no longer applies.
    effective_to: date


@action(
    name="assignment.end",
    summary="End a role period. Periods can be shortened, never reopened or extended.",
    schema=AssignmentEndIn,
    output=AssignmentOut,
    permission="hierarchy.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="assignment.ended",
    config_change=True,
    example={"assignment_id": EXAMPLE_ID, "effective_to": "2027-01-01"},
)
def end_assignment(params: AssignmentEndIn, ctx: ActionContext) -> AssignmentOut:
    assignment = (
        Assignment.objects.select_for_update()
        .filter(org_id=ctx.org_id, assignment_id=params.assignment_id)
        .first()
    )
    if assignment is None:
        raise NotFound("No such assignment.")
    if assignment.effective_to is not None and params.effective_to >= assignment.effective_to:
        raise Conflict(f"The assignment already ends on {assignment.effective_to}.")
    assignment.effective_to = params.effective_to
    assignment.updated_by = ctx.user_id
    assignment.updated_at = timezone.now()
    with conflicts(_CONFLICTS):
        assignment.save(update_fields=["effective_to", "updated_by", "updated_at"])
    return AssignmentOut.model_validate(assignment)


class AssignmentListIn(BaseModel):
    subject_id: uuid.UUID


class AssignmentListOut(BaseModel):
    assignments: list[AssignmentOut]


@action(
    name="assignment.list",
    summary="Every role period a subject has held, oldest first.",
    schema=AssignmentListIn,
    output=AssignmentListOut,
    permission="hierarchy.view",
    read_only=True,
    example={"subject_id": EXAMPLE_ID},
)
def list_assignments(params: AssignmentListIn, ctx: ActionContext) -> AssignmentListOut:
    rows = Assignment.objects.filter(org_id=ctx.org_id, subject_id=params.subject_id)
    return AssignmentListOut(
        assignments=[AssignmentOut.model_validate(a) for a in rows.order_by("effective_from")]
    )
