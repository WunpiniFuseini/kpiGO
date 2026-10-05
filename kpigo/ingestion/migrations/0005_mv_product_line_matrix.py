"""``mv_product_line_matrix``: each subject's daily figure per metric and product line.

The product-line matrix (PRD AP-5) reads every agent's month to date line by line;
this view collapses metric versions (MR-7) to one row per ``metric_code`` × subject
× line × day × currency, a figure with no line under ``line_code`` "". It is
refreshed with ``mv_leaderboard_daily`` after each daily load, archive and restore.
"""

from typing import Any

from django.db import migrations

FORWARD: list[Any] = [
    """
    CREATE MATERIALIZED VIEW mv_product_line_matrix AS
    SELECT f.org_id, m.metric_code, f.subject_id, coalesce(pl.code, '') AS line_code,
           f.activity_date, coalesce(f.currency_code, '') AS currency_code,
           sum(f.actual_value) AS actual_value
    FROM fact_actual_daily f
    JOIN metric m ON m.metric_id = f.metric_id
    LEFT JOIN product_line pl ON pl.line_id = f.product_line_id
    GROUP BY 1, 2, 3, 4, 5, 6
    """,
    """
    CREATE UNIQUE INDEX mv_product_line_matrix_key ON mv_product_line_matrix
        (org_id, metric_code, subject_id, line_code, activity_date, currency_code)
    """,
    """
    CREATE INDEX mv_product_line_matrix_day ON mv_product_line_matrix
        (org_id, metric_code, activity_date)
    """,
]

REVERSE: list[Any] = ["DROP MATERIALIZED VIEW IF EXISTS mv_product_line_matrix"]


class Migration(migrations.Migration):
    dependencies = [
        ("ingestion", "0004_mv_leaderboard_daily"),
        ("hierarchy", "0003_product_line_registry"),
    ]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
