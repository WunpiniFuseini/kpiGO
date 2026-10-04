"""``fact_actual_daily``: the largest table, range-partitioned by month (Schema §8).

Django cannot declare a partitioned table, so it is SQL here and written by
conform with SQL. Conform creates each month's partition before it inserts
(``kpigo.ingestion.conform.ensure_daily_partitions``); there is no default
partition, so a row can never land outside a month's own partition.

``product_line_id`` is nullable (activity with no product line), so the grain
is a unique index with NULLS NOT DISTINCT rather than a primary key, which
Postgres would refuse on a nullable column.
"""

from typing import Any

from django.db import migrations

FORWARD: list[Any] = [
    """
    CREATE TABLE fact_actual_daily (
        org_id uuid NOT NULL,
        metric_id uuid NOT NULL REFERENCES metric (metric_id) DEFERRABLE INITIALLY DEFERRED,
        subject_id uuid NOT NULL REFERENCES subject (subject_id) DEFERRABLE INITIALLY DEFERRED,
        assignment_id uuid NOT NULL
            REFERENCES assignment (assignment_id) DEFERRABLE INITIALLY DEFERRED,
        activity_date date NOT NULL,
        product_line_id uuid NULL
            REFERENCES product_line (line_id) DEFERRABLE INITIALLY DEFERRED,
        actual_value numeric(18, 4) NOT NULL,
        currency_code varchar(3) NULL,
        run_id uuid NULL,
        loaded_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by bigint NULL
    ) PARTITION BY RANGE (activity_date)
    """,
    """
    CREATE UNIQUE INDEX fact_actual_daily_grain ON fact_actual_daily
        (metric_id, subject_id, activity_date, product_line_id) NULLS NOT DISTINCT
    """,
    "CREATE INDEX fact_actual_daily_date_brin ON fact_actual_daily USING brin (activity_date)",
    "CREATE INDEX fact_actual_daily_subject_metric ON fact_actual_daily (subject_id, metric_id)",
    """
    CREATE INDEX fact_actual_daily_line_date ON fact_actual_daily
        (product_line_id, activity_date)
    """,
    "CREATE INDEX fact_actual_daily_run ON fact_actual_daily (run_id)",
]

REVERSE: list[Any] = ["DROP TABLE IF EXISTS fact_actual_daily CASCADE"]


class Migration(migrations.Migration):
    dependencies = [
        ("ingestion", "0001_initial"),
        ("hierarchy", "0002_product_lines_available_members"),
        ("metrics", "0001_initial"),
    ]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
