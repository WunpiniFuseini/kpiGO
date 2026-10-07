"""System roles and the permissions each one grants.

PRD AD-1: fixed system roles, cloneable, with a per-page access matrix. System
roles live here, in code, so an upgrade can grant a new action's permission to
them; Admins clone them into the ``role`` table to customise (``kpigo.access``).
The permission-matrix test reads this module: every registered action's
permission must be granted to at least one role, and every permission granted
here must belong to a registered action.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleSpec:
    code: str
    name: str
    permissions: frozenset[str]


def _role(code: str, name: str, *permissions: str) -> RoleSpec:
    return RoleSpec(code=code, name=name, permissions=frozenset(permissions))


# Reference data every role reads: metric definitions, the calendar, and the
# subjects their own visibility closure lets them see (scope narrows the last).
EVERYONE = ("platform.hello", "metric.view", "calendar.view", "subject.view", "auth.session")

# Users, roles, page access, data scope grants, maker-checker policy, directory.
ACCESS = (
    "user.view",
    "user.manage",
    "role.view",
    "role.manage",
    "scope.view",
    "scope.manage",
    "approval.policy.manage",
    "directory.view",
    "directory.manage",
)

# The licence, the health page, backups and the diagnostic bundle (PRD OP-2–OP-5).
OPERATIONS = (
    "licence.view",
    "licence.manage",
    "system.health.view",
    "system.backup.view",
    "system.backup.manage",
    "system.diagnostics.view",
)

# Configuration custodians: hierarchy, dimensions and FX are data-steward work.
STEWARD = (
    "hierarchy.view",
    "hierarchy.manage",
    "dimension.view",
    "dimension.manage",
    "settings.view",
    "fx.manage",
)

# Ingestion: source connections, feeds and their runs (PRD AD-7, IN-*).
INGESTION = (
    "connection.view",
    "connection.manage",
    "feed.view",
    "feed.manage",
    "feed.run",
)

# Scorecards configuration: taxonomy, profiles' metric sets, bands, settings and
# the target workbench (PRD SC-1, SC-4, SC-10, SC-11).
SCORECARD_CONFIG = (
    "scorecard.config.view",
    "scorecard.config.manage",
    "target.view",
    "target.manage",
    "target.publish",
)

# Overrides (PRD SC-5): requested by one person, approved by another.
OVERRIDE_REQUEST = ("override.view", "override.request")

# Reading your own scorecard (PRD SC-14, SC-15): see it, say you have, ask about it.
SCORECARD_READER = ("scorecard.view", "scorecard.acknowledge", "scorecard.query")
# Managing others' (SC-15, SC-16): answer their queries, comment on their month.
SCORECARD_MANAGER = ("scorecard.query.resolve", "scorecard.comment")
# Manual metric input (PRD MI-1–MI-13): set up who enters what; enter it.
INPUT_MANAGE = ("input.manage", "input.submit", "input.followup")
# Being told when someone else's input is overdue (MI-8): line managers and stakeholders.
INPUT_FOLLOWUP = "input.followup"
# Agent Performance (PRD AP-*): open by default, so reading it is broad (AP-7).
AGENT_VIEW = "agent.view"
# Module settings, and daily retention (AP-12).
AGENT_ADMIN = ("agent.config.manage", "agent.retention.manage")
# The product-line registry (Scope §8.5): the handshake, groups, moves, retirement.
# Product lines are a dimension, so stewards hold it with Admins (pages.py).
PRODUCT_LINES = "product_line.manage"
# Campaign Manager (PRD CM-*): reading campaigns in scope, authoring and managing
# them; objectives and attribution settings are configuration an Admin holds.
CAMPAIGN_VIEW = "campaign.view"
CAMPAIGN_AUTHOR = ("campaign.view", "campaign.manage")
CAMPAIGN_CONFIG = "campaign.config.manage"
# Publishing a campaign result into the shared registry as a metric (PRD CM-19);
# weightier than authoring a campaign, so a distinct grant alongside the author's.
CAMPAIGN_PUBLISH = "campaign.metric.publish"
# Executive Dashboard (PRD EX-*): reading it (data scope narrows what each reader
# sees); placing widgets and choosing their types is an Admin's (Scope §13.3).
EXECUTIVE_VIEW = "executive.view"
WIDGET_MANAGE = "widget.manage"
# Hand-entering an independent executive metric's actual (PRD MI-4): the figures
# that exist only at executive level (cost-to-income, NPS, capital ratios).
EXECUTIVE_INPUT = "executive.input"

SYSTEM_ROLES: dict[str, RoleSpec] = {
    r.code: r
    for r in (
        _role(
            "admin",
            "Admin",
            *EVERYONE,
            *STEWARD,
            *INGESTION,
            *ACCESS,
            *OPERATIONS,
            "platform.registry.view",
            "platform.approval.decide",
            "platform.migrations.view",
            "metric.manage",
            "calendar.manage",
            "settings.manage",
            *SCORECARD_CONFIG,
            *SCORECARD_READER,
            *SCORECARD_MANAGER,
            *OVERRIDE_REQUEST,
            "override.approve",
            "period.close",
            *INPUT_MANAGE,
            AGENT_VIEW,
            *AGENT_ADMIN,
            PRODUCT_LINES,
            *CAMPAIGN_AUTHOR,
            CAMPAIGN_CONFIG,
            CAMPAIGN_PUBLISH,
            EXECUTIVE_VIEW,
            WIDGET_MANAGE,
            EXECUTIVE_INPUT,
        ),
        _role(
            "executive",
            "Executive / Regional Head",
            *EVERYONE,
            "dimension.view",
            *SCORECARD_READER,
            INPUT_FOLLOWUP,
            AGENT_VIEW,
            EXECUTIVE_VIEW,
            EXECUTIVE_INPUT,
        ),
        _role(
            "line_manager",
            "Line Manager",
            *EVERYONE,
            *SCORECARD_READER,
            *SCORECARD_MANAGER,
            *OVERRIDE_REQUEST,
            "input.submit",
            INPUT_FOLLOWUP,
            AGENT_VIEW,
        ),
        _role("agent_supervisor", "Agent Supervisor", *EVERYONE, AGENT_VIEW),
        _role(
            "campaign_manager",
            "Campaign Manager",
            *EVERYONE,
            "dimension.view",
            AGENT_VIEW,
            *CAMPAIGN_AUTHOR,
            CAMPAIGN_PUBLISH,
        ),
        _role(
            "metric_owner",
            "Metric Owner",
            *EVERYONE,
            "metric.manage",
            *SCORECARD_CONFIG,
            "scorecard.view",
            *OVERRIDE_REQUEST,
            "override.approve",
        ),
        _role(
            "data_steward",
            "Data Steward",
            *EVERYONE,
            *STEWARD,
            *INGESTION,
            "licence.view",
            "system.health.view",
            "system.backup.view",
            "system.diagnostics.view",
            "agent.retention.manage",
            PRODUCT_LINES,
        ),
        _role("contributor", "Contributor", *EVERYONE, "input.submit"),
        _role(
            "staff",
            "Relationship Manager / Service Officer",
            *EVERYONE,
            *SCORECARD_READER,
            AGENT_VIEW,
        ),
    )
}


def permissions_for_roles(role_codes: list[str] | set[str] | frozenset[str]) -> frozenset[str]:
    granted: set[str] = set()
    for code in role_codes:
        spec = SYSTEM_ROLES.get(code)
        if spec is not None:
            granted |= spec.permissions
    return frozenset(granted)


def all_granted_permissions() -> frozenset[str]:
    return permissions_for_roles(set(SYSTEM_ROLES))
