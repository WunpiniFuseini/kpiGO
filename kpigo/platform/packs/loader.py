"""Applying a starter pack to an org: additive and non-destructive (Starter Packs doc §1.2, §8).

``apply_pack`` creates the pack's metrics that are not already in the registry and, when the
org is still on the default rating bands, adopts the pack's bands. It never overwrites: a
metric whose code is taken is left exactly as it is and reported as a skip, and custom bands
are never replaced. The metric becomes the client's own registry entry with no link back to
the pack. Taxonomy objectives, product lines, widgets and campaign defaults are carried on the
pack for the client to review and adopt through the workbench; this loader touches only the
registry pieces it can apply without any risk to existing work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from kpigo.action import ActionContext
from kpigo.metrics.models import Metric, MetricBinding, MetricFamily
from kpigo.metrics.naming import code_from_name, exact_family, normalise_name
from kpigo.platform.packs import Pack, PackMetric
from kpigo.platform.vocab import MANUAL_INPUT_PRODUCTS
from kpigo.scorecards.models import RatingBand


@dataclass
class PackReport:
    pack_key: str
    pack_version: str
    metrics_created: list[str] = field(default_factory=list)
    metrics_skipped: list[str] = field(default_factory=list)  # code already in the registry
    name_conflicts: list[str] = field(default_factory=list)  # a different metric owns the name
    bands_applied: bool = False
    bands_note: str = ""


def _create_metric(ctx: ActionContext, m: PackMetric, report: PackReport) -> None:
    if Metric.objects.filter(org_id=ctx.org_id, metric_code=m.code).exists():
        report.metrics_skipped.append(m.code)
        return
    if m.collection == "manual_input" and not set(m.products) <= MANUAL_INPUT_PRODUCTS:
        # A malformed pack, not a client conflict: refuse rather than write a bad metric.
        raise ValueError(f"{m.code}: manual input is only for Scorecards and Executive metrics.")
    normalised = normalise_name(m.name)
    if exact_family(ctx.org_id, normalised) is not None:
        # The client already has a metric by this name; leave theirs untouched.
        report.name_conflicts.append(m.code)
        return
    family = MetricFamily.objects.create(
        org_id=ctx.org_id,
        display_name=m.name,
        normalised_name=normalised,
        description="",
        owner_user_id=ctx.user_id,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    metric = Metric.objects.create(
        org_id=ctx.org_id,
        family=family,
        metric_code=m.code or code_from_name(normalised),
        display_name=m.name,
        direction=m.direction,
        aggregation=m.aggregation,
        unit=m.unit,
        decimal_places=m.decimal_places,
        is_percentage=m.is_percentage,
        target_scope=m.target_scope,
        collection_method=m.collection,
        status="active",
        computation_note="",
        effective_from=timezone.now().date(),
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    for product in m.products:
        MetricBinding.objects.create(metric=metric, product=product, created_by=ctx.user_id)
    report.metrics_created.append(m.code)


def _apply_bands(ctx: ActionContext, pack: Pack, report: PackReport) -> None:
    if not pack.bands:
        report.bands_note = "The pack defines no rating bands."
        return
    if RatingBand.objects.filter(org_id=ctx.org_id, product="scorecards").exists():
        report.bands_note = "Custom rating bands are already set; left as they are."
        return
    for order, band in enumerate(sorted(pack.bands, key=lambda b: b.threshold)):
        RatingBand.objects.create(
            org_id=ctx.org_id,
            product="scorecards",
            label=band.label,
            threshold=band.threshold,
            ramp_position=band.ramp_position,
            sort_order=order,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    report.bands_applied = True
    report.bands_note = f"Adopted the pack's {len(pack.bands)} rating bands."


def apply_pack(ctx: ActionContext, pack: Pack) -> PackReport:
    """Add the pack's metrics and bands that are safe to add. Does no database writes of its
    own beyond those; the caller's transaction (and dry-run rollback) governs persistence."""
    report = PackReport(pack_key=pack.key, pack_version=pack.version)
    for metric in pack.metrics:
        _create_metric(ctx, metric, report)
    _apply_bands(ctx, pack, report)
    return report
