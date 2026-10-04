"""Migration dry run (Schema §15.3; TDD §12): the DDL an upgrade would run and its reach.

Upgrades run on servers kpiGo never sees, so before anything executes the
operator gets the exact SQL, the tables it touches with their estimated row
counts, and a flag on every statement that is not additive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_TABLE = re.compile(
    r"""(?:ALTER\s+TABLE(?:\s+ONLY)?|CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+CONCURRENTLY)?\s+\S+\s+ON|
        UPDATE|DELETE\s+FROM|INSERT\s+INTO|DROP\s+TABLE|TRUNCATE(?:\s+TABLE)?)
        \s+(?:IF\s+EXISTS\s+)?"?([A-Za-z_][A-Za-z0-9_]*)"?""",
    re.IGNORECASE | re.VERBOSE,
)
_DESTRUCTIVE = [
    (re.compile(r"\bDROP\s+TABLE\b", re.I), "drops a table"),
    (re.compile(r"\bDROP\s+COLUMN\b", re.I), "drops a column"),
    (
        re.compile(r"\bALTER\s+COLUMN\s+\S+\s+(?:SET\s+DATA\s+)?TYPE\b", re.I),
        "changes a column type",
    ),
    (re.compile(r"\bSET\s+NOT\s+NULL\b", re.I), "makes a column required"),
    (re.compile(r"\bRENAME\b", re.I), "renames an object"),
    (re.compile(r"\bTRUNCATE\b", re.I), "truncates a table"),
    (re.compile(r"\bDELETE\s+FROM\b", re.I), "deletes rows"),
    (re.compile(r"\bUPDATE\s+\S+\s+SET\b", re.I), "rewrites rows (backfills belong in jobs)"),
]


@dataclass
class StatementReport:
    tables: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def analyse(statements: list[str]) -> StatementReport:
    report = StatementReport()
    for sql in statements:
        if sql.lstrip().startswith("--"):
            continue
        report.tables.update(m.group(1) for m in _TABLE.finditer(sql))
        for pattern, meaning in _DESTRUCTIVE:
            if pattern.search(sql):
                report.warnings.append(f"{meaning}: {sql.strip()[:200]}")
    return report


def estimated_rows(tables: set[str]) -> dict[str, int]:
    """Planner estimates from pg_class; a table not yet created reports 0."""
    if not tables:
        return {}
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname, GREATEST(c.reltuples, 0)::bigint FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') AND c.relname = ANY(%s)",
            [sorted(tables)],
        )
        found = {str(name): int(rows) for name, rows in cur.fetchall()}
    return {t: found.get(t, 0) for t in sorted(tables)}


@dataclass
class PlannedMigration:
    app_label: str
    name: str
    statements: list[str]
    tables: dict[str, int]
    warnings: list[str]


def plan() -> list[PlannedMigration]:
    """Every unapplied migration, in the order ``migrate`` would apply it."""
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    planned: list[PlannedMigration] = []
    for migration, backwards in executor.migration_plan(targets):
        statements = [
            str(s)
            for s in executor.loader.collect_sql([(migration, backwards)])  # type: ignore[attr-defined]
        ]
        report = analyse(statements)
        planned.append(
            PlannedMigration(
                app_label=migration.app_label,
                name=migration.name,
                statements=statements,
                tables=estimated_rows(report.tables),
                warnings=report.warnings,
            )
        )
    return planned
