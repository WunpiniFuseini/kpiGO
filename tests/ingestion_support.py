"""Shared set-up for the ingestion tests: a small org, built through its actions."""

from __future__ import annotations

import base64
from datetime import date, timedelta
from typing import Any

from django.db import connection

from kpigo.hierarchy.models import DimMember, ProductLine
from kpigo.ingestion.models import (
    LANDING,
    FactActualDimensional,
    FactActualMonthly,
    Feed,
    FeedRejection,
    FeedRun,
)
from tests.conftest import run


def month_key(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}"


TODAY = date.today()
LAST_MONTH_END = TODAY.replace(day=1) - timedelta(days=1)
# The month before the current one: closed-for-business but not in the future.
PERIOD = month_key(LAST_MONTH_END)
PRIOR_PERIOD = month_key(LAST_MONTH_END.replace(day=1) - timedelta(days=1))
NEXT_PERIOD = month_key((TODAY.replace(day=28) + timedelta(days=4)).replace(day=1))
SINCE = f"{TODAY.year - 1:04d}-01-01"
ACTIVITY_DAY = LAST_MONTH_END - timedelta(days=3)
STAFF = ("E1", "E2", "E3")


def world() -> dict[str, Any]:
    """Three RMs on one profile, and a metric for each loadable template."""
    ids: dict[str, Any] = {}
    for staff_no in STAFF:
        subject = run(
            "subject.register",
            staff_no=staff_no,
            full_name=f"Person {staff_no}",
            email=f"{staff_no.lower()}@bank.example",
        )
        ids[staff_no] = subject.subject_id
        run(
            "assignment.create",
            subject_id=str(subject.subject_id),
            role_code="rm",
            profile_code="retail_rm",
            effective_from=SINCE,
        )
    _metric("Total deposits", "total_deposits", "currency", ["scorecards"])
    run(
        "metric.profile.assign",
        metric_code="total_deposits",
        profile_code="retail_rm",
        product="scorecards",
        effective_from=SINCE,
    )
    _metric("Calls made", "calls_made", "count", ["agent_sales"])
    _metric("Branch revenue", "branch_revenue", "currency", ["executive"])
    _metric("Draft thing", "draft_thing", "count", ["scorecards"], status="draft")
    _metric("Agent only", "agent_only", "count", ["agent_service"])
    run("dimension.define", dimension_type="region", display_name="Region")
    run(
        "dimension.member.upsert",
        dimension_type="region",
        members=[{"member_code": "GA", "member_name": "Greater Accra"}],
    )
    return ids


def _metric(name: str, code: str, unit: str, products: list[str], status: str = "active") -> None:
    run(
        "metric.register",
        display_name=name,
        metric_code=code,
        direction="higher_is_better",
        aggregation="sum",
        unit=unit,
        products=products,
        status=status,
        effective_from=SINCE,
        acknowledge_similar=True,
    )


def csv_text(header: list[str], rows: list[list[Any]]) -> str:
    lines = [",".join(header)]
    lines += [",".join("" if c is None else str(c) for c in row) for row in rows]
    return "\n".join(lines) + "\n"


def upload(header: list[str], rows: list[list[Any]], filename: str = "load.csv") -> dict[str, str]:
    data = csv_text(header, rows).encode()
    return {"filename": filename, "content_base64": base64.b64encode(data).decode()}


MONTHLY = ["metric_code", "subject_ref", "period_key", "actual_value", "currency_code"]


def monthly_rows(
    values: dict[str, Any] | None = None, period: str = PERIOD, metric: str = "total_deposits"
) -> list[list[Any]]:
    values = values or {"E1": "100.50", "E2": "0", "E3": "250"}
    return [[metric, staff_no, period, value, "GHS"] for staff_no, value in values.items()]


def register_feed(name: str = "monthly", template: str = "actual_monthly", **extra: Any) -> Any:
    return run(
        "feed.register", name=name, template=template, mode=extra.pop("mode", "upload"), **extra
    )


def dry(feed: str, header: list[str], rows: list[list[Any]], **extra: Any) -> Any:
    return run("feed.dry_run", feed=feed, upload=upload(header, rows), **extra)


def live(feed: str, header: list[str], rows: list[list[Any]], **extra: Any) -> Any:
    return run("feed.run", feed=feed, upload=upload(header, rows), **extra)


def daily_count() -> int:
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM fact_actual_daily")
        return int(cur.fetchone()[0])


def snapshot() -> dict[str, Any]:
    """Everything a dry run must leave exactly as it was."""
    feeds = list(
        Feed.objects.order_by("name").values_list(
            "name", "last_run_id", "last_success_at", "freshness_state", "updated_at"
        )
    )
    return {
        "monthly": FactActualMonthly.objects.count(),
        "dimensional": FactActualDimensional.objects.count(),
        "daily": daily_count(),
        "members": sorted(DimMember.objects.values_list("dimension_type", "member_code", "status")),
        "lines": ProductLine.objects.count(),
        "landing": {name: m._default_manager.count() for name, m in LANDING.items()},
        "feeds": feeds,
    }


def runs_and_findings() -> tuple[int, int]:
    return FeedRun.objects.count(), FeedRejection.objects.count()
