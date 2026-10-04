"""The upgrade dry run as an action: report DDL and row impact, execute nothing."""

from pydantic import BaseModel

from kpigo.action import ActionContext, action
from kpigo.platform import migration_plan


class PlanIn(BaseModel):
    pass


class TableImpact(BaseModel):
    table: str
    estimated_rows: int


class PlannedMigrationOut(BaseModel):
    app_label: str
    name: str
    statements: list[str]
    tables: list[TableImpact]
    warnings: list[str]


class PlanOut(BaseModel):
    pending: int
    additive: bool
    migrations: list[PlannedMigrationOut]


@action(
    name="platform.migrations.plan",
    summary="Dry-run pending migrations: the SQL, tables touched, row estimates, warnings.",
    schema=PlanIn,
    output=PlanOut,
    permission="platform.migrations.view",
    read_only=True,
    example={},
)
def plan(params: PlanIn, ctx: ActionContext) -> PlanOut:
    planned = migration_plan.plan()
    return PlanOut(
        pending=len(planned),
        additive=not any(p.warnings for p in planned),
        migrations=[
            PlannedMigrationOut(
                app_label=p.app_label,
                name=p.name,
                statements=p.statements,
                tables=[TableImpact(table=t, estimated_rows=n) for t, n in p.tables.items()],
                warnings=p.warnings,
            )
            for p in planned
        ],
    )
