"""Users, roles, page access and data scope (Schema §1).

The Django user stays the session principal; ``app_user`` is the kpiGo account
on top of it: org, provider, lifecycle and the link to a measured subject. The
password, for local accounts only, is the Django user's Argon2id hash.

System roles are defined in code (``kpigo/action/roles.py``) so an upgrade can
grant a new action's permission to them. The ``role`` table holds the roles an
Admin clones from them; a clone is a snapshot of its source's permissions and
pages that the Admin then edits.
"""

import uuid

from django.conf import settings
from django.db import models

from kpigo.hierarchy.models import Subject
from kpigo.platform.db import CITextField, Stamped, Tracked, one_of, valid_range

AUTH_PROVIDERS = ("local", "ldap", "oidc", "saml")
USER_STATUSES = ("invited", "active", "disabled")
PAGE_ACCESS = ("none", "view", "edit")
# Modules whose data is scoped by explicit grant (Schema §1). Scorecards scope
# through the visibility closure; Agent Performance is open by default.
GRANT_MODULES = ("executive", "campaign")
FLOW_PROVIDERS = ("oidc", "saml")
DIRECTORY_SOURCES = ("ldap", "entra", "file")
IMPORT_STATUSES = ("previewed", "applied", "discarded")


class AppUser(Tracked):
    user_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    auth_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="app_user",
        db_column="auth_user_id",
    )
    email = CITextField()
    display_name = models.TextField()
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, null=True, related_name="app_users"
    )
    auth_provider = models.TextField()
    # The identity provider's stable id (OIDC ``sub``, SAML NameID, LDAP DN).
    external_id = models.TextField(null=True)
    status = models.TextField(db_default="invited")
    last_login_at = models.DateTimeField(null=True)
    failed_logins = models.IntegerField(db_default=0)
    locked_until = models.DateTimeField(null=True)
    invite_token_hash = models.TextField(null=True)
    invite_expires_at = models.DateTimeField(null=True)
    disabled_at = models.DateTimeField(null=True)
    disabled_reason = models.TextField(db_default="")

    class Meta:
        db_table = "app_user"
        constraints = [
            models.UniqueConstraint(fields=["org_id", "email"], name="app_user_email_unique"),
            models.UniqueConstraint(
                fields=["org_id", "auth_provider", "external_id"],
                name="app_user_external_id_unique",
            ),
            models.UniqueConstraint(
                fields=["org_id", "subject"],
                condition=models.Q(subject__isnull=False),
                name="app_user_subject_unique",
            ),
            one_of("auth_provider", AUTH_PROVIDERS, "app_user_provider_valid"),
            one_of("status", USER_STATUSES, "app_user_status_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.status})"


class Role(Tracked):
    """A custom role, cloned from a system role or another custom one."""

    role_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    code = models.TextField()
    name = models.TextField()
    description = models.TextField(db_default="")
    cloned_from = models.TextField(null=True)

    class Meta:
        db_table = "role"
        constraints = [
            models.UniqueConstraint(fields=["org_id", "code"], name="role_code_unique"),
        ]

    def __str__(self) -> str:
        return str(self.code)


class RolePermission(Stamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permissions")
    permission = models.TextField()

    class Meta:
        db_table = "role_permission"
        constraints = [
            models.UniqueConstraint(fields=["role", "permission"], name="role_permission_unique")
        ]

    def __str__(self) -> str:
        return f"{self.role_id}:{self.permission}"


class RolePageAccess(Tracked):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="pages")
    page_key = models.TextField()
    access = models.TextField()

    class Meta:
        db_table = "role_page_access"
        constraints = [
            models.UniqueConstraint(fields=["role", "page_key"], name="role_page_access_unique"),
            one_of("access", PAGE_ACCESS, "role_page_access_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.role_id}:{self.page_key}={self.access}"


class UserRole(Stamped):
    """A role held by a user: a system role code or a custom role's code."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    app_user = models.ForeignKey(AppUser, on_delete=models.CASCADE, related_name="roles")
    role_code = models.TextField()

    class Meta:
        db_table = "user_role"
        constraints = [
            models.UniqueConstraint(fields=["app_user", "role_code"], name="user_role_unique")
        ]

    def __str__(self) -> str:
        return f"{self.app_user_id}:{self.role_code}"


class DataScopeGrant(Tracked):
    """A dimensional grant for Executive or Campaign data. No grant means no data.

    Held by a role (every user with it) or by one user. ``member_code = '*'``
    grants every member of the dimension.
    """

    grant_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    role_code = models.TextField(null=True)
    app_user = models.ForeignKey(
        AppUser, on_delete=models.PROTECT, null=True, related_name="scope_grants"
    )
    module = models.TextField()
    dimension_type = models.TextField()
    member_code = models.TextField()
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)

    class Meta:
        db_table = "data_scope_grant"
        constraints = [
            one_of("module", GRANT_MODULES, "data_scope_grant_module_valid"),
            models.CheckConstraint(
                condition=(
                    models.Q(role_code__isnull=False, app_user__isnull=True)
                    | models.Q(role_code__isnull=True, app_user__isnull=False)
                ),
                name="data_scope_grant_one_holder",
            ),
            valid_range("data_scope_grant_range_valid"),
        ]
        indexes = [models.Index(fields=["org_id", "module"], name="data_scope_grant_module")]

    def __str__(self) -> str:
        holder = self.role_code or self.app_user_id
        return f"{holder}: {self.module} {self.dimension_type}={self.member_code}"


class AuthFlowState(models.Model):
    """One in-flight SSO sign-in: single use, short-lived."""

    state = models.TextField(primary_key=True)
    org_id = models.UUIDField()
    provider = models.TextField()
    nonce = models.TextField(db_default="")
    code_verifier = models.TextField(db_default="")
    next_path = models.TextField(db_default="/")
    created_at = models.DateTimeField(db_default=models.functions.Now())
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "auth_flow_state"
        constraints = [one_of("provider", FLOW_PROVIDERS, "auth_flow_state_provider_valid")]

    def __str__(self) -> str:
        return f"{self.provider}:{self.state[:8]}"


class DirectoryImport(Stamped):
    """A directory read and its diff against the hierarchy, held for review (PRD AD-9)."""

    import_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    source = models.TextField()
    status = models.TextField(db_default="previewed")
    full_roster = models.BooleanField(db_default=True)
    records = models.JSONField()
    diff = models.JSONField()
    summary = models.JSONField()
    applied_at = models.DateTimeField(null=True)
    applied_by = models.BigIntegerField(null=True)

    class Meta:
        db_table = "directory_import"
        constraints = [
            one_of("source", DIRECTORY_SOURCES, "directory_import_source_valid"),
            one_of("status", IMPORT_STATUSES, "directory_import_status_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.source} import {self.import_id} ({self.status})"
