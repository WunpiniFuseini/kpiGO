"""The ingestion pipeline (TDD §5.1).

    DISCOVER  read the registered object, the dropped file or the upload
    LAND      raw rows into tmpl_<template> with the run_id, untouched
    VALIDATE  the six gates; every finding kept with row, column and rule
    DECIDE    dry run: report and stop, writing nothing but the run's report
              live: all-or-nothing commit, or quarantine the whole load
    CONFORM   canonical facts; unknown members registered as ``available``
    PUBLISH   freshness and the feed's last run

Every load carries a content hash: re-running the content of the feed's
current load is a no-op that returns that run. The caller is a registered
action, so the pipeline's own transaction wraps all of it; conform runs in a
savepoint so a failure part-way through rolls back to nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from kpigo.action.context import ActionContext
from kpigo.ingestion import freshness
from kpigo.ingestion import validator as v
from kpigo.ingestion.conform import conform
from kpigo.ingestion.models import LANDING, Feed, FeedRejection, FeedRun
from kpigo.ingestion.reference import build_reference, periods_in, staff_nos_in

logger = logging.getLogger("kpigo.ingestion")

# Findings beyond this are counted but not stored; the counts stay exact.
MAX_STORED_FINDINGS = 100_000
LANDING_BATCH = 5000


@dataclass
class RunResult:
    run: FeedRun
    idempotent: bool = False
    findings: list[v.Issue] = field(default_factory=list)
    registered: dict[str, list[str]] = field(default_factory=dict)


def latest_success(feed: Feed) -> FeedRun | None:
    return (
        FeedRun.objects.filter(feed=feed, is_dry_run=False, outcome="success")
        .order_by("-started_at")
        .first()
    )


def has_passing_dry_run(feed: Feed) -> bool:
    return FeedRun.objects.filter(feed=feed, is_dry_run=True, outcome="success").exists()


def land(run: FeedRun, template: v.Template, table: v.RawTable) -> None:
    model = LANDING[template.name]
    index = {h: i for i, h in enumerate(table.header) if h in template.column_names}
    batch: list[Any] = []
    for n, record in enumerate(table.rows, start=1):
        fields = {name: record[i] if i < len(record) else None for name, i in index.items()}
        batch.append(
            model(org_id=run.org_id, run=run, row_no=n, created_by=run.created_by, **fields)
        )
        if len(batch) >= LANDING_BATCH:
            model._default_manager.bulk_create(batch)
            batch = []
    if batch:
        model._default_manager.bulk_create(batch)


def landed(run_id: Any, template: v.Template) -> list[dict[str, str | None]]:
    model = LANDING[template.name]
    return list(
        model._default_manager.filter(run_id=run_id)
        .order_by("row_no")
        .values(*template.column_names)
    )


def store_findings(run: FeedRun, issues: list[v.Issue]) -> None:
    rows = [
        FeedRejection(
            run=run,
            seq=seq,
            row_no=i.row_no,
            column_name=i.column,
            gate=i.gate,
            rule=i.rule,
            severity=i.severity,
            value=i.value,
            message=i.message,
            created_by=run.created_by,
        )
        for seq, i in enumerate(issues[:MAX_STORED_FINDINGS], start=1)
    ]
    for start in range(0, len(rows), LANDING_BATCH):
        FeedRejection.objects.bulk_create(rows[start : start + LANDING_BATCH])


def _finish(run: FeedRun, *, state: str, outcome: str, error: str | None = None) -> None:
    run.state = state
    run.outcome = outcome
    run.error_text = error
    run.finished_at = timezone.now()
    run.save()


def execute(
    feed: Feed,
    ctx: ActionContext,
    *,
    read: Callable[[], v.RawTable],
    dry_run: bool,
    trigger: str,
    restatement: bool = False,
) -> RunResult:
    template = v.TEMPLATES[feed.template_name]
    now = timezone.now()
    run = FeedRun(
        org_id=feed.org_id,
        feed=feed,
        started_at=now,
        trigger=trigger,
        is_dry_run=dry_run,
        restatement=restatement,
        state="running",
        created_by=ctx.user_id,
    )

    # DISCOVER
    try:
        table = read()
    except (v.SourceError, ValueError, OSError) as exc:
        run.save()
        _finish(run, state="failed", outcome="failed", error=str(exc))
        _after_live(feed, run, dry_run)
        ctx.audit("feed.failed", feed=feed.name, run_id=str(run.run_id), error=str(exc))
        return RunResult(run=run)
    run.source_name = table.source_name
    run.rows_read = len(table.rows)
    run.content_hash = v.content_hash(template, table)

    if not dry_run:
        current = latest_success(feed)
        if current is not None and current.content_hash == run.content_hash:
            ctx.audit(
                "feed.unchanged",
                feed=feed.name,
                run_id=str(current.run_id),
                source=table.source_name,
            )
            return RunResult(run=current, idempotent=True)

    run.save()

    # LAND
    land(run, template, table)

    # VALIDATE
    ref = build_reference(
        str(feed.org_id),
        template,
        periods_in(template, table),
        feed=feed,
        restatement=restatement,
        staff_nos=staff_nos_in(table),
    )
    result = v.validate(template, table, ref)
    store_findings(run, result.issues)
    run.rows_rejected = len(result.rejected_row_nos)
    run.rows_accepted = result.rows_read - run.rows_rejected
    run.warnings = len(result.warnings)
    run.periods = result.periods
    run.gate_summary = result.gate_summary()
    previous = latest_success(feed)
    run.diff_summary = v.diff(
        template,
        landed(run.run_id, template),
        landed(previous.run_id, template) if previous is not None else [],
    ) | {"against_run_id": str(previous.run_id) if previous is not None else None}

    # DECIDE
    outcome = "success" if result.passed else "quarantined"
    if dry_run:
        # Writes nothing: the landed rows go, only the run and its report remain.
        LANDING[template.name]._default_manager.filter(run=run).delete()
        _finish(run, state="validated", outcome=outcome)
        ctx.audit(
            "feed.dry_run",
            feed=feed.name,
            run_id=str(run.run_id),
            passed=result.passed,
            rows_read=run.rows_read,
            rows_rejected=run.rows_rejected,
        )
        return RunResult(run=run, findings=result.issues)

    if not result.passed:
        run.rows_accepted = 0
        _finish(run, state="quarantined", outcome="quarantined")
        _after_live(feed, run, dry_run)
        ctx.audit(
            "feed.quarantined",
            feed=feed.name,
            run_id=str(run.run_id),
            rows_read=run.rows_read,
            rows_rejected=run.rows_rejected,
            errors=len(result.errors),
        )
        return RunResult(run=run, findings=result.issues)

    # CONFORM, all or nothing
    run.state = "validated"
    run.save(update_fields=["state"])
    try:
        with transaction.atomic():
            registered = conform(feed, run, result.accepted_rows, user_id=ctx.user_id, now=now)
    except Exception as exc:
        logger.exception("conform failed for feed %s run %s", feed.name, run.run_id)
        run.rows_accepted = 0
        _finish(
            run, state="failed", outcome="failed", error=f"Conform failed: {type(exc).__name__}"
        )
        _after_live(feed, run, dry_run)
        ctx.audit("feed.failed", feed=feed.name, run_id=str(run.run_id), error=type(exc).__name__)
        return RunResult(run=run, findings=result.issues)

    # PUBLISH
    run.supersedes_run_id = previous.run_id if previous is not None else None
    _finish(run, state="committed", outcome="success")
    feed.last_success_at = run.finished_at
    _after_live(feed, run, dry_run)
    freshness.refresh(feed, timezone.now())
    if any(registered.values()):
        ctx.audit("feed.unmapped_registered", feed=feed.name, run_id=str(run.run_id), **registered)
    ctx.audit(
        "feed.loaded",
        feed=feed.name,
        run_id=str(run.run_id),
        rows=run.rows_accepted,
        periods=run.periods,
    )
    return RunResult(run=run, findings=result.issues, registered=registered)


def _after_live(feed: Feed, run: FeedRun, dry_run: bool) -> None:
    if dry_run:
        return
    feed.last_run_id = run.run_id
    feed.updated_at = timezone.now()
    feed.save()
