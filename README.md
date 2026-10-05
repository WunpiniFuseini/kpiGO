# kpiGo

On-premise performance tracking for financial institutions: Scorecards, Agent
Performance, Campaign Manager and Executive Dashboard, sold as separately
licensed modules and run on the customer's own servers.

This repository is at **R0 Workstream E**: the action layer (A), the core data
model (B), ingestion (C), auth, access and the licence (D), and the design system
with the app shell and admin screens (E). R1 (Scorecards and manual input) is in
progress: the scorecard taxonomy, profile-to-metric assignment and the target
workbench, the scoring engine and overrides, and period close with frozen
snapshots are in; the scorecard UI and manual input follow.

## The one rule

Nothing is a route, a job, a CLI command or an agent tool unless it is first a
registered **action**. See `CLAUDE.md` for how that is enforced.

## Run it

Everything runs in Docker Compose with no internet access needed at runtime.

```bash
cp .env.example .env          # set KPIGO_SECRET_KEY and the Postgres password
docker compose up -d --build --wait
scripts/smoke.sh              # HTTP, CLI, a worker job, the web front, plus audit
```

Open <http://localhost:8080>. Services: `postgres` (16), `redis`, `migrate`
(one-shot), `app` (ASGI on :8000), `worker` and `beat` (Celery), and `web`
(nginx on :8080: the UI, with `/api` proxied to the app on the same origin).
TLS joins at R5 packaging.

## First run, sign-in and the licence

1. Set `KPIGO_SETUP_TOKEN` in `.env`, start the stack, and create the first
   Admin: `docker compose exec app python manage.py action setup.bootstrap --json
   '{"setup_token": "...", "email": "...", "display_name": "...", "password": "..."}'`
   or the setup screen at `/setup`. It works only while no Admin exists.
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
licence heartbeat (`KPIGO_LICENCE_HEARTBEAT_URL`) is the only outbound call to
kpiGo and sends the licence key, install fingerprint and product version, nothing
else. Email, when `KPIGO_EMAIL_HOST` is set, goes only to the bank's own relay.

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

## Frontend

React 18 + TypeScript + Vite in `frontend/`. Node 22.

```bash
cd frontend
npm ci
npm run dev                 # http://localhost:5173, proxies /api to the app on :8000
npm run typecheck && npm run lint && npm test
npm run storybook           # every component and screen in every state
npm run build-storybook && npm run a11y   # axe-core, WCAG 2.1 AA, every story
```

The UI calls actions and nothing else: `invoke("metric.register", {...})` in
`src/api/client.ts`. Routes and types are generated from the registry's OpenAPI
document, so after changing an action's input or output run
`uv run python scripts/export_openapi.py frontend/openapi.json` and
`npm run gen:api`; CI fails if either is stale. Design tokens are in
`src/styles/tokens.css` (Design Brief §4 verbatim, plus darker text variants so
every text pairing meets 4.5:1; `src/test/tokens.test.ts` holds the ratios).

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

## Scorecards: targets

Targets are set in the workbench (Admin → Targets) before anything is scored.
Upload a sheet (CSV/XLSX, one row per metric, scope and period) or copy a
period forward with an uplift; both land as drafts. The coverage grid shows each
profile × period as published, draft, partial or missing, and the weight check
sums each profile's (and subject's) weights against the configured total. A
publish creates a batch, supersedes the previous version and is approved under
`target_publish`; a batch whose periods are all in the future can be reverted.

```bash
uv run python manage.py action target.upload --user ama --json '{"rows": [...], "check_only": true}'
uv run python manage.py action target.publish --user ama --json '{"period_keys": ["202611"]}'
```

## Scorecards: scoring

Open periods are scored at query time through the assignment in force on the
period's last day: its profile decides the metrics, its cycle (else the
product's) adjusts yearly, cumulative, quarterly and prorated targets. Each
metric scores `MIN(% achieved × weight, cap) ÷ 100`; a missing actual is
`not_reported` and leaves the denominator, an explicit `0` scores. The band is
read from the total scaled up by the weight still awaiting data, so a late feed
never drops anyone a band. `scorecard.compute` is one subject (the per-subject
path, with every input for provenance); `scorecard.period.list` scores everyone
visible through the Polars bulk path. `tests/test_scoring_golden.py` holds both
paths to identical output.

Overrides (`override.request` → `override.approve` by a second person) change a
target, weight, cap, actual or target type for a person, a profile or a
branch/region/segment/portfolio over a range of months; person beats profile
beats dimension.

## Scorecards: period close

`scorecard.close.check` lists what stops a month closing: any metric unscored
for anyone (not reported, no target, no FX rate) that has not been excluded with
a reason (`scorecard.exclusion.add`), and weights off the total. Feeds with no
load and pending overrides are warnings. `scorecard.period.close` scores everyone
through the bulk path and freezes the result, with its inputs, into
`score_history` / `score_total` under a `score_snapshot` version; a trigger
refuses any later change. `scorecard.period.restate` (reason required) reopens it:
only a `restating` period takes restatement loads and overrides, and closing it
again writes version n+1 beside the old one. Closed periods are read from the
snapshot, never recomputed.

## Scorecards: the scorecard page

Scorecards (`/scorecards`) shows one person's month: the grade banner, summary
cards, the matrix grouped by the top level of the scorecard structure, and the
history. Every figure in the matrix opens a provenance panel: the target and how
its type was adjusted, the overrides applied with their reasons, the reported
actual and any FX conversion, the feed run, the arithmetic and the period's
footing. On a closed month the person acknowledges it (`scorecard.acknowledge`,
"seen", once per snapshot version) and may query a figure
(`scorecard.query.raise`); a query goes to their solid-line manager, or to the
Admin queue when they have none, and is resolved as explained or adjusted
against a named override (`scorecard.query.resolve`). Managers add commentary for
the person or for managers only (`scorecard.comment.add`).
`scorecard.export.pdf` renders the same `scorecard.compute` output to PDF.

## Scorecards: manual input

A metric registered with `collection_method="manual_input"` is entered by people,
not loaded by a feed. An Admin assigns each slice (one subject, a profile, or a
team such as `branch:ACC`) to a named contributor or to "the line manager of" a
subject, resolved each month (`input.assignment.create`). The contributor's
**My inputs** page lists what they owe and the deadline: the Nth working day of
the following month on the business calendar (`input_due_working_day`, default 5).
`input.save` keeps drafts; `input.submit` (maker-checker via the `manual_input`
approval class, off by default) conforms the value to `fact_actual_monthly` for
every member of the slice. Values are editable until the deadline, then locked; a
later correction is a new version, only while the month is being restated. Until
the month closes, manual values are hidden from everyone's scorecard but an
Admin's. Period close names who still owes an input.

An input still owed climbs the **escalation ladder** (`input.remind`; set
`KPIGO_INPUT_REMINDER_USER` to run it daily): the contributor is reminded N
working days before the due day, their line manager (resolved from the reporting
lines in force that day) is told on the due day, and the stakeholders the first
working day after it. The stakeholders are the slice's own list
(`input.assignment.set_stakeholders`), else the org's, else everyone who manages
input. Offsets, rungs switched off and the org's stakeholders are set with
`input.ladder.set`. The first two rungs stop once the input locks; the third is
how the stakeholders learn it went unreported. Every rung is in-app (the
contributor sees how far each input has gone; anyone told sees it under
"Escalated to you" on My inputs) and audited, and by email when a mail relay is
configured (`KPIGO_EMAIL_HOST`, `KPIGO_EMAIL_FROM` and the other `KPIGO_EMAIL_*`
settings in `.env.example`; `KPIGO_PUBLIC_URL` adds a link): one digest per person
per run, never one email per slice. A relay failure is audited and never stops
the in-app notice. A rung that reaches nobody (a contributor with no line
manager) is recorded, not skipped silently.

**Input compliance** (`input.compliance`, the Input compliance page) shows, per
contributor and month, how many slices they were asked for and whether each
arrived on time, late or never, with how far the ladder climbed. An input's
arrival is its first submission, so a later correction does not make it late; an
input not yet due is "still open", not missing. A contributor late or missing in
3 or more of the months shown is flagged. Admins see everyone, including slices
nobody could be asked for; line managers and executives see the contributors in
their visibility scope.

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
  scorecards/        taxonomy, profile metrics, bands, target workbench, overrides,
                     scoring engine (engine.py rules, scoring.py per subject, bulk.py Polars),
                     close.py (pre-checks, snapshots, restatement)
  licence/           signed licence, fingerprint, grace states, the pipeline's licence gate
tools/licence_vendor.py  vendor-side key generation and licence signing
frontend/
  src/api/           generated routes and types, invoke(), useQuery/useMutation
  src/styles/        tokens, base, components, shell CSS
  src/components/    primitives (GradeBanner, MetricCard, RankedList, TrendChart, ...)
  src/shell/         rail, topbar, view container, page key → path
  src/pages/         sign-in, first run, invite, OIDC callback, admin screens
  scripts/a11y.mjs   the axe-core gate over every story
deploy/nginx/        the web front's config (static UI, /api proxy, CSP)
tests/               pipeline, permission matrix, surfaces, governing rule
```

## CI

- **Lint and typecheck**: ruff, ruff format, mypy (strict on `kpigo/action`).
- **Tests**: Postgres 16 + Redis services, migrations forward/back, the
  permission matrix generated from the registry, then the full suite.
- **Frontend**: generated API types current, tsc, ESLint, Vitest, the build,
  Storybook, then axe-core over every story in Chromium (WCAG 2.1 AA, and no
  horizontal scroll at phone width on screens).
- **No-inference boot gate**: boots the whole Compose stack with no model
  configured, runs the smoke test, then the full suite inside the stack. If
  kpiGo ever needs a model to work, this fails.

## Documents

The product brief and its companion docs (PRD, TDD, Engineering Plan, Backend
Schema, App Flow, Design Brief, Starter Packs, Runbook) are the source of truth
for what to build and how.
