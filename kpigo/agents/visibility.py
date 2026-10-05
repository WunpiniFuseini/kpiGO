"""Who a viewer sees in Agent Performance (PRD AP-7; Scope §7.3).

The module is open by default: every agent sees every agent. An Admin can
restrict a role, or the agents of a profile, to a scope relative to the viewer:
their hierarchy subtree, their own region or branch, or only themselves. Rules a
viewer matches add up, so the widest wins; ``all`` lifts the others. A viewer
always sees their own figures. A system caller with no account (a job, the
CLI) has no roles and no profile, so no rule applies to it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from kpigo.access.identity import app_user_for, role_codes
from kpigo.action import ActionContext
from kpigo.action.context import AllSubjects, SubjectScope
from kpigo.agents.daily import Agent, in_force
from kpigo.agents.models import VISIBILITY_SCOPES, AgentVisibilityRule
from kpigo.hierarchy.models import Assignment

# Narrowest first, for describing a viewer's scope.
ORDER = {s: i for i, s in enumerate(reversed(VISIBILITY_SCOPES))}


@dataclass(frozen=True)
class Visibility:
    # Empty: open (no rule applies, or one says ``all``).
    scopes: frozenset[str] = frozenset()
    # Which rules restricted the viewer, as ("role", "agent_supervisor").
    because: tuple[tuple[str, str], ...] = ()
    subject_id: str | None = None
    region_code: str | None = None
    branch_code: str | None = None
    subtree: SubjectScope = field(default_factory=AllSubjects)

    @property
    def open(self) -> bool:
        return not self.scopes

    def sees(self, agent: Agent) -> bool:
        if self.open or agent.subject_id == self.subject_id:
            return True
        for scope in self.scopes:
            if scope == "subtree" and self.subtree.contains(agent.subject_id):
                return True
            if scope == "region" and self.region_code and agent.region_code == self.region_code:
                return True
            if scope == "branch" and self.branch_code and agent.branch_code == self.branch_code:
                return True
        return False

    def filter(self, agents: Iterable[Agent]) -> list[Agent]:
        return [a for a in agents if self.sees(a)]

    def ordered(self) -> list[str]:
        return sorted(self.scopes, key=lambda s: ORDER[s])


def visibility_for(ctx: ActionContext, product: str, day: date) -> Visibility:
    account = app_user_for(ctx.user, ctx.org_id)
    if account is None:
        return Visibility()
    roles = role_codes(account)
    mine: Sequence[Assignment] = []
    if account.subject_id is not None:
        mine = list(
            Assignment.objects.filter(org_id=ctx.org_id, subject_id=account.subject_id)
            .filter(in_force(day))
            .order_by("effective_from")
        )
    profiles = {a.profile_code for a in mine}
    rules = [
        r
        for r in AgentVisibilityRule.objects.filter(org_id=ctx.org_id, product=product)
        if (r.applies_to == "role" and r.applies_code in roles)
        or (r.applies_to == "profile" and r.applies_code in profiles)
    ]
    if not rules or any(r.scope == "all" for r in rules):
        return Visibility()
    here = mine[0] if mine else None
    return Visibility(
        scopes=frozenset(r.scope for r in rules),
        because=tuple(sorted((r.applies_to, r.applies_code) for r in rules)),
        subject_id=str(account.subject_id) if account.subject_id else None,
        region_code=here.region_code if here else None,
        branch_code=here.branch_code if here else None,
        subtree=ctx.visible_subjects,
    )
