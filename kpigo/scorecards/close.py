"""Period close, snapshots and restatement (Scope §7.4; App Flow §7.2, §7.3).

Close is where a provisional month becomes a published one. The pre-check
refuses while any metric on anyone's scorecard is unscored (not reported, no
target, no FX rate) and not explicitly excluded with a reason, or while a
profile's weights sum outside tolerance; feeds without a load for the period and
overrides still pending are warnings. Close then scores every subject through
the bulk path and freezes the result, inputs and all, into ``score_history`` and
``score_total`` under a new ``score_snapshot`` version. Nothing frozen is ever
updated: a restatement writes version n+1 and only flips the old rows'
``is_current``.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

from django.db.models import Q
from django.utils import timezone

from kpigo.ingestion.models import Feed, FeedRun
from kpigo.periods.models import PeriodDeadline, PeriodStatus
from kpigo.scorecards import inputs
from kpigo.scorecards.bands import Band
from kpigo.scorecards.bulk import score_period
from kpigo.scorecards.config import settings_for
from kpigo.scorecards.engine import (
    COUNTED,
    AppliedOverride,
    CyclePosition,
    MetricScore,
    State,
    SubjectScore,
)
from kpigo.scorecards.models import (
    Override,
    ScoreHistory,
    ScoreSnapshot,
    ScoreTotal,
    Target,
)

PRODUCT = "scorecards"
UNSCORED_WORDS = {
    "not_reported": "no actual reported",
    "no_target": "no target published",
    "no_fx_rate": "no FX rate to convert the actual",
}


@dataclass
class Issue:
    kind: str
    message: str
    metric_code: str | None = None
    profile_code: str | None = None
    # Staff numbers affected; ``count`` is the full number when the list is cut.
    subjects: list[str] = field(default_factory=list)
    count: int = 0
    # Manual input: the contributors who still owe it (MI-9).
    owed_by: list[str] = field(default_factory=list)


@dataclass
class CloseCheck:
    period_key: str
    status: str
    subjects: int
    blockers: list[Issue]
    warnings: list[Issue]
    scores: list[SubjectScore]

    @property
    def ready(self) -> bool:
        return not self.blockers


SHOWN = 25


def pre_check(org_id: str, period_key: str, status: str, staff: dict[str, str]) -> CloseCheck:
    """``staff`` maps subject_id → staff number for the messages."""
    scores = score_period(org_id, period_key)
    blockers: list[Issue] = []
    warnings: list[Issue] = []

    unscored: dict[tuple[str, str], list[str]] = defaultdict(list)
    for s in scores:
        for m in s.metrics:
            if m.state not in COUNTED and m.state != "excluded":
                unscored[(m.state, m.metric_code)].append(staff.get(s.subject_id, s.subject_id))
    owed = inputs.unsubmitted(org_id, period_key)
    for (state, code), who in sorted(unscored.items()):
        people = f"{len(who)} {'person' if len(who) == 1 else 'people'}"
        if state == "not_reported" and code in owed:
            # MI-9: "awaiting data" becomes "awaiting input from X".
            message = (
                f"{code}: awaiting manual input from {', '.join(owed[code])} for {people}. "
                "Chase them, or exclude it with a reason."
            )
        else:
            message = (
                f"{code}: {UNSCORED_WORDS[state]} for {people}. Load it, or exclude it "
                "with a reason."
            )
        blockers.append(
            Issue(
                kind=state,
                metric_code=code,
                subjects=sorted(who)[:SHOWN],
                count=len(who),
                message=message,
                owed_by=owed.get(code, []) if state == "not_reported" else [],
            )
        )

    blockers += weight_issues(org_id, scores, staff)

    pending = Override.objects.filter(
        org_id=org_id, product=PRODUCT, status="pending", period_from__lte=period_key
    ).filter(Q(period_to__gte=period_key) | Q(period_to__isnull=True, period_from=period_key))
    if n := pending.count():
        warnings.append(
            Issue(
                kind="pending_overrides",
                count=n,
                message=f"{n} override request(s) for this period are still awaiting approval; "
                "close freezes scores without them.",
            )
        )
    for name in feeds_without_load(org_id, period_key):
        warnings.append(
            Issue(
                kind="feed",
                message=f"Feed '{name}' was due for this period and has no successful load for it.",
            )
        )
    return CloseCheck(
        period_key=period_key,
        status=status,
        subjects=len(scores),
        blockers=blockers,
        warnings=warnings,
        scores=scores,
    )


def weight_issues(org_id: str, scores: list[SubjectScore], staff: dict[str, str]) -> list[Issue]:
    """The publish-time weight check again, per person, as a safety net (SC-10).

    Each subject whose every metric has a published target must have weights
    summing to the configured total within tolerance. Published weights are
    checked, not overridden ones: an approved weight override is a deliberate
    exception.
    """
    settings = settings_for(org_id)
    ids = [m.target_id for s in scores for m in s.metrics if m.target_id]
    weights = dict(Target.objects.filter(target_id__in=ids).values_list("target_id", "weight"))
    off: dict[tuple[str, Decimal], list[str]] = defaultdict(list)
    for s in scores:
        if not s.metrics or any(m.target_id is None for m in s.metrics):
            continue
        total = sum(
            (Decimal(weights.get(uuid.UUID(m.target_id)) or 0) for m in s.metrics), Decimal(0)
        )
        if abs(total - settings.weight_total) > settings.weight_tolerance:
            off[(s.profile_code, total)].append(staff.get(s.subject_id, s.subject_id))
    return [
        Issue(
            kind="weights",
            profile_code=profile,
            subjects=sorted(who)[:SHOWN],
            count=len(who),
            message=f"{profile}: weights sum to {total.normalize():f} for {len(who)} "
            f"{'person' if len(who) == 1 else 'people'}, not {settings.weight_total.normalize():f}.",
        )
        for (profile, total), who in sorted(off.items())
    ]


def feeds_without_load(org_id: str, period_key: str) -> list[str]:
    due = PeriodDeadline.objects.filter(
        org_id=org_id, period_key=period_key, due_at__lte=timezone.now()
    ).values_list("feed_id", flat=True)
    out = []
    for feed in Feed.objects.filter(org_id=org_id, feed_id__in=list(due), status="active"):
        loaded = FeedRun.objects.filter(
            feed=feed, is_dry_run=False, outcome="success", periods__contains=[period_key]
        ).exists()
        if not loaded:
            out.append(feed.name)
    return sorted(out)


def freeze(
    org_id: str,
    period_key: str,
    scores: list[SubjectScore],
    *,
    version: int,
    reason: str,
    user_id: int | None,
) -> ScoreSnapshot:
    """Write version ``version`` of the period and retire the one before it."""
    now = timezone.now()
    snapshot = ScoreSnapshot.objects.create(
        org_id=org_id,
        product=PRODUCT,
        period_key=period_key,
        snapshot_version=version,
        kind="close" if version == 1 else "restatement",
        reason=reason,
        subjects=len(scores),
        created_by=user_id,
    )
    for model in (ScoreTotal, ScoreHistory):
        model.objects.filter(
            org_id=org_id, product=PRODUCT, period_key=period_key, is_current=True
        ).update(is_current=False)
    totals: list[ScoreTotal] = []
    history: list[ScoreHistory] = []
    for s in scores:
        band = s.band
        totals.append(
            ScoreTotal(
                org_id=org_id,
                snapshot=snapshot,
                subject_id=s.subject_id,
                assignment_id=s.assignment_id,
                product=PRODUCT,
                period_key=period_key,
                profile_code=s.profile_code,
                policy=s.policy,
                months_elapsed=s.cycle.months_elapsed,
                quarters_elapsed=s.cycle.quarters_elapsed,
                cycle_months=s.cycle.months,
                total_score=s.total_score,
                total_cap=s.total_cap,
                weight_scored=s.weight_scored,
                weight_expected=s.weight_expected,
                graded_score=s.graded_score,
                achievement_pct=s.achievement_pct,
                metrics_scored=s.metrics_scored,
                metrics_total=s.metrics_total,
                not_reported=s.not_reported,
                no_target=s.no_target,
                excluded=s.excluded,
                band_id=band.band_id if band else None,
                band_label=band.label if band else None,
                band_threshold=band.threshold if band else None,
                band_ramp_position=band.ramp_position if band else None,
                band_colour_hex=band.colour_hex if band else None,
                snapshot_version=version,
                is_current=True,
                computed_at=now,
            )
        )
        for order, m in enumerate(s.metrics):
            history.append(
                ScoreHistory(
                    org_id=org_id,
                    snapshot=snapshot,
                    subject_id=s.subject_id,
                    assignment_id=s.assignment_id,
                    metric_id=m.metric_id,
                    product=PRODUCT,
                    period_key=period_key,
                    sort_order=order,
                    state=m.state,
                    target_id=m.target_id,
                    target_version=m.target_version,
                    target_scope=m.target_scope,
                    base_target=m.base_target,
                    target_type=m.target_type,
                    target_currency=m.target_currency,
                    target_value=m.target_value,
                    reported_actual=m.reported_actual,
                    actual_currency=m.actual_currency,
                    fx_rate_applied=m.fx_rate,
                    actual_value=m.actual_value,
                    run_ids=[m.run_id] if m.run_id else [],
                    weight=m.weight,
                    cap=m.cap,
                    pct_achieved=m.pct_achieved,
                    score=m.score,
                    override_ids=[o.override_id for o in m.overrides],
                    overrides=[_override_json(o) for o in m.overrides],
                    exclusion_reason=m.exclusion_reason,
                    computed_at=now,
                    snapshot_version=version,
                    is_current=True,
                )
            )
    ScoreTotal.objects.bulk_create(totals, batch_size=2000)
    ScoreHistory.objects.bulk_create(history, batch_size=5000)
    return snapshot


def _override_json(o: Any) -> dict[str, Any]:
    return {
        "override_id": o.override_id,
        "change_type": o.change_type,
        "scope_type": o.scope_type,
        "scope_code": o.scope_code,
        "value": str(o.value) if o.value is not None else None,
        "text": o.text,
        "reason": o.reason,
    }


def status_row(org_id: str, period_key: str, *, lock: bool = False) -> PeriodStatus | None:
    rows = PeriodStatus.objects.filter(org_id=org_id, product=PRODUCT, period_key=period_key)
    return (rows.select_for_update() if lock else rows).first()


def frozen(
    org_id: str, period_key: str, subject_ids: Iterable[str] | None = None
) -> list[SubjectScore]:
    """The current snapshot of a closed period, as the engine's own types; by staff number."""
    totals = ScoreTotal.objects.filter(
        org_id=org_id, product=PRODUCT, period_key=period_key, is_current=True
    ).select_related("subject")
    if subject_ids is not None:
        totals = totals.filter(subject_id__in=list(subject_ids))
    rows = list(totals.order_by("subject__staff_no"))
    metrics: dict[str, list[MetricScore]] = defaultdict(list)
    history = (
        ScoreHistory.objects.filter(
            org_id=org_id,
            product=PRODUCT,
            period_key=period_key,
            is_current=True,
            subject_id__in=[t.subject_id for t in rows],
        )
        .select_related("metric")
        .order_by("subject_id", "sort_order")
    )
    for h in history:
        metrics[str(h.subject_id)].append(
            MetricScore(
                metric_id=str(h.metric_id),
                metric_code=h.metric.metric_code,
                display_name=h.metric.display_name,
                direction=h.metric.direction,
                unit=h.metric.unit,
                decimal_places=h.metric.decimal_places,
                state=cast(State, h.state),
                target_id=str(h.target_id) if h.target_id else None,
                target_version=h.target_version,
                target_scope=h.target_scope,
                base_target=h.base_target,
                target_type=h.target_type,
                target_currency=h.target_currency,
                target_value=h.target_value,
                reported_actual=h.reported_actual,
                actual_currency=h.actual_currency,
                fx_rate=h.fx_rate_applied,
                actual_value=h.actual_value,
                run_id=str(h.run_ids[0]) if h.run_ids else None,
                weight=h.weight,
                cap=h.cap,
                pct_achieved=h.pct_achieved,
                score=h.score,
                overrides=tuple(
                    AppliedOverride(
                        override_id=o["override_id"],
                        change_type=o["change_type"],
                        scope_type=o["scope_type"],
                        scope_code=o["scope_code"],
                        value=Decimal(o["value"]) if o["value"] is not None else None,
                        text=o["text"],
                        reason=o["reason"],
                    )
                    for o in h.overrides
                ),
                exclusion_reason=h.exclusion_reason,
            )
        )
    return [
        SubjectScore(
            subject_id=str(t.subject_id),
            assignment_id=str(t.assignment_id),
            profile_code=t.profile_code,
            period_key=period_key,
            cycle=CyclePosition(
                months=t.cycle_months,
                months_elapsed=t.months_elapsed,
                quarters_elapsed=t.quarters_elapsed,
            ),
            policy=t.policy,
            metrics=tuple(metrics.get(str(t.subject_id), [])),
            total_score=t.total_score,
            total_cap=t.total_cap,
            weight_scored=t.weight_scored,
            weight_expected=t.weight_expected,
            graded_score=t.graded_score,
            achievement_pct=t.achievement_pct,
            metrics_scored=t.metrics_scored,
            metrics_total=t.metrics_total,
            not_reported=t.not_reported,
            no_target=t.no_target,
            excluded=t.excluded,
            band=(
                Band(
                    label=t.band_label,
                    threshold=t.band_threshold or Decimal(0),
                    ramp_position=t.band_ramp_position or 1,
                    band_id=str(t.band_id) if t.band_id else None,
                    colour_hex=t.band_colour_hex,
                )
                if t.band_label is not None
                else None
            ),
        )
        for t in rows
    ]


def current_snapshot(org_id: str, period_key: str) -> ScoreSnapshot | None:
    return (
        ScoreSnapshot.objects.filter(org_id=org_id, product=PRODUCT, period_key=period_key)
        .order_by("-snapshot_version")
        .first()
    )
