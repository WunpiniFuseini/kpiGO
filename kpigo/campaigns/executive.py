"""A published campaign result as an Executive figure for a reporting month (Scope §9.5, §10.1).

A campaign result published as a metric (``campaign.metric.publish``) is read
here for one reporting month, org-wide or broken down by a customer dimension
(segment, product, region, branch). Results are not materialised: each figure is
aggregated on demand from the same rows the campaign surfaces read, filtered to
the month by the date the activity is booked on.

Three result kinds map cleanly onto a month and a customer dimension, because
they come from dated, dimensioned rows:

- **attributed_value** — credited ``campaign_attribution`` for outcomes dated in
  the month, in the outcome's currency, converted to the reporting currency.
- **conversions** — distinct customers whose outcome was credited in the month.
- **winbacks_confirmed** — win-backs that qualified in the month and are
  confirmed (client-confirmed or past the retention window).

``incremental_value`` (a baseline over an event's whole span, with no per-month
or per-customer grain) and ``conversion_rate`` (an event-level control-group
measure, and contacts carry no customer dimensions) have no honest month-by-
dimension figure, so they report themselves pending rather than mislead.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum

from kpigo.campaigns import winbacks as wb
from kpigo.campaigns.models import CampaignAttribution, CampaignPublishedMetric, CampaignWinback
from kpigo.ingestion.reference import org_today
from kpigo.ingestion.validator import CUSTOMER_DIMENSIONS
from kpigo.platform.config import MissingRate, convert
from kpigo.platform.vocab import month_bounds

SUPPORTED = ("attributed_value", "conversions", "winbacks_confirmed")
COUNT_KINDS = ("conversions", "winbacks_confirmed")
DEFERRED = {
    "incremental_value": (
        "Incremental value is a baseline over a campaign's whole span, with no "
        "per-month or per-member figure, so it is not shown on the dashboard."
    ),
    "conversion_rate": (
        "Conversion rate is an event-level control-group measure and contacts carry "
        "no customer dimensions, so it is not shown on the dashboard."
    ),
}


@dataclass
class CampaignResult:
    """A campaign metric's figures for one period. ``pending`` set: nothing is computed."""

    pending: str | None = None
    # series_type -> org-level value (None when nothing stands behind it).
    org: dict[str, Decimal | None] = field(default_factory=dict)
    # series_type -> {member_code -> value}.
    members: dict[str, dict[str, Decimal | None]] = field(default_factory=dict)


def published_metric(org_id: str, metric_code: str) -> CampaignPublishedMetric | None:
    return (
        CampaignPublishedMetric.objects.filter(
            org_id=org_id, metric__metric_code=metric_code, status="active"
        )
        .select_related("campaign", "metric")
        .first()
    )


def _shift(period_key: str, months: int) -> str:
    year, month = int(period_key[:4]), int(period_key[4:])
    index = (year * 12 + (month - 1)) - months
    return f"{index // 12:04d}{index % 12 + 1:02d}"


def _period_for(period_key: str, series_type: str) -> str | None:
    if series_type == "actual":
        return period_key
    if series_type == "prior":
        return _shift(period_key, 1)
    if series_type == "prior_year":
        return _shift(period_key, 12)
    return None  # campaigns carry no target/forecast/budget


def result(
    org_id: str,
    published: CampaignPublishedMetric,
    period_key: str,
    series: list[str],
    dimension: str | None,
    member_codes: list[str],
    reporting: str | None,
) -> CampaignResult:
    kind = published.result_kind
    if kind in DEFERRED:
        return CampaignResult(pending=DEFERRED[kind])
    if kind not in SUPPORTED:  # defensive: a new kind added without a path here
        return CampaignResult(pending="This campaign result is not shown on the dashboard yet.")

    out = CampaignResult()
    column = CUSTOMER_DIMENSIONS.get(dimension or "")
    for series_type in series:
        period = _period_for(period_key, series_type)
        if period is None:
            out.org[series_type] = None
            if dimension is not None:
                out.members[series_type] = {code: None for code in member_codes}
            continue
        org_value, by_member = _figures(org_id, published, kind, period, column, reporting)
        out.org[series_type] = org_value
        if dimension is not None:
            out.members[series_type] = {code: by_member.get(code) for code in member_codes}
    return out


def _figures(
    org_id: str,
    published: CampaignPublishedMetric,
    kind: str,
    period_key: str,
    column: str | None,
    reporting: str | None,
) -> tuple[Decimal | None, dict[str, Decimal | None]]:
    first, following = month_bounds(period_key)
    campaign_id: uuid.UUID = published.campaign_id
    if kind == "winbacks_confirmed":
        return _winbacks(org_id, campaign_id, first, following, column)
    if kind == "conversions":
        return _conversions(org_id, campaign_id, first, following, column)
    return _attributed(org_id, campaign_id, first, following, column, period_key, reporting)


# ── attributed value (currency, converted to the reporting currency) ─────────


def _attributed(
    org_id: str,
    campaign_id: uuid.UUID,
    first: date,
    following: date,
    column: str | None,
    period_key: str,
    reporting: str | None,
) -> tuple[Decimal | None, dict[str, Decimal | None]]:
    base = CampaignAttribution.objects.filter(
        org_id=org_id,
        event__campaign_id=campaign_id,
        credited=True,
        outcome__activity_date__gte=first,
        outcome__activity_date__lt=following,
    )
    org_rows = base.values("outcome__currency_code").annotate(v=Sum("attributed_value"))
    org_value = _convert_sum(
        org_id, [(r["outcome__currency_code"], r["v"]) for r in org_rows], reporting, period_key
    )
    members: dict[str, Decimal | None] = {}
    if column is not None:
        rows = base.values(f"outcome__{column}", "outcome__currency_code").annotate(
            v=Sum("attributed_value")
        )
        buckets: dict[str, list[tuple[str | None, Decimal | None]]] = {}
        for r in rows:
            code = r[f"outcome__{column}"]
            if code:
                buckets.setdefault(code, []).append((r["outcome__currency_code"], r["v"]))
        members = {
            code: _convert_sum(org_id, pairs, reporting, period_key)
            for code, pairs in buckets.items()
        }
    return org_value, members


def _convert_sum(
    org_id: str,
    pairs: list[tuple[str | None, Decimal | None]],
    reporting: str | None,
    period_key: str,
) -> Decimal | None:
    """Sum currency buckets into the reporting currency; a bucket with no rate is dropped."""
    total = Decimal(0)
    seen = False
    for currency, amount in pairs:
        if amount is None:
            continue
        if reporting and currency and currency != reporting:
            try:
                amount = convert(org_id, amount, currency, reporting, period_key)
            except MissingRate:
                continue
        total += amount
        seen = True
    return total if seen else None


# ── counts (conversions, confirmed win-backs) ────────────────────────────────


def _conversions(
    org_id: str, campaign_id: uuid.UUID, first: date, following: date, column: str | None
) -> tuple[Decimal | None, dict[str, Decimal | None]]:
    base = CampaignAttribution.objects.filter(
        org_id=org_id,
        event__campaign_id=campaign_id,
        credited=True,
        outcome__activity_date__gte=first,
        outcome__activity_date__lt=following,
    )
    org_value = base.aggregate(n=Count("outcome__customer_ref", distinct=True))["n"]
    counts: dict[str, int] = {}
    if column is not None:
        rows = base.values(f"outcome__{column}").annotate(
            n=Count("outcome__customer_ref", distinct=True)
        )
        counts = {r[f"outcome__{column}"]: r["n"] for r in rows if r[f"outcome__{column}"]}
    return (_as_count(org_value), {k: _as_count(v) for k, v in counts.items()})


def _winbacks(
    org_id: str, campaign_id: uuid.UUID, first: date, following: date, column: str | None
) -> tuple[Decimal | None, dict[str, Decimal | None]]:
    days = wb.retention_days(org_id)
    base = CampaignWinback.objects.filter(
        wb.confirmed_by(days, org_today(org_id)),
        org_id=org_id,
        event__campaign_id=campaign_id,
        qualified_at__gte=first,
        qualified_at__lt=following,
    )
    org_value = base.count()
    counts: dict[str, int] = {}
    if column is not None:
        rows = base.values(column).annotate(n=Count("winback_id"))
        counts = {r[column]: r["n"] for r in rows if r[column]}
    return (_as_count(org_value), {k: _as_count(v) for k, v in counts.items()})


def _as_count(n: int | None) -> Decimal | None:
    """A count is a figure; 0 is a real zero here (the rows are present), None is absent."""
    return None if n is None else Decimal(n)
