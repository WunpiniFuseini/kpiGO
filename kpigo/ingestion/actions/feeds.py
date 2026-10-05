"""Feed registry (PRD AD-7, App Flow §7.1): template, source, cadence, owner, deadline."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from kpigo.action import ActionContext, InvalidInput, NotFound, action
from kpigo.ingestion import schedule, sources
from kpigo.ingestion import validator as v
from kpigo.ingestion.models import Connection, Feed, FeedRun
from kpigo.ingestion.reference import build_reference
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import Code, PeriodKey

Template = Literal[
    "actual_monthly",
    "actual_daily",
    "actual_dimensional",
    "campaign_outcome",
    "campaign_population",
    "campaign_contact",
    "campaign_winback",
    "widget_data",
]
Mode = Literal["pull", "drop", "upload"]
ObjectName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=400)]
DropPath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
Cron = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
Pct = Annotated[int, Field(ge=0, le=1000)]
Rows = Annotated[int, Field(ge=0)]
Hours = Annotated[int, Field(ge=1, le=24 * 400)]

_CONFLICTS = {"feed_name_unique": "A feed with this name already exists."}
# Fields feed.update may change, as one dict, re-checked together after the change.
_SETTABLE = (
    "connection",
    "source_object",
    "drop_path",
    "cadence_cron",
    "owner_user_id",
    "deadline_offset_hours",
    "expected_row_min",
    "expected_row_max",
    "volume_warn_pct",
    "volume_reject_pct",
    "freshness_tolerance_hours",
    "status",
)


def _check_object(value: str | None) -> str | None:
    if value is not None:
        try:
            v.parse_object_name(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
    return value


def _check_drop(value: str | None) -> str | None:
    return sources.safe_relative(value) if value is not None else None


def _check_cron(value: str | None) -> str | None:
    if value is not None:
        schedule.parse_cron(value)
    return value


class FeedSettings(BaseModel):
    connection: Code | None = None
    source_object: ObjectName | None = None
    drop_path: DropPath | None = None
    cadence_cron: Cron | None = None
    owner_user_id: int | None = None
    deadline_offset_hours: Annotated[int, Field(ge=0, le=24 * 60)] | None = None
    expected_row_min: Rows | None = None
    expected_row_max: Rows | None = None
    volume_warn_pct: Pct = 25
    volume_reject_pct: Pct = 75
    freshness_tolerance_hours: Hours | None = None
    status: Literal["active", "paused"] = "active"

    @field_validator("source_object")
    @classmethod
    def _object(cls, value: str | None) -> str | None:
        return _check_object(value)

    @field_validator("drop_path")
    @classmethod
    def _drop(cls, value: str | None) -> str | None:
        return _check_drop(value)

    @field_validator("cadence_cron")
    @classmethod
    def _cron(cls, value: str | None) -> str | None:
        return _check_cron(value)


def _check_combination(mode: str, values: dict[str, Any]) -> None:
    """Each mode needs its own source and nothing that belongs to another."""
    problems: list[str] = []
    if mode == "pull":
        if not values.get("connection") or not values.get("source_object"):
            problems.append("A pull feed needs a connection and a source object.")
        if values.get("drop_path"):
            problems.append("A pull feed has no drop folder.")
    elif mode == "drop":
        if not values.get("drop_path"):
            problems.append("A drop feed needs a drop folder.")
        if values.get("connection") or values.get("source_object"):
            problems.append("A drop feed reads files, not a connection.")
    else:
        if values.get("connection") or values.get("source_object") or values.get("drop_path"):
            problems.append("An upload feed takes files through the UI only.")
    if values.get("cadence_cron") and mode != "pull":
        problems.append("Only pull feeds run on a cadence; drop folders are watched.")
    low, high = values.get("expected_row_min"), values.get("expected_row_max")
    if low is not None and high is not None and low > high:
        problems.append("expected_row_min is above expected_row_max.")
    if values.get("volume_reject_pct", 75) < values.get("volume_warn_pct", 25):
        problems.append("volume_reject_pct must be at least volume_warn_pct.")
    if problems:
        raise InvalidInput(" ".join(problems), detail={"problems": problems})


class RunSummary(BaseModel):
    run_id: uuid.UUID
    is_dry_run: bool
    state: str
    outcome: str | None
    started_at: datetime
    finished_at: datetime | None
    rows_read: int
    rows_rejected: int


def run_summary(run: FeedRun) -> RunSummary:
    return RunSummary(
        run_id=run.run_id,
        is_dry_run=run.is_dry_run,
        state=run.state,
        outcome=run.outcome,
        started_at=run.started_at,
        finished_at=run.finished_at,
        rows_read=run.rows_read,
        rows_rejected=run.rows_rejected,
    )


class FeedOut(BaseModel):
    feed_id: uuid.UUID
    name: str
    template: str
    mode: str
    connection: str | None
    source_object: str | None
    drop_path: str | None
    cadence_cron: str | None
    owner_user_id: int | None
    deadline_offset_hours: int | None
    expected_row_min: int | None
    expected_row_max: int | None
    volume_warn_pct: int
    volume_reject_pct: int
    freshness_tolerance_hours: int | None
    status: str
    freshness_state: str
    stale_reason: str | None
    stale_since: datetime | None
    # The as-of time a greyed tile shows.
    last_success_at: datetime | None
    # Whether a dry run has passed, which the first live load requires (IN-7).
    dry_run_passed: bool
    last_run: RunSummary | None

    @classmethod
    def of(cls, feed: Feed) -> FeedOut:
        last = (
            FeedRun.objects.filter(run_id=feed.last_run_id).first()
            if feed.last_run_id is not None
            else None
        )
        return cls(
            feed_id=feed.feed_id,
            name=feed.name,
            template=feed.template_name,
            mode=feed.mode,
            connection=feed.connection.name if feed.connection is not None else None,
            source_object=feed.source_object,
            drop_path=feed.drop_path,
            cadence_cron=feed.cadence_cron,
            owner_user_id=feed.owner_user_id,
            deadline_offset_hours=feed.deadline_offset_hours,
            expected_row_min=feed.expected_row_min,
            expected_row_max=feed.expected_row_max,
            volume_warn_pct=feed.volume_warn_pct,
            volume_reject_pct=feed.volume_reject_pct,
            freshness_tolerance_hours=feed.freshness_tolerance_hours,
            status=feed.status,
            freshness_state=feed.freshness_state,
            stale_reason=feed.stale_reason,
            stale_since=feed.stale_since,
            last_success_at=feed.last_success_at,
            dry_run_passed=FeedRun.objects.filter(
                feed=feed, is_dry_run=True, outcome="success"
            ).exists(),
            last_run=run_summary(last) if last is not None else None,
        )


def get_feed(org_id: str, name: str, *, lock: bool = False) -> Feed:
    rows = Feed.objects.filter(org_id=org_id, name=name).select_related("connection")
    if lock:
        rows = rows.select_for_update(of=("self",))
    found = rows.first()
    if found is None:
        raise NotFound(f"No feed named '{name}'.")
    return found


def _connection(org_id: str, name: str | None) -> Connection | None:
    if name is None:
        return None
    found = Connection.objects.filter(org_id=org_id, name=name).first()
    if found is None:
        raise NotFound(f"No connection named '{name}'.")
    return found


class FeedRegisterIn(FeedSettings):
    name: Code
    template: Template
    mode: Mode

    @model_validator(mode="after")
    def _combination(self) -> FeedRegisterIn:
        try:
            _check_combination(self.mode, self.model_dump())
        except InvalidInput as exc:
            raise ValueError(exc.message) from None
        return self


@action(
    name="feed.register",
    summary="Register a feed: template, source, cadence, owner and deadline.",
    schema=FeedRegisterIn,
    output=FeedOut,
    permission="feed.manage",
    read_only=False,
    requires_approval="config_change",
    audit="feed.registered",
    example={
        "name": "monthly_actuals",
        "template": "actual_monthly",
        "mode": "pull",
        "connection": "dw",
        "source_object": "kpi.v_actual_monthly",
        "cadence_cron": "0 6 * * *",
    },
)
def register(params: FeedRegisterIn, ctx: ActionContext) -> FeedOut:
    values = params.model_dump(exclude={"name", "template", "mode", "connection"})
    with conflicts(_CONFLICTS):
        feed = Feed.objects.create(
            org_id=ctx.org_id,
            name=params.name,
            template_name=params.template,
            mode=params.mode,
            connection=_connection(ctx.org_id, params.connection),
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            **values,
        )
    feed.refresh_from_db()
    return FeedOut.of(feed)


class FeedUpdateIn(FeedSettings):
    name: Code
    volume_warn_pct: Pct | None = None  # type: ignore[assignment]
    volume_reject_pct: Pct | None = None  # type: ignore[assignment]
    status: Literal["active", "paused"] | None = None  # type: ignore[assignment]


@action(
    name="feed.update",
    summary="Change a feed's source, cadence, owner, thresholds or status. Fields left out keep their value.",
    schema=FeedUpdateIn,
    output=FeedOut,
    permission="feed.manage",
    read_only=False,
    requires_approval="config_change",
    audit="feed.updated",
    example={"name": "monthly_actuals", "freshness_tolerance_hours": 36},
)
def update(params: FeedUpdateIn, ctx: ActionContext) -> FeedOut:
    feed = get_feed(ctx.org_id, params.name, lock=True)
    current: dict[str, Any] = {
        "connection": feed.connection.name if feed.connection is not None else None,
        **{f: getattr(feed, f) for f in _SETTABLE if f != "connection"},
    }
    changes = {
        f: getattr(params, f)
        for f in params.model_fields_set
        if f in _SETTABLE
        and not (
            getattr(params, f) is None and f in ("volume_warn_pct", "volume_reject_pct", "status")
        )
    }
    merged = current | changes
    _check_combination(feed.mode, merged)
    for field, value in changes.items():
        if field == "connection":
            feed.connection = _connection(ctx.org_id, value)
        else:
            setattr(feed, field, value)
    feed.updated_by = ctx.user_id
    feed.updated_at = timezone.now()
    feed.save()
    return FeedOut.of(feed)


class FeedListIn(BaseModel):
    freshness_state: Literal["never_loaded", "fresh", "stale"] | None = None
    template: Template | None = None


class FeedListOut(BaseModel):
    feeds: list[FeedOut]


@action(
    name="feed.list",
    summary="Feeds with their freshness, as-of time and last run.",
    schema=FeedListIn,
    output=FeedListOut,
    permission="feed.view",
    read_only=True,
    example={"freshness_state": "stale"},
)
def list_feeds(params: FeedListIn, ctx: ActionContext) -> FeedListOut:
    rows = Feed.objects.filter(org_id=ctx.org_id).select_related("connection")
    if params.freshness_state is not None:
        rows = rows.filter(freshness_state=params.freshness_state)
    if params.template is not None:
        rows = rows.filter(template_name=params.template)
    return FeedListOut(feeds=[FeedOut.of(f) for f in rows.order_by("name")])


class FeedGetIn(BaseModel):
    name: Code


class FeedDetailOut(BaseModel):
    feed: FeedOut
    recent_runs: list[RunSummary]


@action(
    name="feed.get",
    summary="One feed with its recent run history.",
    schema=FeedGetIn,
    output=FeedDetailOut,
    permission="feed.view",
    read_only=True,
    example={"name": "monthly_actuals"},
)
def get(params: FeedGetIn, ctx: ActionContext) -> FeedDetailOut:
    feed = get_feed(ctx.org_id, params.name)
    runs = FeedRun.objects.filter(feed=feed).order_by("-started_at")[:20]
    return FeedDetailOut(feed=FeedOut.of(feed), recent_runs=[run_summary(r) for r in runs])


class ContractIn(BaseModel):
    name: Code
    period_from: PeriodKey
    period_to: PeriodKey

    @model_validator(mode="after")
    def _ordered(self) -> ContractIn:
        if self.period_to < self.period_from:
            raise ValueError("period_to is before period_from")
        return self


class ContractOut(BaseModel):
    filename: str
    contract: dict[str, Any]


def _months(first: str, last: str) -> list[str]:
    out: list[str] = []
    year, month = int(first[:4]), int(first[4:])
    while f"{year:04d}{month:02d}" <= last and len(out) < 120:
        out.append(f"{year:04d}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


@action(
    name="feed.contract.export",
    summary="The feed's contract and reference data, for the client's standalone validator.",
    schema=ContractIn,
    output=ContractOut,
    permission="feed.view",
    read_only=True,
    example={"name": "monthly_actuals", "period_from": "202610", "period_to": "202610"},
)
def export_contract(params: ContractIn, ctx: ActionContext) -> ContractOut:
    feed = get_feed(ctx.org_id, params.name)
    template = v.TEMPLATES[feed.template_name]
    ref = build_reference(
        ctx.org_id, template, _months(params.period_from, params.period_to), feed=feed
    )
    contract = {
        "kpigo_contract": v.CONTRACT_VERSION,
        "template": template.name,
        "feed": feed.name,
        "generated_at": timezone.now().isoformat(),
        "periods": [params.period_from, params.period_to],
        "columns": [
            {"name": c.name, "kind": c.kind, "required": c.required, "nullable": c.nullable}
            for c in template.columns
        ],
        "grain": list(template.grain),
        "reference": v.reference_to_json(ref),
    }
    return ContractOut(filename=f"{feed.name}-contract.json", contract=contract)
