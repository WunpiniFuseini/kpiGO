"""Errors raised by the action pipeline.

Every adapter maps these to its own surface (HTTP status, CLI exit code, job
failure) from the same ``code`` and ``http_status``, so a denial looks the same
whichever way the action was called.
"""

from __future__ import annotations

from typing import Any


class ActionError(Exception):
    code = "action_error"
    http_status = 400

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


class InvalidInput(ActionError):
    code = "invalid_input"
    http_status = 422


class NotAuthenticated(ActionError):
    code = "not_authenticated"
    http_status = 401


class PermissionDenied(ActionError):
    code = "permission_denied"
    http_status = 403


class OutOfScope(ActionError):
    """The caller holds the permission but the target data is outside their scope."""

    code = "out_of_scope"
    http_status = 403


class NotFound(ActionError):
    code = "not_found"
    http_status = 404


class Conflict(ActionError):
    code = "conflict"
    http_status = 409


class MaintenanceMode(ActionError):
    """Mutating actions are refused while the install is in maintenance mode."""

    code = "maintenance_mode"
    http_status = 503


class UnknownAction(ActionError):
    code = "unknown_action"
    http_status = 404


class RegistryError(Exception):
    """A programming error in how an action was declared or registered."""
