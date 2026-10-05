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
    SessionBridge,
    SubjectScope,
)
from kpigo.action.definition import ActionDefinition, action
from kpigo.action.errors import (
    ActionError,
    AuthenticationFailed,
    Conflict,
    InvalidInput,
    LicenceRestricted,
    MaintenanceMode,
    NotAuthenticated,
    NotFound,
    OutOfScope,
    PermissionDenied,
    RegistryError,
    SessionExpired,
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
    "AuthenticationFailed",
    "Caller",
    "Conflict",
    "DataScopeGrant",
    "ExplicitSubjects",
    "InvalidInput",
    "LicenceRestricted",
    "MaintenanceMode",
    "NoSubjects",
    "NotAuthenticated",
    "NotFound",
    "OutOfScope",
    "PermissionDenied",
    "Proposal",
    "Registry",
    "RegistryError",
    "SessionBridge",
    "SessionExpired",
    "SubjectScope",
    "UnknownAction",
    "action",
    "invoke",
    "registry",
]
