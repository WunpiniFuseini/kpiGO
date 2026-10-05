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

# The licence and the health page (PRD OP-2, OP-3).
OPERATIONS = ("licence.view", "licence.manage", "system.health.view")

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
            "scorecard.view",
        ),
        _role(
            "executive", "Executive / Regional Head", *EVERYONE, "dimension.view", "scorecard.view"
        ),
        _role("line_manager", "Line Manager", *EVERYONE, "scorecard.view"),
        _role("agent_supervisor", "Agent Supervisor", *EVERYONE),
        _role("campaign_manager", "Campaign Manager", *EVERYONE, "dimension.view"),
        _role(
            "metric_owner",
            "Metric Owner",
            *EVERYONE,
            "metric.manage",
            *SCORECARD_CONFIG,
            "scorecard.view",
        ),
        _role(
            "data_steward",
            "Data Steward",
            *EVERYONE,
            *STEWARD,
            *INGESTION,
            "licence.view",
            "system.health.view",
        ),
        _role("contributor", "Contributor", *EVERYONE),
        _role("staff", "Relationship Manager / Service Officer", *EVERYONE, "scorecard.view"),
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
