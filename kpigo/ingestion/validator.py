#!/usr/bin/env python3
"""kpiGo feed validator: the feed contract and its six validation gates.

This one file is both the code kpiGo's ingestion pipeline runs and the
standalone validator a client's data-engineering team runs against their own
database or files before kpiGo ever connects (PRD IN-10). It imports nothing
from kpiGo or Django and needs only the Python 3.12 standard library; a
database driver is imported only when reading from that database, and
``openpyxl`` only when reading an ``.xlsx`` file.

Usage (copy this file anywhere and run it)::

    python3 validator.py --template actual_monthly --file actuals.csv
    python3 validator.py --template actual_monthly --file actuals.csv \\
        --contract contract.json --report rejections.csv
    KPIGO_SOURCE_PASSWORD=... python3 validator.py --template actual_monthly \\
        --driver postgres --host db1 --port 5432 --database dw --user kpigo_ro \\
        --object kpi.v_actual_monthly --contract contract.json

``contract.json`` comes from kpiGo (``feed.contract.export``). Without it the
schema, grain and domain gates run; with it the referential, volume, period
status and missing-row checks run too, exactly as kpiGo will run them.

Exit status: 0 the load would pass, 1 it would be rejected, 2 usage error.

The gates (TDD §5.3). Any error quarantines the whole load: all or nothing.

=============  =============================================  ==========
Gate           Check                                          Reports
=============  =============================================  ==========
schema         required columns present, values coercible     load
referential    metric active and bound, subject known,         rows
               member known (unknown members become
               ``available``), no missing rows
grain          no duplicates at the template's grain           load
domain         period key well formed, values in bounds,       rows
               no future activity
volume         row count within the expected range             load
period_status  no writes to a closed period unless restating   load
=============  =============================================  ==========

Absent is not zero: a subject expected to report a metric whose row is missing
is a warning on a feed's first live load and an error after it.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import getpass
import hashlib
import importlib
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

CONTRACT_VERSION = 1

Kind = Literal["code", "text", "decimal", "date", "period", "currency"]
Severity = Literal["error", "warning"]
Gate = Literal["schema", "referential", "grain", "domain", "volume", "period_status"]
GATES: tuple[Gate, ...] = ("schema", "referential", "grain", "domain", "volume", "period_status")
# What a failure of each gate rejects (TDD §5.3). Any error quarantines the load.
GATE_REJECTS: dict[Gate, str] = {
    "schema": "load",
    "referential": "rows",
    "grain": "load",
    "domain": "rows",
    "volume": "load",
    "period_status": "load",
}

# numeric(18,4): fourteen integer digits.
VALUE_BOUND = Decimal("1e14")
VALUE_PLACES = 4
MAX_TEXT = 200
PERIOD_KEY_RE = re.compile(r"^[0-9]{4}(0[1-9]|1[0-2])$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[T ]00:00(?::00(?:\.0+)?)?)?$")
# Units for which a negative actual is never meaningful.
NON_NEGATIVE_UNITS = frozenset({"count", "days", "hours"})
# Period statuses that refuse writes unless the load is flagged as a restatement.
LOCKED_STATUSES = frozenset({"closing", "closed", "restating"})


# ── the contract: landing templates ──────────────────────────────────────────


@dataclass(frozen=True)
class Column:
    name: str
    kind: Kind
    required: bool = True  # the column must be present
    nullable: bool = False  # a present column may hold an empty value


@dataclass(frozen=True)
class Template:
    name: str
    columns: tuple[Column, ...]
    grain: tuple[str, ...]
    # A metric loaded through this template must be bound to one of these products.
    products: frozenset[str] = frozenset()
    # Whether kpiGo conforms this template yet; the others land in later releases.
    loadable: bool = False

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)


def _c(name: str, kind: Kind, required: bool = True, nullable: bool = False) -> Column:
    return Column(name, kind, required, nullable)


_CURRENCY = _c("currency_code", "currency", required=False, nullable=True)

# Backend Schema §7. Feeds reference metrics by metric_code, never by uuid.
TEMPLATES: dict[str, Template] = {
    t.name: t
    for t in (
        Template(
            "actual_monthly",
            (
                _c("metric_code", "code"),
                _c("subject_ref", "code"),
                _c("period_key", "period"),
                _c("actual_value", "decimal"),
                _CURRENCY,
            ),
            grain=("metric_code", "subject_ref", "period_key"),
            products=frozenset({"scorecards", "executive"}),
            loadable=True,
        ),
        Template(
            "actual_daily",
            (
                _c("metric_code", "code"),
                _c("subject_ref", "code"),
                _c("activity_date", "date"),
                _c("product_line_code", "code", required=False, nullable=True),
                _c("actual_value", "decimal"),
                _CURRENCY,
            ),
            grain=("metric_code", "subject_ref", "activity_date", "product_line_code"),
            products=frozenset({"agent_sales", "agent_service"}),
            loadable=True,
        ),
        Template(
            "actual_dimensional",
            (
                _c("metric_code", "code"),
                _c("dimension_type", "code"),
                _c("member_code", "code"),
                _c("period_key", "period"),
                _c("actual_value", "decimal"),
                _CURRENCY,
            ),
            grain=("metric_code", "dimension_type", "member_code", "period_key"),
            products=frozenset({"executive"}),
            loadable=True,
        ),
        Template(
            "subject",
            (
                _c("staff_no", "code"),
                _c("full_name", "text"),
                _c("email", "text"),
                _c("portfolio_code", "code", nullable=True),
                _c("staff_ref", "code", nullable=True),
                _c("status", "code"),
            ),
            grain=("staff_no",),
        ),
        Template(
            "assignment",
            (
                _c("staff_no", "code"),
                _c("role_code", "code"),
                _c("profile_code", "code"),
                _c("manager_staff_no", "code", nullable=True),
                _c("relationship_type", "code", nullable=True),
                _c("branch_code", "code", nullable=True),
                _c("region_code", "code", nullable=True),
                _c("segment_code", "code", nullable=True),
                _c("effective_from", "date"),
                _c("effective_to", "date", nullable=True),
            ),
            grain=("staff_no", "effective_from"),
        ),
        Template(
            "dimension",
            (
                _c("dimension_type", "code"),
                _c("member_code", "code"),
                _c("member_name", "text"),
                _c("parent_code", "code", nullable=True),
            ),
            grain=("dimension_type", "member_code"),
        ),
        Template(
            "target",
            (
                _c("metric_code", "code"),
                _c("scope_type", "code"),
                _c("scope_code", "code"),
                _c("period_key", "period"),
                _c("series_type", "code"),
                _c("target_value", "decimal"),
                _c("target_type", "code"),
                _c("weight", "decimal", nullable=True),
                _c("cap", "decimal", nullable=True),
                _CURRENCY,
            ),
            grain=("metric_code", "scope_type", "scope_code", "period_key", "series_type"),
        ),
        Template(
            "widget_data",
            (
                _c("widget_key", "code"),
                _c("metric_code", "code"),
                _c("period_key", "period"),
                _c("dimension_type", "code"),
                _c("member_code", "code"),
                _c("series_type", "code"),
                _c("value", "decimal"),
            ),
            grain=(
                "widget_key",
                "metric_code",
                "period_key",
                "dimension_type",
                "member_code",
                "series_type",
            ),
        ),
        Template(
            "campaign_outcome",
            (
                _c("customer_ref", "code"),
                _c("metric_code", "code"),
                _c("campaign_code", "code", required=False, nullable=True),
                _c("activity_date", "date"),
                _c("activity_value", "decimal"),
                _CURRENCY,
                _c("source_ref", "text", nullable=True),
            ),
            grain=("customer_ref", "metric_code", "activity_date", "source_ref"),
        ),
    )
}
LOADABLE_TEMPLATES: tuple[str, ...] = tuple(n for n, t in TEMPLATES.items() if t.loadable)


# ── raw tables and reading sources ───────────────────────────────────────────


@dataclass
class RawTable:
    """Rows exactly as read, every value as text (or None). Landing keeps them untouched."""

    header: list[str]
    rows: list[tuple[str | None, ...]]
    source_name: str = ""


class SourceError(Exception):
    """The source could not be read: a connection, permission, format or size problem."""


class RowCapExceeded(SourceError):
    pass


def to_text(value: Any) -> str | None:
    """A source value as landing text. Nothing is rounded or reformatted beyond type."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime):
        if value.time() == datetime.min.time():
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip() or None


def normalise_header(names: Iterable[Any]) -> list[str]:
    return [str(n if n is not None else "").strip().lower() for n in names]


def read_csv(data: bytes, *, row_cap: int, source_name: str = "") -> RawTable:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceError(f"The file is not UTF-8 text ({exc.reason}).") from None
    sample = text[:8192]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;|\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = normalise_header(next(reader))
    except StopIteration:
        raise SourceError("The file is empty: expected a header row.") from None
    rows: list[tuple[str | None, ...]] = []
    for record in reader:
        if not any(cell.strip() for cell in record):
            continue
        if len(rows) >= row_cap:
            raise RowCapExceeded(f"More than {row_cap} rows; the row cap stops the read.")
        padded = list(record) + [""] * (len(header) - len(record))
        rows.append(tuple(to_text(v) for v in padded[: len(header)]))
    return RawTable(header=header, rows=rows, source_name=source_name)


def read_xlsx(data: bytes, *, row_cap: int, source_name: str = "") -> RawTable:
    try:
        openpyxl = importlib.import_module("openpyxl")
    except ImportError:
        raise SourceError("Reading .xlsx needs the 'openpyxl' package.") from None
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise SourceError(f"The workbook could not be opened ({type(exc).__name__}).") from None
    try:
        sheet = book.worksheets[0]
        values = sheet.iter_rows(values_only=True)
        try:
            header = normalise_header(next(values))
        except StopIteration:
            raise SourceError("The first sheet is empty: expected a header row.") from None
        rows: list[tuple[str | None, ...]] = []
        for record in values:
            cells = [to_text(v) for v in record]
            if not any(c is not None for c in cells):
                continue
            if len(rows) >= row_cap:
                raise RowCapExceeded(f"More than {row_cap} rows; the row cap stops the read.")
            cells += [None] * (len(header) - len(cells))
            rows.append(tuple(cells[: len(header)]))
    finally:
        book.close()
    return RawTable(header=header, rows=rows, source_name=source_name)


def read_file(name: str, data: bytes, *, row_cap: int) -> RawTable:
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        return read_csv(data, row_cap=row_cap, source_name=name)
    if suffix == ".xlsx":
        return read_xlsx(data, row_cap=row_cap, source_name=name)
    raise SourceError(f"'{name}' is not a .csv or .xlsx file.")


# ── reading a database object (never client SQL) ─────────────────────────────

# kpiGo registers an object name and reads it; it never stores or runs client
# SQL (PRD IN-4). A name is one to three plain identifiers joined by dots, so
# there is nothing in it that SQL could be smuggled through.
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
DRIVERS = ("postgres", "sqlserver", "oracle", "mysql")
Driver = Literal["postgres", "sqlserver", "oracle", "mysql"]
DEFAULT_PORTS: dict[str, int] = {"postgres": 5432, "sqlserver": 1433, "oracle": 1521, "mysql": 3306}


def parse_object_name(name: str) -> tuple[str, ...]:
    """Split ``schema.view`` into identifiers, refusing anything that is not one."""
    parts = tuple(name.split("."))
    if not 1 <= len(parts) <= 3 or not all(IDENTIFIER_RE.match(p) for p in parts):
        raise ValueError(
            f"'{name}' is not an object name. Register a table or view as up to three "
            "identifiers (letters, digits, underscores) joined by dots, e.g. 'kpi.v_actuals'."
        )
    return parts


def quote_identifier(driver: str, identifier: str) -> str:
    if not IDENTIFIER_RE.match(identifier):
        raise ValueError(f"'{identifier}' is not a plain identifier.")
    if driver == "mysql":
        return f"`{identifier}`"
    if driver == "sqlserver":
        return f"[{identifier}]"
    return f'"{identifier}"'


def describe_sql(driver: str, object_name: str) -> str:
    target = ".".join(quote_identifier(driver, p) for p in parse_object_name(object_name))
    return f"SELECT * FROM {target} WHERE 1 = 0"


def select_sql(driver: str, object_name: str, columns: Sequence[str]) -> str:
    if not columns:
        raise ValueError("Select at least one column.")
    target = ".".join(quote_identifier(driver, p) for p in parse_object_name(object_name))
    cols = ", ".join(quote_identifier(driver, c) for c in columns)
    return f"SELECT {cols} FROM {target}"


@dataclass(frozen=True)
class SourceSpec:
    driver: str
    host: str
    port: int
    database: str
    username: str
    password: str
    statement_timeout_seconds: int = 300
    connect_timeout_seconds: int = 15

    def __repr__(self) -> str:  # never print the password
        return (
            f"SourceSpec(driver={self.driver!r}, host={self.host!r}, port={self.port!r}, "
            f"database={self.database!r}, username={self.username!r})"
        )


def _driver_module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError:
        raise SourceError(f"The '{name}' driver package is not installed.") from None


def open_source(spec: SourceSpec) -> Any:
    """A read-only DB-API connection with the statement timeout applied.

    The credentials should be read-only too; these settings are a second line.
    """
    timeout_ms = int(spec.statement_timeout_seconds * 1000)
    try:
        if spec.driver == "postgres":
            psycopg = _driver_module("psycopg")
            return psycopg.connect(
                host=spec.host,
                port=spec.port,
                dbname=spec.database,
                user=spec.username,
                password=spec.password,
                connect_timeout=spec.connect_timeout_seconds,
                options=f"-c statement_timeout={timeout_ms} -c default_transaction_read_only=on",
                application_name="kpigo-ingestion",
            )
        if spec.driver == "mysql":
            pymysql = _driver_module("pymysql")
            conn = pymysql.connect(
                host=spec.host,
                port=spec.port,
                user=spec.username,
                password=spec.password,
                database=spec.database,
                connect_timeout=spec.connect_timeout_seconds,
                read_timeout=spec.statement_timeout_seconds,
            )
            with conn.cursor() as cur:
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}")
                cur.execute("SET SESSION TRANSACTION READ ONLY")
            return conn
        if spec.driver == "sqlserver":
            pymssql = _driver_module("pymssql")
            return pymssql.connect(
                server=spec.host,
                port=str(spec.port),
                user=spec.username,
                password=spec.password,
                database=spec.database,
                login_timeout=spec.connect_timeout_seconds,
                timeout=spec.statement_timeout_seconds,
                appname="kpigo-ingestion",
            )
        if spec.driver == "oracle":
            oracledb = _driver_module("oracledb")
            conn = oracledb.connect(
                user=spec.username,
                password=spec.password,
                dsn=f"{spec.host}:{spec.port}/{spec.database}",
                tcp_connect_timeout=spec.connect_timeout_seconds,
            )
            conn.call_timeout = timeout_ms
            return conn
    except SourceError:
        raise
    except Exception as exc:
        # Driver messages can echo connection details; keep the class and first line.
        first = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else ""
        raise SourceError(f"Could not connect ({type(exc).__name__}): {first}") from None
    raise SourceError(f"Unknown driver '{spec.driver}'. Expected one of {', '.join(DRIVERS)}.")


def describe_source(conn: Any, driver: str, object_name: str) -> list[str]:
    """The object's column names as the database spells them."""
    cur = conn.cursor()
    try:
        cur.execute(describe_sql(driver, object_name))
        return [str(d[0]) for d in (cur.description or [])]
    finally:
        cur.close()


def read_source(
    spec: SourceSpec, object_name: str, template: Template, *, row_cap: int
) -> RawTable:
    """Read the template's columns from a registered object, at most ``row_cap`` rows.

    The only SQL sent is a column list and the object name, both built from
    validated identifiers. More rows than the cap fails the read rather than
    silently truncating it.
    """
    parse_object_name(object_name)
    conn = open_source(spec)
    try:
        try:
            actual = describe_source(conn, spec.driver, object_name)
        except Exception as exc:
            raise SourceError(
                f"Could not read '{object_name}' ({type(exc).__name__}). Check the name and "
                "that the account may select from it."
            ) from None
        by_lower = {a.lower(): a for a in actual}
        wanted = [c for c in template.column_names if c in by_lower]
        if not wanted:
            return RawTable(header=normalise_header(actual), rows=[], source_name=object_name)
        # On Postgres a named cursor streams from the server instead of buffering.
        cur = conn.cursor(name="kpigo_pull") if spec.driver == "postgres" else conn.cursor()
        rows: list[tuple[str | None, ...]] = []
        try:
            cur.execute(select_sql(spec.driver, object_name, [by_lower[c] for c in wanted]))
            while True:
                batch = cur.fetchmany(5000)
                if not batch:
                    break
                for record in batch:
                    if len(rows) >= row_cap:
                        raise RowCapExceeded(
                            f"'{object_name}' returned more than {row_cap} rows; the row cap "
                            "stops the read."
                        )
                    rows.append(tuple(to_text(v) for v in record))
        except SourceError:
            raise
        except Exception as exc:
            first = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else ""
            raise SourceError(
                f"Reading '{object_name}' failed ({type(exc).__name__}): {first}"
            ) from None
        finally:
            cur.close()
        return RawTable(header=wanted, rows=rows, source_name=object_name)
    finally:
        with contextlib.suppress(Exception):
            conn.close()


# ── reference data (what the referential and other gates check against) ─────


@dataclass(frozen=True)
class MetricVersion:
    metric_id: str
    metric_code: str
    status: str
    collection_method: str
    unit: str
    effective_from: date
    effective_to: date | None
    products: frozenset[str]

    def in_force(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day < self.effective_to)


@dataclass(frozen=True)
class AssignmentRef:
    assignment_id: str
    profile_code: str
    effective_from: date
    effective_to: date | None

    def in_force(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day < self.effective_to)

    def overlaps(self, first: date, following: date) -> bool:
        return self.effective_from < following and (
            self.effective_to is None or self.effective_to > first
        )


@dataclass(frozen=True)
class SubjectRef:
    subject_id: str
    staff_no: str
    assignments: tuple[AssignmentRef, ...]


@dataclass(frozen=True)
class ProfileMetric:
    metric_id: str
    profile_code: str
    effective_from: date
    effective_to: date | None

    def in_force(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day < self.effective_to)


@dataclass
class Reference:
    """Everything the gates check a load against, as of ``today``."""

    today: date
    metrics: dict[str, list[MetricVersion]] = field(default_factory=dict)
    subjects: dict[str, SubjectRef] = field(default_factory=dict)
    members: dict[str, set[str]] = field(default_factory=dict)
    product_lines: set[str] = field(default_factory=set)
    # (product, period_key) -> status; a period with no row is open.
    period_status: dict[tuple[str, str], str] = field(default_factory=dict)
    profile_metrics: list[ProfileMetric] = field(default_factory=list)
    # Absent is not zero: warn on a feed's first live load, enforce after it.
    enforce_missing_rows: bool = False
    trailing_rows: float | None = None
    expected_row_min: int | None = None
    expected_row_max: int | None = None
    volume_warn_pct: int = 25
    volume_reject_pct: int = 75
    restatement: bool = False

    @property
    def current_period(self) -> str:
        return f"{self.today.year:04d}{self.today.month:02d}"

    def metric_version(self, code: str, day: date) -> MetricVersion | None:
        for version in self.metrics.get(code, ()):
            if version.in_force(day):
                return version
        return None


def month_bounds(period_key: str) -> tuple[date, date]:
    first = date(int(period_key[:4]), int(period_key[4:]), 1)
    return first, (first + timedelta(days=32)).replace(day=1)


def month_end(period_key: str) -> date:
    return month_bounds(period_key)[1] - timedelta(days=1)


def assignment_for_month(subject: SubjectRef, period_key: str) -> AssignmentRef | None:
    """The assignment in force on the month's last day, else the latest one in the month.

    Same rule as the visibility closure: the last day of the month decides.
    """
    first, following = month_bounds(period_key)
    last = following - timedelta(days=1)
    for a in subject.assignments:
        if a.in_force(last):
            return a
    overlapping = [a for a in subject.assignments if a.overlaps(first, following)]
    return max(overlapping, key=lambda a: a.effective_from) if overlapping else None


def assignment_on(subject: SubjectRef, day: date) -> AssignmentRef | None:
    for a in subject.assignments:
        if a.in_force(day):
            return a
    return None


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def reference_to_json(ref: Reference) -> dict[str, Any]:
    return {
        "today": ref.today.isoformat(),
        "metrics": {
            code: [
                {
                    "metric_id": v.metric_id,
                    "status": v.status,
                    "collection_method": v.collection_method,
                    "unit": v.unit,
                    "effective_from": v.effective_from.isoformat(),
                    "effective_to": v.effective_to.isoformat() if v.effective_to else None,
                    "products": sorted(v.products),
                }
                for v in versions
            ]
            for code, versions in sorted(ref.metrics.items())
        },
        "subjects": {
            staff_no: {
                "subject_id": s.subject_id,
                "assignments": [
                    {
                        "assignment_id": a.assignment_id,
                        "profile_code": a.profile_code,
                        "effective_from": a.effective_from.isoformat(),
                        "effective_to": a.effective_to.isoformat() if a.effective_to else None,
                    }
                    for a in s.assignments
                ],
            }
            for staff_no, s in sorted(ref.subjects.items())
        },
        "members": {k: sorted(v) for k, v in sorted(ref.members.items())},
        "product_lines": sorted(ref.product_lines),
        "period_status": [
            {"product": p, "period_key": k, "status": s}
            for (p, k), s in sorted(ref.period_status.items())
        ],
        "profile_metrics": [
            {
                "metric_id": pm.metric_id,
                "profile_code": pm.profile_code,
                "effective_from": pm.effective_from.isoformat(),
                "effective_to": pm.effective_to.isoformat() if pm.effective_to else None,
            }
            for pm in ref.profile_metrics
        ],
        "enforce_missing_rows": ref.enforce_missing_rows,
        "trailing_rows": ref.trailing_rows,
        "expected_row_min": ref.expected_row_min,
        "expected_row_max": ref.expected_row_max,
        "volume_warn_pct": ref.volume_warn_pct,
        "volume_reject_pct": ref.volume_reject_pct,
    }


def reference_from_json(data: dict[str, Any], *, today: date | None = None) -> Reference:
    return Reference(
        today=today or date.fromisoformat(data["today"]),
        metrics={
            code: [
                MetricVersion(
                    metric_id=v["metric_id"],
                    metric_code=code,
                    status=v["status"],
                    collection_method=v["collection_method"],
                    unit=v["unit"],
                    effective_from=date.fromisoformat(v["effective_from"]),
                    effective_to=_d(v.get("effective_to")),
                    products=frozenset(v["products"]),
                )
                for v in versions
            ]
            for code, versions in data.get("metrics", {}).items()
        },
        subjects={
            staff_no: SubjectRef(
                subject_id=s["subject_id"],
                staff_no=staff_no,
                assignments=tuple(
                    AssignmentRef(
                        assignment_id=a["assignment_id"],
                        profile_code=a["profile_code"],
                        effective_from=date.fromisoformat(a["effective_from"]),
                        effective_to=_d(a.get("effective_to")),
                    )
                    for a in s["assignments"]
                ),
            )
            for staff_no, s in data.get("subjects", {}).items()
        },
        members={k: set(v) for k, v in data.get("members", {}).items()},
        product_lines=set(data.get("product_lines", [])),
        period_status={
            (p["product"], p["period_key"]): p["status"] for p in data.get("period_status", [])
        },
        profile_metrics=[
            ProfileMetric(
                metric_id=pm["metric_id"],
                profile_code=pm["profile_code"],
                effective_from=date.fromisoformat(pm["effective_from"]),
                effective_to=_d(pm.get("effective_to")),
            )
            for pm in data.get("profile_metrics", [])
        ],
        enforce_missing_rows=bool(data.get("enforce_missing_rows", False)),
        trailing_rows=data.get("trailing_rows"),
        expected_row_min=data.get("expected_row_min"),
        expected_row_max=data.get("expected_row_max"),
        volume_warn_pct=int(data.get("volume_warn_pct", 25)),
        volume_reject_pct=int(data.get("volume_reject_pct", 75)),
    )


# ── validation ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Issue:
    """One finding. ``row_no`` is the data row (1 = first row under the header)."""

    gate: Gate
    rule: str
    severity: Severity
    message: str
    row_no: int | None = None
    column: str | None = None
    value: str | None = None


@dataclass
class Row:
    row_no: int
    raw: dict[str, str | None]
    values: dict[str, Any]
    # Ids the referential gate resolved, for conform: metric_id, subject_id, ...
    resolved: dict[str, Any] = field(default_factory=dict)
    # Codes seen in the load but not yet registered; conform registers them as available.
    unmapped: dict[str, str] = field(default_factory=dict)


@dataclass
class Validation:
    template: Template
    rows_read: int
    rows: list[Row]
    issues: list[Issue]
    skipped: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    @property
    def rejected_row_nos(self) -> set[int]:
        return {i.row_no for i in self.errors if i.row_no is not None}

    @property
    def accepted_rows(self) -> list[Row]:
        """Rows with no error. Meaningful only when the load passes: all or nothing."""
        bad = self.rejected_row_nos
        return [r for r in self.rows if r.row_no not in bad]

    def gate_summary(self) -> dict[str, dict[str, int]]:
        summary: dict[str, dict[str, int]] = {g: {"error": 0, "warning": 0} for g in GATES}
        for i in self.issues:
            summary[i.gate][i.severity] += 1
        return summary

    @property
    def periods(self) -> list[str]:
        found: set[str] = set()
        for r in self.rows:
            if r.values.get("period_key"):
                found.add(str(r.values["period_key"]))
            elif "activity_date" in r.values and isinstance(r.values["activity_date"], date):
                d = r.values["activity_date"]
                found.add(f"{d.year:04d}{d.month:02d}")
        return sorted(found)


def parse_date(text: str) -> date | None:
    m = _DATE_RE.match(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_decimal(text: str) -> Decimal | None:
    # Strict: "1,5" is not guessed at. A misread separator is a quietly wrong figure.
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _schema(template: Template, table: RawTable) -> tuple[list[Row], list[Issue], bool]:
    issues: list[Issue] = []
    counts = Counter(table.header)
    for name, n in counts.items():
        if n > 1 and name in template.column_names:
            issues.append(
                Issue(
                    "schema",
                    "duplicate_column",
                    "error",
                    f"Column '{name}' appears {n} times.",
                    column=name,
                )
            )
    for col in template.columns:
        if col.required and col.name not in table.header:
            issues.append(
                Issue(
                    "schema",
                    "missing_column",
                    "error",
                    f"Required column '{col.name}' is missing. The {template.name} template "
                    f"needs: {', '.join(c.name for c in template.columns if c.required)}.",
                    column=col.name,
                )
            )
    if issues:
        return [], issues, False
    position = {name: i for i, name in enumerate(table.header) if name in template.column_names}
    rows: list[Row] = []
    for n, record in enumerate(table.rows, start=1):
        raw = {name: record[i] if i < len(record) else None for name, i in position.items()}
        values: dict[str, Any] = {}
        ok = True
        for col in template.columns:
            text = raw.get(col.name)
            if text is None:
                if col.nullable or (not col.required and col.name not in position):
                    values[col.name] = None
                    continue
                issues.append(
                    Issue(
                        "schema",
                        "required_value",
                        "error",
                        f"'{col.name}' is empty."
                        + (
                            " Absent is not zero: send an explicit 0 when the value is zero."
                            if col.kind == "decimal"
                            else ""
                        ),
                        row_no=n,
                        column=col.name,
                    )
                )
                ok = False
                continue
            if len(text) > MAX_TEXT:
                issues.append(
                    Issue(
                        "schema",
                        "too_long",
                        "error",
                        f"'{col.name}' is longer than {MAX_TEXT} characters.",
                        row_no=n,
                        column=col.name,
                        value=text[:MAX_TEXT],
                    )
                )
                ok = False
                continue
            if col.kind == "decimal":
                number = parse_decimal(text)
                if number is None:
                    issues.append(
                        Issue(
                            "schema",
                            "not_a_number",
                            "error",
                            f"'{col.name}' is not a number.",
                            row_no=n,
                            column=col.name,
                            value=text,
                        )
                    )
                    ok = False
                    continue
                values[col.name] = number
            elif col.kind == "date":
                day = parse_date(text)
                if day is None:
                    issues.append(
                        Issue(
                            "schema",
                            "not_a_date",
                            "error",
                            f"'{col.name}' is not a date (expected YYYY-MM-DD).",
                            row_no=n,
                            column=col.name,
                            value=text,
                        )
                    )
                    ok = False
                    continue
                values[col.name] = day
            else:
                values[col.name] = text
        if ok:
            rows.append(Row(row_no=n, raw=raw, values=values))
    return rows, issues, True


def _grain(template: Template, rows: list[Row]) -> list[Issue]:
    issues: list[Issue] = []
    first_seen: dict[tuple[Any, ...], int] = {}
    for row in rows:
        key = tuple(row.values.get(c) for c in template.grain)
        if key in first_seen:
            issues.append(
                Issue(
                    "grain",
                    "duplicate_at_grain",
                    "error",
                    f"Duplicate of row {first_seen[key]} at the grain "
                    f"({', '.join(template.grain)}). Aggregate in the view.",
                    row_no=row.row_no,
                    column=template.grain[0],
                    value=" | ".join("" if v is None else str(v) for v in key),
                )
            )
        else:
            first_seen[key] = row.row_no
    return issues


def _domain(rows: list[Row], today: date) -> list[Issue]:
    issues: list[Issue] = []
    current = f"{today.year:04d}{today.month:02d}"
    for row in rows:
        v = row.values
        period = v.get("period_key")
        if period is not None:
            if not PERIOD_KEY_RE.match(str(period)):
                issues.append(
                    Issue(
                        "domain",
                        "bad_period_key",
                        "error",
                        "period_key must be YYYYMM, zero-padded (e.g. 202610).",
                        row_no=row.row_no,
                        column="period_key",
                        value=str(period),
                    )
                )
            elif period > current:
                issues.append(
                    Issue(
                        "domain",
                        "future_period",
                        "error",
                        f"period_key {period} is in the future. Bound the view to the period.",
                        row_no=row.row_no,
                        column="period_key",
                        value=str(period),
                    )
                )
        day = v.get("activity_date")
        if isinstance(day, date) and day > today:
            issues.append(
                Issue(
                    "domain",
                    "future_date",
                    "error",
                    f"activity_date {day.isoformat()} is in the future.",
                    row_no=row.row_no,
                    column="activity_date",
                    value=day.isoformat(),
                )
            )
        for name in ("actual_value", "target_value", "value", "activity_value"):
            number = v.get(name)
            if not isinstance(number, Decimal):
                continue
            if abs(number) >= VALUE_BOUND:
                issues.append(
                    Issue(
                        "domain",
                        "out_of_bounds",
                        "error",
                        f"'{name}' is outside the storable range (|value| < 10^14).",
                        row_no=row.row_no,
                        column=name,
                        value=row.raw.get(name),
                    )
                )
            else:
                exponent = number.as_tuple().exponent
                if isinstance(exponent, int) and -exponent > VALUE_PLACES:
                    issues.append(
                        Issue(
                            "domain",
                            "rounded",
                            "warning",
                            f"'{name}' has more than {VALUE_PLACES} decimal places and will "
                            "be rounded.",
                            row_no=row.row_no,
                            column=name,
                            value=row.raw.get(name),
                        )
                    )
        currency = v.get("currency_code")
        if currency is not None and not CURRENCY_RE.match(str(currency)):
            issues.append(
                Issue(
                    "domain",
                    "bad_currency",
                    "error",
                    "currency_code must be a three-letter ISO code in capitals.",
                    row_no=row.row_no,
                    column="currency_code",
                    value=str(currency),
                )
            )
    return issues


def _row_day(row: Row) -> date | None:
    day = row.values.get("activity_date")
    if isinstance(day, date):
        return day
    period = row.values.get("period_key")
    if period is not None and PERIOD_KEY_RE.match(str(period)):
        return month_end(str(period))
    return None


def _referential(template: Template, rows: list[Row], ref: Reference) -> list[Issue]:
    issues: list[Issue] = []
    for row in rows:
        v = row.values
        day = _row_day(row)
        if day is None:
            continue  # the domain gate already rejected the period
        code = str(v["metric_code"])
        if code not in ref.metrics:
            issues.append(
                Issue(
                    "referential",
                    "unknown_metric",
                    "error",
                    f"No metric with code '{code}'. Align the view with the registry.",
                    row_no=row.row_no,
                    column="metric_code",
                    value=code,
                )
            )
            continue
        version = ref.metric_version(code, day)
        if version is None:
            issues.append(
                Issue(
                    "referential",
                    "metric_not_in_force",
                    "error",
                    f"Metric '{code}' has no definition in force on {day.isoformat()}.",
                    row_no=row.row_no,
                    column="metric_code",
                    value=code,
                )
            )
            continue
        if version.status != "active":
            issues.append(
                Issue(
                    "referential",
                    "metric_not_active",
                    "error",
                    f"Metric '{code}' is {version.status}, not active.",
                    row_no=row.row_no,
                    column="metric_code",
                    value=code,
                )
            )
            continue
        if version.collection_method != "feed":
            issues.append(
                Issue(
                    "referential",
                    "metric_not_feed",
                    "error",
                    f"Metric '{code}' is collected by manual input, not by a feed.",
                    row_no=row.row_no,
                    column="metric_code",
                    value=code,
                )
            )
            continue
        if not version.products & template.products:
            issues.append(
                Issue(
                    "referential",
                    "metric_not_bound",
                    "error",
                    f"Metric '{code}' is not bound to {' or '.join(sorted(template.products))}.",
                    row_no=row.row_no,
                    column="metric_code",
                    value=code,
                )
            )
            continue
        row.resolved["metric_id"] = version.metric_id
        row.resolved["unit"] = version.unit
        row.resolved["products"] = sorted(version.products & template.products)

        if "subject_ref" in v:
            staff_no = str(v["subject_ref"])
            subject = ref.subjects.get(staff_no)
            if subject is None:
                issues.append(
                    Issue(
                        "referential",
                        "unknown_subject",
                        "error",
                        f"No subject with staff number '{staff_no}'.",
                        row_no=row.row_no,
                        column="subject_ref",
                        value=staff_no,
                    )
                )
                continue
            if "activity_date" in v:
                assignment = assignment_on(subject, day)
            else:
                assignment = assignment_for_month(subject, str(v["period_key"]))
            if assignment is None:
                issues.append(
                    Issue(
                        "referential",
                        "no_assignment",
                        "error",
                        f"Subject '{staff_no}' has no assignment in force for this period.",
                        row_no=row.row_no,
                        column="subject_ref",
                        value=staff_no,
                    )
                )
                continue
            row.resolved["subject_id"] = subject.subject_id
            row.resolved["assignment_id"] = assignment.assignment_id

        if "dimension_type" in v:
            dim = str(v["dimension_type"])
            if dim not in ref.members:
                issues.append(
                    Issue(
                        "referential",
                        "unknown_dimension",
                        "error",
                        f"No dimension '{dim}'. Define it before loading against it.",
                        row_no=row.row_no,
                        column="dimension_type",
                        value=dim,
                    )
                )
                continue
            member = str(v["member_code"])
            if member not in ref.members[dim]:
                row.unmapped["member"] = member
                issues.append(
                    Issue(
                        "referential",
                        "unmapped_member",
                        "warning",
                        f"Member '{member}' of '{dim}' is new; it will be registered as "
                        "available for an Admin to activate.",
                        row_no=row.row_no,
                        column="member_code",
                        value=member,
                    )
                )

        line = v.get("product_line_code")
        if line is not None and str(line) not in ref.product_lines:
            row.unmapped["product_line"] = str(line)
            issues.append(
                Issue(
                    "referential",
                    "unmapped_product_line",
                    "warning",
                    f"Product line '{line}' is new; it will be registered as available for "
                    "an Admin to name, group and activate.",
                    row_no=row.row_no,
                    column="product_line_code",
                    value=str(line),
                )
            )

        number = v.get("actual_value")
        if isinstance(number, Decimal) and number < 0 and version.unit in NON_NEGATIVE_UNITS:
            issues.append(
                Issue(
                    "domain",
                    "negative_value",
                    "error",
                    f"Metric '{code}' is measured in {version.unit}; it cannot be negative.",
                    row_no=row.row_no,
                    column="actual_value",
                    value=row.raw.get("actual_value"),
                )
            )
    return issues


def _missing_rows(template: Template, rows: list[Row], ref: Reference) -> list[Issue]:
    """Absent is not zero: every subject expected to report a metric must have a row."""
    if template.name != "actual_monthly":
        return []
    present: dict[tuple[str, str], set[str]] = defaultdict(set)
    codes: dict[str, str] = {}
    for row in rows:
        metric_id = row.resolved.get("metric_id")
        if metric_id is None:
            continue
        period = str(row.values["period_key"])
        present[(metric_id, period)].add(str(row.values["subject_ref"]))
        codes[metric_id] = str(row.values["metric_code"])
    issues: list[Issue] = []
    severity: Severity = "error" if ref.enforce_missing_rows else "warning"
    for (metric_id, period), seen in sorted(present.items()):
        last = month_end(period)
        profiles = {
            pm.profile_code
            for pm in ref.profile_metrics
            if pm.metric_id == metric_id and pm.in_force(last)
        }
        if not profiles:
            continue
        for staff_no, subject in sorted(ref.subjects.items()):
            if staff_no in seen:
                continue
            assignment = assignment_on(subject, last)
            if assignment is None or assignment.profile_code not in profiles:
                continue
            issues.append(
                Issue(
                    "referential",
                    "missing_row",
                    severity,
                    f"No row for subject '{staff_no}', metric '{codes[metric_id]}', period "
                    f"{period}. Absent is not zero: send an explicit 0 if the value is zero."
                    + (
                        ""
                        if ref.enforce_missing_rows
                        else " (Warning on the first load; enforced from the next.)"
                    ),
                    column="subject_ref",
                    value=staff_no,
                )
            )
    return issues


def _period_status(template: Template, rows: list[Row], ref: Reference) -> list[Issue]:
    if ref.restatement:
        return []
    issues: list[Issue] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        day = _row_day(row)
        if day is None:
            continue
        period = f"{day.year:04d}{day.month:02d}"
        for product in row.resolved.get("products", ()):
            status = ref.period_status.get((product, period), "open")
            if status in LOCKED_STATUSES and (product, period) not in seen:
                seen.add((product, period))
                issues.append(
                    Issue(
                        "period_status",
                        "period_closed",
                        "error",
                        f"{product} {period} is {status}. Writing to it needs the load to be "
                        "flagged as a restatement.",
                        row_no=row.row_no,
                        column="period_key" if "period_key" in row.values else "activity_date",
                        value=period,
                    )
                )
    return issues


def _volume(rows_read: int, ref: Reference) -> list[Issue]:
    issues: list[Issue] = []
    if ref.expected_row_min is not None and rows_read < ref.expected_row_min:
        issues.append(
            Issue(
                "volume",
                "below_expected_rows",
                "error",
                f"{rows_read} rows is below the expected minimum of {ref.expected_row_min}. "
                "Re-run the source extract.",
            )
        )
    if ref.expected_row_max is not None and rows_read > ref.expected_row_max:
        issues.append(
            Issue(
                "volume",
                "above_expected_rows",
                "error",
                f"{rows_read} rows is above the expected maximum of {ref.expected_row_max}.",
            )
        )
    if ref.trailing_rows:
        change = abs(rows_read - ref.trailing_rows) / ref.trailing_rows * 100
        if change > ref.volume_reject_pct:
            issues.append(
                Issue(
                    "volume",
                    "volume_out_of_range",
                    "error",
                    f"{rows_read} rows is {change:.0f}% away from the trailing average of "
                    f"{ref.trailing_rows:.0f} (limit {ref.volume_reject_pct}%).",
                )
            )
        elif change > ref.volume_warn_pct:
            issues.append(
                Issue(
                    "volume",
                    "volume_unusual",
                    "warning",
                    f"{rows_read} rows is {change:.0f}% away from the trailing average of "
                    f"{ref.trailing_rows:.0f} (warning above {ref.volume_warn_pct}%).",
                )
            )
    return issues


def validate(
    template: Template, table: RawTable, ref: Reference | None, *, today: date | None = None
) -> Validation:
    """Run every gate and collect every finding, so one report shows them all."""
    rows_read = len(table.rows)
    rows, issues, readable = _schema(template, table)
    skipped: list[str] = []
    if not readable:
        return Validation(template, rows_read, [], issues)
    if rows_read == 0:
        issues.append(Issue("volume", "empty_load", "error", "The load has no rows."))
    issues += _grain(template, rows)
    when = ref.today if ref is not None else (today or date.today())
    domain = _domain(rows, when)
    issues += domain
    if ref is None:
        skipped = ["referential", "volume", "period_status"]
        return Validation(template, rows_read, rows, issues, skipped)
    bad_period = {i.row_no for i in domain if i.rule == "bad_period_key"}
    checkable = [r for r in rows if r.row_no not in bad_period]
    issues += _referential(template, checkable, ref)
    issues += _missing_rows(template, checkable, ref)
    issues += _volume(rows_read, ref)
    issues += _period_status(template, checkable, ref)
    return Validation(template, rows_read, rows, issues, skipped)


# ── content hash, diff and the rejection report ─────────────────────────────


def content_hash(template: Template, table: RawTable) -> str:
    """A hash of what was loaded, independent of column and row order."""
    known = sorted(h for h in set(table.header) if h in template.column_names)
    index = {h: table.header.index(h) for h in known}
    rows = sorted(
        json.dumps([r[index[h]] if index[h] < len(r) else None for h in known]) for r in table.rows
    )
    digest = hashlib.sha256()
    digest.update(json.dumps({"template": template.name, "columns": known}).encode())
    for line in rows:
        digest.update(b"\n")
        digest.update(line.encode())
    return digest.hexdigest()


def grain_key(template: Template, raw: dict[str, str | None]) -> tuple[str | None, ...]:
    return tuple(raw.get(c) for c in template.grain)


def diff(
    template: Template,
    current: Iterable[dict[str, str | None]],
    previous: Iterable[dict[str, str | None]],
    value_column: str = "actual_value",
    sample: int = 20,
) -> dict[str, Any]:
    """Added, removed and changed rows at the grain, against the last load."""
    now = {grain_key(template, r): r.get(value_column) for r in current}
    before = {grain_key(template, r): r.get(value_column) for r in previous}

    def same(a: str | None, b: str | None) -> bool:
        if a is None or b is None:
            return a == b
        x, y = parse_decimal(a), parse_decimal(b)
        return x == y if x is not None and y is not None else a == b

    added = [k for k in now if k not in before]
    removed = [k for k in before if k not in now]
    changed = [k for k in now if k in before and not same(now[k], before[k])]
    return {
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
        "unchanged": len(now) - len(added) - len(changed),
        "sample_changes": [
            {"key": list(k), "before": before[k], "after": now[k]}
            for k in sorted(changed, key=lambda k: tuple("" if p is None else p for p in k))[
                :sample
            ]
        ],
        "sample_removed": [list(k) for k in removed[:sample]],
    }


REPORT_COLUMNS = ("row_no", "column", "gate", "rule", "severity", "value", "message")


def report_csv(issues: Iterable[Issue]) -> str:
    """The downloadable rejection report: row, column and rule (PRD IN-8)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(REPORT_COLUMNS)
    for i in issues:
        writer.writerow(
            [
                "" if i.row_no is None else i.row_no,
                i.column or "",
                i.gate,
                i.rule,
                i.severity,
                _safe_cell(i.value),
                i.message,
            ]
        )
    return out.getvalue()


def _safe_cell(value: str | None) -> str:
    """Keep a spreadsheet from reading a client value as a formula."""
    if not value:
        return ""
    return f"'{value}" if value[0] in "=+-@\t\r" and parse_decimal(value) is None else value


# ── command line ─────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validator.py",
        description="Check a kpiGo feed against its contract before kpiGo loads it.",
    )
    p.add_argument("--template", required=True, choices=sorted(LOADABLE_TEMPLATES))
    src = p.add_argument_group("source (a file, or a database object)")
    src.add_argument("--file", help="a .csv or .xlsx file")
    src.add_argument("--driver", choices=DRIVERS)
    src.add_argument("--host")
    src.add_argument("--port", type=int)
    src.add_argument("--database")
    src.add_argument("--user")
    src.add_argument("--object", help="the registered table or view, e.g. kpi.v_actual_monthly")
    p.add_argument("--contract", help="contract.json exported from kpiGo; enables every gate")
    p.add_argument("--report", help="write the rejection report (CSV) here")
    p.add_argument("--row-cap", type=int, default=1_000_000)
    p.add_argument("--timeout", type=int, default=300, help="statement timeout in seconds")
    p.add_argument("--restatement", action="store_true", help="the load restates a closed period")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    return p


def _load_source(args: argparse.Namespace, template: Template) -> RawTable:
    if args.file:
        path = Path(args.file)
        return read_file(path.name, path.read_bytes(), row_cap=args.row_cap)
    missing = [n for n in ("driver", "host", "database", "user", "object") if not getattr(args, n)]
    if missing:
        raise SourceError("Give --file, or all of --" + ", --".join(missing) + ".")
    password = os.environ.get("KPIGO_SOURCE_PASSWORD") or getpass.getpass("Password: ")
    spec = SourceSpec(
        driver=args.driver,
        host=args.host,
        port=args.port or DEFAULT_PORTS[args.driver],
        database=args.database,
        username=args.user,
        password=password,
        statement_timeout_seconds=args.timeout,
    )
    return read_source(spec, args.object, template, row_cap=args.row_cap)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    template = TEMPLATES[args.template]
    ref: Reference | None = None
    if args.contract:
        data = json.loads(Path(args.contract).read_text())
        if data.get("kpigo_contract") != CONTRACT_VERSION:
            print("contract.json is not a kpiGo contract this validator reads.", file=sys.stderr)
            return 2
        if data.get("template") != template.name:
            print(
                f"contract.json is for '{data.get('template')}', not '{template.name}'.",
                file=sys.stderr,
            )
            return 2
        ref = reference_from_json(data["reference"], today=date.today())
        ref.restatement = args.restatement
    try:
        table = _load_source(args, template)
    except (SourceError, ValueError, OSError) as exc:
        print(f"Could not read the source: {exc}", file=sys.stderr)
        return 2
    result = validate(template, table, ref)
    if args.report:
        Path(args.report).write_text(report_csv(result.issues))
    summary: dict[str, Any] = {
        "template": template.name,
        "source": table.source_name,
        "passed": result.passed,
        "rows_read": result.rows_read,
        "rows_rejected": len(result.rejected_row_nos),
        "errors": len(result.errors),
        "warnings": len(result.warnings),
        "gates": result.gate_summary(),
        "skipped_gates": result.skipped,
        "content_hash": content_hash(template, table),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"{template.name} from {table.source_name or 'source'}: {result.rows_read} rows")
        for gate in GATES:
            counts = summary["gates"][gate]
            state = "skipped" if gate in result.skipped else ("FAIL" if counts["error"] else "ok")
            print(f"  {gate:<14} {state:<8} {counts['error']} errors, {counts['warning']} warnings")
        for issue in result.issues[:20]:
            where = f"row {issue.row_no}" if issue.row_no is not None else "load"
            print(
                f"  [{issue.severity}] {where} {issue.column or ''} {issue.rule}: {issue.message}"
            )
        if len(result.issues) > 20:
            print(f"  … {len(result.issues) - 20} more; use --report for all of them")
        print(
            "PASS: kpiGo would load this."
            if result.passed
            else "FAIL: kpiGo would quarantine this load."
        )
    return 0 if result.passed else 1


def iter_issues(issues: Iterable[Issue]) -> Iterator[dict[str, Any]]:
    for i in issues:
        yield {
            "row_no": i.row_no,
            "column_name": i.column,
            "gate": i.gate,
            "rule": i.rule,
            "severity": i.severity,
            "value": i.value,
            "message": i.message,
        }


if __name__ == "__main__":
    sys.exit(main())
