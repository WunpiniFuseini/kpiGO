"""The figures behind an Executive widget (Scope §10.1–10.3, PRD EX-1–EX-4).

A widget's metrics are drawn in one of two ways here:

- **independent** — pre-shaped by the DE team into ``fact_widget_data`` and read
  as it is (TDD §10: a direct read, keyed by ``widget_key``). The row carries the
  series and, when the widget breaks down, the dimension member.
- **roll-up** — aggregated from the Scorecards store (``fact_actual_monthly``) up a
  dimension, over **distinct subjects** and never a sum of subordinate totals
  (§10.1), using the metric's declared aggregation and converting each subject's
  currency to the reporting currency. Absent is never zero: a subject with no row
  contributes nothing, exactly as in scoring.

A **campaign** result published as a metric is computed from the campaign engine
in a later step; here it reports itself pending rather than guessing.

``prior`` and ``prior_year`` are derived by reading the actual at the shifted
period, so a client need not feed them; a fed value (independent) wins if present.
``target``, ``forecast`` and ``budget`` are shown where the data carries them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Q

from kpigo.hierarchy.models import Assignment, DimMember
from kpigo.ingestion.models import FactActualMonthly, FactWidgetData
from kpigo.metrics.models import Metric
from kpigo.platform.config import MissingRate, convert, org_settings
from kpigo.scorecards.models import Target

# The comparison series kpiGo computes itself from the actual at a shifted period.
DERIVED_SERIES = ("prior", "prior_year")
# The series read straight from the data (a fed row, or a rolled-up target row).
STORED_SERIES = ("actual", "target", "forecast", "budget")
# Which dimension columns a subject's assignment carries, for roll-up placement.
ROLLUP_DIMENSIONS = ("branch", "region", "segment")
# Aggregations a distinct-subject roll-up is defined for; others belong to a feed.
ROLLUP_SUM = frozenset({"sum", "count"})
ROLLUP_MEAN = frozenset({"average"})


def shift_period(period_key: str, *, months: int) -> str:
    year, month = int(period_key[:4]), int(period_key[4:])
    index = (year * 12 + (month - 1)) - months
    return f"{index // 12:04d}{index % 12 + 1:02d}"


def prior_period(period_key: str, series_type: str) -> str | None:
    if series_type == "prior":
        return shift_period(period_key, months=1)
    if series_type == "prior_year":
        return shift_period(period_key, months=12)
    return None


@dataclass
class SeriesFigure:
    series_type: str
    value: Decimal | None
    currency: str | None = None
    # Subjects a roll-up actually summed, and those dropped for a missing FX rate.
    subjects: int | None = None
    skipped_no_fx: int = 0


@dataclass
class MetricFigures:
    metric_code: str
    source: str
    # None → an org-level figure in ``org``; a dimension → one entry per member.
    members: dict[str, dict[str, SeriesFigure]] = field(default_factory=dict)
    org: dict[str, SeriesFigure] = field(default_factory=dict)
    pending: str | None = None
    # The latest feed run behind an independent read, for the provenance panel.
    run_id: str | None = None


# ── membership: which subjects sit under a dimension member ──────────────────


def _descendants(org_id: str, dimension: str, codes: set[str]) -> set[str]:
    children: dict[str, list[str]] = defaultdict(list)
    for code, parent in DimMember.objects.filter(
        org_id=org_id, dimension_type=dimension, parent_code__isnull=False
    ).values_list("member_code", "parent_code"):
        children[str(parent)].append(str(code))
    found = set(codes)
    frontier = list(codes)
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in found:
                found.add(child)
                frontier.append(child)
    return found


def _last_day(period_key: str) -> date:
    from kpigo.platform.vocab import month_bounds

    _, following = month_bounds(period_key)
    return date.fromordinal(following.toordinal() - 1)


def subjects_by_member(
    org_id: str, dimension: str, members: list[str], period_key: str
) -> dict[str, set[str]]:
    """Distinct subject ids under each named member (itself plus its descendants)."""
    if dimension not in ROLLUP_DIMENSIONS:
        return {m: set() for m in members}
    reach = {m: _descendants(org_id, dimension, {m}) for m in members}
    column = f"{dimension}_code"
    everything = set().union(*reach.values()) if reach else set()
    placed: dict[str, set[str]] = {m: set() for m in members}
    rows = (
        Assignment.objects.filter(org_id=org_id, subject__status="active")
        .filter(
            effective_from__lte=_last_day(period_key),
            **{f"{column}__in": sorted(everything)},
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=_last_day(period_key)))
        .values_list("subject_id", column)
    )
    for subject_id, code in rows:
        for member, within in reach.items():
            if code in within:
                placed[member].add(str(subject_id))
    return placed


def all_subjects(org_id: str, period_key: str) -> set[str]:
    last = _last_day(period_key)
    return {
        str(s)
        for s in Assignment.objects.filter(org_id=org_id, subject__status="active")
        .filter(effective_from__lte=last)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=last))
        .values_list("subject_id", flat=True)
    }


# ── roll-up of a distinct-subject figure ─────────────────────────────────────


def _aggregate(
    org_id: str,
    metric: Metric,
    subject_ids: set[str],
    period_key: str,
    *,
    values: dict[str, tuple[Decimal, str | None]],
    reporting: str | None,
) -> SeriesFigure:
    """Combine distinct subjects' values with the metric's aggregation, FX-converted."""
    kept: list[Decimal] = []
    skipped = 0
    for subject_id in subject_ids:
        pair = values.get(subject_id)
        if pair is None:
            continue  # absent is not zero
        amount, currency = pair
        if reporting and currency and currency != reporting:
            try:
                amount = convert(org_id, amount, currency, reporting, period_key)
            except MissingRate:
                skipped += 1
                continue
        kept.append(amount)
    if not kept:
        return SeriesFigure("", None, reporting, subjects=0, skipped_no_fx=skipped)
    if metric.aggregation in ROLLUP_MEAN:
        total = sum(kept, Decimal(0)) / Decimal(len(kept))
    elif metric.aggregation in ROLLUP_SUM:
        total = sum(kept, Decimal(0))
    else:
        return SeriesFigure("", None, reporting, subjects=len(kept), skipped_no_fx=skipped)
    return SeriesFigure("", total, reporting, subjects=len(kept), skipped_no_fx=skipped)


def _subject_actuals(
    org_id: str, metric: Metric, subject_ids: set[str], period_key: str
) -> dict[str, tuple[Decimal, str | None]]:
    rows = FactActualMonthly.objects.filter(
        org_id=org_id, metric=metric, period_key=period_key, subject_id__in=sorted(subject_ids)
    ).values_list("subject_id", "actual_value", "currency_code")
    return {str(s): (v, c) for s, v, c in rows}


def _subject_targets(
    org_id: str, metric: Metric, subject_ids: set[str], period_key: str, series_type: str
) -> dict[str, tuple[Decimal, str | None]]:
    """Each subject's published target, resolving a subject-scoped row over a profile one."""
    profiles = {
        str(s): p
        for s, p in Assignment.objects.filter(
            org_id=org_id,
            subject_id__in=sorted(subject_ids),
            effective_from__lte=_last_day(period_key),
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=_last_day(period_key)))
        .values_list("subject_id", "profile_code")
    }
    profile_codes = sorted(set(profiles.values()))
    rows = Target.objects.filter(
        org_id=org_id,
        metric=metric,
        period_key=period_key,
        series_type=series_type,
        product_line_code="",
        state="published",
    ).filter(
        Q(scope_type="subject", scope_code__in=[str(s) for s in subject_ids])
        | Q(scope_type="profile", scope_code__in=profile_codes)
    )
    by_subject: dict[str, tuple[Decimal, str | None]] = {}
    by_profile: dict[str, tuple[Decimal, str | None]] = {}
    for t in rows:
        if t.scope_type == "subject":
            by_subject[t.scope_code] = (t.target_value, t.currency_code)
        else:
            by_profile[t.scope_code] = (t.target_value, t.currency_code)
    out: dict[str, tuple[Decimal, str | None]] = {}
    for subject_id in subject_ids:
        if subject_id in by_subject:
            out[subject_id] = by_subject[subject_id]
        else:
            found = by_profile.get(profiles.get(subject_id, ""))
            if found is not None:
                out[subject_id] = found
    return out


def _rollup_values(
    org_id: str, metric: Metric, subject_ids: set[str], period_key: str, series_type: str
) -> dict[str, tuple[Decimal, str | None]]:
    if series_type in STORED_SERIES and series_type != "actual":
        return _subject_targets(org_id, metric, subject_ids, period_key, series_type)
    return _subject_actuals(org_id, metric, subject_ids, period_key)


def rollup_series(
    org_id: str,
    metric: Metric,
    subject_ids: set[str],
    period_key: str,
    series_type: str,
    reporting: str | None,
) -> SeriesFigure:
    source_period = prior_period(period_key, series_type) or period_key
    kind = "actual" if series_type in DERIVED_SERIES else series_type
    values = _rollup_values(org_id, metric, subject_ids, source_period, kind)
    figure = _aggregate(
        org_id, metric, subject_ids, source_period, values=values, reporting=reporting
    )
    figure.series_type = series_type
    return figure


# ── independent read of fact_widget_data ─────────────────────────────────────


def independent_rows(
    org_id: str, widget_key: str, metric: Metric, period_key: str
) -> dict[tuple[str, str, str], FactWidgetData]:
    """``(dimension_type, member_code, series_type)`` is unique; keyed for lookup."""
    found: dict[tuple[str, str, str], FactWidgetData] = {}
    for row in FactWidgetData.objects.filter(
        org_id=org_id, widget_key=widget_key, metric=metric, period_key=period_key
    ):
        found[(row.dimension_type, row.member_code, row.series_type)] = row
    return found


def independent_series(
    org_id: str,
    widget_key: str,
    metric: Metric,
    period_key: str,
    dimension_type: str,
    member_code: str,
    series_type: str,
    reporting: str | None,
) -> SeriesFigure:
    source_period = prior_period(period_key, series_type) or period_key
    kind = "actual" if series_type in DERIVED_SERIES else series_type
    rows = independent_rows(org_id, widget_key, metric, source_period)
    row = rows.get((dimension_type, member_code, kind))
    if row is None:
        return SeriesFigure(series_type, None, reporting)
    value: Decimal | None = row.value
    currency = row.currency_code
    if reporting and currency and currency != reporting:
        try:
            value = convert(org_id, row.value, currency, reporting, source_period)
            currency = reporting
        except MissingRate:
            value = None
    return SeriesFigure(series_type, value, currency)


def reporting_currency(org_id: str) -> str | None:
    return org_settings(org_id).reporting_currency
