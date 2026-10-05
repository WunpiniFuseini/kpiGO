"""Connection registry (PRD AD-7, TDD §5.2): read-only sources, encrypted credentials.

A connection is where and as whom kpiGo reads; a feed names the object it
reads. The password is encrypted with Fernet before it is stored, is never
returned by any action, and is masked in the audit log.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Annotated, Literal

from django.utils import timezone
from pydantic import BaseModel, Field, SecretStr, StringConstraints, field_validator

from kpigo.action import ActionContext, NotFound, action
from kpigo.ingestion import crypto, sources
from kpigo.ingestion import validator as v
from kpigo.ingestion.actions.feeds import Template
from kpigo.ingestion.models import Connection, CredentialSecret
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import Code

Driver = Literal["postgres", "sqlserver", "oracle", "mysql"]
Host = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9.\-:\[\]]+$", max_length=253)
]
DatabaseName = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_.\-$]+$", max_length=128)
]
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Port = Annotated[int, Field(ge=1, le=65535)]
Timeout = Annotated[int, Field(ge=1, le=3600)]
RowCap = Annotated[int, Field(ge=1, le=100_000_000)]

_CONFLICTS = {"connection_name_unique": "A connection with this name already exists."}


class ConnectionOut(BaseModel):
    connection_id: uuid.UUID
    name: str
    driver: str
    host: str
    port: int
    database: str
    username: str
    has_password: bool
    statement_timeout_seconds: int | None
    row_cap: int | None
    status: str

    @classmethod
    def of(cls, c: Connection) -> ConnectionOut:
        return cls(
            connection_id=c.connection_id,
            name=c.name,
            driver=c.driver,
            host=c.host,
            port=c.port,
            database=c.database,
            username=c.username,
            has_password=c.secret_ref is not None,
            statement_timeout_seconds=c.options.get("statement_timeout_seconds"),
            row_cap=c.options.get("row_cap"),
            status=c.status,
        )


def _get(org_id: str, name: str, *, lock: bool = False) -> Connection:
    rows = Connection.objects.filter(org_id=org_id, name=name)
    if lock:
        rows = rows.select_for_update()
    found = rows.first()
    if found is None:
        raise NotFound(f"No connection named '{name}'.")
    return found


def _store_secret(ctx: ActionContext, password: SecretStr, existing: uuid.UUID | None) -> uuid.UUID:
    token = crypto.encrypt(password.get_secret_value())
    if existing is not None:
        updated = CredentialSecret.objects.filter(org_id=ctx.org_id, secret_id=existing).update(
            ciphertext=token, updated_by=ctx.user_id, updated_at=timezone.now()
        )
        if updated:
            return existing
    secret = CredentialSecret.objects.create(
        org_id=ctx.org_id, ciphertext=token, created_by=ctx.user_id, updated_by=ctx.user_id
    )
    return secret.secret_id


class ConnectionCreateIn(BaseModel):
    name: Code
    driver: Driver
    host: Host
    port: Port | None = None
    database: DatabaseName
    username: Username
    password: SecretStr = Field(min_length=1, max_length=1024)
    statement_timeout_seconds: Timeout | None = None
    row_cap: RowCap | None = None


@action(
    name="connection.create",
    summary="Register a read-only source database. The password is stored encrypted.",
    schema=ConnectionCreateIn,
    output=ConnectionOut,
    permission="connection.manage",
    read_only=False,
    audit="connection.created",
    example={
        "name": "dw",
        "driver": "postgres",
        "host": "dw.bank.local",
        "database": "warehouse",
        "username": "kpigo_ro",
        "password": "example-only",
    },
)
def create(params: ConnectionCreateIn, ctx: ActionContext) -> ConnectionOut:
    options = {
        k: getattr(params, k)
        for k in ("statement_timeout_seconds", "row_cap")
        if getattr(params, k) is not None
    }
    with conflicts(_CONFLICTS):
        connection = Connection.objects.create(
            org_id=ctx.org_id,
            name=params.name,
            driver=params.driver,
            host=params.host,
            port=params.port or v.DEFAULT_PORTS[params.driver],
            database=params.database,
            username=params.username,
            options=options,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    connection.secret_ref = _store_secret(ctx, params.password, None)
    connection.save(update_fields=["secret_ref"])
    return ConnectionOut.of(connection)


class ConnectionUpdateIn(BaseModel):
    name: Code
    host: Host | None = None
    port: Port | None = None
    database: DatabaseName | None = None
    username: Username | None = None
    # Set to rotate the stored password.
    password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    statement_timeout_seconds: Timeout | None = None
    row_cap: RowCap | None = None
    status: Literal["active", "disabled"] | None = None


@action(
    name="connection.update",
    summary="Change a connection's address, account, limits or status, or rotate its password.",
    schema=ConnectionUpdateIn,
    output=ConnectionOut,
    permission="connection.manage",
    read_only=False,
    audit="connection.updated",
    example={"name": "dw", "statement_timeout_seconds": 120},
)
def update(params: ConnectionUpdateIn, ctx: ActionContext) -> ConnectionOut:
    connection = _get(ctx.org_id, params.name, lock=True)
    for field in ("host", "port", "database", "username", "status"):
        value = getattr(params, field)
        if value is not None:
            setattr(connection, field, value)
    options = dict(connection.options)
    for field in ("statement_timeout_seconds", "row_cap"):
        if field in params.model_fields_set:
            value = getattr(params, field)
            if value is None:
                options.pop(field, None)
            else:
                options[field] = value
    connection.options = options
    if params.password is not None:
        connection.secret_ref = _store_secret(ctx, params.password, connection.secret_ref)
    connection.updated_by = ctx.user_id
    connection.updated_at = timezone.now()
    connection.save()
    return ConnectionOut.of(connection)


class ConnectionListIn(BaseModel):
    pass


class ConnectionListOut(BaseModel):
    connections: list[ConnectionOut]


@action(
    name="connection.list",
    summary="Registered source connections. Passwords are never returned.",
    schema=ConnectionListIn,
    output=ConnectionListOut,
    permission="connection.view",
    read_only=True,
    example={},
)
def list_connections(params: ConnectionListIn, ctx: ActionContext) -> ConnectionListOut:
    rows = Connection.objects.filter(org_id=ctx.org_id).order_by("name")
    return ConnectionListOut(connections=[ConnectionOut.of(c) for c in rows])


class ConnectionTestIn(BaseModel):
    name: Code
    # Optionally check that a registered object can be read and has a template's columns.
    source_object: Annotated[str, StringConstraints(max_length=400)] | None = None
    template: Template | None = None

    @field_validator("source_object")
    @classmethod
    def _object(cls, value: str | None) -> str | None:
        if value is not None:
            v.parse_object_name(value)
        return value


class ConnectionTestOut(BaseModel):
    name: str
    ok: bool
    message: str
    columns: list[str] = []
    missing_columns: list[str] = []


@action(
    name="connection.test",
    summary="Connect with the stored credentials and, optionally, describe a source object.",
    schema=ConnectionTestIn,
    output=ConnectionTestOut,
    permission="connection.manage",
    read_only=True,
    example={"name": "dw", "source_object": "kpi.v_actual_monthly", "template": "actual_monthly"},
)
def test_connection(params: ConnectionTestIn, ctx: ActionContext) -> ConnectionTestOut:
    connection = _get(ctx.org_id, params.name)
    try:
        conn = v.open_source(sources.spec_for(connection))
    except v.SourceError as exc:
        return ConnectionTestOut(name=params.name, ok=False, message=str(exc))
    try:
        if params.source_object is None:
            return ConnectionTestOut(name=params.name, ok=True, message="Connected.")
        try:
            columns = v.describe_source(conn, connection.driver, params.source_object)
        except Exception as exc:
            return ConnectionTestOut(
                name=params.name,
                ok=False,
                message=f"Connected, but '{params.source_object}' could not be read "
                f"({type(exc).__name__}).",
            )
        lowered = {c.lower() for c in columns}
        missing: list[str] = []
        if params.template is not None:
            template = v.TEMPLATES[params.template]
            missing = [c.name for c in template.columns if c.required and c.name not in lowered]
        return ConnectionTestOut(
            name=params.name,
            ok=not missing,
            message="Readable." if not missing else "Readable, but required columns are missing.",
            columns=columns,
            missing_columns=missing,
        )
    finally:
        with contextlib.suppress(Exception):
            conn.close()
