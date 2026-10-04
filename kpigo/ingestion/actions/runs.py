"""Running feeds: dry run, live load, run history and the rejection report (PRD IN-5 … IN-8)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from collections import Counter
from typing import Annotated, Any

from django.conf import settings
from django.db import transaction
from pydantic import BaseModel, Field, StringConstraints, field_serializer, model_validator

from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.ingestion import sources
from kpigo.ingestion import validator as v
from kpigo.ingestion.actions.feeds import RunSummary, get_feed, run_summary
from kpigo.ingestion.models import Feed, FeedRejection, FeedRun
from kpigo.ingestion.pipeline import RunResult, execute, has_passing_dry_run, latest_success
from kpigo.platform.vocab import Code

FileName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
FINDINGS_SHOWN = 50


class Upload(BaseModel):
    filename: FileName
    content_base64: str = Field(min_length=1)

    @field_serializer("content_base64", when_used="json")
    def _summarise(self, value: str) -> str:
        """The audit log records what was sent, never the file itself."""
        digest = hashlib.sha256(value.encode()).hexdigest()[:16]
        return f"<{len(value)} base64 characters, sha256 {digest}>"


class FeedRunIn(BaseModel):
    feed: Code
    # Drop feeds: a file in the feed's drop folder (relative to it).
    file: FileName | None = None
    # Upload feeds: the file itself.
    upload: Upload | None = None
    # Allows writing to a closing, closed or restating period.
    restatement: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> FeedRunIn:
        if self.file is not None and self.upload is not None:
            raise ValueError("give a drop-folder file or an upload, not both")
        if self.file is not None:
            sources.safe_relative(self.file)
        return self


class Finding(BaseModel):
    row_no: int | None
    column: str | None
    gate: str
    rule: str
    severity: str
    value: str | None
    message: str


class RunOut(BaseModel):
    run_id: uuid.UUID
    feed: str
    is_dry_run: bool
    # True when the content matched the feed's current load: nothing ran.
    idempotent: bool
    state: str
    outcome: str | None
    passed: bool
    trigger: str
    source_name: str
    restatement: bool
    rows_read: int
    rows_accepted: int
    rows_rejected: int
    warnings: int
    content_hash: str | None
    periods: list[str]
    gates: dict[str, dict[str, int]]
    rules: dict[str, int]
    diff: dict[str, Any] | None
    error: str | None
    registered_members: list[str] = []
    registered_product_lines: list[str] = []
    findings: list[Finding] = []
    findings_total: int = 0


def _finding(i: v.Issue | FeedRejection) -> Finding:
    if isinstance(i, FeedRejection):
        return Finding(
            row_no=i.row_no,
            column=i.column_name,
            gate=i.gate,
            rule=i.rule,
            severity=i.severity,
            value=i.value,
            message=i.message,
        )
    return Finding(
        row_no=i.row_no,
        column=i.column,
        gate=i.gate,
        rule=i.rule,
        severity=i.severity,
        value=i.value,
        message=i.message,
    )


def run_out(run: FeedRun, *, idempotent: bool = False, result: RunResult | None = None) -> RunOut:
    stored = FeedRejection.objects.filter(run=run)
    rules = Counter(stored.values_list("rule", flat=True))
    if result is not None and not idempotent:
        findings = [_finding(i) for i in result.findings[:FINDINGS_SHOWN]]
        total = len(result.findings)
    else:
        findings = [_finding(r) for r in stored.order_by("seq")[:FINDINGS_SHOWN]]
        total = stored.count()
    registered = result.registered if result is not None else {}
    return RunOut(
        run_id=run.run_id,
        feed=run.feed.name,
        is_dry_run=run.is_dry_run,
        idempotent=idempotent,
        state=run.state,
        outcome=run.outcome,
        passed=run.outcome == "success",
        trigger=run.trigger,
        source_name=run.source_name,
        restatement=run.restatement,
        rows_read=run.rows_read,
        rows_accepted=run.rows_accepted,
        rows_rejected=run.rows_rejected,
        warnings=run.warnings,
        content_hash=run.content_hash,
        periods=list(run.periods or []),
        gates=run.gate_summary or {},
        rules=dict(sorted(rules.items())),
        diff=run.diff_summary,
        error=run.error_text,
        registered_members=registered.get("members", []),
        registered_product_lines=registered.get("product_lines", []),
        findings=findings,
        findings_total=total,
    )


def _decode(upload: Upload) -> bytes:
    limit = int(settings.KPIGO_UPLOAD_MAX_BYTES)
    if len(upload.content_base64) > limit * 4 // 3 + 4:
        raise InvalidInput(f"The upload is larger than {limit} bytes.")
    try:
        return base64.b64decode(upload.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidInput("content_base64 is not valid base64.") from None


def _reader(feed: Feed, params: FeedRunIn) -> tuple[Any, str]:
    """The source for this run and the trigger it records."""
    if feed.mode == "pull":
        if params.file is not None or params.upload is not None:
            raise InvalidInput("A pull feed reads its registered object; give no file.")
        return (lambda: sources.pull(feed)), "manual"
    if feed.mode == "drop":
        if params.file is None:
            raise InvalidInput("Name the file in the drop folder to run.")
        name = params.file
        return (lambda: sources.read_drop(feed, name)), "drop"
    if params.upload is None:
        raise InvalidInput("An upload feed needs the file: upload.filename and content_base64.")
    data = _decode(params.upload)
    upload = params.upload
    return (lambda: v.read_file(upload.filename, data, row_cap=sources.row_cap())), "upload"


def _trigger(ctx: ActionContext, trigger: str) -> str:
    return "schedule" if ctx.caller == "job" and trigger == "manual" else trigger


@action(
    name="feed.dry_run",
    summary="Read a feed and run every gate, writing nothing but the run's report.",
    schema=FeedRunIn,
    output=RunOut,
    permission="feed.run",
    read_only=False,
    audit="feed.dry_run_requested",
    example={"feed": "monthly_actuals"},
)
def dry_run(params: FeedRunIn, ctx: ActionContext) -> RunOut:
    feed = get_feed(ctx.org_id, params.feed)
    read, trigger = _reader(feed, params)
    result = execute(
        feed,
        ctx,
        read=read,
        dry_run=True,
        trigger=_trigger(ctx, trigger),
        restatement=params.restatement,
    )
    return run_out(result.run, result=result)


@action(
    name="feed.run",
    summary="Load a feed all or nothing: commit every row, or quarantine the whole load.",
    schema=FeedRunIn,
    output=RunOut,
    permission="feed.run",
    read_only=False,
    audit="feed.run_requested",
    # A load changes what every cached figure is computed from.
    config_change=True,
    example={"feed": "monthly_actuals"},
)
def run(params: FeedRunIn, ctx: ActionContext) -> RunOut:
    feed = get_feed(ctx.org_id, params.feed, lock=True)
    if feed.status != "active":
        raise Conflict(f"Feed '{feed.name}' is paused.")
    if latest_success(feed) is None and not has_passing_dry_run(feed):
        raise Conflict(
            f"Feed '{feed.name}' has not passed a dry run. Its first live load needs one (IN-7).",
            detail={"next": "feed.dry_run"},
        )
    read, trigger = _reader(feed, params)
    result = execute(
        feed,
        ctx,
        read=read,
        dry_run=False,
        trigger=_trigger(ctx, trigger),
        restatement=params.restatement,
    )
    if feed.mode == "drop" and params.file is not None and not ctx.dry_run:
        name, ok = params.file, result.run.outcome == "success"
        transaction.on_commit(lambda: sources.file_away(feed, name, ok))
    return run_out(result.run, idempotent=result.idempotent, result=result)


class RunListIn(BaseModel):
    feed: Code
    is_dry_run: bool | None = None
    limit: int = Field(default=20, ge=1, le=200)


class RunListOut(BaseModel):
    feed: str
    runs: list[RunSummary]


@action(
    name="feed.run.list",
    summary="A feed's run history, newest first.",
    schema=RunListIn,
    output=RunListOut,
    permission="feed.view",
    read_only=True,
    example={"feed": "monthly_actuals"},
)
def list_runs(params: RunListIn, ctx: ActionContext) -> RunListOut:
    feed = get_feed(ctx.org_id, params.feed)
    rows = FeedRun.objects.filter(feed=feed)
    if params.is_dry_run is not None:
        rows = rows.filter(is_dry_run=params.is_dry_run)
    return RunListOut(
        feed=feed.name,
        runs=[run_summary(r) for r in rows.order_by("-started_at")[: params.limit]],
    )


class RunGetIn(BaseModel):
    run_id: uuid.UUID


def _get_run(ctx: ActionContext, run_id: uuid.UUID) -> FeedRun:
    found = FeedRun.objects.filter(org_id=ctx.org_id, run_id=run_id).select_related("feed").first()
    if found is None:
        raise NotFound("No such feed run.")
    return found


@action(
    name="feed.run.get",
    summary="One run: counts per gate and rule, the diff against the last load, first findings.",
    schema=RunGetIn,
    output=RunOut,
    permission="feed.view",
    read_only=True,
    example={"run_id": "00000000-0000-0000-0000-000000000000"},
)
def get_run(params: RunGetIn, ctx: ActionContext) -> RunOut:
    return run_out(_get_run(ctx, params.run_id))


class ExportOut(BaseModel):
    filename: str
    content_type: str
    rows: int
    content: str


@action(
    name="feed.rejections.export",
    summary="The run's rejection report as CSV: row, column, gate, rule and message.",
    schema=RunGetIn,
    output=ExportOut,
    permission="feed.view",
    read_only=True,
    example={"run_id": "00000000-0000-0000-0000-000000000000"},
)
def export_rejections(params: RunGetIn, ctx: ActionContext) -> ExportOut:
    run_row = _get_run(ctx, params.run_id)
    stored = FeedRejection.objects.filter(run=run_row).order_by("seq")
    issues = [
        v.Issue(
            gate=r.gate,  # type: ignore[arg-type]
            rule=r.rule,
            severity=r.severity,  # type: ignore[arg-type]
            message=r.message,
            row_no=r.row_no,
            column=r.column_name,
            value=r.value,
        )
        for r in stored
    ]
    stamp = run_row.started_at.strftime("%Y%m%d-%H%M")
    return ExportOut(
        filename=f"{run_row.feed.name}-{stamp}-rejections.csv",
        content_type="text/csv",
        rows=len(issues),
        content=v.report_csv(issues),
    )
