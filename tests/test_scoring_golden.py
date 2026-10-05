"""Golden cases for the scoring engine (TDD §4.3, Scope §6.2–6.6).

Each case states its expected figures by hand, and every case is scored twice:
by the per-subject path (``scoring.score_subject``) and by the Polars bulk path
(``bulk.score_period``). The two must agree on every field of every metric.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from kpigo.hierarchy.models import Assignment, Subject
from kpigo.ingestion.models import FactActualMonthly
from kpigo.metrics.models import Metric
from kpigo.periods.models import PerformanceCycle
from kpigo.platform.models import FxRate
from kpigo.scorecards.bulk import score_period
from kpigo.scorecards.engine import SubjectScore
from kpigo.scorecards.models import Override, ScorecardSettings, ScoreExclusion, Target
from kpigo.scorecards.scoring import score_subject
from tests.conftest import ORG_ID, run
from tests.scorecard_support import PROFILE, SINCE, TODAY, world

pytestmark = pytest.mark.django_db

P = f"{TODAY.year:04d}06"  # June: month 6 of a Jan–Dec cycle, month 3 of Apr–Mar
OTHER = "sme_rm_b"
D = Decimal


def metric(code: str) -> Metric:
    return Metric.objects.get(org_id=ORG_ID, metric_code=code)


def subject(staff_no: str, profile: str, **assignment: Any) -> str:
    s = Subject.objects.create(
        org_id=ORG_ID,
        staff_no=staff_no,
        full_name=f"Person {staff_no}",
        email=f"{staff_no.lower()}@bank.example",
    )
    Assignment.objects.create(
        org_id=ORG_ID,
        subject=s,
        role_code="rm",
        profile_code=profile,
        effective_from=SINCE,
        **assignment,
    )
    return str(s.subject_id)


def target(
    code: str,
    scope_type: str,
    scope_code: str,
    value: str,
    weight: str,
    cap: str,
    target_type: str = "monthly",
    currency: str | None = None,
) -> None:
    Target.objects.create(
        org_id=ORG_ID,
        metric=metric(code),
        scope_type=scope_type,
        scope_code=scope_code,
        period_key=P,
        target_value=D(value),
        target_type=target_type,
        weight=D(weight),
        cap=D(cap),
        currency_code=currency,
        version=1,
        state="published",
    )


def actual(code: str, subject_id: str, value: str, currency: str | None = None) -> None:
    a = Assignment.objects.get(subject_id=subject_id)
    FactActualMonthly.objects.create(
        org_id=ORG_ID,
        metric=metric(code),
        subject_id=subject_id,
        assignment=a,
        period_key=P,
        actual_value=D(value),
        currency_code=currency,
        loaded_at=timezone.now(),
    )


_CLOCK = datetime(2026, 1, 1, tzinfo=UTC)


def override(
    code: str,
    scope_type: str,
    scope_code: str,
    change: str,
    value: str | None = None,
    text: str | None = None,
    *,
    status: str = "approved",
    period_from: str = P,
    period_to: str | None = None,
    minutes: int = 0,
) -> str:
    approved = status == "approved"
    o = Override.objects.create(
        org_id=ORG_ID,
        scope_type=scope_type,
        scope_code=scope_code,
        metric=metric(code),
        period_from=period_from,
        period_to=period_to,
        change_type=change,
        override_value=D(value) if value is not None else None,
        override_text=text,
        reason=f"golden {change}",
        status=status,
        requested_by=1,
        approved_by=2 if approved else None,
        approved_at=_CLOCK + timedelta(minutes=minutes) if approved else None,
    )
    return str(o.override_id)


@pytest.fixture
def g() -> dict[str, Any]:
    ids = world()  # E1–E3 on sme_rm; casa_growth, ntb_accounts, service_tat, fee_income
    run(
        "scorecard.profile.set_metrics",
        profile_code=OTHER,
        metric_codes=["casa_growth", "ntb_accounts", "service_tat", "fee_income"],
        effective_from=SINCE,
    )
    april = PerformanceCycle.objects.create(
        org_id=ORG_ID, name="April to March", start_month=4, end_month=3
    )
    ids["E4"] = subject("E4", OTHER, cycle=april, branch_code="ACC")
    ids["E5"] = subject("E5", OTHER, branch_code="KSI")
    ids["E6"] = subject("E6", PROFILE)
    ids["E7"] = subject("E7", "no_metrics_here")
    ids["E8"] = subject("E8", PROFILE, region_code="NORTH")
    FxRate.objects.create(
        org_id=ORG_ID,
        from_currency="GHS",
        to_currency="USD",
        period_key=P,
        rate_type="average",
        rate=D("0.08"),
    )

    # sme_rm: plain monthly targets; fee income per subject.
    target("casa_growth", "profile", PROFILE, "100", "40", "60")
    target("ntb_accounts", "profile", PROFILE, "10", "20", "30")
    target("service_tat", "profile", PROFILE, "5", "15", "22")
    for staff_no in ("E1", "E3", "E6", "E8"):
        target("fee_income", "subject", ids[staff_no], "100", "25", "37")

    # sme_rm_b: every target type.
    target("casa_growth", "profile", OTHER, "1200", "40", "60", "yearly")
    target("ntb_accounts", "profile", OTHER, "10", "20", "30", "quarterly")
    target("service_tat", "profile", OTHER, "5", "15", "22", "cumulative")
    target("fee_income", "subject", ids["E4"], "2400", "25", "37", "prorated", "USD")
    target("fee_income", "subject", ids["E5"], "2400", "25", "37", "prorated", "USD")

    # E1: everything reported; an explicit zero scores as zero.
    actual("casa_growth", ids["E1"], "105")
    actual("ntb_accounts", ids["E1"], "0")
    actual("service_tat", ids["E1"], "4")
    actual("fee_income", ids["E1"], "150")

    # E2: casa missing (absent is not zero); no fee target (configuration gap).
    actual("ntb_accounts", ids["E2"], "12")
    actual("service_tat", ids["E2"], "5")
    actual("fee_income", ids["E2"], "90")

    # E3: overrides at every scope.
    actual("casa_growth", ids["E3"], "90")
    actual("ntb_accounts", ids["E3"], "10")
    actual("service_tat", ids["E3"], "10")
    actual("fee_income", ids["E3"], "100")
    override("casa_growth", "subject", ids["E3"], "target", "80")
    override("ntb_accounts", "profile", PROFILE, "weight", "30", minutes=1)
    override("ntb_accounts", "subject", ids["E3"], "weight", "10", minutes=0)
    override("service_tat", "subject", ids["E3"], "actual", "0")
    override("fee_income", "subject", ids["E3"], "cap", "20", minutes=1)
    override("fee_income", "subject", ids["E3"], "cap", "50", minutes=5)  # later wins
    override("fee_income", "subject", ids["E3"], "cap", "1", status="pending")
    override("fee_income", "subject", ids["E3"], "cap", "2", status="rejected")
    override("casa_growth", "subject", ids["E3"], "target", "1", period_from="202001")

    # E4: April–March cycle (June = month 3, quarter 1), branch override, FX.
    actual("casa_growth", ids["E4"], "110")
    actual("ntb_accounts", ids["E4"], "9")
    actual("service_tat", ids["E4"], "12")
    actual("fee_income", ids["E4"], "7500", "GHS")  # 7500 × 0.08 = 600 USD
    override("casa_growth", "dimension", "branch:ACC", "cap", "45")

    # E5: Jan–Dec (June = month 6, quarter 2); a negative actual and no FX rate.
    actual("casa_growth", ids["E5"], "-20")
    actual("ntb_accounts", ids["E5"], "25")
    actual("service_tat", ids["E5"], "30")
    actual("fee_income", ids["E5"], "100", "EUR")

    # E6: nothing reported. E7: a profile with no metrics.
    # E8: target type changed by override; a region override that a subject one beats.
    actual("casa_growth", ids["E8"], "500")
    actual("ntb_accounts", ids["E8"], "10")
    actual("service_tat", ids["E8"], "5")
    actual("fee_income", ids["E8"], "100")
    override("casa_growth", "subject", ids["E8"], "target_type", text="cumulative")
    override("fee_income", "dimension", "region:NORTH", "weight", "35")
    override("fee_income", "profile", PROFILE, "weight", "25")
    override(
        "ntb_accounts",
        "dimension",
        "region:NORTH",
        "target",
        "20",
        period_to=f"{TODAY.year:04d}12",
        period_from=f"{TODAY.year:04d}01",
    )
    return ids


def both(ids: dict[str, Any]) -> dict[str, SubjectScore]:
    bulk = {s.subject_id: s for s in score_period(ORG_ID, P)}
    single = {}
    for staff_no, sid in ids.items():
        one = score_subject(ORG_ID, sid, P)
        assert one is not None, staff_no
        single[staff_no] = one
        assert bulk[sid] == one, f"{staff_no}: bulk and per-subject paths disagree"
    assert set(bulk) == {s.subject_id for s in single.values()}
    return single


def by_code(s: SubjectScore) -> dict[str, Any]:
    return {m.metric_code: m for m in s.metrics}


@pytest.mark.parametrize("policy", ["reduced", "redistribute"])
def test_both_paths_agree_on_every_case(g: dict[str, Any], policy: str) -> None:
    ScorecardSettings.objects.update_or_create(
        org_id=ORG_ID, defaults={"denominator_policy": policy}
    )
    both(g)


def test_fully_reported_scorecard(g: dict[str, Any]) -> None:
    e1 = both(g)["E1"]
    m = by_code(e1)
    assert m["casa_growth"].state == "scored"
    assert m["casa_growth"].pct_achieved == D("1.05")
    assert m["casa_growth"].score == D("0.42")  # 1.05 × 40 ÷ 100
    assert m["ntb_accounts"].state == "zero_actual" and m["ntb_accounts"].score == 0
    assert m["service_tat"].pct_achieved == D("1.25")  # lower is better: 5 ÷ 4
    assert m["service_tat"].score == D("0.1875")
    assert m["fee_income"].score == D("0.37")  # 1.5 × 25 = 37.5, capped at 37
    assert e1.total_score == D("0.9775")
    assert e1.graded_score == D("0.9775")
    assert e1.total_cap == D("1.49")
    assert e1.metrics_scored == 4 and e1.not_reported == 0
    assert e1.band is not None and e1.band.label == "Gaining Momentum"
    assert e1.statement == "4 of 4 metrics scored"


def test_absent_is_not_zero(g: dict[str, Any]) -> None:
    e2 = both(g)["E2"]
    m = by_code(e2)
    assert m["casa_growth"].state == "not_reported" and m["casa_growth"].score is None
    assert m["fee_income"].state == "no_target"
    # ntb 1.2 × 30 (the profile's weight override) = 36, capped at 30; service
    # 1.0 × 15 = 15 → 0.45 out of 45 scored weight. Casa's 40 is still expected,
    # so the grade reads 0.45 × 85 ÷ 45.
    assert e2.total_score == D("0.45")
    assert e2.weight_scored == 45 and e2.weight_expected == 85
    assert e2.graded_score == D("0.85")
    assert e2.band is not None and e2.band.label == "Gaining Momentum"
    assert e2.statement == "2 of 4 metrics scored · 1 awaiting data · 1 without a target"


def test_redistribute_spreads_missing_weight(g: dict[str, Any]) -> None:
    ScorecardSettings.objects.update_or_create(
        org_id=ORG_ID, defaults={"denominator_policy": "redistribute"}
    )
    e2 = both(g)["E2"]
    assert e2.total_score == e2.graded_score == D("0.85")
    assert by_code(e2)["ntb_accounts"].score == D("0.566667")  # 0.30 × 85 ÷ 45


def test_override_precedence(g: dict[str, Any]) -> None:
    e3 = both(g)["E3"]
    m = by_code(e3)
    assert m["casa_growth"].target_value == 80 and m["casa_growth"].pct_achieved == D("1.125")
    assert m["ntb_accounts"].weight == 10  # subject beats the profile's 30
    assert m["service_tat"].state == "zero_actual" and m["service_tat"].actual_value == 0
    assert m["service_tat"].score == D("0.22")  # nothing on a lower-is-better metric: full cap
    assert m["service_tat"].run_id is None
    assert m["fee_income"].cap == 50  # the later approval; pending and rejected ignored
    assert [(o.change_type, o.scope_type) for o in m["fee_income"].overrides] == [
        ("cap", "subject"),
        ("weight", "profile"),
    ]
    e1 = both(g)["E1"]
    assert by_code(e1)["ntb_accounts"].weight == 30  # the profile override reaches E1


def test_target_types_follow_the_subjects_cycle(g: dict[str, Any]) -> None:
    s = both(g)
    e4, e5 = by_code(s["E4"]), by_code(s["E5"])
    assert s["E4"].cycle.months_elapsed == 3 and s["E4"].cycle.quarters_elapsed == 1
    assert s["E5"].cycle.months_elapsed == 6 and s["E5"].cycle.quarters_elapsed == 2
    assert e4["casa_growth"].target_value == 100  # yearly 1200 ÷ 12
    assert e4["casa_growth"].cap == 45  # branch:ACC override
    assert e4["casa_growth"].score == D("0.44")  # 1.1 × 40 = 44, under the 45 cap
    assert e4["ntb_accounts"].target_value == 10 and e5["ntb_accounts"].target_value == 20
    assert e4["service_tat"].target_value == 15 and e5["service_tat"].target_value == 30
    assert e4["fee_income"].target_value == 600  # (2400 ÷ 12) × 3
    assert e4["fee_income"].actual_value == 600 and e4["fee_income"].fx_rate == D("0.08")
    assert e4["fee_income"].reported_actual == 7500
    assert e5["fee_income"].state == "no_fx_rate" and e5["fee_income"].score is None
    assert e5["casa_growth"].state == "scored" and e5["casa_growth"].score == 0  # floored


def test_nothing_reported_has_no_grade(g: dict[str, Any]) -> None:
    s = both(g)
    assert s["E6"].metrics_scored == 0 and s["E6"].not_reported == 4
    assert s["E6"].graded_score is None and s["E6"].band is None
    assert s["E7"].metrics_total == 0 and s["E7"].band is None
    assert s["E7"].statement == "No metrics on this profile's scorecard."


def test_target_type_and_dimension_overrides(g: dict[str, Any]) -> None:
    e8 = by_code(both(g)["E8"])
    assert e8["casa_growth"].target_type == "cumulative"
    assert e8["casa_growth"].target_value == 600  # 100 × 6 months
    assert e8["fee_income"].weight == 25  # profile beats dimension
    assert [o.scope_type for o in e8["fee_income"].overrides] == ["profile"]
    assert e8["ntb_accounts"].target_value == 20  # a year-long region override covers June


def test_exclusions_leave_the_denominator(g: dict[str, Any]) -> None:
    ScoreExclusion.objects.create(
        org_id=ORG_ID,
        period_key=P,
        metric=metric("casa_growth"),
        subject_id=g["E2"],
        reason="Core banking migration lost E2's June balances.",
    )
    ScoreExclusion.objects.create(
        org_id=ORG_ID, period_key=P, metric=metric("fee_income"), reason="Fee feed retired."
    )
    ScoreExclusion.objects.create(
        org_id=ORG_ID,
        period_key=P,
        metric=metric("fee_income"),
        subject_id=g["E5"],
        reason="No EUR rate this month.",
    )
    s = both(g)
    e2 = by_code(s["E2"])
    assert e2["casa_growth"].state == "excluded"
    assert e2["casa_growth"].exclusion_reason == "Core banking migration lost E2's June balances."
    assert e2["fee_income"].exclusion_reason == "Fee feed retired."
    assert s["E2"].weight_expected == 45 and s["E2"].graded_score == D("0.45")
    assert s["E2"].statement == "2 of 4 metrics scored · 2 excluded"
    # The subject's own exclusion beats the one for everyone.
    assert by_code(s["E5"])["fee_income"].exclusion_reason == "No EUR rate this month."
    # A scored metric stays scored: an exclusion only covers what is missing.
    assert by_code(s["E1"])["fee_income"].state == "scored"


def test_no_assignment_in_force() -> None:
    world()
    assert score_subject(ORG_ID, "00000000-0000-0000-0000-00000000dead", P) is None
    assert score_period(ORG_ID, "199901") == []
