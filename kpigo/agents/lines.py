"""The product-line registry on a day (Scope §8.5, App Flow §4.2–4.3).

A line is in the matrix on a day when it is ``active`` or ``retired`` and the day
falls in ``[effective_from, effective_to)``: a retired line still renders for the
periods it was sold in. Which group it sits in comes from ``product_line_group``
in force that day, so a move never regroups history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db.models import Q

from kpigo.hierarchy.models import ProductGroup, ProductLine, ProductLineGroup

# Past this many lines, the expanded matrix is for drilling; suggest grouping.
GROUPING_SUGGESTED_ABOVE = 8


def in_force(day: date) -> Q:
    return Q(effective_from__lte=day) & (Q(effective_to__isnull=True) | Q(effective_to__gt=day))


def shows_in(module: str, product: str | None) -> bool:
    """``agent_performance`` lines show in both modules; others in their own."""
    return product is None or module in ("agent_performance", product)


@dataclass(frozen=True)
class Line:
    code: str
    display_name: str
    sort_order: int
    status: str
    module: str
    group_code: str
    group_name: str
    group_sort: int


def lines_on(org_id: str, day: date, product: str | None = None) -> list[Line]:
    """The lines in the matrix on ``day``, in group then line order."""
    return lines_between(org_id, day, day, product)


def lines_between(org_id: str, first: date, last: date, product: str | None = None) -> list[Line]:
    """The lines in force on any day of ``[first, last]``, grouped as on their last such day.

    A line retired mid-month still has a column in that month's matrix: the
    agents sold it for part of the month (Scope §8.5).
    """
    overlaps = Q(effective_from__lte=last) & (
        Q(effective_to__isnull=True) | Q(effective_to__gt=first)
    )
    rows = (
        ProductLine.objects.filter(org_id=org_id, status__in=("active", "retired"))
        .filter(overlaps)
        .order_by("sort_order", "code")
    )
    groupings: dict[str, ProductGroup] = {}
    for g in (
        ProductLineGroup.objects.filter(line__org_id=org_id)
        .filter(overlaps)
        .select_related("group")
        .order_by("effective_from")
    ):
        groupings[str(g.line_id)] = g.group  # the latest overlapping grouping wins
    out: list[Line] = []
    for line in rows:
        group = groupings.get(str(line.line_id))
        if group is None or not shows_in(line.module, product):
            continue
        out.append(
            Line(
                code=line.code,
                display_name=line.display_name,
                sort_order=line.sort_order,
                status=line.status,
                module=line.module,
                group_code=group.code,
                group_name=group.display_name,
                group_sort=group.sort_order,
            )
        )
    return sorted(out, key=lambda x: (x.group_sort, x.group_name, x.sort_order, x.code))


def default_group(org_id: str, module: str) -> ProductGroup | None:
    """The group a line joins when none is named: the only active one, if there is one."""
    groups = list(ProductGroup.objects.filter(org_id=org_id, status="active")[:2])
    fitting = [g for g in groups if g.module in ("agent_performance", module)]
    return fitting[0] if len(groups) == 1 and fitting else None
