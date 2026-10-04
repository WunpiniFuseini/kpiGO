# kpiGo

On-premise performance tracking for financial institutions: Scorecards, Agent
Performance, Campaign Manager and Executive Dashboard, sold as separately
licensed modules and run on the customer's own servers.

This repository is at **R0 Workstream A**: the action layer, proven end to end
by one `platform.hello` action. Domain modules come in later increments.

## The one rule

Nothing is a route, a job, a CLI command or an agent tool unless it is first a
registered **action**. See `CLAUDE.md` for how that is enforced.

## Run it

Everything runs in Docker Compose with no internet access needed at runtime.

```bash
cp .env.example .env          # set KPIGO_SECRET_KEY and the Postgres password
docker compose up -d --build --wait
scripts/smoke.sh              # hello over HTTP, CLI and a worker job, plus audit
```

Services: `postgres` (16), `redis`, `migrate` (one-shot), `app` (ASGI on :8000),
`worker` and `beat` (Celery). nginx with TLS joins at R5 packaging.

## Develop

Python 3.12 with [uv](https://docs.astral.sh/uv/), Postgres 16 and Redis on
localhost:

```bash
uv sync
export KPIGO_TESTING=1         # dev secret key; never set in production
uv run python manage.py migrate
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy kpigo tests
```

Call an action from the CLI (every invocation runs as a named user):

```bash
uv run python manage.py action --list
uv run python manage.py action platform.hello --user ama --name Wunpini
uv run python manage.py action platform.hello --user ama --enqueue   # via a worker
```

Over HTTP the same action is `GET /api/v1/hello?name=Wunpini` with a Django
session. `GET /api/v1/registry` lists every action with its schemas (admin
only). OpenAPI docs are at `/api/v1/docs` for staff.

## Layout

```
kpigo/
  action/            the action layer (decorator, context, registry, pipeline)
    adapters/        http (Django Ninja), cli, jobs (Celery)
    roles.py         system roles and the permissions they grant
  platform/          audit_log, approval_request, action_idempotency
    actions/         platform.hello, platform.registry.list, platform.approval.*
tests/               pipeline, permission matrix, surfaces, governing rule
```

## CI

- **Lint and typecheck**: ruff, ruff format, mypy (strict on `kpigo/action`).
- **Tests**: Postgres 16 + Redis services, migrations forward/back, the
  permission matrix generated from the registry, then the full suite.
- **No-inference boot gate**: boots the whole Compose stack with no model
  configured, runs the smoke test, then the full suite inside the stack. If
  kpiGo ever needs a model to work, this fails.

## Documents

The product brief and its companion docs (PRD, TDD, Engineering Plan, Backend
Schema, App Flow, Design Brief, Starter Packs, Runbook) are the source of truth
for what to build and how.
