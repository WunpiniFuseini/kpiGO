"""Daily retention: hot for a window, then rolled up and archived (PRD AP-12, Scope §8.4).

``fact_actual_daily`` is the largest table and it lives on a server the client
maintains, so it cannot grow without bound. A month older than the hot window
(``KPIGO_DAILY_HOT_MONTHS``, default 24) is:

1. rolled up into ``fact_actual_monthly`` with each metric's declared
   aggregation (a day's figure is first summed across product lines; ``sum`` and
   ``count`` add the days, ``average`` and ``ratio`` take their mean, ``latest``
   the last day's). A month that already has a monthly row for a metric and
   subject keeps it: a monthly feed's figure is the source of record;
2. detached from ``fact_actual_daily`` and moved, whole, into the archive schema
   (and the archive tablespace when ``KPIGO_ARCHIVE_TABLESPACE`` names one).

Nothing is deleted. ``restore`` moves the partition back and re-attaches it, for
the dispute or audit that needs three-year-old daily detail. A load into an
archived month is refused by the ``period_status`` gate until it is restored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import connection

from kpigo.ingestion.conform import daily_partition_name
from kpigo.ingestion.models import DailyArchive

ARCHIVE_SCHEMA = "kpigo_archive"


def _following(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1)


def hot_months() -> int:
    return int(settings.KPIGO_DAILY_HOT_MONTHS)


def cutoff(today: date, months: int | None = None) -> date:
    """The first month kept hot: a month starting before it is archivable."""
    keep = hot_months() if months is None else months
    index = today.year * 12 + today.month - 1 - (keep - 1)
    return date(index // 12, index % 12 + 1, 1)


def _attached() -> list[date]:
    """Months with a partition attached to ``fact_actual_daily``, oldest first."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = 'fact_actual_daily'
            """
        )
        names = [r[0] for r in cur.fetchall()]
    months = []
    for name in names:
        stamp = name.rsplit("_p", 1)[-1]
        if len(stamp) == 6 and stamp.isdigit():
            months.append(date(int(stamp[:4]), int(stamp[4:]), 1))
    return sorted(months)


def archivable(today: date, months: int | None = None) -> list[date]:
    first_hot = cutoff(today, months)
    return [m for m in _attached() if m < first_hot]


def archived_months() -> set[str]:
    """``YYYYMM`` of every month whose daily detail is in the archive."""
    return {
        f"{m.year:04d}{m.month:02d}"
        for m in DailyArchive.objects.filter(status="archived").values_list("month", flat=True)
    }


@dataclass(frozen=True)
class Archived:
    month: date
    partition_name: str
    daily_rows: int
    rolled_up_rows: int
    mixed_currency_pairs: int


_ROLLUP = """
WITH day AS (
    SELECT org_id, metric_id, subject_id, activity_date,
           sum(actual_value) AS value,
           (array_agg(assignment_id ORDER BY loaded_at DESC))[1] AS assignment_id,
           min(coalesce(currency_code, '')) AS lo, max(coalesce(currency_code, '')) AS hi
    FROM {table} GROUP BY 1, 2, 3, 4
), pair AS (
    SELECT d.org_id, d.metric_id, d.subject_id, m.aggregation,
           CASE WHEN m.aggregation IN ('sum', 'count') THEN sum(d.value)
                WHEN m.aggregation = 'latest'
                    THEN (array_agg(d.value ORDER BY d.activity_date DESC))[1]
                ELSE avg(d.value) END AS value,
           (array_agg(d.assignment_id ORDER BY d.activity_date DESC))[1] AS assignment_id,
           min(d.lo) AS lo, max(d.hi) AS hi
    FROM day d JOIN metric m ON m.metric_id = d.metric_id
    GROUP BY 1, 2, 3, 4
), ins AS (
    INSERT INTO fact_actual_monthly (org_id, metric_id, subject_id, assignment_id, period_key,
        actual_value, currency_code, run_id, loaded_at, created_at, created_by)
    SELECT org_id, metric_id, subject_id, assignment_id, %(period_key)s, round(value, 4),
           nullif(lo, ''), NULL, %(now)s, %(now)s, %(user_id)s
    FROM pair WHERE lo = hi
    ON CONFLICT (metric_id, subject_id, period_key) DO NOTHING
    RETURNING 1
)
SELECT (SELECT count(*) FROM ins), (SELECT count(*) FROM pair WHERE lo <> hi),
       (SELECT count(*) FROM {table})
"""


def archive(month: date, *, now: datetime, user_id: int | None) -> Archived:
    """Roll one month up and move its partition into the archive. Run in a transaction."""
    name = daily_partition_name(month)
    with connection.cursor() as cur:
        cur.execute(
            _ROLLUP.format(table=f'"{name}"'),
            {"period_key": f"{month.year:04d}{month.month:02d}", "now": now, "user_id": user_id},
        )
        written, mixed, rows = cur.fetchone()
        # Names are built from dates, never from input text.
        cur.execute(f'ALTER TABLE fact_actual_daily DETACH PARTITION "{name}"')
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {ARCHIVE_SCHEMA}")
        cur.execute(f'ALTER TABLE "{name}" SET SCHEMA {ARCHIVE_SCHEMA}')
        tablespace = settings.KPIGO_ARCHIVE_TABLESPACE
        if tablespace:
            cur.execute(f'ALTER TABLE {ARCHIVE_SCHEMA}."{name}" SET TABLESPACE "{tablespace}"')
    DailyArchive.objects.update_or_create(
        month=month,
        defaults={
            "partition_name": name,
            "archive_schema": ARCHIVE_SCHEMA,
            "status": "archived",
            "daily_rows": rows,
            "rolled_up_rows": written,
            "mixed_currency_pairs": mixed,
            "archived_at": now,
            "restored_at": None,
            "updated_by": user_id,
        },
        create_defaults={
            "partition_name": name,
            "archive_schema": ARCHIVE_SCHEMA,
            "status": "archived",
            "daily_rows": rows,
            "rolled_up_rows": written,
            "mixed_currency_pairs": mixed,
            "archived_at": now,
            "created_by": user_id,
            "updated_by": user_id,
        },
    )
    return Archived(month, name, rows, written, mixed)


def restore(record: DailyArchive, *, now: datetime, user_id: int | None) -> None:
    """Move an archived month back and re-attach it to ``fact_actual_daily``."""
    name = daily_partition_name(record.month)
    following = _following(record.month)
    with connection.cursor() as cur:
        cur.execute(f'ALTER TABLE {record.archive_schema}."{name}" SET SCHEMA public')
        cur.execute(
            f'ALTER TABLE fact_actual_daily ATTACH PARTITION "{name}" '
            f"FOR VALUES FROM ('{record.month.isoformat()}') TO ('{following.isoformat()}')"
        )
    record.status = "restored"
    record.restored_at = now
    record.updated_by = user_id
    record.save()
