"""Scorecard settings with their defaults, and where a period stands."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from kpigo.hierarchy.scope import current_period_key
from kpigo.periods.models import PeriodStatus
from kpigo.scorecards.models import ScorecardSettings

PRODUCT = "scorecards"
LOCKED = frozenset({"closing", "closed", "restating"})


@dataclass(frozen=True)
class Settings:
    weight_total: Decimal = Decimal(100)
    weight_tolerance: Decimal = Decimal("0.5")
    cap_min_ratio: Decimal = Decimal(1)
    cap_max_ratio: Decimal = Decimal(3)
    denominator_policy: str = "reduced"


def settings_for(org_id: str) -> Settings:
    row = ScorecardSettings.objects.filter(org_id=org_id).first()
    if row is None:
        return Settings()
    return Settings(
        weight_total=Decimal(row.weight_total),
        weight_tolerance=Decimal(row.weight_tolerance),
        cap_min_ratio=Decimal(row.cap_min_ratio),
        cap_max_ratio=Decimal(row.cap_max_ratio),
        denominator_policy=row.denominator_policy,
    )


PeriodPhase = Literal["future", "open", "locked"]


def period_status(org_id: str, period_key: str, product: str = PRODUCT) -> str:
    """The recorded status, or ``open`` when the period has no row yet."""
    found = (
        PeriodStatus.objects.filter(org_id=org_id, product=product, period_key=period_key)
        .values_list("status", flat=True)
        .first()
    )
    return found or "open"


def period_phase(org_id: str, period_key: str, product: str = PRODUCT) -> PeriodPhase:
    """``future`` before it starts, ``open`` once it has, ``locked`` from closing on."""
    if period_status(org_id, period_key, product) in LOCKED:
        return "locked"
    return "open" if period_key <= current_period_key(org_id) else "future"
