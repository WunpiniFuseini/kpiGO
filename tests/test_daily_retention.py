"""Daily retention: roll up, archive, never delete, restore (PRD AP-12; Scope §8.4)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.db import connection
from django.utils import timezone

from kpigo.action import Conflict, PermissionDenied
from kpigo.hierarchy.models import Assignment
from kpigo.ingestion.models import DailyArchive, FactActualMonthly
from kpigo.metrics.models import Metric
from tests.agent_support import day, load, upload, world
from tests.conftest import ORG_ID, role_ctx, run

pytestmark = pytest.mark.django_db


def months_back(n: int) -> str:
    today = timezone.localdate()
    index = today.year * 12 + today.month - 1 - n
    return f"{index // 12:04d}{index % 12 + 1:02d}"


OLD = months_back(5)
RECENT = months_back(1)


def daily_rows(month: str | None = None) -> int:
    sql = "SELECT count(*) FROM fact_actual_daily"
    args: list[Any] = []
    if month:
        sql += " WHERE to_char(activity_date, 'YYYYMM') = %s"
        args.append(month)
    with connection.cursor() as cur:
        cur.execute(sql, args)
        return int(cur.fetchone()[0])


def schema_of(month: str) -> str | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT schemaname FROM pg_tables WHERE tablename = %s",
            [f"fact_actual_daily_p{month}"],
        )
        row = cur.fetchone()
        return row[0] if row else None


@pytest.fixture
def org(settings: Any) -> dict[str, str]:
    settings.KPIGO_DAILY_HOT_MONTHS = 3
    ids = world()
    load(
        [
            ["value_booked", "A1", day(3, OLD), None, "100", "GHS"],
            ["value_booked", "A1", day(3, OLD), "CARDS", "50", "GHS"],
            ["value_booked", "A1", day(10, OLD), None, "200", "GHS"],
            ["service_tat", "A1", day(3, OLD), None, "3", None],
            ["service_tat", "A1", day(10, OLD), None, "5", None],
            ["accounts_opened", "A1", day(3, OLD), None, "2", None],
            ["accounts_opened", "A1", day(10, OLD), None, "3", None],
            # Days in two currencies cannot roll up into one monthly figure.
            ["value_booked", "A2", day(3, OLD), None, "10", "GHS"],
            ["value_booked", "A2", day(10, OLD), None, "5", "USD"],
            ["value_booked", "A1", day(3, RECENT), None, "70", "GHS"],
        ]
    )
    return ids


def monthly(ids: dict[str, str], staff_no: str, code: str) -> FactActualMonthly | None:
    return FactActualMonthly.objects.filter(
        subject_id=ids[staff_no], metric__metric_code=code, period_key=OLD
    ).first()


def test_a_month_past_the_window_rolls_up_and_moves_to_the_archive(org: dict[str, str]) -> None:
    # A monthly feed already reported accounts for the month: that figure is kept.
    FactActualMonthly.objects.create(
        org_id=ORG_ID,
        metric=Metric.objects.get(metric_code="accounts_opened"),
        subject_id=org["A1"],
        assignment=Assignment.objects.get(subject_id=org["A1"]),
        period_key=OLD,
        actual_value=Decimal(99),
        loaded_at=timezone.now(),
    )
    before = daily_rows()
    out = run("agent.daily.archive")
    assert [m.period_key for m in out.archived] == [OLD]
    assert out.remaining == []
    record = out.archived[0]
    assert (record.daily_rows, record.rolled_up_rows, record.mixed_currency_pairs) == (9, 2, 1)

    # Sum across lines and days; mean of daily figures; the monthly feed's row kept.
    assert monthly(org, "A1", "value_booked").actual_value == Decimal(350)  # type: ignore[union-attr]
    assert monthly(org, "A1", "service_tat").actual_value == Decimal(4)  # type: ignore[union-attr]
    assert monthly(org, "A1", "accounts_opened").actual_value == Decimal(99)  # type: ignore[union-attr]
    assert monthly(org, "A2", "value_booked") is None

    # Detached and moved, never deleted.
    assert daily_rows() == before - 9
    assert daily_rows(RECENT) == 1
    assert schema_of(OLD) == "kpigo_archive"
    with connection.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM kpigo_archive.fact_actual_daily_p{OLD}")
        assert cur.fetchone()[0] == 9


def test_an_archived_month_refuses_loads_until_restored(org: dict[str, str]) -> None:
    run("agent.daily.archive")
    run("feed.register", name="late", template="actual_daily", mode="upload")
    late = [["value_booked", "A1", day(11, OLD), None, "1", "GHS"]]
    dry = run("feed.dry_run", feed="late", upload=upload(late))
    assert not dry.passed
    assert {f.rule for f in dry.findings} == {"period_archived"}

    restored = run("agent.daily.restore", period_key=OLD)
    assert restored.status == "restored"
    assert schema_of(OLD) == "public"
    assert daily_rows(OLD) == 9
    assert run("feed.dry_run", feed="late", upload=upload(late)).passed
    with pytest.raises(Conflict):
        run("agent.daily.restore", period_key=OLD)


def test_a_restored_month_stays_until_an_admin_archives_it_again(org: dict[str, str]) -> None:
    run("agent.daily.archive")
    run("agent.daily.restore", period_key=OLD)
    assert run("agent.daily.archive").archived == []
    again = run("agent.daily.archive", period_key=OLD)
    # The roll-up already exists, so nothing new is written.
    assert [(m.period_key, m.rolled_up_rows) for m in again.archived] == [(OLD, 0)]
    assert DailyArchive.objects.get(month=date(int(OLD[:4]), int(OLD[4:]), 1)).status == "archived"
    with pytest.raises(Conflict):
        run("agent.daily.archive", period_key=RECENT)
    listed = run("agent.daily.archive.list")
    assert listed.hot_months == 3 and [m.status for m in listed.months] == ["archived"]


def test_archiving_is_an_admin_or_steward_job(org: dict[str, str]) -> None:
    with pytest.raises(PermissionDenied):
        run("agent.daily.archive", role_ctx("staff"))
    assert run("agent.daily.archive", role_ctx("data_steward")).archived


def test_the_hot_window_counts_back_from_this_month() -> None:
    from kpigo.ingestion.retention import cutoff

    assert cutoff(date(2026, 10, 5), 24) == date(2024, 11, 1)
    assert cutoff(date(2026, 1, 31), 3) == date(2025, 11, 1)
    assert cutoff(date(2026, 1, 31), 3) - timedelta(days=1) == date(2025, 10, 31)
