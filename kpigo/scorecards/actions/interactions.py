"""What people say about a scorecard (PRD SC-14–SC-16; App Flow §6.2, §6.3).

Three things, all on one table:

- **Acknowledgement.** The subject marks a closed scorecard as *seen*, never
  *agreed*. One per snapshot version, so a restatement asks again.
- **Query.** The subject questions one figure. It is routed to their solid-line
  manager in force on the period's last day, and nowhere else; with no such
  manager it waits in the Admin queue (anyone holding the resolve permission who
  can see the subject). A query never reopens a period. Its resolution either
  explains the standing figure or names the override that will change it.
- **Manager commentary.** A note on someone else's scorecard, shown to the
  subject by default or kept among managers.

Who "the subject" is comes from the caller's own kpiGo account, never a
parameter: you acknowledge and query your own scorecard only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, StringConstraints, model_validator

from kpigo.access.identity import app_user_for
from kpigo.access.models import AppUser
from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, OutOfScope, action
from kpigo.hierarchy.models import ReportingEdge, Subject
from kpigo.hierarchy.scope import current_period_key
from kpigo.metrics.actions.metric import MetricCode
from kpigo.platform.vocab import PeriodKey
from kpigo.scorecards import roster
from kpigo.scorecards.close import current_snapshot
from kpigo.scorecards.config import period_phase, period_status
from kpigo.scorecards.cycles import last_day
from kpigo.scorecards.models import Override, ScorecardInteraction

PRODUCT = "scorecards"
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=4000)]
Outcome = Literal["explained", "adjusted"]


class InteractionOut(BaseModel):
    interaction_id: uuid.UUID
    interaction_type: str
    subject_id: uuid.UUID
    staff_no: str
    full_name: str
    period_key: str
    metric_code: str | None
    metric_name: str | None
    body: str
    author_user_id: int | None
    author_name: str | None
    visibility: str
    snapshot_version: int | None
    routed_to_id: uuid.UUID | None
    routed_to_name: str | None
    created_at: datetime
    resolved_at: datetime | None
    resolved_by_name: str | None
    outcome: str | None
    resolution: str | None
    resulting_override_id: uuid.UUID | None
    # True when the caller wrote it.
    mine: bool


def _out(rows: list[ScorecardInteraction], ctx: ActionContext) -> list[InteractionOut]:
    users = {u for r in rows for u in (r.author_user_id, r.resolved_by) if u is not None}
    names = dict(
        AppUser.objects.filter(org_id=ctx.org_id, auth_user_id__in=users).values_list(
            "auth_user_id", "display_name"
        )
    )
    return [
        InteractionOut(
            interaction_id=r.interaction_id,
            interaction_type=r.interaction_type,
            subject_id=r.subject.subject_id,
            staff_no=r.subject.staff_no,
            full_name=r.subject.full_name,
            period_key=r.period_key,
            metric_code=r.metric.metric_code if r.metric else None,
            metric_name=r.metric.display_name if r.metric else None,
            body=r.body,
            author_user_id=r.author_user_id,
            author_name=names.get(r.author_user_id) if r.author_user_id else None,
            visibility=r.visibility,
            snapshot_version=r.snapshot_version,
            routed_to_id=r.routed_to.subject_id if r.routed_to else None,
            routed_to_name=r.routed_to.full_name if r.routed_to else None,
            created_at=r.created_at,
            resolved_at=r.resolved_at,
            resolved_by_name=names.get(r.resolved_by) if r.resolved_by else None,
            outcome=r.outcome,
            resolution=r.resolution,
            resulting_override_id=r.resulting_override_id,
            mine=r.author_user_id is not None and r.author_user_id == ctx.user_id,
        )
        for r in rows
    ]


def _rows(**filters: object) -> list[ScorecardInteraction]:
    return list(
        ScorecardInteraction.objects.filter(**filters)
        .select_related("subject", "metric", "routed_to")
        .order_by("created_at")
    )


def own_subject_id(ctx: ActionContext) -> str | None:
    """The subject the caller is, or None for an account not linked to one."""
    account = app_user_for(ctx.user, ctx.org_id)
    return str(account.subject_id) if account and account.subject_id else None


def _require_self(ctx: ActionContext) -> Subject:
    mine = own_subject_id(ctx)
    if mine is None:
        raise Conflict(
            "Your account is not linked to a person in the hierarchy, so you have no "
            "scorecard of your own. An Admin links it under Access → Users."
        )
    return Subject.objects.get(subject_id=mine)


def solid_manager(org_id: str, subject_id: str, period_key: str) -> Subject | None:
    edge = (
        ReportingEdge.objects.filter(
            org_id=org_id, subject_id=subject_id, relationship_type="solid"
        )
        .filter(roster.in_force(last_day(period_key)))
        .select_related("manager")
        .first()
    )
    return edge.manager if edge else None


# ── the thread on one scorecard ────────────────────────────────────────────


class InteractionListIn(BaseModel):
    subject_id: uuid.UUID
    period_key: PeriodKey | None = None


class InteractionListOut(BaseModel):
    period_key: str
    # Who the caller is to this scorecard.
    is_self: bool
    # The current frozen version, when the period is closed.
    snapshot_version: int | None
    # Acknowledged the current version.
    acknowledged: bool
    acknowledged_at: datetime | None
    # Acknowledged an earlier version only: it has been restated since.
    acknowledged_version: int | None
    can_acknowledge: bool
    can_query: bool
    can_comment: bool
    queries: list[InteractionOut]
    comments: list[InteractionOut]
    # Why there is nothing here, when that needs saying.
    note: str | None


@action(
    name="scorecard.interaction.list",
    summary="Acknowledgement, queries and manager commentary on one scorecard.",
    schema=InteractionListIn,
    output=InteractionListOut,
    permission="scorecard.view",
    read_only=True,
    module="scorecards",
    scope="subject",
    example={"subject_id": EXAMPLE_ID, "period_key": "202610"},
)
def list_interactions(params: InteractionListIn, ctx: ActionContext) -> InteractionListOut:
    period_key = params.period_key or current_period_key(ctx.org_id)
    subject_id = str(params.subject_id)
    is_self = own_subject_id(ctx) == subject_id
    rows = _rows(org_id=ctx.org_id, subject_id=subject_id, period_key=period_key, product=PRODUCT)
    snap = current_snapshot(ctx.org_id, period_key)
    closed = period_status(ctx.org_id, period_key) == "closed" and snap is not None
    version = snap.snapshot_version if closed and snap else None
    acks = sorted(
        (r for r in rows if r.interaction_type == "acknowledgement"),
        key=lambda r: r.snapshot_version or 0,
    )
    current = next((a for a in acks if a.snapshot_version == version), None)
    comments = [
        r
        for r in rows
        if r.interaction_type == "manager_comment" and not (is_self and r.visibility == "managers")
    ]
    phase = period_phase(ctx.org_id, period_key)
    note = None
    if phase == "future":
        note = f"{period_key} has not started, so there is nothing to acknowledge or query yet."
    elif not closed and is_self:
        note = "You can acknowledge this scorecard once the period is closed and published."
    return InteractionListOut(
        period_key=period_key,
        is_self=is_self,
        snapshot_version=version,
        acknowledged=current is not None,
        acknowledged_at=current.created_at if current else None,
        acknowledged_version=acks[-1].snapshot_version if acks and current is None else None,
        can_acknowledge=is_self and closed and current is None and ctx.has("scorecard.acknowledge"),
        can_query=is_self and phase != "future" and ctx.has("scorecard.query"),
        can_comment=not is_self and phase != "future" and ctx.has("scorecard.comment"),
        queries=_out([r for r in rows if r.interaction_type == "query"], ctx),
        comments=_out(comments, ctx),
        note=note,
    )


# ── acknowledge ─────────────────────────────────────────────────────────────


class AcknowledgeIn(BaseModel):
    period_key: PeriodKey


@action(
    name="scorecard.acknowledge",
    summary="Mark your own closed scorecard as seen (not agreed).",
    schema=AcknowledgeIn,
    output=InteractionOut,
    permission="scorecard.acknowledge",
    read_only=False,
    module="scorecards",
    audit="scorecard.acknowledged",
    example={"period_key": "202609"},
)
def acknowledge(params: AcknowledgeIn, ctx: ActionContext) -> InteractionOut:
    subject = _require_self(ctx)
    snap = current_snapshot(ctx.org_id, params.period_key)
    if period_status(ctx.org_id, params.period_key) != "closed" or snap is None:
        raise Conflict(
            f"{params.period_key} is not closed yet. A scorecard is acknowledged once it is "
            "published; until then every figure is provisional."
        )
    existing = _rows(
        org_id=ctx.org_id,
        subject=subject,
        period_key=params.period_key,
        product=PRODUCT,
        interaction_type="acknowledgement",
        snapshot_version=snap.snapshot_version,
    )
    if existing:
        return _out(existing, ctx)[0]
    row = ScorecardInteraction.objects.create(
        org_id=ctx.org_id,
        subject=subject,
        period_key=params.period_key,
        product=PRODUCT,
        interaction_type="acknowledgement",
        author_user_id=ctx.user_id,
        snapshot_version=snap.snapshot_version,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    return _out(_rows(interaction_id=row.interaction_id), ctx)[0]


# ── queries ─────────────────────────────────────────────────────────────────


class QueryRaiseIn(BaseModel):
    period_key: PeriodKey
    metric_code: MetricCode
    body: Body


@action(
    name="scorecard.query.raise",
    summary="Question one figure on your own scorecard; it goes to your line manager.",
    schema=QueryRaiseIn,
    output=InteractionOut,
    permission="scorecard.query",
    read_only=False,
    module="scorecards",
    audit="scorecard.query_raised",
    example={"period_key": "202609", "metric_code": "casa_growth", "body": "My CASA is low."},
)
def raise_query(params: QueryRaiseIn, ctx: ActionContext) -> InteractionOut:
    subject = _require_self(ctx)
    if period_phase(ctx.org_id, params.period_key) == "future":
        raise Conflict(f"{params.period_key} has not started; there is nothing to query yet.")
    member = roster.member_of(ctx.org_id, str(subject.subject_id), params.period_key)
    metrics = (
        roster.profile_metrics(ctx.org_id, params.period_key, [member.profile_code]).get(
            member.profile_code, []
        )
        if member
        else []
    )
    metric = next((m for m in metrics if m.metric_code == params.metric_code), None)
    if metric is None:
        raise InvalidInput(
            f"'{params.metric_code}' is not on your scorecard for {params.period_key}."
        )
    row = ScorecardInteraction.objects.create(
        org_id=ctx.org_id,
        subject=subject,
        period_key=params.period_key,
        product=PRODUCT,
        interaction_type="query",
        metric=metric,
        body=params.body,
        author_user_id=ctx.user_id,
        routed_to=solid_manager(ctx.org_id, str(subject.subject_id), params.period_key),
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    return _out(_rows(interaction_id=row.interaction_id), ctx)[0]


class QueryListIn(BaseModel):
    status: Literal["open", "resolved", "all"] = "open"
    period_key: PeriodKey | None = None


class QueryListOut(BaseModel):
    # Queries on your own scorecard.
    mine: list[InteractionOut]
    # Queries you can resolve: on people you can see, other than you.
    queue: list[InteractionOut]
    # Of the queue, those routed to you as line manager.
    routed_to_me: int


@action(
    name="scorecard.query.list",
    summary="Your own scorecard queries, and the queue of queries you can resolve.",
    schema=QueryListIn,
    output=QueryListOut,
    permission="scorecard.query",
    read_only=True,
    module="scorecards",
    example={"status": "open"},
)
def list_queries(params: QueryListIn, ctx: ActionContext) -> QueryListOut:
    base = Q(org_id=ctx.org_id, product=PRODUCT, interaction_type="query")
    if params.period_key:
        base &= Q(period_key=params.period_key)
    if params.status != "all":
        base &= Q(resolved_at__isnull=params.status == "open")
    rows = list(
        ScorecardInteraction.objects.filter(base)
        .select_related("subject", "metric", "routed_to")
        .order_by("-created_at")
    )
    me = own_subject_id(ctx)
    mine = [r for r in rows if str(r.subject_id) == me]
    queue = (
        [
            r
            for r in rows
            if str(r.subject_id) != me and ctx.visible_subjects.contains(str(r.subject_id))
        ]
        if ctx.has("scorecard.query.resolve")
        else []
    )
    # Routed to me first, then the oldest open.
    queue.sort(key=lambda r: (str(r.routed_to_id) != me, r.created_at))
    return QueryListOut(
        mine=_out(mine, ctx),
        queue=_out(queue, ctx),
        routed_to_me=sum(1 for r in queue if me and str(r.routed_to_id) == me),
    )


class QueryResolveIn(BaseModel):
    interaction_id: uuid.UUID
    outcome: Outcome
    resolution: Body
    # adjusted: the override (pending or approved) that will change the figure.
    override_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _shape(self) -> QueryResolveIn:
        if self.outcome == "adjusted" and self.override_id is None:
            raise ValueError("an adjusted outcome names the override that changes the figure")
        if self.outcome == "explained" and self.override_id is not None:
            raise ValueError("an explained outcome changes nothing, so it takes no override")
        return self


@action(
    name="scorecard.query.resolve",
    summary="Answer a scorecard query: explain the figure, or point at the override.",
    schema=QueryResolveIn,
    output=InteractionOut,
    permission="scorecard.query.resolve",
    read_only=False,
    module="scorecards",
    audit="scorecard.query_resolved",
    example={
        "interaction_id": EXAMPLE_ID,
        "outcome": "explained",
        "resolution": "The September CASA figure includes the reversal on the 30th.",
    },
)
def resolve_query(params: QueryResolveIn, ctx: ActionContext) -> InteractionOut:
    row = (
        ScorecardInteraction.objects.select_for_update(of=("self",))
        .select_related("metric")
        .filter(org_id=ctx.org_id, interaction_id=params.interaction_id, interaction_type="query")
        .first()
    )
    if row is None:
        raise NotFound("No such query.")
    if not ctx.visible_subjects.contains(str(row.subject_id)):
        raise OutOfScope("That query is on a scorecard outside your scope.")
    if own_subject_id(ctx) == str(row.subject_id):
        raise Conflict("A query on your own scorecard is answered by someone else.")
    if row.resolved_at is not None:
        raise Conflict("This query is already resolved.")
    override = None
    if params.override_id is not None:
        override = Override.objects.filter(
            org_id=ctx.org_id, override_id=params.override_id
        ).first()
        if override is None:
            raise NotFound("No such override.")
        if row.metric is None or override.metric.metric_code != row.metric.metric_code:
            raise InvalidInput("The override must be on the queried metric.")
        if override.status not in ("pending", "approved"):
            raise Conflict(f"That override is {override.status}; it will not change the figure.")
    now = timezone.now()
    row.resolved_at = now
    row.resolved_by = ctx.user_id
    row.outcome = params.outcome
    row.resolution = params.resolution
    row.resulting_override = override
    row.updated_by = ctx.user_id
    row.save(
        update_fields=[
            "resolved_at",
            "resolved_by",
            "outcome",
            "resolution",
            "resulting_override",
            "updated_by",
            "updated_at",
        ]
    )
    return _out(_rows(interaction_id=row.interaction_id), ctx)[0]


# ── manager commentary ──────────────────────────────────────────────────────


class CommentAddIn(BaseModel):
    subject_id: uuid.UUID
    period_key: PeriodKey
    body: Body
    # subject: the person sees it; managers: kept among people who manage them.
    visibility: Literal["subject", "managers"] = "subject"


@action(
    name="scorecard.comment.add",
    summary="Add a manager's comment to someone's scorecard.",
    schema=CommentAddIn,
    output=InteractionOut,
    permission="scorecard.comment",
    read_only=False,
    module="scorecards",
    scope="subject",
    audit="scorecard.commented",
    example={"subject_id": EXAMPLE_ID, "period_key": "202609", "body": "Strong quarter on CASA."},
)
def add_comment(params: CommentAddIn, ctx: ActionContext) -> InteractionOut:
    if own_subject_id(ctx) == str(params.subject_id):
        raise Conflict("Commentary is for scorecards you manage, not your own.")
    if period_phase(ctx.org_id, params.period_key) == "future":
        raise Conflict(f"{params.period_key} has not started; there is nothing to comment on.")
    subject = Subject.objects.filter(org_id=ctx.org_id, subject_id=params.subject_id).first()
    if subject is None:
        raise NotFound("No such subject.")
    row = ScorecardInteraction.objects.create(
        org_id=ctx.org_id,
        subject=subject,
        period_key=params.period_key,
        product=PRODUCT,
        interaction_type="manager_comment",
        body=params.body,
        visibility=params.visibility,
        author_user_id=ctx.user_id,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    return _out(_rows(interaction_id=row.interaction_id), ctx)[0]
