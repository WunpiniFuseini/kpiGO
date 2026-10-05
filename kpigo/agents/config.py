"""Agent Performance settings with their defaults."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from kpigo.agents.models import AgentSettings

AgentProduct = Literal["agent_sales", "agent_service"]
Grain = Literal["daily", "weekly"]
CohortType = Literal["all", "profile", "branch", "region", "cohort"]


@dataclass(frozen=True)
class Settings:
    product: str
    grain: str = "daily"
    pace_cap: Decimal = Decimal(2)
    rag_green: Decimal = Decimal(1)
    rag_amber: Decimal = Decimal("0.85")
    # None ranks by the composite: the mean of each metric's capped pace.
    rank_metric_code: str | None = None
    tiebreak_metric_code: str | None = None
    cohort_type: str = "profile"

    @property
    def window(self) -> Literal["month", "week"]:
        return "week" if self.grain == "weekly" else "month"


def settings_for(org_id: str, product: str) -> Settings:
    row = AgentSettings.objects.filter(org_id=org_id, product=product).first()
    if row is None:
        return Settings(product=product)
    return Settings(
        product=product,
        grain=row.grain,
        pace_cap=Decimal(row.pace_cap),
        rag_green=Decimal(row.rag_green),
        rag_amber=Decimal(row.rag_amber),
        rank_metric_code=row.rank_metric_code,
        tiebreak_metric_code=row.tiebreak_metric_code,
        cohort_type=row.cohort_type,
    )


def rag(settings: Settings, achieved: Decimal | None) -> Literal["green", "amber", "red"] | None:
    if achieved is None:
        return None
    if achieved >= settings.rag_green:
        return "green"
    return "amber" if achieved >= settings.rag_amber else "red"
