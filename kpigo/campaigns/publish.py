"""Publishing a campaign result as a registry metric (Scope §9.5, PRD CM-19).

A client may expose one of a campaign's results as a metric registered like any
other, bound to ``scorecards``, ``agent_sales`` or ``executive``. Each result
kind fixes the registry definition it must carry (an attributed-revenue figure is
summed currency; a conversion rate is an averaged percentage), so a client picks
the result, the name and the products, not the direction or unit. The definition
here is the single source for both the action and its tests.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    label: str
    direction: str
    aggregation: str
    unit: str
    is_percentage: bool
    # Where the figure lives: per subject (an RM's revenue) or org-wide (a rate).
    target_scope: str


# How each publishable result maps to a registry definition (Metric §2 fields).
METRIC_SPECS: dict[str, MetricSpec] = {
    "attributed_value": MetricSpec(
        "Attributed campaign value", "higher_is_better", "sum", "currency", False, "subject"
    ),
    "incremental_value": MetricSpec(
        "Incremental campaign value", "higher_is_better", "sum", "currency", False, "subject"
    ),
    "conversions": MetricSpec(
        "Campaign conversions", "higher_is_better", "sum", "count", False, "subject"
    ),
    "conversion_rate": MetricSpec(
        "Campaign conversion rate", "higher_is_better", "average", "percent", True, "profile"
    ),
    "winbacks_confirmed": MetricSpec(
        "Confirmed win-backs", "higher_is_better", "sum", "count", False, "subject"
    ),
}
