"""Reference data for the gates, read from the registry, hierarchy and calendar.

The same ``Reference`` is what ``feed.contract.export`` hands a client's DE team
for the standalone validator, so a load checked there is checked here the same way.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date

from django.db.models import Avg, Q
from django.utils import timezone

from kpigo.campaigns.models import Campaign, CampaignEvent
from kpigo.hierarchy.models import Assignment, Dimension, DimMember, ProductLine, Subject
from kpigo.ingestion import validator as v
from kpigo.ingestion.models import Feed, FeedRun
from kpigo.ingestion.retention import archived_months
from kpigo.metrics.models import Metric, MetricProfileAssignment
from kpigo.periods.models import PeriodStatus
from kpigo.platform.config import reporting_zone

TRAILING_RUNS = 5


def org_today(org_id: str) -> date:
    return timezone.now().astimezone(reporting_zone(org_id)).date()


def periods_in(template: v.Template, table: v.RawTable) -> list[str]:
    """The well-formed months a raw load touches, for scoping reference reads."""
    found: set[str] = set()
    for column in ("period_key", *v.DATE_COLUMNS):
        if column not in table.header or column not in template.column_names:
            continue
        i = table.header.index(column)
        for row in table.rows:
            text = row[i] if i < len(row) else None
            if not text:
                continue
            if column == "period_key" and v.PERIOD_KEY_RE.match(text):
                found.add(text)
            elif column != "period_key":
                day = v.parse_date(text)
                if day is not None:
                    found.add(f"{day.year:04d}{day.month:02d}")
    return sorted(found)


def campaign_windows(org_id: str) -> dict[str, tuple[v.EventWindow, ...]]:
    """Every campaign's code and its published events' contact days."""
    found: dict[str, list[v.EventWindow]] = {
        code: [] for code in Campaign.objects.filter(org_id=org_id).values_list("code", flat=True)
    }
    for code, event_id, start, end in (
        CampaignEvent.objects.filter(org_id=org_id)
        .exclude(state="draft")
        .order_by("campaign__code", "period_start", "sequence_no")
        .values_list("campaign__code", "event_id", "period_start", "period_end")
    ):
        found[code].append(v.EventWindow(str(event_id), start, end))
    return {code: tuple(events) for code, events in found.items()}


def staff_nos_in(table: v.RawTable) -> set[str]:
    if "subject_ref" not in table.header:
        return set()
    i = table.header.index("subject_ref")
    return {row[i] for row in table.rows if i < len(row) and row[i]}  # type: ignore[misc]


def build_reference(
    org_id: str,
    template: v.Template,
    periods: Iterable[str],
    *,
    feed: Feed | None = None,
    restatement: bool = False,
    staff_nos: Iterable[str] = (),
) -> v.Reference:
    periods = sorted(set(periods))
    ref = v.Reference(today=org_today(org_id), restatement=restatement)

    bindings: dict[str, set[str]] = defaultdict(set)
    metrics = list(
        Metric.objects.filter(org_id=org_id)
        .prefetch_related("bindings")
        .order_by("metric_code", "effective_from")
    )
    for m in metrics:
        bindings[str(m.metric_id)] = {b.product for b in m.bindings.all() if b.is_active}
        ref.metrics.setdefault(m.metric_code, []).append(
            v.MetricVersion(
                metric_id=str(m.metric_id),
                metric_code=m.metric_code,
                status=m.status,
                collection_method=m.collection_method,
                unit=m.unit,
                effective_from=m.effective_from,
                effective_to=m.effective_to,
                products=frozenset(bindings[str(m.metric_id)]),
            )
        )

    if "subject_ref" in template.column_names and periods:
        first = v.month_bounds(periods[0])[0]
        following = v.month_bounds(periods[-1])[1]
        rows = (
            Assignment.objects.filter(org_id=org_id, effective_from__lt=following)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=first))
            .select_related("subject")
            .order_by("subject__staff_no", "effective_from")
        )
        grouped: dict[str, list[Assignment]] = defaultdict(list)
        for a in rows:
            grouped[a.subject.staff_no].append(a)
        for staff_no, assignments in grouped.items():
            ref.subjects[staff_no] = v.SubjectRef(
                subject_id=str(assignments[0].subject_id),
                staff_no=staff_no,
                assignments=tuple(
                    v.AssignmentRef(
                        assignment_id=str(a.assignment_id),
                        profile_code=a.profile_code,
                        effective_from=a.effective_from,
                        effective_to=a.effective_to,
                    )
                    for a in assignments
                ),
            )
        # Subjects the load names but who hold no assignment in the window: known,
        # so the gate says "no assignment" rather than "unknown subject".
        unseen = set(staff_nos) - set(grouped)
        for subject_id, staff_no in Subject.objects.filter(
            org_id=org_id, staff_no__in=sorted(unseen)
        ).values_list("subject_id", "staff_no"):
            ref.subjects[staff_no] = v.SubjectRef(
                subject_id=str(subject_id), staff_no=staff_no, assignments=()
            )
        ref.profile_metrics = [
            v.ProfileMetric(
                metric_id=str(pm.metric_id),
                profile_code=pm.profile_code,
                effective_from=pm.effective_from,
                effective_to=pm.effective_to,
            )
            for pm in MetricProfileAssignment.objects.filter(
                metric__org_id=org_id, product="scorecards"
            )
        ]

    customer_dims = any(c in template.column_names for c in v.CUSTOMER_DIMENSIONS.values())
    if "dimension_type" in template.column_names or customer_dims:
        for dim in Dimension.objects.filter(org_id=org_id).values_list("dimension_type", flat=True):
            ref.members.setdefault(dim, set())
        for dim, code in DimMember.objects.filter(org_id=org_id).values_list(
            "dimension_type", "member_code"
        ):
            ref.members.setdefault(dim, set()).add(code)

    if "product_line_code" in template.column_names:
        ref.product_lines = set(
            ProductLine.objects.filter(org_id=org_id).values_list("code", flat=True)
        )

    if periods:
        for product, period_key, status in PeriodStatus.objects.filter(
            org_id=org_id, product__in=sorted(template.products), period_key__in=periods
        ).values_list("product", "period_key", "status"):
            ref.period_status[(product, period_key)] = status
        if template.name == "actual_daily":
            ref.archived_months = archived_months() & set(periods)

    if "campaign_code" in template.column_names:
        ref.campaigns = campaign_windows(org_id)

    if feed is not None:
        live = FeedRun.objects.filter(feed=feed, is_dry_run=False, outcome="success")
        ref.enforce_missing_rows = live.exists()
        recent = live.order_by("-started_at").values_list("run_id", flat=True)[:TRAILING_RUNS]
        trailing = FeedRun.objects.filter(run_id__in=list(recent)).aggregate(n=Avg("rows_read"))
        ref.trailing_rows = float(trailing["n"]) if trailing["n"] is not None else None
        ref.expected_row_min = feed.expected_row_min
        ref.expected_row_max = feed.expected_row_max
        ref.volume_warn_pct = feed.volume_warn_pct
        ref.volume_reject_pct = feed.volume_reject_pct
    return ref
