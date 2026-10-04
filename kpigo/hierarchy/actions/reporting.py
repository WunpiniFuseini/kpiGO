"""Reporting lines, roll-up policy and the visibility closure (Schema §1)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, model_validator

from kpigo.action import ActionContext, Conflict, NotFound, action
from kpigo.hierarchy import closure
from kpigo.hierarchy.models import ReportingEdge, RollupPolicy, Subject, VisibilityClosure
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import Code, PeriodKey

Relationship = Literal["solid", "dotted"]
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"

_CONFLICTS = {
    "reporting_edge_not_self": "A subject cannot report to themself.",
    "reporting_edge_no_overlap": (
        "This subject already reports to that manager for an overlapping period."
    ),
    "reporting_edge_one_solid_manager": (
        "This subject already has a solid-line manager for an overlapping period. End that "
        "line first, or add this one as dotted."
    ),
    "reporting_edge_range_valid": "effective_to must be after effective_from.",
    "rollup_policy_no_overlap": (
        "A roll-up policy for this scope and line type already covers an overlapping period."
    ),
}


class _Ranged(BaseModel):
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def _range(self) -> _Ranged:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class EdgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    edge_id: uuid.UUID
    subject_id: uuid.UUID
    manager_id: uuid.UUID
    relationship_type: str
    counts_toward_rollup: bool
    effective_from: date
    effective_to: date | None


class EdgeCreateIn(_Ranged):
    subject_id: uuid.UUID
    manager_id: uuid.UUID
    relationship_type: Relationship = "solid"
    counts_toward_rollup: bool = True


@action(
    name="reporting_edge.create",
    summary="Add a solid or dotted reporting line for an effective period.",
    schema=EdgeCreateIn,
    output=EdgeOut,
    permission="hierarchy.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="reporting_edge.created",
    config_change=True,
    example={
        "subject_id": EXAMPLE_ID,
        "manager_id": "00000000-0000-0000-0000-000000000001",
        "relationship_type": "solid",
        "effective_from": "2026-10-01",
    },
)
def create_edge(params: EdgeCreateIn, ctx: ActionContext) -> EdgeOut:
    found = set(
        Subject.objects.filter(
            org_id=ctx.org_id, subject_id__in=[params.subject_id, params.manager_id]
        ).values_list("subject_id", flat=True)
    )
    if {params.subject_id, params.manager_id} - found:
        raise NotFound("Both the subject and the manager must be registered subjects.")
    with conflicts(_CONFLICTS):
        edge = ReportingEdge.objects.create(
            org_id=ctx.org_id,
            **params.model_dump(),
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    return EdgeOut.model_validate(edge)


class EdgeEndIn(BaseModel):
    edge_id: uuid.UUID
    effective_to: date


@action(
    name="reporting_edge.end",
    summary="End a reporting line. Lines can be shortened, never reopened or extended.",
    schema=EdgeEndIn,
    output=EdgeOut,
    permission="hierarchy.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="reporting_edge.ended",
    config_change=True,
    example={"edge_id": EXAMPLE_ID, "effective_to": "2027-01-01"},
)
def end_edge(params: EdgeEndIn, ctx: ActionContext) -> EdgeOut:
    edge = (
        ReportingEdge.objects.select_for_update()
        .filter(org_id=ctx.org_id, edge_id=params.edge_id)
        .first()
    )
    if edge is None:
        raise NotFound("No such reporting line.")
    if edge.effective_to is not None and params.effective_to >= edge.effective_to:
        raise Conflict(f"The line already ends on {edge.effective_to}.")
    edge.effective_to = params.effective_to
    edge.updated_by = ctx.user_id
    edge.updated_at = timezone.now()
    with conflicts(_CONFLICTS):
        edge.save(update_fields=["effective_to", "updated_by", "updated_at"])
    return EdgeOut.model_validate(edge)


class EdgeListIn(BaseModel):
    subject_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    as_of: date | None = None


class EdgeListOut(BaseModel):
    edges: list[EdgeOut]


@action(
    name="reporting_edge.list",
    summary="Reporting lines, filtered by subject, manager or the date they are in force.",
    schema=EdgeListIn,
    output=EdgeListOut,
    permission="hierarchy.view",
    read_only=True,
    example={"manager_id": EXAMPLE_ID},
)
def list_edges(params: EdgeListIn, ctx: ActionContext) -> EdgeListOut:
    rows = ReportingEdge.objects.filter(org_id=ctx.org_id)
    if params.subject_id is not None:
        rows = rows.filter(subject_id=params.subject_id)
    if params.manager_id is not None:
        rows = rows.filter(manager_id=params.manager_id)
    if params.as_of is not None:
        rows = rows.filter(effective_from__lte=params.as_of).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gt=params.as_of)
        )
    return EdgeListOut(
        edges=[EdgeOut.model_validate(e) for e in rows.order_by("effective_from", "edge_id")[:5000]]
    )


# ── rollup_policy ───────────────────────────────────────────────────────────


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    policy_id: uuid.UUID
    scope_type: str
    scope_code: str
    relationship_type: str
    counts_toward_rollup: bool
    effective_from: date
    effective_to: date | None


class PolicySetIn(_Ranged):
    scope_type: Literal["role", "profile"]
    scope_code: Code
    relationship_type: Relationship
    counts_toward_rollup: bool


@action(
    name="rollup_policy.set",
    summary="Override whether a line type counts toward roll-up for a role or profile.",
    schema=PolicySetIn,
    output=PolicyOut,
    permission="hierarchy.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="rollup_policy.set",
    config_change=True,
    example={
        "scope_type": "profile",
        "scope_code": "retail_rm",
        "relationship_type": "dotted",
        "counts_toward_rollup": False,
        "effective_from": "2026-10-01",
    },
)
def set_policy(params: PolicySetIn, ctx: ActionContext) -> PolicyOut:
    with conflicts(_CONFLICTS):
        policy = RollupPolicy.objects.create(
            org_id=ctx.org_id,
            **params.model_dump(),
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    return PolicyOut.model_validate(policy)


class PolicyListIn(BaseModel):
    scope_type: Literal["role", "profile"] | None = None


class PolicyListOut(BaseModel):
    policies: list[PolicyOut]


@action(
    name="rollup_policy.list",
    summary="Roll-up policy overrides.",
    schema=PolicyListIn,
    output=PolicyListOut,
    permission="hierarchy.view",
    read_only=True,
    example={},
)
def list_policies(params: PolicyListIn, ctx: ActionContext) -> PolicyListOut:
    rows = RollupPolicy.objects.filter(org_id=ctx.org_id)
    if params.scope_type is not None:
        rows = rows.filter(scope_type=params.scope_type)
    return PolicyListOut(
        policies=[
            PolicyOut.model_validate(p)
            for p in rows.order_by("scope_type", "scope_code", "effective_from")
        ]
    )


# ── visibility ──────────────────────────────────────────────────────────────


class RebuildIn(BaseModel):
    period_key: PeriodKey


class RebuildOut(BaseModel):
    period_key: str
    rows: int
    subjects: int
    edges: int


@action(
    name="visibility.rebuild",
    summary="Rebuild a period's visibility closure. Refuses if the lines contain a cycle.",
    schema=RebuildIn,
    output=RebuildOut,
    permission="hierarchy.manage",
    read_only=False,
    audit="visibility.rebuilt",
    config_change=True,
    example={"period_key": "202610"},
)
def rebuild(params: RebuildIn, ctx: ActionContext) -> RebuildOut:
    result = closure.build(ctx.org_id, params.period_key, ctx.user_id)
    return RebuildOut(
        period_key=result.period_key,
        rows=result.rows,
        subjects=result.subjects,
        edges=result.edges,
    )


class VisibleIn(BaseModel):
    period_key: PeriodKey
    viewer_subject_id: uuid.UUID


class VisibleRow(BaseModel):
    visible_subject_id: uuid.UUID
    via: str
    counts_toward_rollup: bool


class VisibleOut(BaseModel):
    period_key: str
    viewer_subject_id: uuid.UUID
    visible: list[VisibleRow]


@action(
    name="visibility.list",
    summary="Who a subject can see in a period, and whether each counts toward roll-up.",
    schema=VisibleIn,
    output=VisibleOut,
    permission="hierarchy.view",
    read_only=True,
    example={"period_key": "202610", "viewer_subject_id": EXAMPLE_ID},
)
def list_visible(params: VisibleIn, ctx: ActionContext) -> VisibleOut:
    rows = VisibilityClosure.objects.filter(
        org_id=ctx.org_id,
        period_key=params.period_key,
        viewer_subject_id=params.viewer_subject_id,
    ).order_by("via", "visible_subject_id")
    return VisibleOut(
        period_key=params.period_key,
        viewer_subject_id=params.viewer_subject_id,
        visible=[
            VisibleRow(
                visible_subject_id=r.visible_subject_id,
                via=r.via,
                counts_toward_rollup=r.counts_toward_rollup,
            )
            for r in rows
        ],
    )
