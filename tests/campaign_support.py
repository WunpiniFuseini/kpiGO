"""Shared set-up for the Campaign Manager tests: a bank's dimensions, built through actions.

Customers are described by three dimensions the outcome feed also carries:
``segment`` (retail, with mass and affluent below it, and sme), ``product``
(savings, cards) and ``region`` (GA, AS). GHS is the currency.
"""

from __future__ import annotations

from typing import Any

from kpigo.action import ActionContext
from kpigo.action.context import DataScopeGrant
from tests.conftest import role_ctx, run

DIMENSIONS: dict[str, list[tuple[str, str | None]]] = {
    "segment": [("retail", None), ("mass", "retail"), ("affluent", "retail"), ("sme", None)],
    "product": [("savings", None), ("cards", None)],
    "region": [("GA", None), ("AS", None)],
}

EVERYTHING = (DataScopeGrant(module="campaign", dimension_type="campaign", member_code="*"),)


def world() -> None:
    run("currency.upsert", code="GHS", name="Ghana cedi")
    for dim, members in DIMENSIONS.items():
        run("dimension.define", dimension_type=dim, display_name=dim.title())
        run(
            "dimension.member.upsert",
            dimension_type=dim,
            members=[
                {"member_code": c, "member_name": c.title(), "parent_code": p} for c, p in members
            ],
        )


def admin() -> ActionContext:
    """An Admin who sees every campaign."""
    return role_ctx("admin", data_scopes=EVERYTHING)


def event(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_name": "October wave",
        "period_start": "2026-10-01",
        "period_end": "2026-10-31",
        "budget_amount": "25000",
        "budget_currency": "GHS",
        "channels": ["sms"],
        "audience": [{"dimension_type": "segment", "member_code": "retail"}],
    }
    payload.update(extra)
    return payload


def campaign(ctx: ActionContext | None = None, /, **extra: Any) -> Any:
    payload: dict[str, Any] = {
        "code": "SAVE-Q4",
        "name": "Save more this quarter",
        "campaign_type": "seasonal",
        "objective": "deposit_growth",
        "product_code": "savings",
        "events": [event()],
    }
    payload.update(extra)
    return run("campaign.create", ctx or admin(), **payload)
