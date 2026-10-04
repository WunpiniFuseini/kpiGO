"""Ingestion: the six gates, dry run, all-or-nothing, idempotency, freshness (TDD §5)."""

from __future__ import annotations

import csv
import io
import os
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from kpigo.action import Conflict, InvalidInput
from kpigo.action.identity import build_context
from kpigo.celery import app as celery_app
from kpigo.hierarchy.models import DimMember, ProductLine
from kpigo.ingestion import conform as conform_module
from kpigo.ingestion.models import (
    FactActualDimensional,
    FactActualMonthly,
    Feed,
    FeedRun,
    TmplActualMonthly,
)
from kpigo.platform.models import AuditLog
from tests.conftest import run
from tests.ingestion_support import (
    ACTIVITY_DAY,
    MONTHLY,
    NEXT_PERIOD,
    PERIOD,
    PRIOR_PERIOD,
    daily_count,
    dry,
    live,
    monthly_rows,
    register_feed,
    runs_and_findings,
    snapshot,
    upload,
    world,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> dict[str, Any]:
    return world()


def rules(out: Any) -> set[str]:
    return set(out.rules)


def ready_feed(name: str = "monthly", **extra: Any) -> None:
    """A registered upload feed that has passed a dry run."""
    register_feed(name, **extra)
    assert dry(name, MONTHLY, monthly_rows()).passed


# ── the six gates ────────────────────────────────────────────────────────────


def test_a_clean_load_passes_every_gate(org: dict[str, Any]) -> None:
    register_feed()
    out = dry("monthly", MONTHLY, monthly_rows())
    assert out.passed and out.outcome == "success" and out.state == "validated"
    assert out.rows_read == 3 and out.rows_accepted == 3 and out.rows_rejected == 0
    assert set(out.gates) == {
        "schema",
        "referential",
        "grain",
        "domain",
        "volume",
        "period_status",
    }
    assert all(g["error"] == 0 for g in out.gates.values())


def test_schema_gate_rejects_missing_columns_and_uncoercible_values(org: dict[str, Any]) -> None:
    register_feed()
    out = dry(
        "monthly", ["metric_code", "subject_ref", "actual_value"], [["total_deposits", "E1", "1"]]
    )
    assert not out.passed and out.outcome == "quarantined"
    assert "missing_column" in rules(out)
    assert any(f.column == "period_key" for f in out.findings)

    rows = monthly_rows()
    rows[1][3] = "twelve"
    rows[2][3] = ""
    out = dry("monthly", MONTHLY, rows)
    assert {"not_a_number", "required_value"} <= rules(out)
    bad = {(f.row_no, f.rule) for f in out.findings}
    assert (2, "not_a_number") in bad and (3, "required_value") in bad
    # An empty value is not zero.
    assert "explicit 0" in next(f.message for f in out.findings if f.rule == "required_value")


def test_referential_gate_rejects_unknown_and_unbound_rows(org: dict[str, Any]) -> None:
    register_feed()
    rows = monthly_rows()
    rows += [
        ["no_such_metric", "E1", PERIOD, "1", "GHS"],
        ["total_deposits", "E99", PERIOD, "1", "GHS"],
        ["calls_made", "E1", PERIOD, "1", None],  # bound to agent_sales only
        ["draft_thing", "E1", PERIOD, "1", None],
    ]
    out = dry("monthly", MONTHLY, rows)
    assert not out.passed
    assert {"unknown_metric", "unknown_subject", "metric_not_bound", "metric_not_active"} <= rules(
        out
    )
    assert out.rows_rejected == 4


def test_referential_gate_registers_unknown_members_as_available(org: dict[str, Any]) -> None:
    header = ["metric_code", "dimension_type", "member_code", "period_key", "actual_value"]
    rows = [
        ["branch_revenue", "region", "GA", PERIOD, "10"],
        ["branch_revenue", "region", "NR", PERIOD, "20"],
        ["branch_revenue", "zone", "Z1", PERIOD, "30"],
    ]
    register_feed("regional", template="actual_dimensional")
    out = dry("regional", header, rows)
    assert "unmapped_member" in rules(out) and "unknown_dimension" in rules(out)
    assert not out.passed  # the unknown dimension type rejects its row

    out = dry("regional", header, rows[:2])
    assert out.passed and out.warnings == 1
    assert not DimMember.objects.filter(member_code="NR").exists()  # a dry run registers nothing

    out = live("regional", header, rows[:2])
    assert out.outcome == "success"
    assert out.registered_members == ["region:NR"]
    nr = DimMember.objects.get(dimension_type="region", member_code="NR")
    assert nr.status == "available" and nr.first_detected_at is not None
    assert FactActualDimensional.objects.filter(member_code="NR").count() == 1
    listed = run("dimension.member.list", dimension_type="region", status="available")
    assert [m.member_code for m in listed.members] == ["NR"]


def test_grain_gate_rejects_duplicates(org: dict[str, Any]) -> None:
    register_feed()
    rows = [*monthly_rows(), ["total_deposits", "E1", PERIOD, "5", "GHS"]]
    out = dry("monthly", MONTHLY, rows)
    assert not out.passed and rules(out) == {"duplicate_at_grain"}
    (finding,) = out.findings
    assert finding.row_no == 4 and "row 1" in finding.message


def test_domain_gate_rejects_bad_periods_values_and_future_activity(org: dict[str, Any]) -> None:
    register_feed()
    rows = [
        ["total_deposits", "E1", "2026-9", "1", "GHS"],
        ["total_deposits", "E2", NEXT_PERIOD, "1", "GHS"],
        ["total_deposits", "E3", PERIOD, "1e20", "ghs"],
    ]
    out = dry("monthly", MONTHLY, rows)
    assert {"bad_period_key", "future_period", "out_of_bounds", "bad_currency"} <= rules(out)

    register_feed("calls", template="actual_daily")
    header = ["metric_code", "subject_ref", "activity_date", "actual_value"]
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    out = dry(
        "calls",
        header,
        [["calls_made", "E1", tomorrow, "3"], ["calls_made", "E2", ACTIVITY_DAY.isoformat(), "-2"]],
    )
    assert {"future_date", "negative_value"} <= rules(out)


def test_volume_gate_checks_expected_range_and_trailing_average(org: dict[str, Any]) -> None:
    register_feed(expected_row_min=5)
    out = dry("monthly", MONTHLY, monthly_rows())
    assert rules(out) == {"below_expected_rows"}
    run("feed.update", name="monthly", expected_row_min=None)
    assert dry("monthly", MONTHLY, monthly_rows()).passed

    # Two good loads of three rows set the trailing average; one row is 67% off.
    assert live("monthly", MONTHLY, monthly_rows(), restatement=False).outcome == "success"
    run("feed.update", name="monthly", volume_warn_pct=10, volume_reject_pct=50)
    out = dry("monthly", MONTHLY, monthly_rows({"E1": "1"}))
    assert "volume_out_of_range" in rules(out) and not out.passed
    run("feed.update", name="monthly", volume_reject_pct=90)
    out = dry("monthly", MONTHLY, monthly_rows({"E1": "1", "E2": "2"}, period=PRIOR_PERIOD))
    assert "volume_unusual" in rules(out)


def test_period_status_gate_refuses_a_closed_period_unless_restating(org: dict[str, Any]) -> None:
    ready_feed()
    for status in ("open", "closing", "closed"):
        run("period.transition", product="scorecards", period_key=PERIOD, to_status=status)
    out = dry("monthly", MONTHLY, monthly_rows())
    assert rules(out) == {"period_closed"} and not out.passed
    out = dry("monthly", MONTHLY, monthly_rows(), restatement=True)
    assert out.passed and out.restatement


def test_missing_rows_warn_on_the_first_load_then_enforce(org: dict[str, Any]) -> None:
    """Absent is not zero: a subject expected to report must have a row."""
    ready_feed()
    partial = monthly_rows({"E1": "1", "E2": "0"})
    out = dry("monthly", MONTHLY, partial)
    assert out.passed and rules(out) == {"missing_row"}
    (finding,) = out.findings
    assert finding.severity == "warning" and finding.value == "E3"
    assert live("monthly", MONTHLY, partial).outcome == "success"
    assert not FactActualMonthly.objects.filter(subject_id=org["E3"]).exists()

    out = dry("monthly", MONTHLY, monthly_rows({"E1": "1", "E2": "0"}, period=PRIOR_PERIOD))
    assert not out.passed and out.findings[0].severity == "error"


# ── dry run, IN-7, quarantine ────────────────────────────────────────────────


def test_dry_run_writes_nothing_but_its_report(org: dict[str, Any]) -> None:
    register_feed("regional", template="actual_dimensional")
    register_feed("calls", template="actual_daily")
    before = snapshot()
    runs_before = runs_and_findings()
    header = ["metric_code", "dimension_type", "member_code", "period_key", "actual_value"]
    out = dry("regional", header, [["branch_revenue", "region", "NEW", PERIOD, "1"]])
    assert out.passed and out.diff is not None and out.diff["added"] == 1
    daily = ["metric_code", "subject_ref", "activity_date", "product_line_code", "actual_value"]
    dry("calls", daily, [["calls_made", "E1", ACTIVITY_DAY.isoformat(), "LOANS", "4"]])
    register_feed()
    dry("monthly", MONTHLY, monthly_rows({"E1": "bad"}))
    after = snapshot()
    after["feeds"] = [f for f in after["feeds"] if f[0] != "monthly"]
    assert after == before
    runs, findings = runs_and_findings()
    assert runs == runs_before[0] + 3 and findings > runs_before[1]
    assert FeedRun.objects.filter(is_dry_run=True).count() == 3


def test_first_live_load_needs_a_passing_dry_run(org: dict[str, Any]) -> None:
    register_feed()
    with pytest.raises(Conflict, match="dry run"):
        live("monthly", MONTHLY, monthly_rows())
    dry("monthly", MONTHLY, monthly_rows({"E1": "oops"}))  # a failing dry run is not enough
    with pytest.raises(Conflict, match="dry run"):
        live("monthly", MONTHLY, monthly_rows())
    assert dry("monthly", MONTHLY, monthly_rows()).passed
    assert live("monthly", MONTHLY, monthly_rows()).outcome == "success"
    assert run("feed.get", name="monthly").feed.dry_run_passed


def test_a_load_with_any_error_is_quarantined_whole(org: dict[str, Any]) -> None:
    ready_feed()
    rows = [*monthly_rows(), ["total_deposits", "E99", PERIOD, "1", "GHS"]]
    out = live("monthly", MONTHLY, rows)
    assert out.outcome == "quarantined" and out.state == "quarantined"
    assert out.rows_accepted == 0 and out.rows_rejected == 1
    assert FactActualMonthly.objects.count() == 0
    # The raw rows stay landed for inspection, under the run.
    assert TmplActualMonthly.objects.filter(run_id=out.run_id).count() == 4
    feed = Feed.objects.get(name="monthly")
    assert feed.last_run_id == out.run_id and feed.freshness_state == "never_loaded"
    assert AuditLog.objects.filter(event="feed.quarantined").exists()


def test_conform_failure_rolls_back_to_nothing(
    org: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """All or nothing under failure injection: half the facts written, then a crash."""
    register_feed("regional", template="actual_dimensional", volume_reject_pct=1000)
    header = ["metric_code", "dimension_type", "member_code", "period_key", "actual_value"]
    good = [["branch_revenue", "region", "GA", PERIOD, "10"]]
    assert dry("regional", header, good).passed
    first = live("regional", header, good)
    assert first.outcome == "success"

    real = conform_module.WRITERS["actual_dimensional"]

    def explode(feed: Any, run_row: Any, rows: list[Any], previous: Any, now: Any) -> None:
        real(feed, run_row, rows[:1], previous, now)
        assert FactActualDimensional.objects.filter(run_id=run_row.run_id).exists()
        raise RuntimeError("disk full")

    monkeypatch.setitem(conform_module.WRITERS, "actual_dimensional", explode)
    rows = [
        ["branch_revenue", "region", "GA", PERIOD, "99"],
        ["branch_revenue", "region", "NEW1", PERIOD, "5"],
    ]
    out = live("regional", header, rows)
    assert out.outcome == "failed" and out.state == "failed" and "RuntimeError" in (out.error or "")
    # Nothing of the failed load survives: the first load's fact is untouched,
    # and the member it would have registered is not there.
    facts = list(FactActualDimensional.objects.values_list("member_code", "actual_value", "run_id"))
    assert [(m, int(v), r) for m, v, r in facts] == [("GA", 10, first.run_id)]
    assert not DimMember.objects.filter(member_code="NEW1").exists()
    assert Feed.objects.get(name="regional").last_run_id == out.run_id


# ── idempotency and supersede ────────────────────────────────────────────────


def test_identical_reload_is_a_no_op_returning_the_prior_run(org: dict[str, Any]) -> None:
    ready_feed()
    first = live("monthly", MONTHLY, monthly_rows())
    runs = FeedRun.objects.count()
    again = live("monthly", MONTHLY, list(reversed(monthly_rows())))  # same content, new order
    assert again.idempotent and again.run_id == first.run_id
    assert FeedRun.objects.count() == runs
    assert AuditLog.objects.filter(event="feed.unchanged").count() == 1


def test_changed_reload_supersedes_and_keeps_both_runs(org: dict[str, Any]) -> None:
    ready_feed()
    first = live("monthly", MONTHLY, monthly_rows({"E1": "100", "E2": "0", "E3": "250"}))
    second = live("monthly", MONTHLY, monthly_rows({"E1": "120", "E2": "0", "E3": "250"}))
    assert second.outcome == "success" and not second.idempotent
    assert second.diff is not None and second.diff["changed"] == 1
    assert second.diff["against_run_id"] == str(first.run_id)
    values = {str(f.subject_id): int(f.actual_value) for f in FactActualMonthly.objects.filter()}
    assert values[str(org["E1"])] == 120 and len(values) == 3
    assert set(FactActualMonthly.objects.values_list("run_id", flat=True)) == {second.run_id}
    assert FeedRun.objects.get(run_id=second.run_id).supersedes_run_id == first.run_id
    assert TmplActualMonthly.objects.filter(run_id=first.run_id).count() == 3
    # Back to the first content is a real reload, not a no-op: it is not the current load.
    third = live("monthly", MONTHLY, monthly_rows({"E1": "100", "E2": "0", "E3": "250"}))
    assert not third.idempotent and third.run_id not in (first.run_id, second.run_id)


def test_daily_facts_land_in_monthly_partitions(org: dict[str, Any]) -> None:
    register_feed("calls", template="actual_daily")
    header = ["metric_code", "subject_ref", "activity_date", "product_line_code", "actual_value"]
    day = ACTIVITY_DAY.isoformat()
    rows: list[list[Any]] = [
        ["calls_made", "E1", day, "LOANS", "4"],
        ["calls_made", "E1", day, None, "2"],
        ["calls_made", "E2", day, "LOANS", "0"],
    ]
    assert dry("calls", header, rows).passed
    out = live("calls", header, rows)
    assert out.outcome == "success" and out.registered_product_lines == ["LOANS"]
    assert daily_count() == 3
    line = ProductLine.objects.get(code="LOANS")
    assert line.status == "available" and line.group_id is None
    assert [p.code for p in run("product_line.list", status="available").product_lines] == ["LOANS"]
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT tableoid::regclass::text, count(*) FROM fact_actual_daily GROUP BY 1")
        assert cur.fetchall() == [
            (f"fact_actual_daily_p{ACTIVITY_DAY.year:04d}{ACTIVITY_DAY.month:02d}", 3)
        ]
    # A reload of the day supersedes it; the dropped row is absent, not zero.
    out = live("calls", header, rows[:2])
    assert out.outcome == "success" and daily_count() == 2


# ── reports, freshness, drop folder, HTTP ───────────────────────────────────


def test_rejection_report_exports_row_column_and_rule(org: dict[str, Any]) -> None:
    register_feed()
    rows = monthly_rows()
    rows[0][1] = "=HYPERLINK(1)"
    rows[1][3] = "x"
    out = dry("monthly", MONTHLY, rows)
    report = run("feed.rejections.export", run_id=str(out.run_id))
    assert report.content_type == "text/csv" and report.filename.endswith("-rejections.csv")
    parsed = list(csv.DictReader(io.StringIO(report.content)))
    assert parsed[0].keys() == {"row_no", "column", "gate", "rule", "severity", "value", "message"}
    by_rule = {r["rule"]: r for r in parsed}
    assert by_rule["not_a_number"]["row_no"] == "2"
    assert by_rule["not_a_number"]["column"] == "actual_value"
    assert by_rule["unknown_subject"]["value"].startswith("'=")  # never a live formula
    fetched = run("feed.run.get", run_id=str(out.run_id))
    assert fetched.findings_total == report.rows


def test_freshness_tracks_tolerance_and_missed_deadlines(
    org: dict[str, Any], make_user: Any
) -> None:
    ready_feed(freshness_tolerance_hours=24)
    tick_user = make_user("data_steward")
    ctx = build_context(tick_user, caller="job")
    out = run("feed.tick", ctx)
    assert out.freshness_changes == []  # never loaded, nothing due yet

    feed = Feed.objects.get(name="monthly")
    run(
        "period.deadline.set",
        feed_id=str(feed.feed_id),
        period_key=PERIOD,
        due_at=(timezone.now() - timedelta(hours=1)).isoformat(),
    )
    out = run("feed.tick", build_context(tick_user, caller="job"))
    assert [(c.feed, c.after) for c in out.freshness_changes] == [("monthly", "stale")]
    assert "deadline" in (out.freshness_changes[0].reason or "")
    assert AuditLog.objects.filter(event="feed.stale").exists()

    live("monthly", MONTHLY, monthly_rows())
    feed.refresh_from_db()
    assert feed.freshness_state == "fresh" and feed.last_success_at is not None

    Feed.objects.filter(pk=feed.pk).update(last_success_at=timezone.now() - timedelta(hours=30))
    out = run("feed.tick", build_context(tick_user, caller="job"))
    assert out.freshness_changes[0].after == "stale"
    assert run("feed.list", freshness_state="stale").feeds[0].stale_reason


def test_drop_folder_files_are_claimed_loaded_and_filed(
    org: dict[str, Any],
    make_user: Any,
    tmp_path: Path,
    settings: Any,
    django_capture_on_commit_callbacks: Any,
    request: pytest.FixtureRequest,
) -> None:
    settings.KPIGO_DROP_ROOT = str(tmp_path)
    settings.KPIGO_DROP_SETTLE_SECONDS = 0
    folder = tmp_path / "bank" / "monthly"
    folder.mkdir(parents=True)
    run(
        "feed.register",
        name="dropped",
        template="actual_monthly",
        mode="drop",
        drop_path="bank/monthly",
    )
    from tests.ingestion_support import csv_text

    (folder / "first.csv").write_text(csv_text(MONTHLY, monthly_rows()))
    user = make_user("data_steward", username="ingest")

    out = run("feed.tick", build_context(user, caller="job"))
    assert out.enqueued == [] and "dry run" in out.waiting[0].reason
    assert (folder / "first.csv").exists()

    assert run("feed.dry_run", feed="dropped", file="first.csv").passed
    # The app reads Django's CELERY_* settings, so eager mode is set under that name.
    celery_app.conf.update(CELERY_TASK_ALWAYS_EAGER=True)
    request.addfinalizer(lambda: celery_app.conf.update(CELERY_TASK_ALWAYS_EAGER=False))
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        out = run("feed.tick", build_context(user, caller="job"))
    assert len(callbacks) >= 1
    assert out.enqueued == [{"feed": "dropped", "file": ".processing/first.csv"}]
    loaded = FeedRun.objects.get(is_dry_run=False)
    assert loaded.outcome == "success" and loaded.trigger == "drop"
    assert FactActualMonthly.objects.count() == 3
    assert (folder / "processed" / "first.csv").exists() and not (folder / "first.csv").exists()

    with pytest.raises(InvalidInput):
        run("feed.dry_run", feed="dropped", file="../../etc/passwd")
    with pytest.raises(InvalidInput):
        run(
            "feed.register", name="escape", template="actual_monthly", mode="drop", drop_path="/etc"
        )


def test_upload_over_http(org: dict[str, Any], make_user: Any) -> None:
    register_feed()
    client = Client()
    client.force_login(make_user("data_steward"))
    body = {"feed": "monthly", "upload": upload(MONTHLY, monthly_rows())}
    response = client.post("/api/v1/actions/feed.dry_run", body, content_type="application/json")
    assert response.status_code == 200, response.content
    assert response.json()["passed"] is True
    # The audit row names the upload but does not carry the file.
    payload = AuditLog.objects.filter(event="feed.dry_run_requested").latest("occurred_at").payload
    sent = payload["params"]["upload"]["content_base64"]
    assert sent.startswith("<") and "sha256" in sent
    client.force_login(make_user("staff"))
    response = client.post("/api/v1/actions/feed.dry_run", body, content_type="application/json")
    assert response.status_code == 403


def test_row_cap_fails_the_read_rather_than_truncating(org: dict[str, Any], settings: Any) -> None:
    settings.KPIGO_INGEST_ROW_CAP = 2
    register_feed()
    out = dry("monthly", MONTHLY, monthly_rows())
    assert out.outcome == "failed" and "row cap" in (out.error or "")


def test_feed_registration_checks_mode_and_object_names(org: dict[str, Any]) -> None:
    with pytest.raises(InvalidInput) as refused:
        run("feed.register", name="p", template="actual_monthly", mode="pull")
    assert "connection" in str(refused.value.detail)
    for bad in ("kpi.v; DROP TABLE x", "kpi.v --", "a.b.c.d", 'kpi."v"', "(select 1)"):
        with pytest.raises(InvalidInput):
            run(
                "feed.register",
                name="p",
                template="actual_monthly",
                mode="pull",
                connection="dw",
                source_object=bad,
            )
    with pytest.raises(InvalidInput) as refused:
        register_feed("u", cadence_cron="0 6 * * *")
    assert "cadence" in str(refused.value.detail)
    with pytest.raises(InvalidInput):
        register_feed("u", template="target")


def test_pipeline_dry_run_context_rolls_everything_back(
    org: dict[str, Any], make_user: Callable[..., User]
) -> None:
    ready_feed()
    runs = FeedRun.objects.count()
    ctx = build_context(make_user(), caller="cli", dry_run=True)
    ctx = type(ctx)(**{**ctx.__dict__, "permissions": frozenset({"feed.run"})})
    run("feed.run", ctx, feed="monthly", upload=upload(MONTHLY, monthly_rows()))
    assert FeedRun.objects.count() == runs and FactActualMonthly.objects.count() == 0


def test_contract_export_matches_the_server(org: dict[str, Any]) -> None:
    register_feed()
    out = run("feed.contract.export", name="monthly", period_from=PERIOD, period_to=PERIOD)
    contract = out.contract
    assert contract["template"] == "actual_monthly"
    assert set(contract["reference"]["subjects"]) == {"E1", "E2", "E3"}
    assert "total_deposits" in contract["reference"]["metrics"]
    assert os.environ.get("KPIGO_TESTING")  # dev key path is in use
    assert time.time() > 0
