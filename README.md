# kpiGo

On-premise performance tracking for financial institutions: Scorecards, Agent
Performance, Campaign Manager and Executive Dashboard, sold as separately
licensed modules and run on the customer's own servers.

This repository is at **R0 Workstream C**: the action layer (A), the core data
model (B) and ingestion (C). Auth, the licence service and the design system come
next; domain modules in later releases.

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

## First run, sign-in and the licence

1. Set `KPIGO_SETUP_TOKEN` in `.env`, start the stack, and create the first
   Admin: `docker compose exec app python manage.py action setup.bootstrap --json
   '{"setup_token": "...", "email": "...", "display_name": "...", "password": "..."}'`
   (or the setup screen). It works only while no Admin exists.
2. `setup.status` shows the **install fingerprint**. kpiGo issues a signed
   licence for it; the Admin activates it with `licence.activate`, offline.
   Modules load from the licence file on the next restart. A production install
   without a licence runs only setup, sign-in, the licence screen and health.
   `KPIGO_DEBUG=true` is a development install: no licence, modules from
   `KPIGO_ENTITLED_MODULES`.
3. Users are invited (`user.invite`), never self-registered. Sign-in is local
   (Argon2id), an LDAP bind, OIDC or SAML; configure the providers in `.env`.
   MFA is the identity provider's job (`KPIGO_OIDC_REQUIRE_MFA` and
   `KPIGO_SAML_REQUIRED_AUTHN_CONTEXT` refuse sign-ins that skipped it).
4. Import the roster from LDAP, Entra ID or a CSV with
   `directory.import.preview`; review the diff; `directory.import.apply`.

After expiry a licence gives 30 days of full function, then 45 days read-only,
then locks to the licence screen. Data is never touched at any stage. The
licence heartbeat (`KPIGO_LICENCE_HEARTBEAT_URL`) is the only outbound call and
sends the licence key, install fingerprint and product version, nothing else.

Licences are signed with `tools/licence_vendor.py` (vendor side, offline). The
public keys a build trusts are in `kpigo/licence/document.py`.

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

## Ingestion

A feed reads one landing template (`actual_monthly`, `actual_daily`,
`actual_dimensional`) from a registered database object (`pull`: Postgres, SQL
Server, Oracle, MySQL), a watched folder (`drop`) or a file sent through the UI
(`upload`). kpiGo never stores or runs client SQL: it reads the template's
columns from an object name. Every load is discover → land → validate (six
gates) → decide → conform → publish, all or nothing.

```bash
uv run python manage.py action connection.create --user ama --json '{"name": "dw", "driver": "postgres", "host": "dw", "database": "warehouse", "username": "kpigo_ro", "password": "..."}'
uv run python manage.py action feed.register --user ama --json '{"name": "monthly", "template": "actual_monthly", "mode": "pull", "connection": "dw", "source_object": "kpi.v_actual_monthly", "cadence_cron": "0 6 * * *"}'
uv run python manage.py action feed.dry_run --user ama --feed monthly   # writes nothing; required before the first load
uv run python manage.py action feed.run --user ama --feed monthly       # commits whole or quarantines whole
```

Set `KPIGO_CREDENTIAL_KEYS` (Fernet) before saving a connection, and
`KPIGO_INGESTION_SCHEDULE_USER` to run `feed.tick` (drop folders, cadences,
freshness) every minute.

**Standalone validator for the client's DE team.** `kpigo/ingestion/validator.py`
is one standard-library file: copy it out and run it against a CSV/XLSX or the
client's own view. With `contract.json` from `feed.contract.export` it runs every
gate exactly as kpiGo will.

```bash
python3 validator.py --template actual_monthly --file actuals.csv --contract contract.json --report rejections.csv
```

## Layout

```
kpigo/
  action/            the action layer (decorator, context, registry, pipeline)
    adapters/        http (Django Ninja), cli, jobs (Celery)
    roles.py         system roles and the permissions they grant
  platform/          audit_log, approval_request, action_idempotency, org settings, FX
  periods/           business calendar, period status, feed deadlines
  hierarchy/         subjects, assignments, reporting lines, closure, dimensions, product lines
  metrics/           metric registry
  ingestion/         connections, feeds, runs, tmpl_* landing, fact_* tables
    validator.py     the feed contract and six gates; also the standalone validator
  access/            users, roles, page access, data scope grants, sign-in, directory import
    auth/            local + LDAP, OIDC, SAML, Entra Graph
  licence/           signed licence, fingerprint, grace states, the pipeline's licence gate
tools/licence_vendor.py  vendor-side key generation and licence signing
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
