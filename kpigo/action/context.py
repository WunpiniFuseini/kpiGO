"""ActionContext: who is calling, from where, and with what rights (TDD §3.2)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from kpigo.action.errors import PermissionDenied

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

Caller = Literal["ui", "http", "cli", "job", "agent", "mcp"]
CALLERS: tuple[Caller, ...] = ("ui", "http", "cli", "job", "agent", "mcp")


class SubjectScope(Protocol):
    """The set of subjects a caller may see. Backed by ``visibility_closure`` once it exists."""

    def contains(self, subject_id: str) -> bool: ...


class NoSubjects:
    """Deny-all scope. No grant means no data: absence is never a wildcard."""

    def contains(self, subject_id: str) -> bool:
        return False

    def __repr__(self) -> str:
        return "NoSubjects()"


class AllSubjects:
    def contains(self, subject_id: str) -> bool:
        return True

    def __repr__(self) -> str:
        return "AllSubjects()"


@dataclass(frozen=True)
class ExplicitSubjects:
    subject_ids: frozenset[str]

    @classmethod
    def of(cls, ids: Iterable[str]) -> ExplicitSubjects:
        return cls(frozenset(str(i) for i in ids))

    def contains(self, subject_id: str) -> bool:
        return str(subject_id) in self.subject_ids


@dataclass(frozen=True)
class DataScopeGrant:
    """A dimensional grant for Executive / Campaign data (Schema §1, ``data_scope_grant``)."""

    module: str
    dimension_type: str
    member_code: str


@dataclass(frozen=True)
class ApprovalGrant:
    """Present when an action is being replayed from an approved ``approval_request``."""

    approval_request_id: str
    action_name: str
    approved_by_id: int | None


@dataclass(frozen=True)
class AuditEvent:
    event: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ActionContext:
    caller: Caller
    user: AbstractBaseUser | None
    org_id: str
    permissions: frozenset[str]
    visible_subjects: SubjectScope = field(default_factory=NoSubjects)
    data_scopes: tuple[DataScopeGrant, ...] = ()
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    dry_run: bool = False
    ip_address: str | None = None
    approval: ApprovalGrant | None = None
    # Extra audit events an action records with ctx.audit(); the pipeline writes
    # them in the same transaction as the invocation's own audit row.
    pending_audit: list[AuditEvent] = field(default_factory=list, compare=False, repr=False)

    @property
    def user_id(self) -> int | None:
        if self.user is None:
            return None
        pk = self.user.pk
        return int(pk) if pk is not None else None

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise PermissionDenied(f"Missing permission '{permission}'.")

    def audit(self, event: str, **payload: Any) -> None:
        self.pending_audit.append(AuditEvent(event=event, payload=payload))
