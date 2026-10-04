"""Freshness per feed (PRD IN-9): fresh, stale, or never loaded.

A feed is stale when its last good load is older than its tolerance, or when
the latest deadline that has passed is for a period no good load has covered.
Dashboards grey a stale feed's tiles and show the as-of time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from kpigo.ingestion.models import Feed, FeedRun
from kpigo.periods.models import PeriodDeadline


@dataclass(frozen=True)
class Freshness:
    state: str
    reason: str | None


def evaluate(feed: Feed, now: datetime) -> Freshness:
    deadline = (
        PeriodDeadline.objects.filter(org_id=feed.org_id, feed_id=feed.feed_id, due_at__lte=now)
        .order_by("-due_at")
        .first()
    )
    if deadline is not None:
        covered = any(
            deadline.period_key in (periods or [])
            for periods in FeedRun.objects.filter(
                feed=feed, is_dry_run=False, outcome="success"
            ).values_list("periods", flat=True)
        )
        if not covered:
            return Freshness(
                "stale",
                f"Missed the deadline for {deadline.period_key} "
                f"({deadline.due_at:%Y-%m-%d %H:%M} UTC).",
            )
    if feed.last_success_at is None:
        return Freshness("never_loaded", None)
    tolerance = feed.freshness_tolerance_hours
    if tolerance is not None and now - feed.last_success_at > timedelta(hours=tolerance):
        return Freshness(
            "stale",
            f"Last good load {feed.last_success_at:%Y-%m-%d %H:%M} UTC is older than "
            f"{tolerance} hours.",
        )
    return Freshness("fresh", None)


def refresh(feed: Feed, now: datetime) -> tuple[str, str] | None:
    """Store the feed's freshness; return (before, after) when it changed."""
    found = evaluate(feed, now)
    before = feed.freshness_state
    if found.state == before and found.reason == feed.stale_reason:
        return None
    feed.freshness_state = found.state
    feed.stale_reason = found.reason
    if found.state == "stale" and before != "stale":
        feed.stale_since = now
    elif found.state != "stale":
        feed.stale_since = None
    feed.updated_at = now
    feed.save(update_fields=["freshness_state", "stale_reason", "stale_since", "updated_at"])
    return (before, found.state) if before != found.state else None
