"""CLI adapter entry point: ``python manage.py action <name> --user <username> [--field value]``.

This is the only kpiGo management command. Everything it can do is a registered
action, invoked through the same pipeline as HTTP and jobs.
"""

import json
import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from kpigo.action import ActionError, invoke, registry
from kpigo.action.adapters.cli import parse_field_args
from kpigo.action.adapters.jobs import task_name
from kpigo.action.identity import anonymous_context, build_context, resolve_user


class Command(BaseCommand):
    help = "Invoke a registered kpiGo action. Use --list to see them."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("action_name", nargs="?", help="Action name, e.g. platform.hello")
        parser.add_argument("--list", action="store_true", help="List registered actions.")
        parser.add_argument("--user", help="Username the action runs as (required to invoke).")
        parser.add_argument("--json", dest="json_payload", help="Whole payload as a JSON object.")
        parser.add_argument("--dry-run", action="store_true", help="Run without committing writes.")
        parser.add_argument(
            "--enqueue",
            action="store_true",
            help="Run through the job adapter on a worker and wait for the result.",
        )
        parser.add_argument(
            "--timeout", type=float, default=60.0, help="Seconds to wait with --enqueue."
        )
        parser.epilog = "Any other --field value pairs are passed to the action's input schema."

    def create_parser(self, prog_name: str, subcommand: str, **kwargs: Any) -> CommandParser:
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        # Unknown --field options are the action's input, collected rather than rejected.
        parser.__class__ = _FieldCollectingParser
        return parser

    def handle(self, *args: Any, **options: Any) -> None:
        if options["list"]:
            for d in registry:
                kind = "read " if d.read_only else "write"
                self.stdout.write(f"{d.name:40} {kind}  {d.permission:32} {d.summary}")
            return
        name = options["action_name"]
        if not name:
            raise CommandError("Name an action, or pass --list.")
        try:
            definition = registry.get(name)
            if not options["user"] and not definition.public:
                raise CommandError("--user is required: every invocation runs as a named user.")
            payload: dict[str, Any] = {}
            if options["json_payload"]:
                loaded = json.loads(options["json_payload"])
                if not isinstance(loaded, dict):
                    raise CommandError("--json must be a JSON object.")
                payload.update(loaded)
            payload.update(parse_field_args(options["params"] or []))

            if options["enqueue"]:
                from kpigo.celery import app

                async_result = app.send_task(
                    task_name(definition.name),
                    kwargs={
                        "params": payload,
                        "run_as": options["user"],
                        "dry_run": options["dry_run"],
                    },
                )
                out = async_result.get(timeout=options["timeout"])
            else:
                ctx = (
                    anonymous_context(caller="cli")
                    if definition.public
                    else build_context(
                        resolve_user(options["user"]), caller="cli", dry_run=options["dry_run"]
                    )
                )
                out = invoke(definition, payload, ctx).model_dump(mode="json")
        except ActionError as exc:
            self.stderr.write(json.dumps(exc.as_dict(), default=str))
            sys.exit(_exit_code(exc))
        except json.JSONDecodeError as exc:
            raise CommandError(f"--json is not valid JSON: {exc}") from None
        self.stdout.write(json.dumps(out, indent=2, default=str))


class _FieldCollectingParser(CommandParser):
    def parse_args(self, args: Any = None, namespace: Any = None) -> Any:
        parsed, extra = self.parse_known_args(args, namespace)
        parsed.params = extra
        return parsed


def _exit_code(exc: ActionError) -> int:
    # Distinct, stable exit codes so scripts can tell a denial from bad input.
    return {401: 3, 403: 4, 404: 5, 409: 6, 422: 2, 503: 7}.get(exc.http_status, 1)
