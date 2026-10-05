"""Performance-cycle arithmetic (Scope §6.2, §6.9).

``months_elapsed`` and ``quarters_elapsed`` count against the client's cycle,
not the calendar year: on an April–March cycle June is month 3 of quarter 1 and
July is month 4 of quarter 2. Quarters accrue on entry, as months do.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db.models import Q

from kpigo.periods.models import CycleBinding, PerformanceCycle
from kpigo.platform.vocab import month_bounds


@dataclass(frozen=True)
class Cycle:
    start_month: int
    end_month: int
    cycle_id: str | None = None
    name: str = "January to December"

    @property
    def months(self) -> int:
        return (self.end_month - self.start_month) % 12 + 1

    def month_no(self, period_key: str) -> int:
        """1-based position of the period's month in the cycle (``months_elapsed``)."""
        month = int(period_key[4:])
        return (month - self.start_month) % 12 + 1

    def quarter_no(self, period_key: str) -> int:
        return (self.month_no(period_key) - 1) // 3 + 1

    def first_period(self, period_key: str) -> str:
        """The first period of the cycle year containing ``period_key``."""
        year, month = int(period_key[:4]), int(period_key[4:])
        if month < self.start_month:
            year -= 1
        return f"{year:04d}{self.start_month:02d}"

    def periods(self, period_key: str) -> list[str]:
        """Every period of the cycle year containing ``period_key``, in order."""
        first = self.first_period(period_key)
        return [shift(first, i) for i in range(self.months)]


DEFAULT_CYCLE = Cycle(start_month=1, end_month=12)


def shift(period_key: str, months: int) -> str:
    index = int(period_key[:4]) * 12 + int(period_key[4:]) - 1 + months
    return f"{index // 12:04d}{index % 12 + 1:02d}"


def period_range(first: str, last: str) -> list[str]:
    out: list[str] = []
    current = first
    while current <= last:
        out.append(current)
        current = shift(current, 1)
    return out


def last_day(period_key: str) -> date:
    _, following = month_bounds(period_key)
    return date.fromordinal(following.toordinal() - 1)


def of(row: PerformanceCycle) -> Cycle:
    return Cycle(
        start_month=row.start_month,
        end_month=row.end_month,
        cycle_id=str(row.cycle_id),
        name=row.name,
    )


def product_cycle(org_id: str, product: str, period_key: str) -> Cycle:
    """The cycle a product follows in a period (on its last day). Default Jan–Dec."""
    day = last_day(period_key)
    binding = (
        CycleBinding.objects.filter(org_id=org_id, product=product, effective_from__lte=day)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=day))
        .select_related("cycle")
        .first()
    )
    return of(binding.cycle) if binding is not None else DEFAULT_CYCLE


def cycle_for(org_id: str, product: str, period_key: str, assignment_cycle_id: str | None) -> Cycle:
    """A subject's cycle: their assignment's, else the product's (Scope §6.9)."""
    if assignment_cycle_id is not None:
        row = PerformanceCycle.objects.filter(cycle_id=assignment_cycle_id).first()
        if row is not None:
            return of(row)
    return product_cycle(org_id, product, period_key)
