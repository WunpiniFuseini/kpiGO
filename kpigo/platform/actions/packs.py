"""Starter-pack actions (PRD OP-9): list the shipped packs, and load one into the org.

``pack.list`` shows each pack in full so an Admin sees exactly what adopting it brings before
anything changes. ``pack.load`` applies a pack additively and non-destructively and returns a
report of what it created and what it left alone, so the setup wizard (step 5) and Admin can
show the client the outcome.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from kpigo.action import ActionContext, action
from kpigo.action.errors import InvalidInput
from kpigo.platform.packs import ALL_PACKS, Pack
from kpigo.platform.packs.loader import apply_pack


class PackMetricOut(BaseModel):
    code: str
    name: str
    objective: str
    direction: str
    aggregation: str
    unit: str
    target_scope: str
    collection: str
    products: list[str]
    weight: int | None
    cap: int | None


class PackBandOut(BaseModel):
    label: str
    threshold: str
    ramp_position: int


class PackOut(BaseModel):
    key: str
    version: str
    title: str
    role: str
    summary: str
    metric_count: int
    metrics: list[PackMetricOut]
    objectives: list[str]
    bands: list[PackBandOut]
    product_lines: list[str]
    widgets: list[str]
    campaign_defaults: dict[str, object]
    feed_template: str


def _pack_out(pack: Pack) -> PackOut:
    return PackOut(
        key=pack.key,
        version=pack.version,
        title=pack.title,
        role=pack.role,
        summary=pack.summary,
        metric_count=len(pack.metrics),
        metrics=[
            PackMetricOut(
                code=m.code,
                name=m.name,
                objective=m.objective,
                direction=m.direction,
                aggregation=m.aggregation,
                unit=m.unit,
                target_scope=m.target_scope,
                collection=m.collection,
                products=list(m.products),
                weight=m.weight,
                cap=m.cap,
            )
            for m in pack.metrics
        ],
        objectives=list(pack.objectives),
        bands=[
            PackBandOut(label=b.label, threshold=b.threshold, ramp_position=b.ramp_position)
            for b in pack.bands
        ],
        product_lines=list(pack.product_lines),
        widgets=list(pack.widgets),
        campaign_defaults=dict(pack.campaign_defaults),
        feed_template=pack.feed_template,
    )


class PackListIn(BaseModel):
    pass


class PackListOut(BaseModel):
    packs: list[PackOut]


@action(
    name="pack.list",
    summary="The starter packs shipped with this version, with their full contents.",
    schema=PackListIn,
    output=PackListOut,
    permission="pack.view",
    read_only=True,
    http={"method": "GET", "path": "/packs"},
    example={},
)
def list_packs(params: PackListIn, ctx: ActionContext) -> PackListOut:
    return PackListOut(packs=[_pack_out(p) for p in ALL_PACKS.values()])


class PackLoadIn(BaseModel):
    pack_key: str = Field(min_length=1, max_length=64)


class PackLoadOut(BaseModel):
    pack_key: str
    pack_version: str
    metrics_created: list[str]
    metrics_skipped: list[str]
    name_conflicts: list[str]
    bands_applied: bool
    bands_note: str
    message: str


@action(
    name="pack.load",
    summary="Load a starter pack into this org: additive and non-destructive (adopt-and-amend).",
    schema=PackLoadIn,
    output=PackLoadOut,
    permission="pack.manage",
    read_only=False,
    audit="pack.loaded",
    config_change=True,
    http={"method": "POST", "path": "/packs/load"},
    example={"pack_key": "retail_rm"},
)
def load_pack(params: PackLoadIn, ctx: ActionContext) -> PackLoadOut:
    pack = ALL_PACKS.get(params.pack_key)
    if pack is None:
        raise InvalidInput(
            f"No starter pack '{params.pack_key}'. Known packs: {', '.join(sorted(ALL_PACKS))}."
        )
    report = apply_pack(ctx, pack)
    ctx.audit(
        "pack.loaded",
        pack_key=pack.key,
        pack_version=pack.version,
        metrics_created=report.metrics_created,
        metrics_skipped=report.metrics_skipped,
        bands_applied=report.bands_applied,
    )
    created, skipped = len(report.metrics_created), len(report.metrics_skipped)
    message = (
        f"Loaded {pack.title} v{pack.version}: {created} metric(s) added"
        f"{f', {skipped} already present' if skipped else ''}. {report.bands_note}"
    )
    return PackLoadOut(
        pack_key=report.pack_key,
        pack_version=report.pack_version,
        metrics_created=report.metrics_created,
        metrics_skipped=report.metrics_skipped,
        name_conflicts=report.name_conflicts,
        bands_applied=report.bands_applied,
        bands_note=report.bands_note,
        message=message,
    )
