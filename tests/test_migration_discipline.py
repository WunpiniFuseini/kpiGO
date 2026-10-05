"""Migration discipline (Schema §15): additive only, reversible, backfills as jobs.

Upgrades run on servers kpiGo never sees, so every rule is checked against the
migration files themselves. A deliberate exception names the migration and why
in ``ALLOWED``; it is reviewed, never silent.
"""

from typing import Any

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import (
    AddField,
    AlterField,
    AlterModelTable,
    DeleteModel,
    RemoveField,
    RenameField,
    RenameModel,
    RunPython,
    RunSQL,
)
from django.db.models import NOT_PROVIDED

from kpigo.platform.migration_plan import analyse
from tests.conftest import run

KPIGO_APPS = {
    "platform",
    "periods",
    "hierarchy",
    "metrics",
    "ingestion",
    "access",
    "licence",
    "scorecards",
}

# Destructive operations are deferred a full major version (TDD §12). When one is
# genuinely due, list it here: {(app_label, migration_name): "why"}.
ALLOWED: dict[tuple[str, str], str] = {}

DESTRUCTIVE = (RemoveField, DeleteModel, RenameField, RenameModel, AlterModelTable, AlterField)


def _kpigo_migrations() -> list[Any]:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    return [
        migration
        for (app, _), migration in sorted(loader.disk_migrations.items())
        if app in KPIGO_APPS
    ]


MIGRATIONS = _kpigo_migrations()


def test_every_kpigo_app_has_migrations() -> None:
    assert {m.app_label for m in MIGRATIONS} == KPIGO_APPS


@pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda m: f"{m.app_label}.{m.name}")
def test_migration_is_additive_and_reversible(migration: Any) -> None:
    key = (migration.app_label, migration.name)
    if key in ALLOWED:
        pytest.skip(ALLOWED[key])
    for op in migration.operations:
        name = type(op).__name__
        assert not isinstance(op, DESTRUCTIVE), (
            f"{key}: {name} is not additive. Drops and rewrites wait a full major version."
        )
        if isinstance(op, AddField):
            field = op.field
            assert (
                field.null
                or field.default is not NOT_PROVIDED
                or getattr(field, "db_default", NOT_PROVIDED) is not NOT_PROVIDED
            ), f"{key}: new column {op.model_name}.{op.name} must be nullable or defaulted."
        if isinstance(op, RunPython):
            raise AssertionError(
                f"{key}: RunPython in a migration. Backfills are jobs (registered actions), "
                "not migrations."
            )
        if isinstance(op, RunSQL):
            assert op.reversible, f"{key}: RunSQL needs reverse_sql to roll back within a major."
            statements = op.sql if isinstance(op.sql, list | tuple) else [op.sql]
            warnings = analyse([str(s) for s in statements])
            assert not warnings.warnings, f"{key}: {warnings.warnings}"
        assert op.reversible, f"{key}: {name} cannot be reversed."


def test_analyser_flags_destructive_sql_and_finds_tables() -> None:
    report = analyse(
        [
            'ALTER TABLE "metric" ADD COLUMN "note" text NULL',
            'CREATE INDEX "metric_note" ON "metric" ("note")',
            'ALTER TABLE "subject" DROP COLUMN "email" CASCADE',
            'ALTER TABLE "subject" ALTER COLUMN "full_name" SET NOT NULL',
            'UPDATE "metric" SET "note" = \'\'',
            "-- Raw Python operation",
        ]
    )
    assert report.tables == {"metric", "subject"}
    assert len(report.warnings) == 3
    assert (
        analyse(['ALTER TABLE "metric" ADD COLUMN "x" integer DEFAULT 0 NOT NULL']).warnings == []
    )


@pytest.mark.django_db
def test_migration_dry_run_reports_nothing_pending_on_a_migrated_database() -> None:
    out = run("platform.migrations.plan")
    assert out.pending == 0
    assert out.additive is True


@pytest.mark.django_db
def test_migration_dry_run_reports_ddl_and_row_impact(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the newest metrics migration is unapplied and dry-run it."""
    from django.db.migrations.recorder import MigrationRecorder

    from kpigo.platform import migration_plan

    run(
        "metric.register",
        display_name="Total deposits",
        direction="higher_is_better",
        aggregation="sum",
        unit="currency",
        products=["scorecards"],
    )
    with connection.cursor() as cur:
        cur.execute("ANALYZE metric_family")
    latest = max(m.name for m in MIGRATIONS if m.app_label == "metrics")
    real = MigrationRecorder.applied_migrations

    def applied(self: MigrationRecorder) -> dict[tuple[str, str], Any]:
        found = dict(real(self))
        found.pop(("metrics", latest), None)
        return found

    monkeypatch.setattr(MigrationRecorder, "applied_migrations", applied)
    (planned,) = migration_plan.plan()
    assert (planned.app_label, planned.name) == ("metrics", latest)
    assert any("CREATE TABLE" in s for s in planned.statements)
    assert planned.tables.get("metric_family") == 1
