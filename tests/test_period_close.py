"""Period close, frozen snapshots and restatement (PRD SC-9–SC-13; App Flow §7.2, §7.3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, InternalError, transaction
from django.utils import timezone

from kpigo.action import ActionContext, Conflict, InvalidInput, Proposal
from kpigo.action.context import AllSubjects
from kpigo.hierarchy.models import Assignment
from kpigo.ingestion.models import FactActualMonthly
from kpigo.metrics.models import Metric
from kpigo.periods.models import PeriodStatus
from kpigo.platform.models import ApprovalPolicy
from kpigo.scorecards.models import RatingBand, ScoreHistory, ScoreSnapshot, ScoreTotal, Target
from tests.conftest import ORG_ID, role_ctx, run
from tests.scorecard_support import METRICS, NEXT, PREVIOUS, PROFILE, world

pytestmark = pytest.mark.django_db

P = PREVIOUS


def admin() -> ActionContext:
    return replace(role_ctx(), visible_subjects=AllSubjects())


def metric(code: str) -> Metric:
    return Metric.objects.get(org_id=ORG_ID, metric_code=code)


def fact(ids: dict[str, Any], staff_no: str, code: str, value: str) -> None:
    FactActualMonthly.objects.update_or_create(
        metric=metric(code),
        subject_id=ids[staff_no],
        period_key=P,
        defaults={
            "org_id": ORG_ID,
            "assignment": Assignment.objects.get(subject_id=ids[staff_no]),
            "actual_value": Decimal(value),
            "loaded_at": timezone.now(),
        },
    )


@pytest.fixture
def ids() -> dict[str, Any]:
    ids = world()
    for code, (_, _, _, _, scope, weight, cap) in METRICS.items():
        codes = [PROFILE] if scope == "profile" else [ids["E1"], ids["E2"]]  # none for E3
        for scope_code in codes:
            Target.objects.create(
                org_id=ORG_ID,
                metric=metric(code),
                scope_type=scope,
                scope_code=scope_code,
                period_key=P,
                target_value=Decimal(100),
                weight=Decimal(weight),
                cap=Decimal(cap),
                version=1,
                state="published",
            )
    for staff_no in ("E1", "E2", "E3"):
        for code in ("casa_growth", "ntb_accounts", "service_tat"):
            fact(ids, staff_no, code, "100")
    fact(ids, "E1", "fee_income", "110")  # E2's fee income never arrives
    fact(ids, "E3", "fee_income", "90")  # E3 has no fee target
    return ids


def check() -> Any:
    return run("scorecard.close.check", period_key=P)


def resolve(ids: dict[str, Any]) -> None:
    run(
        "scorecard.exclusion.add",
        period_key=P,
        metric_code="fee_income",
        subject="E2",
        reason="Fee system outage; E2's month is unrecoverable.",
    )
    run(
        "scorecard.exclusion.add",
        period_key=P,
        metric_code="fee_income",
        subject="E3",
        reason="Joined the fee scheme after targets were set.",
    )


def test_pre_check_names_every_blocker(ids: dict[str, Any]) -> None:
    out = check()
    assert not out.ready and out.refused is None and out.subjects == 3
    by_kind = {b.kind: b for b in out.blockers}
    assert by_kind["not_reported"].metric_code == "fee_income"
    assert by_kind["not_reported"].subjects == ["E2"]
    assert by_kind["no_target"].subjects == ["E3"]
    with pytest.raises(Conflict, match="cannot close") as e:
        run("scorecard.period.close", period_key=P)
    assert {b["kind"] for b in e.value.detail["blockers"]} == {"not_reported", "no_target"}
    assert not ScoreSnapshot.objects.exists()

    resolve(ids)
    assert check().ready
    excluded = run("scorecard.exclusion.list", period_key=P).exclusions
    assert [(x.staff_no, x.metric_code) for x in excluded] == [
        ("E2", "fee_income"),
        ("E3", "fee_income"),
    ]


def test_weights_outside_tolerance_block_close(ids: dict[str, Any]) -> None:
    resolve(ids)
    Target.objects.filter(metric=metric("casa_growth")).update(weight=Decimal(50))
    out = check()
    (blocker,) = out.blockers
    assert blocker.kind == "weights" and blocker.profile_code == PROFILE


def test_close_freezes_inputs_and_outputs(ids: dict[str, Any]) -> None:
    resolve(ids)
    live = run("scorecard.compute", admin(), subject_id=ids["E1"], period_key=P)
    assert live.source == "live" and live.next_band is not None

    snap = run("scorecard.period.close", period_key=P)
    assert snap.snapshot_version == 1 and snap.kind == "close" and snap.subjects == 3
    status = PeriodStatus.objects.get(org_id=ORG_ID, product="scorecards", period_key=P)
    assert (status.status, status.snapshot_version) == ("closed", 1)
    assert ScoreTotal.objects.count() == 3 and ScoreHistory.objects.count() == 12

    frozen = run("scorecard.compute", admin(), subject_id=ids["E1"], period_key=P)
    assert frozen.source == "snapshot" and frozen.snapshot_version == 1
    assert frozen.restated_at is None and frozen.next_band is None
    for field in ("total_score", "graded_score", "band", "statement", "metrics_scored"):
        assert getattr(frozen, field) == getattr(live, field), field
    assert [m.model_dump(exclude={"path"}) for m in frozen.metrics] == [
        m.model_dump(exclude={"path"}) for m in live.metrics
    ]
    e2 = run("scorecard.compute", admin(), subject_id=ids["E2"], period_key=P)
    fee = next(m for m in e2.metrics if m.metric_code == "fee_income")
    assert fee.state == "excluded" and fee.exclusion_reason.startswith("Fee system outage")

    # The published month no longer moves: not with a source change, not with new bands.
    fact(ids, "E1", "casa_growth", "1")
    RatingBand.objects.create(
        org_id=ORG_ID, product="scorecards", label="Everyone", threshold=0, ramp_position=1
    )
    again = run("scorecard.compute", admin(), subject_id=ids["E1"], period_key=P)
    assert again.total_score == live.total_score and again.band == live.band

    listed = run("scorecard.period.list", admin(), period_key=P)
    assert listed.source == "snapshot" and [r.staff_no for r in listed.rows] == ["E1", "E2", "E3"]

    with pytest.raises(Conflict, match="Restate it"):
        run("scorecard.period.close", period_key=P)
    with pytest.raises(Conflict, match="Restate it first"):
        run(
            "scorecard.exclusion.add",
            period_key=P,
            metric_code="casa_growth",
            reason="Too late for this",
        )


def test_frozen_rows_cannot_be_changed(ids: dict[str, Any]) -> None:
    resolve(ids)
    run("scorecard.period.close", period_key=P)
    total = ScoreTotal.objects.first()
    assert total is not None
    for change in (
        lambda: ScoreTotal.objects.filter(pk=total.pk).update(total_score=Decimal(9)),
        lambda: ScoreTotal.objects.filter(pk=total.pk).update(
            is_current=False, total_score=Decimal(9)
        ),
        lambda: ScoreHistory.objects.filter(subject_id=total.subject_id).delete(),
        lambda: ScoreSnapshot.objects.update(reason="edited"),
    ):
        with pytest.raises((IntegrityError, InternalError)), transaction.atomic():
            change()


def test_restatement_writes_a_new_version_beside_the_old(
    ids: dict[str, Any], make_user: Callable[..., User]
) -> None:
    resolve(ids)
    run("scorecard.period.close", period_key=P)
    v1 = run("scorecard.compute", admin(), subject_id=ids["E1"], period_key=P)

    with pytest.raises(InvalidInput):
        run("scorecard.period.restate", period_key=P, reason="")
    out = run("scorecard.period.restate", period_key=P, reason="Corrected CASA balances for E1.")
    assert out.status == "restating" and out.ready

    # Writes are open for this period only: a corrected actual and an approved override.
    fact(ids, "E1", "casa_growth", "130")
    requester = replace(role_ctx("admin"), user=make_user("admin"))
    approver = replace(role_ctx("metric_owner"), user=make_user("metric_owner"))
    o = run(
        "override.request",
        replace(requester, visible_subjects=AllSubjects()),
        scope_type="subject",
        scope_code="E2",
        metric_code="ntb_accounts",
        period_from=P,
        change_type="target",
        override_value="50",
        reason="Target set before the branch merger.",
    )
    run(
        "override.approve",
        replace(approver, visible_subjects=AllSubjects()),
        override_id=o.override_id,
    )
    preview = run("scorecard.compute", admin(), subject_id=ids["E1"], period_key=P)
    assert preview.source == "live" and preview.period_status == "restating"

    v2 = run("scorecard.period.close", period_key=P)
    assert v2.snapshot_version == 2 and v2.kind == "restatement"
    assert v2.reason == "Corrected CASA balances for E1."
    restated = run("scorecard.compute", admin(), subject_id=ids["E1"], period_key=P)
    assert restated.snapshot_version == 2 and restated.restated_at is not None
    assert restated.restatement_reason == "Corrected CASA balances for E1."
    assert restated.total_score > v1.total_score
    e2 = run("scorecard.compute", admin(), subject_id=ids["E2"], period_key=P)
    ntb = next(m for m in e2.metrics if m.metric_code == "ntb_accounts")
    assert ntb.target_value == 50 and [x.override_id for x in ntb.overrides] == [o.override_id]

    # Nothing is overwritten: both versions remain, one current.
    assert ScoreTotal.objects.filter(subject_id=ids["E1"]).count() == 2
    assert (
        ScoreTotal.objects.filter(subject_id=ids["E1"], is_current=True).get().snapshot_version == 2
    )
    old = ScoreTotal.objects.get(subject_id=ids["E1"], snapshot_version=1)
    assert old.total_score == v1.total_score
    versions = run("scorecard.snapshot.list", period_key=P).snapshots
    assert [s.snapshot_version for s in versions] == [2, 1]


def test_restate_only_a_closed_period(ids: dict[str, Any]) -> None:
    with pytest.raises(Conflict, match="Only a closed period"):
        run("scorecard.period.restate", period_key=P, reason="Nothing to restate")
    out = run("scorecard.close.check", period_key=NEXT)
    assert out.refused is not None and "has not started" in out.refused
    with pytest.raises(Conflict, match="has not started"):
        run("scorecard.period.close", period_key=NEXT)


def test_close_is_held_for_approval_when_the_class_is_on(ids: dict[str, Any]) -> None:
    resolve(ids)
    ApprovalPolicy.objects.create(org_id=ORG_ID, approval_class="period_close", enabled=True)
    out = run("scorecard.period.close", period_key=P)
    assert isinstance(out, Proposal)
    assert not ScoreSnapshot.objects.exists()
