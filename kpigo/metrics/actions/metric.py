"""Metric registry actions (PRD §6.1, MR-1 … MR-9)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date
from typing import Annotated, Any, Literal

from django.db.models import Q, QuerySet
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.metrics.models import (
    DEFINITIONAL_FIELDS,
    METRIC_CODE_PATTERN,
    Aggregation,
    CollectionMethod,
    Direction,
    Metric,
    MetricBinding,
    MetricFamily,
    MetricProfileAssignment,
    MetricStatus,
    TargetScope,
    Unit,
)
from kpigo.metrics.naming import (
    code_from_name,
    exact_family,
    normalise_name,
    similar_families,
)
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import MANUAL_INPUT_PRODUCTS, Code, Product

MetricCode = Annotated[str, StringConstraints(pattern=METRIC_CODE_PATTERN, max_length=80)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

# Allowed status changes (App Flow §8: draft → active → inactive → deprecated).
# Reactivation is allowed; nothing returns to draft and nothing is deleted (MR-6).
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active"}),
    "active": frozenset({"inactive"}),
    "inactive": frozenset({"active", "deprecated"}),
    "deprecated": frozenset(),
}

_CONFLICTS = {
    "metric_family_name_unique": "A metric with this name already exists.",
    "metric_code_no_overlap": "Another metric already uses this code for an overlapping period.",
    "metric_profile_no_overlap": "This metric is already assigned to that profile for an overlapping period.",
}


def _today() -> date:
    return timezone.localdate()


def _check_manual_input(method: str, products: Iterable[str]) -> None:
    if method == "manual_input" and not set(products) <= MANUAL_INPUT_PRODUCTS:
        raise InvalidInput(
            "Manual input is available only for Scorecards and Executive metrics.",
            detail={"products": sorted(set(products) - MANUAL_INPUT_PRODUCTS)},
        )


# ── outputs ──────────────────────────────────────────────────────────────────


class MetricBindingOut(BaseModel):
    product: str
    is_active: bool


class ProfileOut(BaseModel):
    profile_code: str
    product: str
    effective_from: date
    effective_to: date | None


class MetricOut(BaseModel):
    metric_id: uuid.UUID
    family_id: uuid.UUID
    metric_code: str
    display_name: str
    direction: str
    aggregation: str
    unit: str
    decimal_places: int
    is_percentage: bool
    target_scope: str
    collection_method: str
    status: str
    computation_note: str
    effective_from: date
    effective_to: date | None
    supersedes_id: uuid.UUID | None
    bindings: list[MetricBindingOut] = []
    profiles: list[ProfileOut] = []

    @classmethod
    def of(cls, metric: Metric) -> MetricOut:
        fields = {
            name: getattr(metric, name)
            for name in cls.model_fields
            if name not in ("bindings", "profiles")
        }
        return cls(
            **fields,
            bindings=[
                MetricBindingOut(product=b.product, is_active=b.is_active)
                for b in metric.bindings.order_by("product")
            ],
            profiles=[
                ProfileOut(
                    profile_code=p.profile_code,
                    product=p.product,
                    effective_from=p.effective_from,
                    effective_to=p.effective_to,
                )
                for p in metric.profiles.order_by("profile_code", "product", "effective_from")
            ],
        )


class FamilyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    family_id: uuid.UUID
    display_name: str
    normalised_name: str
    description: str


class SimilarMetric(BaseModel):
    metric_code: str
    unit: str
    direction: str
    aggregation: str
    status: str
    products: list[str]


class SimilarFamily(BaseModel):
    """One row of the comparison panel shown before a near-duplicate is registered."""

    family_id: uuid.UUID
    display_name: str
    normalised_name: str
    similarity: float
    metrics: list[SimilarMetric]


def _similar(org_id: str, normalised: str) -> list[SimilarFamily]:
    out: list[SimilarFamily] = []
    for family, score in similar_families(org_id, normalised):
        current = _current_rows(family.metrics.all())
        out.append(
            SimilarFamily(
                family_id=family.family_id,
                display_name=family.display_name,
                normalised_name=family.normalised_name,
                similarity=score,
                metrics=[
                    SimilarMetric(
                        metric_code=m.metric_code,
                        unit=m.unit,
                        direction=m.direction,
                        aggregation=m.aggregation,
                        status=m.status,
                        products=sorted(b.product for b in m.bindings.all()),
                    )
                    for m in current
                ],
            )
        )
    return out


def _current_rows(rows: QuerySet[Metric]) -> list[Metric]:
    return list(rows.filter(effective_to__isnull=True).order_by("metric_code"))


def _current(org_id: str, metric_code: str, *, lock: bool = False) -> Metric:
    rows = Metric.objects.filter(org_id=org_id, metric_code=metric_code, effective_to__isnull=True)
    if lock:
        rows = rows.select_for_update()
    found = rows.first()
    if found is None:
        raise NotFound(f"No current metric with code '{metric_code}'.")
    return found


# ── metric.check_name ───────────────────────────────────────────────────────


class CheckNameIn(BaseModel):
    display_name: Name


class CheckNameOut(BaseModel):
    normalised_name: str
    exact: FamilyOut | None
    similar: list[SimilarFamily]


@action(
    name="metric.check_name",
    summary="Check a proposed metric name for duplicates and similar existing metrics.",
    schema=CheckNameIn,
    output=CheckNameOut,
    permission="metric.view",
    read_only=True,
    example={"display_name": "Total deposits"},
)
def check_name(params: CheckNameIn, ctx: ActionContext) -> CheckNameOut:
    normalised = normalise_name(params.display_name)
    exact = exact_family(ctx.org_id, normalised)
    return CheckNameOut(
        normalised_name=normalised,
        exact=FamilyOut.model_validate(exact) if exact is not None else None,
        similar=_similar(ctx.org_id, normalised),
    )


# ── metric.register ─────────────────────────────────────────────────────────


class RegisterIn(BaseModel):
    display_name: Name
    metric_code: MetricCode | None = None
    description: str = Field(default="", max_length=4000)
    direction: Direction
    aggregation: Aggregation
    unit: Unit
    decimal_places: int = Field(default=0, ge=0, le=6)
    is_percentage: bool = False
    target_scope: TargetScope = "profile"
    collection_method: CollectionMethod = "feed"
    computation_note: str = Field(default="", max_length=4000)
    products: list[Product] = Field(min_length=1)
    # Advanced (MR-4): one metric per product in the family instead of one shared metric.
    fork_per_product: bool = False
    status: Literal["draft", "active"] = "active"
    effective_from: date | None = None
    # Set after reviewing the comparison panel to register despite similar names.
    acknowledge_similar: bool = False

    @model_validator(mode="after")
    def _distinct_products(self) -> RegisterIn:
        if len(set(self.products)) != len(self.products):
            raise ValueError("products must not repeat")
        return self


class RegisterOut(BaseModel):
    family: FamilyOut
    metrics: list[MetricOut]
    acknowledged_similar: list[SimilarFamily]


@action(
    name="metric.register",
    summary="Register a metric: its family, definition and product bindings.",
    schema=RegisterIn,
    output=RegisterOut,
    permission="metric.manage",
    read_only=False,
    requires_approval="metric_change",
    audit="metric.registered",
    config_change=True,
    example={
        "display_name": "Total deposits",
        "direction": "higher_is_better",
        "aggregation": "sum",
        "unit": "currency",
        "decimal_places": 2,
        "products": ["scorecards", "executive"],
        "computation_note": "Month-end deposit balance per RM portfolio.",
    },
)
def register(params: RegisterIn, ctx: ActionContext) -> RegisterOut:
    _check_manual_input(params.collection_method, params.products)
    normalised = normalise_name(params.display_name)
    if not normalised:
        raise InvalidInput("A metric name needs at least one letter or digit.")
    existing = exact_family(ctx.org_id, normalised)
    if existing is not None:
        raise Conflict(
            f"A metric named '{existing.display_name}' already exists.",
            detail={"family_id": str(existing.family_id), "normalised_name": normalised},
        )
    similar = _similar(ctx.org_id, normalised)
    if similar and not params.acknowledge_similar:
        raise Conflict(
            "Similar metrics already exist. Review them, then register again with "
            "acknowledge_similar set if this is genuinely a different metric.",
            detail={"similar": [s.model_dump(mode="json") for s in similar]},
        )

    base_code = params.metric_code or code_from_name(normalised)
    if params.fork_per_product:
        codes = {product: f"{base_code}_{product}" for product in params.products}
    else:
        codes = {product: base_code for product in params.products}
    taken = (
        Metric.objects.filter(org_id=ctx.org_id, metric_code__in=set(codes.values()))
        .values_list("metric_code", flat=True)
        .distinct()
    )
    if taken:
        raise Conflict(
            "Metric code already in use; choose another metric_code.",
            detail={"metric_codes": sorted(taken)},
        )

    effective_from = params.effective_from or _today()
    with conflicts(_CONFLICTS):
        family = MetricFamily.objects.create(
            org_id=ctx.org_id,
            display_name=params.display_name,
            normalised_name=normalised,
            description=params.description,
            owner_user_id=ctx.user_id,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        created: list[Metric] = []
        for code in dict.fromkeys(codes.values()):
            metric = Metric.objects.create(
                org_id=ctx.org_id,
                family=family,
                metric_code=code,
                display_name=params.display_name,
                direction=params.direction,
                aggregation=params.aggregation,
                unit=params.unit,
                decimal_places=params.decimal_places,
                is_percentage=params.is_percentage,
                target_scope=params.target_scope,
                collection_method=params.collection_method,
                status=params.status,
                computation_note=params.computation_note,
                effective_from=effective_from,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
            )
            for product, product_code in codes.items():
                if product_code == code:
                    MetricBinding.objects.create(
                        metric=metric, product=product, created_by=ctx.user_id
                    )
            created.append(metric)
    if similar:
        ctx.audit(
            "metric.similar_acknowledged",
            family_id=str(family.family_id),
            similar=[str(s.family_id) for s in similar],
        )
    return RegisterOut(
        family=FamilyOut.model_validate(family),
        metrics=[MetricOut.of(m) for m in created],
        acknowledged_similar=similar,
    )


# ── metric.update ───────────────────────────────────────────────────────────


class MetricUpdateIn(BaseModel):
    metric_code: MetricCode
    display_name: Name | None = None
    decimal_places: int | None = Field(default=None, ge=0, le=6)
    is_percentage: bool | None = None
    computation_note: str | None = Field(default=None, max_length=4000)
    collection_method: CollectionMethod | None = None
    # Definitional: on an active metric these open a new effective period (MR-7).
    direction: Direction | None = None
    aggregation: Aggregation | None = None
    unit: Unit | None = None
    target_scope: TargetScope | None = None
    # First day the new definition applies. Defaults to today.
    effective_from: date | None = None


class UpdateOut(BaseModel):
    metric: MetricOut
    new_period: bool
    superseded: MetricOut | None = None


@action(
    name="metric.update",
    summary="Change a metric. Definitional changes open a new effective period.",
    schema=MetricUpdateIn,
    output=UpdateOut,
    permission="metric.manage",
    read_only=False,
    requires_approval="metric_change",
    audit="metric.updated",
    config_change=True,
    example={"metric_code": "total_deposits", "unit": "count", "effective_from": "2026-11-01"},
)
def update(params: MetricUpdateIn, ctx: ActionContext) -> UpdateOut:
    current = _current(ctx.org_id, params.metric_code, lock=True)
    changes: dict[str, Any] = {
        k: v
        for k, v in params.model_dump(exclude={"metric_code", "effective_from"}).items()
        if v is not None and getattr(current, k) != v
    }
    if not changes:
        raise InvalidInput("Nothing to change.")
    if "collection_method" in changes:
        _check_manual_input(
            changes["collection_method"],
            list(current.bindings.values_list("product", flat=True)),
        )
    definitional = sorted(set(changes) & set(DEFINITIONAL_FIELDS))
    versioned = bool(definitional) and current.status != "draft"
    if params.effective_from is not None and not versioned:
        raise InvalidInput(
            "effective_from applies only to a definitional change "
            f"({', '.join(DEFINITIONAL_FIELDS)}) on a non-draft metric."
        )

    if not versioned:
        for field, value in changes.items():
            setattr(current, field, value)
        current.updated_by = ctx.user_id
        current.updated_at = timezone.now()
        current.save()
        return UpdateOut(metric=MetricOut.of(current), new_period=False)

    starts = params.effective_from or _today()
    if starts <= current.effective_from:
        raise Conflict(
            "A definitional change must start after the current period began "
            f"({current.effective_from}); history is never rewritten.",
            detail={"current_effective_from": str(current.effective_from)},
        )
    with conflicts(_CONFLICTS):
        current.effective_to = starts
        current.updated_by = ctx.user_id
        current.updated_at = timezone.now()
        current.save(update_fields=["effective_to", "updated_by", "updated_at"])
        successor = Metric.objects.create(
            org_id=current.org_id,
            family_id=current.family_id,
            metric_code=current.metric_code,
            display_name=changes.get("display_name", current.display_name),
            direction=changes.get("direction", current.direction),
            aggregation=changes.get("aggregation", current.aggregation),
            unit=changes.get("unit", current.unit),
            decimal_places=changes.get("decimal_places", current.decimal_places),
            is_percentage=changes.get("is_percentage", current.is_percentage),
            target_scope=changes.get("target_scope", current.target_scope),
            collection_method=changes.get("collection_method", current.collection_method),
            status=current.status,
            computation_note=changes.get("computation_note", current.computation_note),
            effective_from=starts,
            supersedes=current,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        for binding in current.bindings.all():
            MetricBinding.objects.create(
                metric=successor,
                product=binding.product,
                is_active=binding.is_active,
                created_by=ctx.user_id,
            )
        # Profile assignments still running carry over to the new period.
        running = current.profiles.filter(Q(effective_to__isnull=True) | Q(effective_to__gt=starts))
        for profile in running:
            MetricProfileAssignment.objects.create(
                metric=successor,
                profile_code=profile.profile_code,
                product=profile.product,
                effective_from=max(profile.effective_from, starts),
                effective_to=profile.effective_to,
                created_by=ctx.user_id,
            )
        running.filter(effective_from__gte=starts).delete()
        running.update(effective_to=starts)
    ctx.audit(
        "metric.period_opened",
        metric_code=current.metric_code,
        changed=definitional,
        superseded_id=str(current.metric_id),
        metric_id=str(successor.metric_id),
        effective_from=str(starts),
    )
    return UpdateOut(
        metric=MetricOut.of(successor), new_period=True, superseded=MetricOut.of(current)
    )


# ── metric.set_status ───────────────────────────────────────────────────────


class SetStatusIn(BaseModel):
    metric_code: MetricCode
    status: MetricStatus


@action(
    name="metric.set_status",
    summary="Activate, deactivate or deprecate a metric. Nothing is deleted.",
    schema=SetStatusIn,
    output=MetricOut,
    permission="metric.manage",
    read_only=False,
    requires_approval="metric_change",
    audit="metric.status_changed",
    config_change=True,
    example={"metric_code": "total_deposits", "status": "inactive"},
)
def set_status(params: SetStatusIn, ctx: ActionContext) -> MetricOut:
    current = _current(ctx.org_id, params.metric_code, lock=True)
    if params.status not in TRANSITIONS[current.status]:
        raise Conflict(
            f"A {current.status} metric cannot become {params.status}.",
            detail={"allowed": sorted(TRANSITIONS[current.status])},
        )
    current.status = params.status
    current.updated_by = ctx.user_id
    current.updated_at = timezone.now()
    current.save(update_fields=["status", "updated_by", "updated_at"])
    return MetricOut.of(current)


# ── metric.binding.set ──────────────────────────────────────────────────────


class BindingIn(BaseModel):
    metric_code: MetricCode
    product: Product
    is_active: bool = True


@action(
    name="metric.binding.set",
    summary="Bind a metric to a product, or switch an existing binding on or off.",
    schema=BindingIn,
    output=MetricOut,
    permission="metric.manage",
    read_only=False,
    requires_approval="metric_change",
    audit="metric.binding_set",
    config_change=True,
    example={"metric_code": "total_deposits", "product": "agent_sales"},
)
def set_binding(params: BindingIn, ctx: ActionContext) -> MetricOut:
    current = _current(ctx.org_id, params.metric_code, lock=True)
    if params.is_active:
        _check_manual_input(current.collection_method, [params.product])
    updated = MetricBinding.objects.filter(metric=current, product=params.product).update(
        is_active=params.is_active
    )
    if not updated:
        MetricBinding.objects.create(
            metric=current,
            product=params.product,
            is_active=params.is_active,
            created_by=ctx.user_id,
        )
    return MetricOut.of(current)


# ── metric.profile.assign ───────────────────────────────────────────────────


class ProfileAssignIn(BaseModel):
    metric_code: MetricCode
    profile_code: Code
    product: Product
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def _range(self) -> ProfileAssignIn:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


@action(
    name="metric.profile.assign",
    summary="Show a metric for a profile in a product, for an effective period.",
    schema=ProfileAssignIn,
    output=MetricOut,
    permission="metric.manage",
    read_only=False,
    requires_approval="metric_change",
    audit="metric.profile_assigned",
    config_change=True,
    example={
        "metric_code": "total_deposits",
        "profile_code": "retail_rm",
        "product": "scorecards",
        "effective_from": "2026-11-01",
    },
)
def assign_profile(params: ProfileAssignIn, ctx: ActionContext) -> MetricOut:
    current = _current(ctx.org_id, params.metric_code)
    if not current.bindings.filter(product=params.product, is_active=True).exists():
        raise Conflict(f"'{params.metric_code}' is not bound to {params.product}.")
    if params.effective_from < current.effective_from:
        raise Conflict(
            "A profile assignment cannot start before the metric's current period "
            f"({current.effective_from})."
        )
    with conflicts(_CONFLICTS):
        MetricProfileAssignment.objects.create(
            metric=current,
            profile_code=params.profile_code,
            product=params.product,
            effective_from=params.effective_from,
            effective_to=params.effective_to,
            created_by=ctx.user_id,
        )
    return MetricOut.of(current)


# ── metric.list / metric.get ────────────────────────────────────────────────


class MetricListIn(BaseModel):
    product: Product | None = None
    status: MetricStatus | None = None
    # The date whose definitions to list. Defaults to today.
    as_of: date | None = None


class MetricListOut(BaseModel):
    as_of: date
    metrics: list[MetricOut]


@action(
    name="metric.list",
    summary="List metric definitions in force on a date.",
    schema=MetricListIn,
    output=MetricListOut,
    permission="metric.view",
    read_only=True,
    example={"product": "scorecards"},
)
def list_metrics(params: MetricListIn, ctx: ActionContext) -> MetricListOut:
    as_of = params.as_of or _today()
    rows = Metric.objects.filter(org_id=ctx.org_id, effective_from__lte=as_of).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gt=as_of)
    )
    if params.status is not None:
        rows = rows.filter(status=params.status)
    if params.product is not None:
        rows = rows.filter(bindings__product=params.product, bindings__is_active=True)
    return MetricListOut(
        as_of=as_of, metrics=[MetricOut.of(m) for m in rows.order_by("metric_code")]
    )


class MetricGetIn(BaseModel):
    metric_code: MetricCode


class GetOut(BaseModel):
    family: FamilyOut
    # Every effective period, oldest first: the definition's full history.
    periods: list[MetricOut]


@action(
    name="metric.get",
    summary="A metric's family and every effective period of its definition.",
    schema=MetricGetIn,
    output=GetOut,
    permission="metric.view",
    read_only=True,
    example={"metric_code": "total_deposits"},
)
def get_metric(params: MetricGetIn, ctx: ActionContext) -> GetOut:
    rows = list(
        Metric.objects.filter(org_id=ctx.org_id, metric_code=params.metric_code)
        .select_related("family")
        .order_by("effective_from")
    )
    if not rows:
        raise NotFound(f"No metric with code '{params.metric_code}'.")
    return GetOut(
        family=FamilyOut.model_validate(rows[0].family),
        periods=[MetricOut.of(m) for m in rows],
    )
