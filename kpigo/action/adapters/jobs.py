"""Job adapter: one Celery task per registered action (TDD §3.3).

A job runs as a named user so its permissions and audit attribution are the
same as if that user had called the action over HTTP. Scheduled actions are
declared in ``settings.KPIGO_SCHEDULED_ACTIONS`` and become beat entries.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from kpigo.action.definition import ActionDefinition
from kpigo.action.identity import build_context, resolve_user
from kpigo.action.pipeline import invoke
from kpigo.action.registry import Registry

if TYPE_CHECKING:
    from celery import Celery

TASK_PREFIX = "kpigo.action."


def task_name(action_name: str) -> str:
    return f"{TASK_PREFIX}{action_name}"


def run_job(
    definition: ActionDefinition,
    params: dict[str, Any],
    run_as: str,
    request_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    ctx = build_context(resolve_user(run_as), caller="job", request_id=request_id, dry_run=dry_run)
    result = invoke(definition, params, ctx)
    out: dict[str, Any] = result.model_dump(mode="json")
    return out


def register_tasks(app: Celery, registry: Registry) -> list[str]:
    names: list[str] = []
    for definition in registry:
        name = task_name(definition.name)
        if name in app.tasks:
            names.append(name)
            continue

        def make(defn: ActionDefinition) -> Any:
            def task(
                params: dict[str, Any] | None = None,
                run_as: str = "",
                request_id: str | None = None,
                dry_run: bool = False,
            ) -> dict[str, Any]:
                return run_job(defn, params or {}, run_as, request_id, dry_run)

            task.__name__ = defn.name.replace(".", "_")
            task.__kpigo_action__ = defn  # type: ignore[attr-defined]
            return task

        app.task(name=name)(make(definition))
        names.append(name)
    return names


def beat_schedule(entries: list[dict[str, Any]], registry: Registry) -> dict[str, dict[str, Any]]:
    """Build ``CELERY_BEAT_SCHEDULE`` from declared scheduled actions.

    Each entry: ``{"action": "platform.hello", "run_as": "ops", "every_seconds": 3600,
    "params": {...}}``. An entry naming an unregistered action fails loudly.
    """
    schedule: dict[str, dict[str, Any]] = {}
    for entry in entries:
        definition = registry.get(entry["action"])
        key = entry.get("key") or f"{definition.name}:{entry['run_as']}"
        schedule[key] = {
            "task": task_name(definition.name),
            "schedule": timedelta(seconds=int(entry["every_seconds"])),
            "kwargs": {"params": entry.get("params", {}), "run_as": entry["run_as"]},
        }
    return schedule
