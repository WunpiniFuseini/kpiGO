"""CONFORM: validated rows into canonical facts (TDD §5.1).

Runs inside the caller's savepoint, so a failure anywhere here leaves no fact
and no registered member behind: all or nothing. A changed reload supersedes
what this feed's earlier runs wrote for the same slices (metric × period, or
metric × day for daily facts); both runs stay in ``feed_run`` and their landed
rows stay in ``tmpl_*``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from django.db import connection
from django.db.models import Q

from kpigo.hierarchy.models import DimMember, ProductLine
from kpigo.ingestion import validator as v
from kpigo.ingestion.models import FactActualDimensional, FactActualMonthly, Feed, FeedRun

BATCH = 5000


def _chunks(items: Sequence[Any], size: int = BATCH) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def prior_runs(feed: Feed, current: FeedRun) -> list[str]:
    return [
        str(r)
        for r in FeedRun.objects.filter(feed=feed, is_dry_run=False, outcome="success")
        .exclude(run_id=current.run_id)
        .values_list("run_id", flat=True)
    ]


def register_unmapped(
    rows: list[v.Row], org_id: str, user_id: int | None, now: datetime
) -> dict[str, list[str]]:
    """Codes the feed carried that kpiGo did not know, registered as ``available`` (IN-11)."""
    members: set[tuple[str, str]] = set()
    lines: dict[str, date] = {}
    for row in rows:
        if "member" in row.unmapped:
            members.add((str(row.values["dimension_type"]), row.unmapped["member"]))
        if "product_line" in row.unmapped:
            code = row.unmapped["product_line"]
            day = row.values["activity_date"]
            lines[code] = min(lines.get(code, day), day)
    if members:
        DimMember.objects.bulk_create(
            [
                DimMember(
                    org_id=org_id,
                    dimension_type=dim,
                    member_code=code,
                    member_name=code,
                    status="available",
                    first_detected_at=now,
                    created_by=user_id,
                    updated_by=user_id,
                )
                for dim, code in sorted(members)
            ],
            ignore_conflicts=True,
        )
    if lines:
        ProductLine.objects.bulk_create(
            [
                ProductLine(
                    org_id=org_id,
                    code=code,
                    display_name=code,
                    status="available",
                    source_member_code=code,
                    first_detected_at=now,
                    effective_from=first_seen,
                    created_by=user_id,
                    updated_by=user_id,
                )
                for code, first_seen in sorted(lines.items())
            ],
            ignore_conflicts=True,
        )
    return {
        "members": [f"{dim}:{code}" for dim, code in sorted(members)],
        "product_lines": sorted(lines),
    }


def conform(
    feed: Feed, run: FeedRun, rows: list[v.Row], *, user_id: int | None, now: datetime
) -> dict[str, list[str]]:
    registered = register_unmapped(rows, str(feed.org_id), user_id, now)
    previous = prior_runs(feed, run)
    writer = WRITERS[feed.template_name]
    writer(feed, run, rows, previous, now)
    return registered


# ── writers, one per loadable template ───────────────────────────────────────


def write_monthly(
    feed: Feed, run: FeedRun, rows: list[v.Row], previous: list[str], now: datetime
) -> None:
    slices: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        slices[row.resolved["metric_id"]].add(str(row.values["period_key"]))
    if previous:
        match = Q()
        for metric_id, periods in slices.items():
            match |= Q(metric_id=metric_id, period_key__in=sorted(periods))
        FactActualMonthly.objects.filter(org_id=feed.org_id, run_id__in=previous).filter(
            match
        ).delete()
    facts = [
        FactActualMonthly(
            org_id=feed.org_id,
            metric_id=row.resolved["metric_id"],
            subject_id=row.resolved["subject_id"],
            assignment_id=row.resolved["assignment_id"],
            period_key=str(row.values["period_key"]),
            actual_value=row.values["actual_value"],
            currency_code=row.values.get("currency_code"),
            run_id=run.run_id,
            loaded_at=now,
            created_by=run.created_by,
        )
        for row in rows
    ]
    for chunk in _chunks(facts):
        FactActualMonthly.objects.bulk_create(
            chunk,
            update_conflicts=True,
            unique_fields=["metric", "subject", "period_key"],
            update_fields=["assignment", "actual_value", "currency_code", "run_id", "loaded_at"],
        )


def write_dimensional(
    feed: Feed, run: FeedRun, rows: list[v.Row], previous: list[str], now: datetime
) -> None:
    slices: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        key = (row.resolved["metric_id"], str(row.values["dimension_type"]))
        slices[key].add(str(row.values["period_key"]))
    if previous:
        match = Q()
        for (metric_id, dim), periods in slices.items():
            match |= Q(metric_id=metric_id, dimension_type=dim, period_key__in=sorted(periods))
        FactActualDimensional.objects.filter(org_id=feed.org_id, run_id__in=previous).filter(
            match
        ).delete()
    facts = [
        FactActualDimensional(
            org_id=feed.org_id,
            metric_id=row.resolved["metric_id"],
            dimension_type=str(row.values["dimension_type"]),
            member_code=str(row.values["member_code"]),
            period_key=str(row.values["period_key"]),
            actual_value=row.values["actual_value"],
            currency_code=row.values.get("currency_code"),
            run_id=run.run_id,
            loaded_at=now,
            created_by=run.created_by,
        )
        for row in rows
    ]
    for chunk in _chunks(facts):
        FactActualDimensional.objects.bulk_create(
            chunk,
            update_conflicts=True,
            unique_fields=["metric", "dimension_type", "member_code", "period_key"],
            update_fields=["actual_value", "currency_code", "run_id", "loaded_at"],
        )


def daily_partition_name(month_start: date) -> str:
    return f"fact_actual_daily_p{month_start.year:04d}{month_start.month:02d}"


def ensure_daily_partitions(days: Iterable[date]) -> list[str]:
    """Create the monthly partition for every month a load touches, if missing."""
    months = sorted({d.replace(day=1) for d in days})
    names: list[str] = []
    with connection.cursor() as cur:
        for first in months:
            following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
            name = daily_partition_name(first)
            # Name and bounds are built from dates, never from input text.
            cur.execute(
                f'CREATE TABLE IF NOT EXISTS "{name}" PARTITION OF fact_actual_daily '
                f"FOR VALUES FROM ('{first.isoformat()}') TO ('{following.isoformat()}')"
            )
            names.append(name)
    return names


def refresh_daily_totals() -> None:
    """Bring ``mv_leaderboard_daily`` and ``mv_product_line_matrix`` up to date.

    Concurrently, so readers keep the previous contents until the new ones are in.
    """
    with connection.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_leaderboard_daily")
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_product_line_matrix")


def write_daily(
    feed: Feed, run: FeedRun, rows: list[v.Row], previous: list[str], now: datetime
) -> None:
    ensure_daily_partitions(row.values["activity_date"] for row in rows)
    line_ids = dict(ProductLine.objects.filter(org_id=feed.org_id).values_list("code", "line_id"))
    with connection.cursor() as cur:
        if previous:
            pairs = sorted(
                {(row.resolved["metric_id"], row.values["activity_date"]) for row in rows}
            )
            for chunk in _chunks(pairs):
                cur.execute(
                    "DELETE FROM fact_actual_daily f USING unnest(%s::uuid[], %s::date[]) "
                    "AS s(metric_id, activity_date) WHERE f.org_id = %s "
                    "AND f.run_id = ANY(%s::uuid[]) AND f.metric_id = s.metric_id "
                    "AND f.activity_date = s.activity_date",
                    [[p[0] for p in chunk], [p[1] for p in chunk], feed.org_id, previous],
                )
        for chunk in _chunks(rows):
            line_codes = [r.values.get("product_line_code") for r in chunk]
            cur.execute(
                """
                INSERT INTO fact_actual_daily (org_id, metric_id, subject_id, assignment_id,
                    activity_date, product_line_id, actual_value, currency_code, run_id,
                    loaded_at, created_by)
                SELECT %s, s.metric_id, s.subject_id, s.assignment_id, s.activity_date,
                    s.product_line_id, s.actual_value, s.currency_code, %s, %s, %s
                FROM unnest(%s::uuid[], %s::uuid[], %s::uuid[], %s::date[], %s::uuid[],
                    %s::numeric[], %s::varchar[])
                    AS s(metric_id, subject_id, assignment_id, activity_date, product_line_id,
                         actual_value, currency_code)
                ON CONFLICT (metric_id, subject_id, activity_date, product_line_id)
                DO UPDATE SET assignment_id = EXCLUDED.assignment_id,
                    actual_value = EXCLUDED.actual_value,
                    currency_code = EXCLUDED.currency_code,
                    run_id = EXCLUDED.run_id,
                    loaded_at = EXCLUDED.loaded_at
                """,
                [
                    feed.org_id,
                    run.run_id,
                    now,
                    run.created_by,
                    [r.resolved["metric_id"] for r in chunk],
                    [r.resolved["subject_id"] for r in chunk],
                    [r.resolved["assignment_id"] for r in chunk],
                    [r.values["activity_date"] for r in chunk],
                    [line_ids.get(str(c)) if c is not None else None for c in line_codes],
                    [r.values["actual_value"] for r in chunk],
                    [r.values.get("currency_code") for r in chunk],
                ],
            )
    refresh_daily_totals()


WRITERS = {
    "actual_monthly": write_monthly,
    "actual_daily": write_daily,
    "actual_dimensional": write_dimensional,
}
