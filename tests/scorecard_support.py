"""Shared set-up for the Scorecards tests: a small org built through its actions.

Three SME relationship managers on one profile, measured on four metrics: three
take profile-level targets, one (fee income) takes a target per person.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from tests.conftest import run


def month_key(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}"


def shift(period_key: str, months: int) -> str:
    index = int(period_key[:4]) * 12 + int(period_key[4:]) - 1 + months
    return f"{index // 12:04d}{index % 12 + 1:02d}"


TODAY = date.today()
CURRENT = month_key(TODAY)
PREVIOUS = shift(CURRENT, -1)
NEXT = shift(CURRENT, 1)
# A whole cycle year that has not started: January to December next year.
NEXT_YEAR = TODAY.year + 1
FUTURE = [f"{NEXT_YEAR:04d}{m:02d}" for m in range(1, 13)]
THIS_YEAR = [f"{TODAY.year:04d}{m:02d}" for m in range(1, 13)]
SINCE = f"{TODAY.year - 1:04d}-01-01"
STAFF = ("E1", "E2", "E3")
PROFILE = "sme_rm"

# metric_code → (name, unit, aggregation, direction, target_scope, weight, cap)
METRICS: dict[str, tuple[str, str, str, str, str, int, int]] = {
    "casa_growth": (
        "CASA balance growth",
        "currency",
        "sum",
        "higher_is_better",
        "profile",
        40,
        60,
    ),
    "ntb_accounts": ("New-to-bank accounts", "count", "sum", "higher_is_better", "profile", 20, 30),
    "service_tat": ("Service TAT", "days", "average", "lower_is_better", "profile", 15, 22),
    "fee_income": (
        "Fee and commission income",
        "currency",
        "sum",
        "higher_is_better",
        "subject",
        25,
        37,
    ),
}


def world() -> dict[str, Any]:
    ids: dict[str, Any] = {}
    for staff_no in STAFF:
        subject = run(
            "subject.register",
            staff_no=staff_no,
            full_name=f"Person {staff_no}",
            email=f"{staff_no.lower()}@bank.example",
        )
        ids[staff_no] = str(subject.subject_id)
        run(
            "assignment.create",
            subject_id=str(subject.subject_id),
            role_code="rm",
            profile_code=PROFILE,
            effective_from=SINCE,
        )
    for code, (name, unit, agg, direction, scope, _, _) in METRICS.items():
        run(
            "metric.register",
            display_name=name,
            metric_code=code,
            direction=direction,
            aggregation=agg,
            unit=unit,
            target_scope=scope,
            products=["scorecards"],
            effective_from=SINCE,
            acknowledge_similar=True,
        )
    run(
        "scorecard.profile.set_metrics",
        profile_code=PROFILE,
        metric_codes=list(METRICS),
        effective_from=SINCE,
    )
    return ids


def target_rows(
    periods: list[str],
    *,
    ids: dict[str, Any] | None = None,
    weights: dict[str, int] | None = None,
    value: str = "100",
) -> list[dict[str, Any]]:
    """A full, balanced set of target rows for the profile in each period."""
    rows: list[dict[str, Any]] = []
    for period in periods:
        for code, (_, _, _, _, scope, weight, cap) in METRICS.items():
            w = (weights or {}).get(code, weight)
            base = {
                "metric_code": code,
                "scope_type": scope,
                "period_key": period,
                "target_value": value,
                "weight": str(w),
                "cap": str(max(cap, w)),
            }
            if scope == "profile":
                rows.append({**base, "scope_code": PROFILE})
            else:
                rows += [{**base, "scope_code": staff_no} for staff_no in STAFF]
    return rows


def publish_targets(periods: list[str], **kw: Any) -> Any:
    out = run("target.upload", rows=target_rows(periods, **kw))
    assert out.accepted, out.findings
    return run("target.publish", period_keys=periods)
