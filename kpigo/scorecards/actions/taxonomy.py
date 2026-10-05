"""Scorecard taxonomy (PRD SC-1, Schema §3).

One to three client-named levels above the metric. Metrics sit under the
deepest level; a profile may place a metric differently from the default.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from django.db.models import Count
from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints

from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.metrics.actions.metric import MetricCode
from kpigo.metrics.models import Metric
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import Code
from kpigo.scorecards.models import (
    ScorecardLevel,
    ScorecardNode,
    ScorecardPlacement,
    ScorecardTemplate,
)

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"

_CONFLICTS = {
    "scorecard_template_name": "A scorecard template with this name already exists.",
    "scorecard_template_one_active": "Another template is already active.",
    "scorecard_node_label_unique": "A node with this label already exists under that parent.",
    "scorecard_assignment_unique": "That metric is already placed for that profile.",
}


# ── outputs ──────────────────────────────────────────────────────────────────


class TaxonomyLevelOut(BaseModel):
    level_no: int
    label: str


class TaxonomyNodeOut(BaseModel):
    node_id: uuid.UUID
    level_no: int
    parent_id: uuid.UUID | None
    label: str
    sort_order: int


class PlacementOut(BaseModel):
    placement_id: uuid.UUID
    node_id: uuid.UUID
    metric_code: str
    metric_name: str | None
    profile_code: str | None
    sort_order: int


class TemplateOut(BaseModel):
    template_id: uuid.UUID
    name: str
    level_count: int
    status: str
    levels: list[TaxonomyLevelOut]
    nodes: list[TaxonomyNodeOut]
    placements: list[PlacementOut]


def template_out(template: ScorecardTemplate) -> TemplateOut:
    names = dict(
        Metric.objects.filter(org_id=template.org_id, effective_to__isnull=True).values_list(
            "metric_code", "display_name"
        )
    )
    return TemplateOut(
        template_id=template.template_id,
        name=template.name,
        level_count=template.level_count,
        status=template.status,
        levels=[
            TaxonomyLevelOut(level_no=lv.level_no, label=lv.label)
            for lv in template.levels.order_by("level_no")
        ],
        nodes=[
            TaxonomyNodeOut(
                node_id=n.node_id,
                level_no=n.level_no,
                parent_id=n.parent_id,
                label=n.label,
                sort_order=n.sort_order,
            )
            for n in template.nodes.order_by("level_no", "sort_order", "label")
        ],
        placements=[
            PlacementOut(
                placement_id=p.placement_id,
                node_id=p.node_id,
                metric_code=p.metric_code,
                metric_name=names.get(p.metric_code),
                profile_code=p.profile_code,
                sort_order=p.sort_order,
            )
            for p in template.placements.order_by("sort_order", "metric_code", "profile_code")
        ],
    )


def _template(
    ctx: ActionContext, template_id: uuid.UUID, *, lock: bool = False
) -> ScorecardTemplate:
    rows = ScorecardTemplate.objects.filter(org_id=ctx.org_id, template_id=template_id)
    found = (rows.select_for_update() if lock else rows).first()
    if found is None:
        raise NotFound("No such scorecard template.")
    return found


def _node(template: ScorecardTemplate, node_id: uuid.UUID) -> ScorecardNode:
    found = template.nodes.filter(node_id=node_id).first()
    if found is None:
        raise NotFound("No such node in this template.")
    return found


# ── templates ────────────────────────────────────────────────────────────────


class TemplateSaveIn(BaseModel):
    # Omit to create a template; give it to rename or relabel one.
    template_id: uuid.UUID | None = None
    name: Label
    # One label per level, top first: ["Strategic objective", "Value driver"].
    levels: list[Label] = Field(min_length=1, max_length=3)


@action(
    name="scorecard.template.save",
    summary="Create a scorecard template, or rename it and relabel its levels.",
    schema=TemplateSaveIn,
    output=TemplateOut,
    permission="scorecard.config.manage",
    read_only=False,
    module="scorecards",
    requires_approval="config_change",
    audit="scorecard.template_saved",
    config_change=True,
    example={"name": "Retail scorecard", "levels": ["Strategic objective"]},
)
def save_template(params: TemplateSaveIn, ctx: ActionContext) -> TemplateOut:
    now = timezone.now()
    with conflicts(_CONFLICTS):
        if params.template_id is None:
            template = ScorecardTemplate.objects.create(
                org_id=ctx.org_id,
                name=params.name,
                level_count=len(params.levels),
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
        else:
            template = _template(ctx, params.template_id, lock=True)
            deepest = (
                template.nodes.order_by("-level_no").values_list("level_no", flat=True).first()
            )
            if deepest is not None and deepest > len(params.levels):
                raise Conflict(
                    f"The template has nodes at level {deepest}; remove them before "
                    f"reducing it to {len(params.levels)} level(s)."
                )
            if len(params.levels) != template.level_count and template.placements.exists():
                raise Conflict(
                    "Metrics are placed in this template; changing its depth would orphan them. "
                    "Clear the placements first, or create a new template."
                )
            template.name = params.name
            template.level_count = len(params.levels)
            template.updated_by = ctx.user_id
            template.updated_at = now
            template.save()
        template.levels.filter(level_no__gt=len(params.levels)).delete()
        for no, label in enumerate(params.levels, start=1):
            ScorecardLevel.objects.update_or_create(
                template=template,
                level_no=no,
                defaults={"label": label, "updated_by": ctx.user_id, "updated_at": now},
                create_defaults={"label": label, "created_by": ctx.user_id},
            )
    return template_out(template)


class TemplateActivateIn(BaseModel):
    template_id: uuid.UUID


@action(
    name="scorecard.template.activate",
    summary="Make a template the one every scorecard renders from.",
    schema=TemplateActivateIn,
    output=TemplateOut,
    permission="scorecard.config.manage",
    read_only=False,
    module="scorecards",
    requires_approval="config_change",
    audit="scorecard.template_activated",
    config_change=True,
    example={"template_id": EXAMPLE_ID},
)
def activate_template(params: TemplateActivateIn, ctx: ActionContext) -> TemplateOut:
    template = _template(ctx, params.template_id, lock=True)
    if not template.nodes.filter(level_no=template.level_count).exists():
        raise Conflict("A template needs at least one node at its deepest level before it is used.")
    now = timezone.now()
    ScorecardTemplate.objects.filter(org_id=ctx.org_id, status="active").exclude(
        template_id=template.template_id
    ).update(status="inactive", updated_by=ctx.user_id, updated_at=now)
    template.status = "active"
    template.updated_by = ctx.user_id
    template.updated_at = now
    template.save()
    return template_out(template)


class TemplateListIn(BaseModel):
    pass


class TemplateSummary(BaseModel):
    template_id: uuid.UUID
    name: str
    level_count: int
    status: str
    node_count: int
    placement_count: int


class TemplateListOut(BaseModel):
    templates: list[TemplateSummary]


@action(
    name="scorecard.template.list",
    summary="Scorecard templates, the active one first.",
    schema=TemplateListIn,
    output=TemplateListOut,
    permission="scorecard.config.view",
    read_only=True,
    module="scorecards",
    example={},
)
def list_templates(params: TemplateListIn, ctx: ActionContext) -> TemplateListOut:
    rows = (
        ScorecardTemplate.objects.filter(org_id=ctx.org_id)
        .annotate(
            node_count=Count("nodes", distinct=True),
            placement_count=Count("placements", distinct=True),
        )
        .order_by("name")
    )
    out = [
        TemplateSummary(
            template_id=t.template_id,
            name=t.name,
            level_count=t.level_count,
            status=t.status,
            node_count=t.node_count,
            placement_count=t.placement_count,
        )
        for t in rows
    ]
    out.sort(key=lambda t: t.status != "active")
    return TemplateListOut(templates=out)


class TemplateGetIn(BaseModel):
    # Omit for the active template.
    template_id: uuid.UUID | None = None


class TemplateGetOut(BaseModel):
    # Null when no template exists yet (the setup screen explains why).
    template: TemplateOut | None


@action(
    name="scorecard.template.get",
    summary="A template with its levels, nodes and metric placements.",
    schema=TemplateGetIn,
    output=TemplateGetOut,
    permission="scorecard.config.view",
    read_only=True,
    module="scorecards",
    example={},
)
def get_template(params: TemplateGetIn, ctx: ActionContext) -> TemplateGetOut:
    if params.template_id is not None:
        return TemplateGetOut(template=template_out(_template(ctx, params.template_id)))
    active = ScorecardTemplate.objects.filter(org_id=ctx.org_id, status="active").first()
    return TemplateGetOut(template=template_out(active) if active is not None else None)


# ── nodes ────────────────────────────────────────────────────────────────────


class NodeSaveIn(BaseModel):
    template_id: uuid.UUID
    # Omit to add a node; give it to rename or reorder one.
    node_id: uuid.UUID | None = None
    # Omit for a top-level node.
    parent_id: uuid.UUID | None = None
    label: Label
    sort_order: int = 0


@action(
    name="scorecard.node.save",
    summary="Add a node to the taxonomy, or rename or reorder one.",
    schema=NodeSaveIn,
    output=TemplateOut,
    permission="scorecard.config.manage",
    read_only=False,
    module="scorecards",
    requires_approval="config_change",
    audit="scorecard.node_saved",
    config_change=True,
    example={"template_id": EXAMPLE_ID, "label": "Grow the balance sheet"},
)
def save_node(params: NodeSaveIn, ctx: ActionContext) -> TemplateOut:
    template = _template(ctx, params.template_id, lock=True)
    parent = _node(template, params.parent_id) if params.parent_id is not None else None
    level_no = parent.level_no + 1 if parent is not None else 1
    if level_no > template.level_count:
        raise InvalidInput(
            f"The template has {template.level_count} level(s); metrics sit under its deepest "
            "nodes, not more nodes."
        )
    with conflicts(_CONFLICTS):
        if params.node_id is None:
            ScorecardNode.objects.create(
                template=template,
                level_no=level_no,
                parent=parent,
                label=params.label,
                sort_order=params.sort_order,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
        else:
            node = _node(template, params.node_id)
            if node.parent_id != (parent.node_id if parent else None):
                raise Conflict("Moving a node to another parent is not supported; add a new one.")
            node.label = params.label
            node.sort_order = params.sort_order
            node.updated_by = ctx.user_id
            node.updated_at = timezone.now()
            node.save()
    return template_out(template)


class NodeDeleteIn(BaseModel):
    template_id: uuid.UUID
    node_id: uuid.UUID


@action(
    name="scorecard.node.delete",
    summary="Remove a node that has no children and no metrics placed under it.",
    schema=NodeDeleteIn,
    output=TemplateOut,
    permission="scorecard.config.manage",
    read_only=False,
    module="scorecards",
    requires_approval="config_change",
    audit="scorecard.node_deleted",
    config_change=True,
    example={"template_id": EXAMPLE_ID, "node_id": EXAMPLE_ID},
)
def delete_node(params: NodeDeleteIn, ctx: ActionContext) -> TemplateOut:
    template = _template(ctx, params.template_id, lock=True)
    node = _node(template, params.node_id)
    if node.children.exists() or node.placements.exists():
        raise Conflict("Move or remove what is under this node first.")
    node.delete()
    return template_out(template)


# ── placements ───────────────────────────────────────────────────────────────


class PlacementSetIn(BaseModel):
    template_id: uuid.UUID
    node_id: uuid.UUID
    metric_code: MetricCode
    # Omit for the default placement every profile uses unless it has its own.
    profile_code: Code | None = None
    sort_order: int = 0


@action(
    name="scorecard.placement.set",
    summary="Place a metric under a node of the taxonomy, by default or for one profile.",
    schema=PlacementSetIn,
    output=TemplateOut,
    permission="scorecard.config.manage",
    read_only=False,
    module="scorecards",
    requires_approval="config_change",
    audit="scorecard.placement_set",
    config_change=True,
    example={"template_id": EXAMPLE_ID, "node_id": EXAMPLE_ID, "metric_code": "total_deposits"},
)
def set_placement(params: PlacementSetIn, ctx: ActionContext) -> TemplateOut:
    template = _template(ctx, params.template_id, lock=True)
    node = _node(template, params.node_id)
    if node.level_no != template.level_count:
        raise InvalidInput("Metrics sit under the deepest level of the taxonomy.")
    if not Metric.objects.filter(
        org_id=ctx.org_id, metric_code=params.metric_code, bindings__product="scorecards"
    ).exists():
        raise NotFound(f"No Scorecards metric with code '{params.metric_code}'.")
    now = timezone.now()
    with conflicts(_CONFLICTS):
        ScorecardPlacement.objects.update_or_create(
            template=template,
            metric_code=params.metric_code,
            profile_code=params.profile_code,
            defaults={
                "node": node,
                "sort_order": params.sort_order,
                "updated_by": ctx.user_id,
                "updated_at": now,
            },
            create_defaults={
                "node": node,
                "sort_order": params.sort_order,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
            },
        )
    return template_out(template)


class PlacementClearIn(BaseModel):
    template_id: uuid.UUID
    metric_code: MetricCode
    profile_code: Code | None = None


@action(
    name="scorecard.placement.clear",
    summary="Remove a metric's placement; it then shows under the default, or ungrouped.",
    schema=PlacementClearIn,
    output=TemplateOut,
    permission="scorecard.config.manage",
    read_only=False,
    module="scorecards",
    requires_approval="config_change",
    audit="scorecard.placement_cleared",
    config_change=True,
    example={"template_id": EXAMPLE_ID, "metric_code": "total_deposits"},
)
def clear_placement(params: PlacementClearIn, ctx: ActionContext) -> TemplateOut:
    template = _template(ctx, params.template_id, lock=True)
    deleted, _ = template.placements.filter(
        metric_code=params.metric_code, profile_code=params.profile_code
    ).delete()
    if not deleted:
        raise NotFound("That metric has no such placement.")
    return template_out(template)
