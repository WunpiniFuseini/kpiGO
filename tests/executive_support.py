"""Shared set-up for the Executive Dashboard tests: a bank's executive metrics, through actions.

Regions form a small tree (``south`` above ``GA`` and ``AS``; ``north`` above
``NR``). The metrics cover each source a widget can draw from: fed at executive
level (revenue, cost-to-income, NPS by hand), rolled up from RM scorecards (CASA),
and Scorecards-only or draft metrics a widget must refuse.
"""

from __future__ import annotations

from typing import Any

from tests.conftest import run
from tests.ingestion_support import SINCE

REGIONS: list[tuple[str, str | None]] = [
    ("south", None),
    ("GA", "south"),
    ("AS", "south"),
    ("north", None),
    ("NR", "north"),
]


def metric(
    code: str,
    unit: str,
    products: list[str],
    *,
    aggregation: str = "sum",
    direction: str = "higher_is_better",
    status: str = "active",
    collection_method: str = "feed",
    name: str | None = None,
) -> None:
    run(
        "metric.register",
        display_name=name or code.replace("_", " ").title(),
        metric_code=code,
        direction=direction,
        aggregation=aggregation,
        unit=unit,
        is_percentage=unit == "percent",
        products=products,
        status=status,
        collection_method=collection_method,
        effective_from=SINCE,
        acknowledge_similar=True,
    )


def world() -> None:
    run("currency.upsert", code="GHS", name="Ghana cedi")
    run("dimension.define", dimension_type="region", display_name="Region")
    run(
        "dimension.member.upsert",
        dimension_type="region",
        members=[{"member_code": c, "member_name": c, "parent_code": p} for c, p in REGIONS],
    )
    run("dimension.define", dimension_type="channel", display_name="Channel")
    run(
        "dimension.member.upsert",
        dimension_type="channel",
        members=[{"member_code": c, "member_name": c} for c in ("branch", "digital")],
    )
    metric("ex_revenue", "currency", ["executive"], name="Total revenue")
    metric(
        "ex_cti",
        "percent",
        ["executive"],
        aggregation="average",
        direction="lower_is_better",
        name="Cost-to-income ratio",
    )
    metric("ex_leads", "count", ["executive"], name="Leads")
    metric("ex_accounts", "count", ["executive"], name="Accounts opened")
    metric("ex_casa", "currency", ["scorecards", "executive"], name="CASA growth")
    metric(
        "ex_nps",
        "score",
        ["executive"],
        aggregation="average",
        collection_method="manual_input",
        name="Net promoter score",
    )
    metric("sc_only", "currency", ["scorecards"], name="Scorecards only")
    metric("ex_draft", "currency", ["executive"], status="draft", name="Draft revenue")


def place(**over: Any) -> Any:
    payload: dict[str, Any] = {
        "widget_key": "revenue",
        "widget_type": "kpi_card",
        "metrics": [{"metric_code": "ex_revenue"}],
    }
    payload.update(over)
    return run("widget.place", **payload)
