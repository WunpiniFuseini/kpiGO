"""Shared set-up for the Agent Performance tests: a small sales floor, built through actions.

Three RMs on the ``sme_rm`` profile in two regions, measured in Agent Sales on
value booked (a sum, profile target), accounts opened (a count, a target each)
and service TAT (an average, lower is better). June 2025 is the month: it starts
on a Sunday and has 21 working days on the default Monday-to-Friday week.
"""

from __future__ import annotations

import base64
from datetime import date
from typing import Any

from tests.conftest import run

STAFF = {"A1": "GA", "A2": "GA", "A3": "AS"}
PROFILE = "sme_rm"
PRODUCT = "agent_sales"
MONTH = "202506"
SINCE = "2024-01-01"
DAILY = [
    "metric_code",
    "subject_ref",
    "activity_date",
    "product_line_code",
    "actual_value",
    "currency_code",
]

# metric_code → (name, unit, aggregation, direction, target_scope)
METRICS: dict[str, tuple[str, str, str, str, str]] = {
    "value_booked": ("Value booked", "currency", "sum", "higher_is_better", "profile"),
    "accounts_opened": ("Accounts opened", "count", "count", "higher_is_better", "subject"),
    "service_tat": ("Service TAT", "hours", "average", "lower_is_better", "profile"),
}


def world() -> dict[str, str]:
    ids: dict[str, str] = {}
    for dim, codes in (("region", ["GA", "AS"]), ("branch", ["GA-01", "AS-01"])):
        run("dimension.define", dimension_type=dim, display_name=dim.title())
        run(
            "dimension.member.upsert",
            dimension_type=dim,
            members=[{"member_code": c, "member_name": c} for c in codes],
        )
    for staff_no, region in STAFF.items():
        subject = run(
            "subject.register",
            staff_no=staff_no,
            full_name=f"Agent {staff_no}",
            email=f"{staff_no.lower()}@bank.example",
        )
        ids[staff_no] = str(subject.subject_id)
        run(
            "assignment.create",
            subject_id=str(subject.subject_id),
            role_code="rm",
            profile_code=PROFILE,
            branch_code=f"{region}-01",
            region_code=region,
            effective_from=SINCE,
        )
    for code, (name, unit, agg, direction, scope) in METRICS.items():
        run(
            "metric.register",
            display_name=name,
            metric_code=code,
            direction=direction,
            aggregation=agg,
            unit=unit,
            target_scope=scope,
            products=[PRODUCT],
            effective_from=SINCE,
            acknowledge_similar=True,
        )
        run(
            "metric.profile.assign",
            metric_code=code,
            profile_code=PROFILE,
            product=PRODUCT,
            effective_from=SINCE,
        )
    run("currency.upsert", code="GHS", name="Ghana cedi")
    run("currency.upsert", code="USD", name="US dollar")
    return ids


def targets(months: list[str] = [MONTH], **values: str) -> Any:  # noqa: B006
    """Publish targets: value booked 2100 (100 a working day in June), 21 accounts each."""
    rows: list[dict[str, Any]] = []
    for month in months:
        rows.append(
            {
                "metric_code": "value_booked",
                "scope_type": "profile",
                "scope_code": PROFILE,
                "period_key": month,
                "target_value": values.get("value_booked", "2100"),
                "currency_code": "GHS",
            }
        )
        rows.append(
            {
                "metric_code": "service_tat",
                "scope_type": "profile",
                "scope_code": PROFILE,
                "period_key": month,
                "target_value": values.get("service_tat", "4"),
            }
        )
        rows += [
            {
                "metric_code": "accounts_opened",
                "scope_type": "subject",
                "scope_code": staff_no,
                "period_key": month,
                "target_value": values.get("accounts_opened", "21"),
            }
            for staff_no in STAFF
        ]
    out = run("target.upload", rows=rows)
    assert out.accepted, out.findings
    return run("target.publish", period_keys=months)


def upload(rows: list[list[Any]]) -> dict[str, str]:
    lines = [",".join(DAILY)] + [",".join("" if c is None else str(c) for c in row) for row in rows]
    data = ("\n".join(lines) + "\n").encode()
    return {"filename": "daily.csv", "content_base64": base64.b64encode(data).decode()}


def load(rows: list[list[Any]], feed: str = "daily") -> Any:
    """Dry-run then load daily rows through a registered upload feed."""
    from kpigo.ingestion.models import Feed

    if not Feed.objects.filter(name=feed).exists():
        run("feed.register", name=feed, template="actual_daily", mode="upload")
    dry = run("feed.dry_run", feed=feed, upload=upload(rows))
    assert dry.passed, dry.findings
    return run("feed.run", feed=feed, upload=upload(rows))


def day(n: int, month: str = MONTH) -> str:
    return date(int(month[:4]), int(month[4:]), n).isoformat()
