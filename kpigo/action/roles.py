"""System roles and the permissions each one grants.

PRD AD-1: fixed system roles, cloneable, with a per-page access matrix. Until the
``role`` tables land (R0 Workstream D) this module is the source of truth, and
the permission-matrix test reads it: every registered action's permission must
be granted to at least one role, and every permission granted here must belong
to a registered action.
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


EVERYONE = ("platform.hello",)

SYSTEM_ROLES: dict[str, RoleSpec] = {
    r.code: r
    for r in (
        _role(
            "admin",
            "Admin",
            *EVERYONE,
            "platform.registry.view",
            "platform.approval.decide",
        ),
        _role("executive", "Executive / Regional Head", *EVERYONE),
        _role("line_manager", "Line Manager", *EVERYONE),
        _role("agent_supervisor", "Agent Supervisor", *EVERYONE),
        _role("campaign_manager", "Campaign Manager", *EVERYONE),
        _role("metric_owner", "Metric Owner", *EVERYONE),
        _role("data_steward", "Data Steward", *EVERYONE),
        _role("contributor", "Contributor", *EVERYONE),
        _role("staff", "Relationship Manager / Service Officer", *EVERYONE),
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
