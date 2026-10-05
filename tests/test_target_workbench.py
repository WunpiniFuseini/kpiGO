"""The target workbench (PRD SC-10, SC-11; Scope §6.7; App Flow §7.4)."""

from __future__ import annotations

import base64
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, transaction

from kpigo.action import Conflict, InvalidInput, Proposal
from kpigo.metrics.models import Metric
from kpigo.periods.models import PeriodStatus
from kpigo.platform.models import ApprovalPolicy, AuditLog
from kpigo.scorecards.models import Target, TargetPublishBatch
from tests.conftest import ORG_ID, run
from tests.scorecard_support import (
    CURRENT,
    FUTURE,
    METRICS,
    PROFILE,
    STAFF,
    THIS_YEAR,
    publish_targets,
    target_rows,
    world,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def ids() -> dict[str, Any]:
    return world()


def codes(out: Any) -> set[str]:
    return {f.code for f in out.findings}


def test_check_only_validates_and_writes_nothing(ids: dict[str, Any]) -> None:
    out = run("target.upload", rows=target_rows(FUTURE[:1]), check_only=True)
    assert out.accepted and out.errors == 0 and out.written == 0
    assert out.rows_read == out.rows_valid == 6
    assert not Target.objects.exists()


def test_a_sheet_with_any_error_writes_nothing(ids: dict[str, Any]) -> None:
    rows = target_rows(FUTURE[:1])
    rows[0]["scope_type"] = "subject"  # casa_growth is profile-level
    rows[1]["weight"] = None
    rows[2]["cap"] = "5"  # below its weight of 15
    rows.append({**rows[3]})  # duplicate key
    rows.append({**rows[4], "metric_code": "no_such_metric"})
    rows.append({**rows[4], "target_value": "0"})
    rows.append({**rows[4], "period_key": "2027-01"})
    out = run("target.upload", rows=rows)
    assert not out.accepted and out.written == 0
    assert {
        "scope_mismatch",
        "weight_required",
        "cap_below_weight",
        "duplicate",
        "unknown_metric",
        "target_not_positive",
        "period_key",
    } <= codes(out)
    assert not Target.objects.exists()


def test_subject_level_rows_resolve_staff_numbers(ids: dict[str, Any]) -> None:
    out = run("target.upload", rows=target_rows(FUTURE[:1]))
    assert out.accepted and out.written == 6
    fee = Target.objects.filter(metric__metric_code="fee_income")
    assert {t.scope_code for t in fee} == {ids[s] for s in STAFF}
    assert {t.state for t in Target.objects.all()} == {"draft"}
    unknown = run(
        "target.upload",
        rows=[{**target_rows(FUTURE[:1])[-1], "scope_code": "NOBODY"}],
        check_only=True,
    )
    assert codes(unknown) == {"unknown_subject"}


def test_reupload_replaces_the_draft(ids: dict[str, Any]) -> None:
    run("target.upload", rows=target_rows(FUTURE[:1]))
    run("target.upload", rows=target_rows(FUTURE[:1], value="250"))
    assert Target.objects.count() == 6
    assert {t.target_value for t in Target.objects.all()} == {Decimal("250")}
    assert {t.version for t in Target.objects.all()} == {1}


def test_a_csv_sheet_uploads(ids: dict[str, Any]) -> None:
    rows = target_rows(FUTURE[:1])
    header = list(rows[0])
    text = ",".join(header) + "\n" + "\n".join(",".join(r[h] for h in header) for r in rows)
    sheet = {"filename": "targets.csv", "content_base64": base64.b64encode(text.encode()).decode()}
    out = run("target.upload", sheet=sheet)
    assert out.accepted and out.written == 6
    broken = run(
        "target.upload",
        sheet={
            "filename": "t.csv",
            "content_base64": base64.b64encode(
                (
                    "\n".join(
                        [",".join(header), ",".join(rows[0][h] for h in header).replace("40", "x")]
                    )
                ).encode()
            ).decode(),
        },
    )
    # Sheet rows are numbered as the user sees them: the header is row 1.
    assert broken.findings[0].row_no == 2
    with pytest.raises(InvalidInput, match="missing required columns"):
        run(
            "target.upload",
            sheet={"filename": "t.csv", "content_base64": base64.b64encode(b"a,b\n1,2\n").decode()},
        )


def test_coverage_grid_reports_set_missing_and_draft(ids: dict[str, Any]) -> None:
    empty = run("target.coverage", period_key=FUTURE[0])
    assert empty.period_keys == FUTURE
    assert empty.profiles == [PROFILE]
    assert {c.state for c in empty.cells} == {"missing"}
    assert empty.gaps == 4 * 12 and not empty.complete

    rows = [
        r
        for r in target_rows(FUTURE[:1])
        if not (r["metric_code"] == "fee_income" and r["scope_code"] == "E3")
    ]
    run("target.upload", rows=rows)
    grid = run("target.coverage", period_key=FUTURE[0])
    jan = {c.metric_code: c for c in grid.cells if c.period_key == FUTURE[0]}
    assert jan["casa_growth"].state == "draft"
    assert jan["fee_income"].state == "partial"
    assert (jan["fee_income"].expected, jan["fee_income"].drafts) == (3, 2)
    weights = {w.period_key: w for w in grid.weights}
    assert not weights[FUTURE[0]].complete
    assert "fee_income for E3" in weights[FUTURE[0]].missing


def test_publish_blocks_on_weight_sums(ids: dict[str, Any]) -> None:
    run("target.upload", rows=target_rows(FUTURE[:1], weights={"casa_growth": 35}))
    with pytest.raises(Conflict) as exc:
        run("target.publish", period_keys=FUTURE[:1])
    (check,) = exc.value.detail["weight_checks"]
    assert check["profile_code"] == PROFILE
    assert check["weight_sum"] == "95.000"
    assert not TargetPublishBatch.objects.exists()
    assert {t.state for t in Target.objects.all()} == {"draft"}


def test_subject_weights_are_checked_per_person(ids: dict[str, Any]) -> None:
    rows = target_rows(FUTURE[:1])
    for r in rows:
        if r["metric_code"] == "fee_income" and r["scope_code"] == "E2":
            r["weight"] = "30"
    run("target.upload", rows=rows)
    with pytest.raises(Conflict) as exc:
        run("target.publish", period_keys=FUTURE[:1])
    (check,) = exc.value.detail["weight_checks"]
    assert check["subjects_off"] == [{"staff_no": "E2", "weight_sum": "105.000"}]


def test_publish_versions_and_supersedes(ids: dict[str, Any]) -> None:
    first = publish_targets(FUTURE[:2])
    assert first.batch.row_count == 12 and first.superseded == 0
    assert {t.state for t in Target.objects.all()} == {"published"}
    assert first.batch.weight_check_result["checks"][0]["ok"] is True

    run("target.upload", rows=target_rows(FUTURE[:1], value="120"))
    grid = run("target.coverage", period_key=FUTURE[0])
    assert {c.state for c in grid.cells if c.period_key == FUTURE[0]} == {"revision"}
    second = run("target.publish", period_keys=FUTURE[:1])
    assert second.superseded == 6
    live = Target.objects.filter(period_key=FUTURE[0], state="published")
    assert {(t.version, t.target_value) for t in live} == {(2, Decimal("120"))}
    old = Target.objects.filter(period_key=FUTURE[0], state="superseded")
    assert {t.version for t in old} == {1}

    history = run("target.get", target_id=str(live.first().target_id))  # type: ignore[union-attr]
    assert [v.version for v in history.versions] == [2, 1]
    assert AuditLog.objects.filter(event="target.batch_published").count() == 2


def test_nothing_to_publish_is_a_conflict(ids: dict[str, Any]) -> None:
    with pytest.raises(Conflict, match="no drafts"):
        run("target.publish", period_keys=FUTURE[:1])


def test_a_started_period_is_revised_by_override_not_a_new_version(ids: dict[str, Any]) -> None:
    # The first targets for a period that has started are still welcome: they are late, not a revision.
    publish_targets([CURRENT])
    out = run("target.upload", rows=target_rows([CURRENT], value="90"), check_only=True)
    assert codes(out) == {"open_period_revision"}
    # A draft sneaked in before the period started cannot be published after it has.
    t = Target.objects.filter(period_key=CURRENT).first()
    assert t is not None
    Target.objects.create(
        org_id=ORG_ID,
        metric=t.metric,
        scope_type=t.scope_type,
        scope_code=t.scope_code,
        period_key=CURRENT,
        target_value=1,
        weight=t.weight,
        cap=t.cap,
        version=2,
    )
    with pytest.raises(Conflict) as exc:
        run("target.publish", period_keys=[CURRENT])
    assert exc.value.detail["blocked"][0]["reason"] == "open_period_revision"


def test_a_closed_period_takes_no_targets(ids: dict[str, Any]) -> None:
    period = THIS_YEAR[0] if THIS_YEAR[0] < CURRENT else FUTURE[0]
    PeriodStatus.objects.create(
        org_id=ORG_ID, product="scorecards", period_key=period, status="closed"
    )
    out = run("target.upload", rows=target_rows([period]), check_only=True)
    assert "period_locked" in codes(out)


def test_revert_restores_the_prior_version(ids: dict[str, Any]) -> None:
    publish_targets(FUTURE[:1])
    run("target.upload", rows=target_rows(FUTURE[:1], value="500"))
    second = run("target.publish", period_keys=FUTURE[:1])
    out = run("target.batch.revert", batch_id=str(second.batch.batch_id), reason="Wrong file")
    assert out.restored == 6 and out.batch.status == "reverted"
    live = Target.objects.filter(state="published")
    assert {t.target_value for t in live} == {Decimal("100")}
    with pytest.raises(Conflict, match="already been reverted"):
        run("target.batch.revert", batch_id=str(second.batch.batch_id), reason="again")


def test_revert_refuses_a_started_period(ids: dict[str, Any]) -> None:
    done = publish_targets([CURRENT])
    with pytest.raises(Conflict, match="future"):
        run("target.batch.revert", batch_id=str(done.batch.batch_id), reason="x")


def test_copy_forward_with_uplift(ids: dict[str, Any]) -> None:
    publish_targets(FUTURE[:2])
    out = run(
        "target.copy_forward",
        from_period=FUTURE[0],
        to_period=FUTURE[1],
        uplift_pct="10",
        metric_uplift={"service_tat": "-5"},
    )
    assert out.accepted and out.written == 12
    drafts = Target.objects.filter(state="draft").select_related("metric")
    assert {t.period_key for t in drafts} == {
        f"{int(FUTURE[0][:4]) + 1}01",
        f"{int(FUTURE[0][:4]) + 1}02",
    }
    values = {t.metric.metric_code: t.target_value for t in drafts}
    assert values["casa_growth"] == Decimal("110")
    assert values["service_tat"] == Decimal("95")
    assert {t.source for t in drafts} == {"copy_forward"}
    assert {t.weight for t in drafts if t.metric.metric_code == "casa_growth"} == {Decimal("40")}


def test_copy_forward_needs_something_to_copy(ids: dict[str, Any]) -> None:
    with pytest.raises(InvalidInput, match="no published targets"):
        run("target.copy_forward", from_period=FUTURE[0], to_period=FUTURE[0])


def test_discard_only_touches_drafts(ids: dict[str, Any]) -> None:
    publish_targets(FUTURE[:1])
    run("target.upload", rows=target_rows(FUTURE[1:2]))
    every = [str(t.target_id) for t in Target.objects.all()]
    out = run("target.draft.discard", target_ids=every)
    assert out.discarded == 6
    assert Target.objects.filter(state="published").count() == 6


def test_the_database_refuses_a_scope_that_contradicts_the_metric(ids: dict[str, Any]) -> None:
    metric = Metric.objects.get(metric_code="casa_growth")
    with pytest.raises(IntegrityError, match="target_scope"), transaction.atomic():
        Target.objects.create(
            org_id=ORG_ID,
            metric=metric,
            scope_type="subject",
            scope_code=ids["E1"],
            period_key=FUTURE[0],
            target_value=1,
            version=1,
        )


def test_publish_goes_through_maker_checker_when_enabled(ids: dict[str, Any]) -> None:
    ApprovalPolicy.objects.create(org_id=ORG_ID, approval_class="target_publish", enabled=True)
    run("target.upload", rows=target_rows(FUTURE[:1]))
    out = run("target.publish", period_keys=FUTURE[:1])
    assert isinstance(out, Proposal)
    assert {t.state for t in Target.objects.all()} == {"draft"}


def test_list_filters_and_labels_subjects(ids: dict[str, Any]) -> None:
    run("target.upload", rows=target_rows(FUTURE[:1]))
    out = run("target.list", period_key=FUTURE[0], metric_code="fee_income")
    assert {t.scope_label for t in out.targets} == {f"{s} Person {s}" for s in STAFF}
    assert run("target.list", scope_code="E2").targets[0].scope_code == ids["E2"]
    assert len(run("target.list", state="published").targets) == 0


def test_metrics_cover_the_profile() -> None:
    assert sum(m[5] for m in METRICS.values()) == 100
