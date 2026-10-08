"""The target workbench (PRD SC-10, SC-11; Scope §6.7; App Flow §7.4).

Upload a sheet or copy last cycle forward → validated before anything is
written → drafts, visible only here → publish as a batch → live. The coverage
grid answers "are we ready to open the cycle?" on one screen.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from django.conf import settings
from django.db.models import Q
from pydantic import BaseModel, Field, StringConstraints, field_serializer, model_validator

from kpigo.action import ActionContext, InvalidInput, NotFound, action
from kpigo.hierarchy.models import Subject
from kpigo.hierarchy.scope import current_period_key
from kpigo.ingestion import validator as v
from kpigo.metrics.actions.metric import MetricCode
from kpigo.platform.vocab import Code, PeriodKey
from kpigo.scorecards import targets as wb
from kpigo.scorecards.cycles import period_range, product_cycle
from kpigo.scorecards.models import Target, TargetPublishBatch

SheetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"
FINDINGS_SHOWN = 200
ROW_CAP = 50_000


class TargetSheet(BaseModel):
    """A CSV or XLSX target sheet with the ``tmpl_target`` columns."""

    filename: SheetName
    content_base64: str = Field(min_length=1)

    @field_serializer("content_base64", when_used="json")
    def _summarise(self, value: str) -> str:
        """The audit log records what was sent, never the sheet itself."""
        digest = hashlib.sha256(value.encode()).hexdigest()[:16]
        return f"<{len(value)} base64 characters, sha256 {digest}>"


class TargetRowIn(BaseModel):
    metric_code: str
    scope_type: str
    # A profile code, or for subject-level metrics a staff number or subject_id.
    scope_code: str
    period_key: str
    series_type: str = "target"
    target_value: str
    target_type: str = "monthly"
    weight: str | None = None
    cap: str | None = None
    currency_code: str | None = None
    # An Agent Performance target for one product line; omitted for the metric's own.
    product_line_code: str | None = None


class FindingOut(BaseModel):
    code: str
    message: str
    severity: str
    row_no: int | None
    column: str | None
    value: str | None


class SheetResultOut(BaseModel):
    rows_read: int
    rows_valid: int
    errors: int
    warnings: int
    # The first findings, row order; the counts above are complete.
    findings: list[FindingOut]
    written: int
    # Nothing is written when any row has an error: a sheet is all or nothing.
    accepted: bool


def _decode_sheet(sheet: TargetSheet) -> list[dict[str, Any]]:
    limit = int(getattr(settings, "KPIGO_UPLOAD_MAX_BYTES", 50 * 1024 * 1024))
    if len(sheet.content_base64) > limit * 4 // 3 + 4:
        raise InvalidInput(f"The sheet is larger than the {limit // (1024 * 1024)} MB limit.")
    try:
        data = base64.b64decode(sheet.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidInput("The sheet is not valid base64.") from None
    try:
        table = v.read_file(sheet.filename, data, row_cap=ROW_CAP)
    except v.SourceError as exc:
        raise InvalidInput(str(exc)) from None
    missing = [c for c in wb.REQUIRED if c not in table.header]
    if missing:
        raise InvalidInput(
            "The sheet is missing required columns.",
            detail={"missing": missing, "expected": list(wb.COLUMNS)},
        )
    return [dict(zip(table.header, row, strict=False)) for row in table.rows]


def _result(checked: wb.Checked, rows_read: int, written: int) -> SheetResultOut:
    errors = len(checked.errors)
    return SheetResultOut(
        rows_read=rows_read,
        rows_valid=len(checked.drafts),
        errors=errors,
        warnings=len(checked.findings) - errors,
        findings=[
            FindingOut(
                code=f.code,
                message=f.message,
                severity=f.severity,
                row_no=f.row_no,
                column=f.column,
                value=f.value,
            )
            for f in checked.findings[:FINDINGS_SHOWN]
        ],
        written=written,
        accepted=errors == 0,
    )


class UploadIn(BaseModel):
    # One of: a sheet (CSV or XLSX), or rows typed or pasted into the workbench.
    sheet: TargetSheet | None = None
    rows: list[TargetRowIn] | None = Field(default=None, max_length=ROW_CAP)
    # Validate only and write nothing (the "check" button).
    check_only: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> UploadIn:
        if (self.sheet is None) == (self.rows is None):
            raise ValueError("give a sheet or rows, not both")
        return self


@action(
    name="target.upload",
    summary="Validate a target sheet and, when it is clean, save it as drafts.",
    schema=UploadIn,
    output=SheetResultOut,
    permission="target.manage",
    read_only=False,
    module="scorecards",
    audit="target.uploaded",
    example={
        "rows": [
            {
                "metric_code": "total_deposits",
                "scope_type": "profile",
                "scope_code": "retail_rm",
                "period_key": "202701",
                "target_value": "4200000",
                "weight": "20",
                "cap": "30",
            }
        ],
        "check_only": True,
    },
)
def upload(params: UploadIn, ctx: ActionContext) -> SheetResultOut:
    rows: list[dict[str, Any]]
    first_row_no = 1
    if params.sheet is not None:
        rows = _decode_sheet(params.sheet)
        first_row_no = 2  # the header is row 1 in the sheet the user opens
    else:
        rows = [r.model_dump() for r in params.rows or []]
    if not rows:
        raise InvalidInput("The sheet has no rows.")
    checked = wb.check_rows(ctx.org_id, rows, first_row_no=first_row_no)
    written = 0
    if not checked.errors and not params.check_only:
        written = wb.save_drafts(ctx.org_id, checked.drafts, source="upload", user_id=ctx.user_id)
    return _result(checked, len(rows), written)


class CopyForwardIn(BaseModel):
    # The first and last source periods, e.g. last cycle's twelve months.
    from_period: PeriodKey
    to_period: PeriodKey
    # How far forward to copy; 12 is "same months next year".
    months: int = Field(default=12, ge=1, le=60)
    # Across-the-board uplift, in percent (5 = +5%). A metric's own figure wins.
    uplift_pct: Decimal = Field(default=Decimal(0), ge=-100, le=1000)
    metric_uplift: dict[str, Decimal] = Field(default_factory=dict)
    check_only: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> CopyForwardIn:
        if self.to_period < self.from_period:
            raise ValueError("to_period must not be before from_period")
        if len(period_range(self.from_period, self.to_period)) > 24:
            raise ValueError("copy at most 24 periods at once")
        for pct in self.metric_uplift.values():
            if not Decimal(-100) <= pct <= Decimal(1000):
                raise ValueError("a metric uplift must be between -100 and 1000 percent")
        return self


@action(
    name="target.copy_forward",
    summary="Copy published targets forward as drafts, with an optional uplift.",
    schema=CopyForwardIn,
    output=SheetResultOut,
    permission="target.manage",
    read_only=False,
    module="scorecards",
    audit="target.copied_forward",
    example={"from_period": "202601", "to_period": "202612", "uplift_pct": "5", "check_only": True},
)
def copy_forward(params: CopyForwardIn, ctx: ActionContext) -> SheetResultOut:
    rows = wb.copy_forward_rows(
        ctx.org_id,
        period_range(params.from_period, params.to_period),
        months=params.months,
        uplift_pct=params.uplift_pct,
        metric_uplift=params.metric_uplift,
    )
    if not rows:
        raise InvalidInput("There are no published targets in those periods to copy.")
    checked = wb.check_rows(ctx.org_id, rows)
    written = 0
    if not checked.errors and not params.check_only:
        written = wb.save_drafts(
            ctx.org_id, checked.drafts, source="copy_forward", user_id=ctx.user_id
        )
    return _result(checked, len(rows), written)


class TargetDiscardIn(BaseModel):
    target_ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)


class TargetDiscardOut(BaseModel):
    discarded: int


@action(
    name="target.draft.discard",
    summary="Throw away drafts. Published targets are never deleted.",
    schema=TargetDiscardIn,
    output=TargetDiscardOut,
    permission="target.manage",
    read_only=False,
    module="scorecards",
    audit="target.drafts_discarded",
    example={"target_ids": [EXAMPLE_ID]},
)
def discard(params: TargetDiscardIn, ctx: ActionContext) -> TargetDiscardOut:
    deleted, _ = Target.objects.filter(
        org_id=ctx.org_id, target_id__in=params.target_ids, state="draft"
    ).delete()
    return TargetDiscardOut(discarded=deleted)


# ── reads ───────────────────────────────────────────────────────────────────


class TargetOut(BaseModel):
    target_id: uuid.UUID
    metric_code: str
    metric_name: str
    scope_type: str
    scope_code: str
    # The profile code, or "staff_no full name" for a subject.
    scope_label: str
    period_key: str
    series_type: str
    target_value: Decimal
    target_type: str
    weight: Decimal | None
    cap: Decimal | None
    currency_code: str | None
    # "" for the metric's own target; else the product line it is for.
    product_line_code: str
    version: int
    state: str
    source: str
    batch_id: uuid.UUID | None
    published_at: datetime | None


class TargetListIn(BaseModel):
    period_key: PeriodKey | None = None
    metric_code: MetricCode | None = None
    scope_code: Code | None = None
    state: Literal["draft", "published", "superseded"] | None = None
    # "" for the metrics' own targets only; a code for one line's.
    product_line_code: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=500, ge=1, le=5000)


class TargetListOut(BaseModel):
    targets: list[TargetOut]
    truncated: bool


def target_rows(rows: list[Target]) -> list[TargetOut]:
    subject_ids = {t.scope_code for t in rows if t.scope_type == "subject"}
    labels = {
        str(s.subject_id): f"{s.staff_no} {s.full_name}"
        for s in Subject.objects.filter(subject_id__in=subject_ids)
    }
    return [
        TargetOut(
            target_id=t.target_id,
            metric_code=t.metric.metric_code,
            metric_name=t.metric.display_name,
            scope_type=t.scope_type,
            scope_code=t.scope_code,
            scope_label=labels.get(t.scope_code, t.scope_code),
            period_key=t.period_key,
            series_type=t.series_type,
            target_value=t.target_value,
            target_type=t.target_type,
            weight=t.weight,
            cap=t.cap,
            currency_code=t.currency_code,
            product_line_code=t.product_line_code,
            version=t.version,
            state=t.state,
            source=t.source,
            batch_id=t.batch_id,
            published_at=t.published_at,
        )
        for t in rows
    ]


@action(
    name="target.list",
    summary="Targets on the workbench, filtered by period, metric, scope or state.",
    schema=TargetListIn,
    output=TargetListOut,
    permission="target.view",
    read_only=True,
    module="scorecards",
    example={"period_key": "202701", "state": "draft"},
)
def list_targets(params: TargetListIn, ctx: ActionContext) -> TargetListOut:
    rows = Target.objects.filter(org_id=ctx.org_id).select_related("metric")
    if params.period_key is not None:
        rows = rows.filter(period_key=params.period_key)
    if params.metric_code is not None:
        rows = rows.filter(metric__metric_code=params.metric_code)
    if params.scope_code is not None:
        subject = Subject.objects.filter(org_id=ctx.org_id, staff_no=params.scope_code).first()
        codes = [params.scope_code] + ([str(subject.subject_id)] if subject else [])
        rows = rows.filter(scope_code__in=codes)
    if params.state is not None:
        rows = rows.filter(state=params.state)
    if params.product_line_code is not None:
        rows = rows.filter(product_line_code=params.product_line_code)
    found = list(
        rows.order_by(
            "period_key",
            "metric__metric_code",
            "scope_type",
            "scope_code",
            "product_line_code",
            "-version",
        )[: params.limit + 1]
    )
    return TargetListOut(
        targets=target_rows(found[: params.limit]), truncated=len(found) > params.limit
    )


class CoverageIn(BaseModel):
    # Any period in the cycle to show; defaults to the current one. The grid
    # covers the whole cycle year the Scorecards product follows.
    period_key: PeriodKey | None = None
    profile_code: Code | None = None


class CoverageCellOut(BaseModel):
    profile_code: str
    metric_code: str
    period_key: str
    target_scope: str
    # published · draft (new, unpublished) · revision (draft over a published one)
    # · partial (some subjects set) · missing
    state: str
    expected: int
    published: int
    drafts: int


class WeightCheckOut(BaseModel):
    profile_code: str
    period_key: str
    weight_sum: Decimal
    expected: Decimal
    complete: bool
    ok: bool
    missing: list[str]
    subjects_off: list[dict[str, str]]


class CoverageOut(BaseModel):
    cycle_name: str
    period_keys: list[str]
    profiles: list[str]
    cells: list[CoverageCellOut]
    # Over published targets with drafts applied: what publishing would produce.
    weights: list[WeightCheckOut]
    complete: bool
    gaps: int
    drafts: int


@action(
    name="target.coverage",
    summary="The coverage grid: profile × metric × period, what is set, missing or draft.",
    schema=CoverageIn,
    output=CoverageOut,
    permission="target.view",
    read_only=True,
    module="scorecards",
    example={"period_key": "202701"},
)
def coverage(params: CoverageIn, ctx: ActionContext) -> CoverageOut:
    anchor = params.period_key or current_period_key(ctx.org_id)
    cycle = product_cycle(ctx.org_id, "scorecards", anchor)
    periods = cycle.periods(anchor)
    profiles = [params.profile_code] if params.profile_code else None
    cells = wb.coverage(ctx.org_id, periods, profiles)
    checks = wb.weight_checks(ctx.org_id, periods, include_drafts=True, profiles=profiles)
    return CoverageOut(
        cycle_name=cycle.name,
        period_keys=periods,
        profiles=sorted({c.profile_code for c in cells}),
        cells=[CoverageCellOut(**c.__dict__) for c in cells],
        weights=[WeightCheckOut(**c.as_dict()) for c in checks],
        complete=bool(cells) and all(c.state in ("published", "revision") for c in cells),
        gaps=sum(c.state in ("missing", "partial") for c in cells),
        drafts=sum(c.state in ("draft", "revision") for c in cells)
        + sum(1 for c in cells if c.state == "partial" and c.drafts),
    )


# ── publish and batches ─────────────────────────────────────────────────────


class BatchOut(BaseModel):
    batch_id: uuid.UUID
    period_keys: list[str]
    published_at: datetime
    published_by: int | None
    row_count: int
    note: str
    status: str
    reverted_at: datetime | None
    weight_check_result: dict[str, Any]


def _batch_out(b: TargetPublishBatch) -> BatchOut:
    return BatchOut(
        batch_id=b.batch_id,
        period_keys=list(b.period_keys),
        published_at=b.published_at,
        published_by=b.published_by,
        row_count=b.row_count,
        note=b.note,
        status=b.status,
        reverted_at=b.reverted_at,
        weight_check_result=b.weight_check_result,
    )


class PublishIn(BaseModel):
    period_keys: list[PeriodKey] = Field(min_length=1, max_length=24)
    # Publish only these metrics' drafts; omit for every draft in the periods.
    metric_codes: list[MetricCode] | None = None
    note: str = Field(default="", max_length=2000)


class PublishOut(BaseModel):
    batch: BatchOut
    superseded: int


@action(
    name="target.publish",
    agent_forbidden=True,
    summary="Publish drafts as one versioned batch, after the weight-sum check.",
    schema=PublishIn,
    output=PublishOut,
    permission="target.publish",
    read_only=False,
    module="scorecards",
    requires_approval="target_publish",
    audit="target.published",
    config_change=True,
    example={"period_keys": ["202701", "202702"], "note": "FY2027 targets"},
)
def publish(params: PublishIn, ctx: ActionContext) -> PublishOut:
    cycle = product_cycle(ctx.org_id, "scorecards", min(params.period_keys))
    done = wb.publish(
        ctx.org_id,
        sorted(set(params.period_keys)),
        metric_codes=params.metric_codes,
        cycle_id=cycle.cycle_id,
        note=params.note,
        user_id=ctx.user_id,
    )
    ctx.audit(
        "target.batch_published",
        batch_id=str(done.batch.batch_id),
        rows=done.batch.row_count,
        superseded=done.superseded,
    )
    return PublishOut(batch=_batch_out(done.batch), superseded=done.superseded)


class BatchListIn(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)


class BatchListOut(BaseModel):
    batches: list[BatchOut]


@action(
    name="target.batch.list",
    summary="Publish batches, newest first.",
    schema=BatchListIn,
    output=BatchListOut,
    permission="target.view",
    read_only=True,
    module="scorecards",
    example={},
)
def list_batches(params: BatchListIn, ctx: ActionContext) -> BatchListOut:
    rows = TargetPublishBatch.objects.filter(org_id=ctx.org_id).order_by("-published_at")
    return BatchListOut(batches=[_batch_out(b) for b in rows[: params.limit]])


class BatchRevertIn(BaseModel):
    batch_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


class BatchRevertOut(BaseModel):
    batch: BatchOut
    restored: int


@action(
    name="target.batch.revert",
    agent_forbidden=True,
    summary="Revert a publish while its periods are still in the future.",
    schema=BatchRevertIn,
    output=BatchRevertOut,
    permission="target.publish",
    read_only=False,
    module="scorecards",
    requires_approval="target_publish",
    audit="target.batch_reverted",
    config_change=True,
    example={"batch_id": EXAMPLE_ID, "reason": "Published the wrong uplift"},
)
def revert_batch(params: BatchRevertIn, ctx: ActionContext) -> BatchRevertOut:
    batch, restored = wb.revert(ctx.org_id, str(params.batch_id), user_id=ctx.user_id)
    return BatchRevertOut(batch=_batch_out(batch), restored=restored)


class TargetGetIn(BaseModel):
    target_id: uuid.UUID


class TargetHistoryOut(BaseModel):
    target: TargetOut
    # Every version of the same key, newest first.
    versions: list[TargetOut]


@action(
    name="target.get",
    summary="One target and every version of it.",
    schema=TargetGetIn,
    output=TargetHistoryOut,
    permission="target.view",
    read_only=True,
    module="scorecards",
    example={"target_id": EXAMPLE_ID},
)
def get_target(params: TargetGetIn, ctx: ActionContext) -> TargetHistoryOut:
    t = (
        Target.objects.filter(org_id=ctx.org_id, target_id=params.target_id)
        .select_related("metric")
        .first()
    )
    if t is None:
        raise NotFound("No such target.")
    versions = list(
        Target.objects.filter(
            Q(metric_id=t.metric_id)
            & Q(scope_type=t.scope_type, scope_code=t.scope_code)
            & Q(period_key=t.period_key, series_type=t.series_type)
            & Q(product_line_code=t.product_line_code)
        )
        .select_related("metric")
        .order_by("-version")
    )
    return TargetHistoryOut(target=target_rows([t])[0], versions=target_rows(versions))
