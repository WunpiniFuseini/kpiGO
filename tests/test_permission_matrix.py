"""The permission matrix, generated from the action registry (TDD §14).

Every registered action × every system role. An action shipped without a
declared, granted permission fails here, so a new action cannot ship untested.
"""

import pytest
from django.contrib.auth.models import AnonymousUser

from kpigo.action import ActionContext, NotAuthenticated, PermissionDenied, invoke, registry
from kpigo.action.definition import ActionDefinition
from kpigo.action.identity import build_context
from kpigo.action.pipeline import authorise
from kpigo.action.roles import SYSTEM_ROLES, all_granted_permissions

ACTIONS: list[ActionDefinition] = list(registry)
ORG = "00000000-0000-0000-0000-000000000001"


def ctx_for_role(role: str) -> ActionContext:
    return ActionContext(
        caller="http", user=None, org_id=ORG, permissions=SYSTEM_ROLES[role].permissions
    )


def test_registry_is_not_empty() -> None:
    assert ACTIONS, "autodiscovery found no actions"


@pytest.mark.parametrize("definition", ACTIONS, ids=lambda d: d.name)
def test_every_action_permission_is_granted_to_some_role(definition: ActionDefinition) -> None:
    assert definition.permission in all_granted_permissions(), (
        f"'{definition.name}' declares '{definition.permission}', which no system role grants. "
        "Add it to kpigo/action/roles.py."
    )


def test_no_role_grants_a_permission_no_action_uses() -> None:
    used = {d.permission for d in ACTIONS}
    stale = all_granted_permissions() - used
    assert not stale, f"Roles grant permissions no registered action declares: {sorted(stale)}"


@pytest.mark.parametrize("definition", ACTIONS, ids=lambda d: d.name)
def test_every_action_declares_a_valid_example(definition: ActionDefinition) -> None:
    assert definition.example is not None, f"'{definition.name}' must declare an example payload."
    definition.schema.model_validate(definition.example)


@pytest.mark.parametrize("role", sorted(SYSTEM_ROLES))
@pytest.mark.parametrize("definition", ACTIONS, ids=lambda d: d.name)
def test_matrix(definition: ActionDefinition, role: str) -> None:
    ctx = ctx_for_role(role)
    expected = definition.permission in SYSTEM_ROLES[role].permissions
    if expected:
        authorise(definition, ctx)
    else:
        with pytest.raises(PermissionDenied):
            authorise(definition, ctx)


DENIED = [
    pytest.param(d, role, id=f"{d.name}-{role}")
    for d in ACTIONS
    for role in sorted(SYSTEM_ROLES)
    if d.permission not in SYSTEM_ROLES[role].permissions
]


@pytest.mark.django_db
@pytest.mark.parametrize(("definition", "role"), DENIED)
def test_denied_roles_are_refused_through_the_full_pipeline(
    definition: ActionDefinition, role: str
) -> None:
    with pytest.raises(PermissionDenied):
        invoke(definition, definition.example or {}, ctx_for_role(role))


@pytest.mark.django_db
@pytest.mark.parametrize("definition", ACTIONS, ids=lambda d: d.name)
def test_no_roles_means_no_access(definition: ActionDefinition) -> None:
    ctx = ActionContext(caller="http", user=None, org_id=ORG, permissions=frozenset())
    with pytest.raises(PermissionDenied):
        invoke(definition, definition.example or {}, ctx)


def test_anonymous_cannot_build_a_context() -> None:
    with pytest.raises(NotAuthenticated):
        build_context(AnonymousUser(), caller="http")
    with pytest.raises(NotAuthenticated):
        build_context(None, caller="cli")
