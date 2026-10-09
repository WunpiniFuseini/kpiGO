"""The page access matrix (PRD AD-1, App Flow §1).

Pages are what the navigation shows. A role's access to each is ``none``,
``view`` or ``edit``; nav items render only where it is ``view`` or ``edit``,
and only for licensed modules, so a user with one module sees one module.
Permissions on actions remain the enforcement; the matrix decides what a user
is shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Access = Literal["none", "view", "edit"]
ACCESS_RANK: dict[str, int] = {"none": 0, "view": 1, "edit": 2}


@dataclass(frozen=True)
class Page:
    key: str
    label: str
    group: Literal["modules", "administer"]
    # The licensable module the page belongs to; "platform" pages ship with every licence.
    module: str


PAGES: tuple[Page, ...] = (
    Page("scorecards", "Scorecards", "modules", "scorecards"),
    Page("agent_performance", "Agent Performance", "modules", "agent_performance"),
    Page("campaign", "Campaign Manager", "modules", "campaign"),
    Page("executive", "Executive", "modules", "executive"),
    Page("my_inputs", "My inputs", "modules", "platform"),
    Page("input_compliance", "Input compliance", "modules", "platform"),
    Page("admin.users", "Users & access", "administer", "platform"),
    Page("admin.metrics", "Metric registry", "administer", "platform"),
    Page("admin.targets", "Targets", "administer", "scorecards"),
    Page("admin.scorecard_setup", "Scorecard setup", "administer", "scorecards"),
    Page("admin.calendar", "Business calendar", "administer", "platform"),
    Page("admin.product_lines", "Product lines", "administer", "agent_performance"),
    Page("admin.data_integration", "Data integration", "administer", "platform"),
    Page("admin.imports", "Import data", "administer", "platform"),
    Page("admin.widgets", "Widgets", "administer", "executive"),
    Page("admin.integrations", "Integrations", "administer", "platform"),
    Page("admin.approvals", "Approvals", "administer", "platform"),
    Page("admin.health", "Health", "administer", "platform"),
    Page("admin.audit", "Audit log", "administer", "platform"),
)
PAGE_KEYS: tuple[str, ...] = tuple(p.key for p in PAGES)
PAGE_BY_KEY: dict[str, Page] = {p.key: p for p in PAGES}

_ADMIN_PAGES = {p.key: "edit" for p in PAGES if p.group == "administer"}

# Default matrix for the system roles. Unlisted pages are ``none``.
SYSTEM_PAGE_ACCESS: dict[str, dict[str, Access]] = {
    "admin": {
        **_ADMIN_PAGES,  # type: ignore[dict-item]
        "scorecards": "view",
        "agent_performance": "view",
        "campaign": "view",
        "executive": "view",
        "my_inputs": "edit",
        "input_compliance": "view",
    },
    # An executive may be a stakeholder told about overdue inputs (MI-8).
    "executive": {
        "executive": "view",
        "scorecards": "view",
        "agent_performance": "view",
        "my_inputs": "view",
        "input_compliance": "view",
    },
    # A line manager enters inputs owed by "the line manager of" their people (MI-2).
    # ...and sees how punctually their team enters inputs (MI-12).
    "line_manager": {
        "scorecards": "view",
        "agent_performance": "view",
        "my_inputs": "edit",
        "input_compliance": "view",
    },
    "agent_supervisor": {"agent_performance": "view"},
    "campaign_manager": {"campaign": "edit", "agent_performance": "view"},
    "metric_owner": {
        "scorecards": "view",
        "admin.metrics": "edit",
        "admin.targets": "edit",
        "admin.scorecard_setup": "edit",
    },
    "data_steward": {
        "admin.data_integration": "edit",
        "admin.product_lines": "edit",
        "admin.metrics": "view",
        "admin.calendar": "edit",
        "admin.health": "view",
    },
    "contributor": {"my_inputs": "edit"},
    # Agent Performance is open by default: the comparison is the point (AP-7).
    "staff": {"scorecards": "view", "agent_performance": "view"},
}


def merge(*matrices: dict[str, str]) -> dict[str, str]:
    """The widest access any of the matrices gives each page."""
    merged: dict[str, str] = {}
    for matrix in matrices:
        for key, access in matrix.items():
            if ACCESS_RANK.get(access, 0) > ACCESS_RANK.get(merged.get(key, "none"), 0):
                merged[key] = access
    return merged
