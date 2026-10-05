"""``mv_leaderboard_daily``: each subject's daily figure per metric, summed across lines.

The leaderboard and the matrix read every agent's month to date at once; this
view collapses product lines and metric versions (MR-7) to one row per
``metric_code`` × subject × day × currency, so they read a fraction of the rows.
It is refreshed concurrently after each daily load and each archive or restore
(``kpigo.ingestion.conform.refresh_daily_totals``). An archived month's partition
is detached, so it drops out of the view on the next refresh.
"""

from typing import Any

from django.db import migrations

FORWARD: list[Any] = [
    """
    CREATE MATERIALIZED VIEW mv_leaderboard_daily AS
    SELECT f.org_id, m.metric_code, f.subject_id, f.activity_date,
           coalesce(f.currency_code, '') AS currency_code,
           sum(f.actual_value) AS actual_value
    FROM fact_actual_daily f JOIN metric m ON m.metric_id = f.metric_id
    GROUP BY 1, 2, 3, 4, 5
    """,
    """
    CREATE UNIQUE INDEX mv_leaderboard_daily_key ON mv_leaderboard_daily
        (org_id, metric_code, subject_id, activity_date, currency_code)
    """,
    "CREATE INDEX mv_leaderboard_daily_day ON mv_leaderboard_daily (org_id, activity_date)",
]

REVERSE: list[Any] = ["DROP MATERIALIZED VIEW IF EXISTS mv_leaderboard_daily"]


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0003_daily_archive")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
