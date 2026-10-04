# kpiGo: working notes for Claude and contributors

kpiGo is an on-premise product: it runs on the customer's servers, is upgraded by
their infra team, and kpiGo never sees their data. Build as if you will never
touch the running system again after handover.

Stack: Python 3.12, Django 5.2 + Django Ninja, Pydantic v2, Postgres 16,
Celery + Redis, Polars (later), React 18 + TS + Vite + ECharts (later).

## The governing rule

**Nothing is a route, a job, a CLI command or an agent tool unless it is first a
registered action.** If it isn't an action, it doesn't exist. This is what makes
the MCP (R6) and agent (R7) releases integrations rather than rewrites.

How it is enforced:

- `kpigo/urls.py` mounts only the API built from the registry.
- `tests/test_governing_rule.py` fails on any URL, Celery task or custom
  management command that is not an action (OpenAPI docs are the only allowance).
- The only custom management command is `action`.

## Writing an action

Put it in `kpigo/<app>/actions/<module>.py`; it is auto-discovered.

```python
@action(
    name="scorecard.compute",          # dotted lower_snake
    summary="Compute a subject's scorecard for a period.",
    schema=ComputeScorecardIn,         # Pydantic input
    output=ScorecardOut,               # Pydantic output
    permission="scorecard.view",       # required; grant it in kpigo/action/roles.py
    read_only=True,
    module="scorecards",               # licensable unit; "platform" for shared foundations
    scope="subject",                   # checked against ctx.visible_subjects
    http={"method": "GET", "path": "/scorecard/{subject_id}/{period_key}"},
    example={"subject_id": "...", "period_key": "202610"},  # required, used by the matrix test
)
def compute_scorecard(params: ComputeScorecardIn, ctx: ActionContext) -> ScorecardOut: ...
```

Mutating actions may add `requires_approval="<class>"` (classes in use:
`metric_change`, `hierarchy_change`, `calendar_change`, `period_close`,
`config_change`), `audit="event.name"`, `idempotency_key=lambda p: ...` and
`config_change=True`, which makes the pipeline bump `org_settings.config_version`
in the same transaction. Every change to metrics, hierarchy, calendar or money
settings declares it.

Never do these by hand; the pipeline does them for every invocation, in order:
validate → authorise → narrow scope → maintenance gate → idempotency → approval
intercept → run (in a transaction for writes; dry runs roll back) → bump
config_version → audit → span.

Checklist for every new action (the review gate):

1. Permission declared and granted to the right system roles in `roles.py`.
2. `example` payload declared and valid.
3. `module` set correctly, so an unlicensed module's actions do not register.
4. Read-only actions do not write. Mutating actions declare approval/idempotency
   where the PRD calls for it.
5. No business logic in adapters; adapters only translate.

## Build-time reminders (Product Brief §6)

- **Absent is not zero.** A missing row excludes a metric (reduced denominator);
  an explicit `0` scores as zero.
- **kpiGo never stores or executes client SQL.** The DE team registers a view
  name. No "paste your SQL here" box, ever.
- **The assignment overlap constraint is load-bearing.** Keep the GiST exclusion
  forbidding overlapping role periods per subject.
- **Roll-ups aggregate distinct subjects**, never sums of subordinate totals.
- **`score_history` stores inputs, not just outputs.** Provenance depends on it.
- **Every empty screen says why it is empty.**
- **Colour is never the sole carrier of meaning**; grade colours and
  movement/state colours are separate token sets.
- **The permission-matrix test is generated from the registry**, so an action
  without a declared, granted permission fails the build.
- **Contributors are a real persona**; the input grid gets extra design care.
- **No grant means no data.** Absence of a scope row is a deny, never a wildcard.
  Subject scope comes from `visibility_closure`; no closure for the period is an
  empty scope.
- **Effective periods are half-open.** `effective_to` is the first day a row no
  longer applies, so `daterange(effective_from, effective_to)` is exact and a row
  ending the day the next begins does not overlap it. Overlaps are refused by GiST
  exclusion constraints (`kpigo.platform.db.no_overlap`).
- **A load is all or nothing.** Any gate error quarantines the whole load; conform
  runs in a savepoint. Dry runs write only `feed_run` and `feed_rejection`. The
  gates live in `kpigo/ingestion/validator.py`, which must stay standard-library
  only: it ships to client DE teams as the standalone validator.
- **Metric definitions version, never mutate.** A change to direction, aggregation,
  unit or target scope on a non-draft metric closes the row and opens a new one
  under the same `metric_code` (MR-7).

## Deployment constraints

- No telemetry, no error reporting home. The only outbound call is the licence
  heartbeat.
- Must work fully with no LLM configured. CI boots the stack without one.
- Migrations are additive only: new columns nullable or defaulted, drops deferred
  a full major version, backfills are jobs not migrations, every migration
  reversible within a major.
- `audit_log` is append-only (a trigger rejects UPDATE and DELETE).
- Schema conventions: snake_case singular tables, uuid keys, `org_id` on tenant
  tables, enums as `text` + `CHECK`, `created_at`/`created_by` everywhere.

## Commands

```bash
uv sync
export KPIGO_TESTING=1
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy kpigo tests
scripts/migrations_roundtrip.sh   # forward, every app to zero, forward again
docker compose up -d --build --wait && scripts/smoke.sh
```
