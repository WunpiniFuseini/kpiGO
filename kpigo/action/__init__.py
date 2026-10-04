"""kpiGo's action layer: the spine every route, job and command is an adapter over.

Governing rule: nothing is a route, a job, a CLI command or an agent tool unless
it is first a registered action.
"""

from kpigo.action.context import (
    ActionContext,
    AllSubjects,
    ApprovalGrant,
    Caller,
    DataScopeGrant,
    ExplicitSubjects,
    NoSubjects,
    SubjectScope,
)
from kpigo.action.definition import ActionDefinition, action
from kpigo.action.errors import (
    ActionError,
    Conflict,
    InvalidInput,
    MaintenanceMode,
    NotAuthenticated,
    NotFound,
    OutOfScope,
    PermissionDenied,
    RegistryError,
    UnknownAction,
)
from kpigo.action.pipeline import Proposal, invoke
from kpigo.action.registry import Registry, registry

__all__ = [
    "ActionContext",
    "ActionDefinition",
    "ActionError",
    "AllSubjects",
    "ApprovalGrant",
    "Caller",
    "Conflict",
    "DataScopeGrant",
    "ExplicitSubjects",
    "InvalidInput",
    "MaintenanceMode",
    "NoSubjects",
    "NotAuthenticated",
    "NotFound",
    "OutOfScope",
    "PermissionDenied",
    "Proposal",
    "Registry",
    "RegistryError",
    "SubjectScope",
    "UnknownAction",
    "action",
    "invoke",
    "registry",
]
