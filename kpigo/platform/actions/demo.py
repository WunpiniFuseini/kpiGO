"""Demo-mode actions (PRD OP-9): seed a synthetic Scorecards world an evaluator can explore,
and remove it again.

``demo.seed`` builds a small retail-banking team with metrics, published targets and recent
actuals so the live scorecard, history and roll-up screens light up. ``demo.reset`` removes
exactly what the seeder created — tracked in the demo ledger — and nothing a client authored.
``demo.status`` says whether demo data is present. The world is namespaced (``demo_`` codes,
``DEMO`` staff numbers) so it cannot collide with a real registry, and it closes no period, so
the data stays live and reset stays clean.
"""

from __future__ import annotations

from pydantic import BaseModel

from kpigo.action import ActionContext, action
from kpigo.action.errors import InvalidInput
from kpigo.platform import demo


class DemoStatusIn(BaseModel):
    pass


class DemoStatusOut(BaseModel):
    present: bool
    batches: int
    message: str


@action(
    name="demo.status",
    summary="Whether demonstration data is loaded in this org.",
    schema=DemoStatusIn,
    output=DemoStatusOut,
    permission="demo.view",
    read_only=True,
    http={"method": "GET", "path": "/demo/status"},
    example={},
)
def demo_status(params: DemoStatusIn, ctx: ActionContext) -> DemoStatusOut:
    from kpigo.platform.models import DemoArtifact

    batches = (
        DemoArtifact.objects.filter(org_id=ctx.org_id)
        .values_list("batch_id", flat=True)
        .distinct()
        .count()
    )
    present = batches > 0
    return DemoStatusOut(
        present=present,
        batches=batches,
        message=(
            "Demo data is loaded; run demo.reset to remove it."
            if present
            else "No demo data loaded."
        ),
    )


class DemoSeedIn(BaseModel):
    pass


class DemoSeedOut(BaseModel):
    batch_id: str
    subjects: int
    metrics: int
    targets: int
    actuals: int
    periods: list[str]
    bands_applied: bool
    message: str


@action(
    name="demo.seed",
    summary="Load a synthetic Scorecards world for demonstration and evaluation.",
    schema=DemoSeedIn,
    output=DemoSeedOut,
    permission="demo.manage",
    read_only=False,
    audit="demo.seeded",
    config_change=True,
    http={"method": "POST", "path": "/demo/seed"},
    example={},
)
def demo_seed(params: DemoSeedIn, ctx: ActionContext) -> DemoSeedOut:
    if demo.has_demo(ctx.org_id):
        raise InvalidInput("Demo data is already loaded. Run demo.reset before seeding again.")
    report = demo.seed_demo(ctx)
    ctx.audit(
        "demo.seeded",
        batch_id=report.batch_id,
        subjects=report.subjects,
        metrics=report.metrics,
        targets=report.targets,
        actuals=report.actuals,
        periods=report.periods,
    )
    return DemoSeedOut(
        batch_id=report.batch_id,
        subjects=report.subjects,
        metrics=report.metrics,
        targets=report.targets,
        actuals=report.actuals,
        periods=report.periods,
        bands_applied=report.bands_applied,
        message=report.note,
    )


class DemoResetIn(BaseModel):
    pass


class DemoResetOut(BaseModel):
    batches: int
    deleted: int
    periods: list[str]
    message: str


@action(
    name="demo.reset",
    summary="Remove all demonstration data this org's demo seeder created.",
    schema=DemoResetIn,
    output=DemoResetOut,
    permission="demo.manage",
    read_only=False,
    audit="demo.reset",
    config_change=True,
    http={"method": "POST", "path": "/demo/reset"},
    example={},
)
def demo_reset(params: DemoResetIn, ctx: ActionContext) -> DemoResetOut:
    report = demo.reset_demo(ctx)
    ctx.audit(
        "demo.reset",
        batches=report.batches,
        deleted=report.deleted,
        periods=report.periods,
    )
    return DemoResetOut(
        batches=report.batches,
        deleted=report.deleted,
        periods=report.periods,
        message=report.note,
    )
