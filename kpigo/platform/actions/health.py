"""The health page (PRD OP-3, TDD §11): the client operations team's first stop.

Services, database growth, feed status, queue depth, licence and version, read
directly. Every probe fails soft: a dead dependency is reported, never raised,
so the page still renders when the thing it reports on is down.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from pydantic import BaseModel

from kpigo.action import ActionContext, action
from kpigo.action.pipeline import maintenance_mode

Status = Literal["ok", "degraded", "down"]


class ServiceOut(BaseModel):
    name: str
    status: Status
    detail: str


class TableSize(BaseModel):
    table: str
    bytes: int
    rows_estimate: int


class DatabaseOut(BaseModel):
    size_bytes: int
    largest_tables: list[TableSize]
    pending_migrations: int


class FeedsOut(BaseModel):
    total: int
    fresh: int
    stale: int
    never_loaded: int
    quarantined: list[str]
    failed: list[str]


class LicenceHealth(BaseModel):
    state: str
    mode: str
    message: str
    restart_required: bool


class VersionOut(BaseModel):
    product_version: str
    migrations: dict[str, str]


class BackupHealth(BaseModel):
    last_at: datetime | None
    last_outcome: str | None
    last_ok_at: datetime | None


class HealthIn(BaseModel):
    check_workers: bool = True


class HealthOut(BaseModel):
    status: Status
    checked_at: datetime
    maintenance_mode: bool
    services: list[ServiceOut]
    database: DatabaseOut | None
    queue_depth: int | None
    feeds: FeedsOut | None
    licence: LicenceHealth | None
    backup: BackupHealth | None
    version: VersionOut


def _database() -> tuple[ServiceOut, DatabaseOut | None, dict[str, str]]:
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_database_size(current_database())")
            size = int(cur.fetchone()[0])
            cur.execute(
                "SELECT c.relname, pg_total_relation_size(c.oid), GREATEST(c.reltuples, 0)::bigint "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'p') AND n.nspname = current_schema() "
                "ORDER BY 2 DESC LIMIT 10"
            )
            tables = [TableSize(table=t, bytes=int(b), rows_estimate=int(r)) for t, b, r in cur]
        executor = MigrationExecutor(connection)
        pending = len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
        applied = executor.loader.applied_migrations
        schema: dict[str, str] = {}
        for app, name in sorted(applied):
            if app.startswith(
                ("platform", "periods", "hierarchy", "metrics", "ingestion", "access", "licence")
            ):
                schema[app] = max(schema.get(app, ""), name)
    except Exception as exc:
        return ServiceOut(name="database", status="down", detail=str(exc)[:300]), None, {}
    service = ServiceOut(
        name="database",
        status="degraded" if pending else "ok",
        detail=f"{pending} migrations pending" if pending else "Connected; schema current.",
    )
    return (
        service,
        DatabaseOut(size_bytes=size, largest_tables=tables, pending_migrations=pending),
        schema,
    )


def _broker() -> tuple[ServiceOut, int | None]:
    try:
        import redis

        client = redis.Redis.from_url(str(settings.CELERY_BROKER_URL), socket_timeout=2)
        client.ping()
        depth = int(client.llen("celery"))  # type: ignore[arg-type]
    except Exception as exc:
        return ServiceOut(name="queue", status="down", detail=str(exc)[:300]), None
    return ServiceOut(name="queue", status="ok", detail=f"{depth} jobs waiting."), depth


def _workers() -> ServiceOut:
    try:
        from kpigo.celery import app

        replies = app.control.inspect(timeout=1.0).ping() or {}
    except Exception as exc:
        return ServiceOut(name="workers", status="down", detail=str(exc)[:300])
    if not replies:
        return ServiceOut(name="workers", status="down", detail="No worker answered.")
    return ServiceOut(name="workers", status="ok", detail=f"{len(replies)} answering.")


def _feeds(org_id: str) -> FeedsOut | None:
    try:
        from kpigo.ingestion.models import Feed, FeedRun

        feeds = list(Feed.objects.filter(org_id=org_id, status="active"))
        last = dict(
            FeedRun.objects.filter(
                run_id__in=[f.last_run_id for f in feeds if f.last_run_id]
            ).values_list("run_id", "state")
        )
    except Exception:
        return None
    states = [f.freshness_state for f in feeds]
    return FeedsOut(
        total=len(feeds),
        fresh=states.count("fresh"),
        stale=states.count("stale"),
        never_loaded=states.count("never_loaded"),
        quarantined=sorted(
            f.name for f in feeds if f.last_run_id and last.get(f.last_run_id) == "quarantined"
        ),
        failed=sorted(
            f.name for f in feeds if f.last_run_id and last.get(f.last_run_id) == "failed"
        ),
    )


def _licence(org_id: str) -> LicenceHealth | None:
    try:
        from kpigo.licence.actions.licence import describe

        info = describe(org_id)
    except Exception:
        return None
    return LicenceHealth(
        state=info.state,
        mode=info.mode,
        message=info.message,
        restart_required=info.restart_required,
    )


def _backup(org_id: str) -> BackupHealth | None:
    try:
        from kpigo.platform.models import BackupRun

        last = BackupRun.objects.filter(org_id=org_id).order_by("-created_at").first()
        last_ok = (
            BackupRun.objects.filter(org_id=org_id, outcome="ok").order_by("-created_at").first()
        )
    except Exception:
        return None
    return BackupHealth(
        last_at=last.created_at if last else None,
        last_outcome=last.outcome if last else None,
        last_ok_at=last_ok.created_at if last_ok else None,
    )


@action(
    name="system.health",
    summary="Service, database, feed, queue, licence and version status for operations.",
    schema=HealthIn,
    output=HealthOut,
    permission="system.health.view",
    read_only=True,
    http={"method": "GET", "path": "/health"},
    example={"check_workers": False},
)
def health(params: HealthIn, ctx: ActionContext) -> HealthOut:
    from kpigo.licence.state import product_version

    database_service, database, schema = _database()
    broker_service, depth = _broker()
    services = [database_service, broker_service]
    if params.check_workers:
        services.append(_workers())
    feeds = _feeds(ctx.org_id)
    licence = _licence(ctx.org_id)
    backup = _backup(ctx.org_id)
    if feeds is not None and (feeds.stale or feeds.quarantined or feeds.failed):
        services.append(
            ServiceOut(
                name="feeds",
                status="degraded",
                detail=f"{feeds.stale} stale, {len(feeds.quarantined)} quarantined, "
                f"{len(feeds.failed)} failed.",
            )
        )
    if licence is not None and (licence.mode != "full" or licence.restart_required):
        services.append(ServiceOut(name="licence", status="degraded", detail=licence.message))
    worst: Status = "ok"
    if any(s.status != "ok" for s in services):
        worst = "degraded"
    if database_service.status == "down":
        worst = "down"
    return HealthOut(
        status=worst,
        checked_at=timezone.now(),
        maintenance_mode=maintenance_mode(),
        services=services,
        database=database,
        queue_depth=depth,
        feeds=feeds,
        licence=licence,
        backup=backup,
        version=VersionOut(product_version=product_version(), migrations=schema),
    )
