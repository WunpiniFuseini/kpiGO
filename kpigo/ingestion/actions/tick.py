"""The ingestion heartbeat: watch drop folders, start due pulls, track freshness.

Scheduled every minute through the job adapter (``KPIGO_INGESTION_SCHEDULE_USER``).
It does no loading itself: each file and each due pull becomes its own
``feed.run`` job, so every load is a separately audited, all-or-nothing action
run as the same named user.
"""

from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel

from kpigo.action import ActionContext, InvalidInput, action
from kpigo.action.adapters.jobs import task_name
from kpigo.ingestion import freshness, schedule, sources
from kpigo.ingestion import validator as v
from kpigo.ingestion.models import Feed
from kpigo.ingestion.pipeline import has_passing_dry_run, latest_success
from kpigo.platform.config import reporting_zone


class TickIn(BaseModel):
    pass


class Waiting(BaseModel):
    feed: str
    reason: str


class FreshnessChange(BaseModel):
    feed: str
    before: str
    after: str
    reason: str | None


class TickOut(BaseModel):
    enqueued: list[dict[str, str]]
    waiting: list[Waiting]
    freshness_changes: list[FreshnessChange]


def enqueue(params: dict[str, Any], run_as: str) -> None:
    """Queue a ``feed.run`` job once this transaction commits."""
    from kpigo.celery import app

    task = app.tasks[task_name("feed.run")]
    transaction.on_commit(lambda: task.apply_async(kwargs={"params": params, "run_as": run_as}))


@action(
    name="feed.tick",
    summary="Pick up dropped files, start due pulls and mark feeds fresh or stale.",
    schema=TickIn,
    output=TickOut,
    permission="feed.run",
    read_only=False,
    audit="feed.ticked",
    example={},
)
def tick(params: TickIn, ctx: ActionContext) -> TickOut:
    if ctx.user is None:
        raise InvalidInput("The tick runs as a named user; its jobs run as that user too.")
    run_as = ctx.user.get_username()
    now = timezone.now()
    zone = reporting_zone(ctx.org_id)
    enqueued: list[dict[str, str]] = []
    waiting: list[Waiting] = []
    feeds = list(
        Feed.objects.filter(org_id=ctx.org_id, status="active")
        .select_for_update(skip_locked=True)
        .order_by("name")
    )
    for feed in feeds:
        ready = latest_success(feed) is not None or has_passing_dry_run(feed)
        if feed.mode == "drop":
            try:
                files = sources.settled_files(
                    feed, time.time(), int(settings.KPIGO_DROP_SETTLE_SECONDS)
                )
            except v.SourceError as exc:
                waiting.append(Waiting(feed=feed.name, reason=str(exc)))
                continue
            if files and not ready:
                waiting.append(
                    Waiting(
                        feed=feed.name,
                        reason=f"{len(files)} file(s) wait for a passing dry run before the "
                        "first live load.",
                    )
                )
                continue
            for path in files:
                claimed = sources.claim(feed, path)
                enqueue({"feed": feed.name, "file": claimed}, run_as)
                enqueued.append({"feed": feed.name, "file": claimed})
        elif feed.mode == "pull" and feed.cadence_cron:
            if not schedule.is_due(feed.cadence_cron, feed.last_scheduled_at, now, zone):
                continue
            feed.last_scheduled_at = now
            feed.save(update_fields=["last_scheduled_at"])
            if not ready:
                waiting.append(
                    Waiting(feed=feed.name, reason="Due, but waits for a passing dry run.")
                )
                continue
            enqueue({"feed": feed.name}, run_as)
            enqueued.append({"feed": feed.name})

    changes: list[FreshnessChange] = []
    for feed in Feed.objects.filter(org_id=ctx.org_id).order_by("name"):
        changed = freshness.refresh(feed, now)
        if changed is None:
            continue
        before, after = changed
        changes.append(
            FreshnessChange(feed=feed.name, before=before, after=after, reason=feed.stale_reason)
        )
        if after == "stale":
            ctx.audit("feed.stale", feed=feed.name, reason=feed.stale_reason)
    return TickOut(enqueued=enqueued, waiting=waiting, freshness_changes=changes)
