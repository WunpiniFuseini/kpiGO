"""Live scorecards (PRD SC-2–SC-8, TDD §4).

``scorecard.compute`` is one subject's scorecard for a period through the
per-subject path, with every input the provenance panel needs: the target and
how it was adjusted, the overrides that applied, the actual and the run that
loaded it. ``scorecard.period.list`` scores everyone the caller can see through
the bulk path. Open periods are computed at query time; frozen snapshots for
closed periods arrive with period close.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from kpigo.action import ActionContext, NotFound, action
from kpigo.hierarchy.models import Subject
from kpigo.hierarchy.scope import current_period_key
from kpigo.platform.vocab import Code, PeriodKey
from kpigo.scorecards import roster
from kpigo.scorecards.bands import Band, bands_for, next_band
from kpigo.scorecards.bulk import score_period
from kpigo.scorecards.config import period_phase, period_status
from kpigo.scorecards.engine import MetricScore, SubjectScore
from kpigo.scorecards.scoring import score_subject
from kpigo.scorecards.taxonomy import Placer

EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"


class ScoreBandOut(BaseModel):
    label: str
    threshold: Decimal
    ramp_position: int
    colour_hex: str | None


def _band(b: Band | None) -> ScoreBandOut | None:
    if b is None:
        return None
    return ScoreBandOut(
        label=b.label, threshold=b.threshold, ramp_position=b.ramp_position, colour_hex=b.colour_hex
    )


class AppliedOverrideOut(BaseModel):
    override_id: uuid.UUID
    change_type: str
    scope_type: str
    scope_code: str
    value: Decimal | None
    text: str | None
    reason: str


class MetricScoreOut(BaseModel):
    metric_id: uuid.UUID
    metric_code: str
    display_name: str
    direction: str
    unit: str
    decimal_places: int
    # Taxonomy path, top level first; empty when the metric is not placed.
    path: list[str]
    # scored | zero_actual | not_reported | no_target | no_fx_rate
    state: str
    target_id: uuid.UUID | None
    target_version: int | None
    target_scope: str | None
    # The stored target and its type; ``target_value`` is the period's after both.
    base_target: Decimal | None
    target_type: str | None
    target_currency: str | None
    target_value: Decimal | None
    # As the feed reported it, before any conversion or override.
    reported_actual: Decimal | None
    actual_currency: str | None
    fx_rate: Decimal | None
    actual_value: Decimal | None
    run_id: uuid.UUID | None
    weight: Decimal | None
    cap: Decimal | None
    pct_achieved: Decimal | None
    # Out of 1.0 (×100 for points); null unless scored.
    score: Decimal | None
    overrides: list[AppliedOverrideOut]


def _metric(m: MetricScore, path: list[str]) -> MetricScoreOut:
    return MetricScoreOut(
        metric_id=uuid.UUID(m.metric_id),
        metric_code=m.metric_code,
        display_name=m.display_name,
        direction=m.direction,
        unit=m.unit,
        decimal_places=m.decimal_places,
        path=path,
        state=m.state,
        target_id=uuid.UUID(m.target_id) if m.target_id else None,
        target_version=m.target_version,
        target_scope=m.target_scope,
        base_target=m.base_target,
        target_type=m.target_type,
        target_currency=m.target_currency,
        target_value=m.target_value,
        reported_actual=m.reported_actual,
        actual_currency=m.actual_currency,
        fx_rate=m.fx_rate,
        actual_value=m.actual_value,
        run_id=uuid.UUID(m.run_id) if m.run_id else None,
        weight=m.weight,
        cap=m.cap,
        pct_achieved=m.pct_achieved,
        score=m.score,
        overrides=[
            AppliedOverrideOut(
                override_id=uuid.UUID(o.override_id),
                change_type=o.change_type,
                scope_type=o.scope_type,
                scope_code=o.scope_code,
                value=o.value,
                text=o.text,
                reason=o.reason,
            )
            for o in m.overrides
        ],
    )


class ScorecardComputeIn(BaseModel):
    subject_id: uuid.UUID
    # Defaults to the current period.
    period_key: PeriodKey | None = None


class ScorecardOut(BaseModel):
    subject_id: uuid.UUID
    staff_no: str
    full_name: str
    period_key: str
    # future | open | locked, and the recorded period status.
    phase: str
    period_status: str
    # live: computed now from current inputs. Closed periods read snapshots (period close).
    source: str
    # False when no assignment is in force in the period: there is nothing to score.
    assigned: bool
    assignment_id: uuid.UUID | None
    profile_code: str | None
    policy: str
    months_elapsed: int | None
    quarters_elapsed: int | None
    cycle_months: int | None
    metrics: list[MetricScoreOut]
    total_score: Decimal
    total_cap: Decimal
    weight_scored: Decimal
    weight_expected: Decimal
    # What the band is read from: the total, scaled for weight still awaiting data.
    graded_score: Decimal | None
    achievement_pct: Decimal | None
    metrics_scored: int
    metrics_total: int
    not_reported: int
    no_target: int
    band: ScoreBandOut | None
    next_band: ScoreBandOut | None
    # e.g. "5 of 7 metrics scored · 2 awaiting data" (SC-7).
    statement: str


def scorecard_out(org_id: str, subject: Subject, period_key: str) -> ScorecardOut:
    s = score_subject(org_id, str(subject.subject_id), period_key)
    base = {
        "subject_id": subject.subject_id,
        "staff_no": subject.staff_no,
        "full_name": subject.full_name,
        "period_key": period_key,
        "phase": period_phase(org_id, period_key),
        "period_status": period_status(org_id, period_key),
        "source": "live",
    }
    if s is None:
        return ScorecardOut(
            **base,
            assigned=False,
            assignment_id=None,
            profile_code=None,
            policy="",
            months_elapsed=None,
            quarters_elapsed=None,
            cycle_months=None,
            metrics=[],
            total_score=Decimal(0),
            total_cap=Decimal(0),
            weight_scored=Decimal(0),
            weight_expected=Decimal(0),
            graded_score=None,
            achievement_pct=None,
            metrics_scored=0,
            metrics_total=0,
            not_reported=0,
            no_target=0,
            band=None,
            next_band=None,
            statement="No role in force for this period, so there is no scorecard.",
        )
    placer = Placer.active(org_id)
    return ScorecardOut(
        **base,
        assigned=True,
        assignment_id=uuid.UUID(s.assignment_id),
        profile_code=s.profile_code,
        policy=s.policy,
        months_elapsed=s.cycle.months_elapsed,
        quarters_elapsed=s.cycle.quarters_elapsed,
        cycle_months=s.cycle.months,
        metrics=[_metric(m, placer.path(m.metric_code, s.profile_code)) for m in s.metrics],
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
        band=_band(s.band),
        next_band=_band(next_band(bands_for(org_id), s.band)),
        statement=s.statement,
    )


@action(
    name="scorecard.compute",
    summary="A subject's scorecard for a period: every metric, the total and the band.",
    schema=ScorecardComputeIn,
    output=ScorecardOut,
    permission="scorecard.view",
    read_only=True,
    module="scorecards",
    scope="subject",
    example={"subject_id": EXAMPLE_ID, "period_key": "202610"},
)
def compute_scorecard(params: ScorecardComputeIn, ctx: ActionContext) -> ScorecardOut:
    subject = Subject.objects.filter(org_id=ctx.org_id, subject_id=params.subject_id).first()
    if subject is None:
        raise NotFound("No such subject.")
    return scorecard_out(ctx.org_id, subject, params.period_key or current_period_key(ctx.org_id))


# ── everyone visible, through the bulk path ─────────────────────────────────


class ScorecardListIn(BaseModel):
    period_key: PeriodKey | None = None
    profile_code: Code | None = None
    limit: int = Field(default=500, ge=1, le=5000)


class ScorecardRowOut(BaseModel):
    subject_id: uuid.UUID
    staff_no: str
    full_name: str
    profile_code: str
    total_score: Decimal
    graded_score: Decimal | None
    achievement_pct: Decimal | None
    metrics_scored: int
    metrics_total: int
    not_reported: int
    no_target: int
    band: ScoreBandOut | None


class ScorecardListOut(BaseModel):
    period_key: str
    phase: str
    source: str
    rows: list[ScorecardRowOut]
    # How many visible subjects matched before ``limit``.
    total: int


@action(
    name="scorecard.period.list",
    summary="Scorecard totals and bands for everyone you can see in a period.",
    schema=ScorecardListIn,
    output=ScorecardListOut,
    permission="scorecard.view",
    read_only=True,
    module="scorecards",
    example={"period_key": "202610"},
)
def list_scorecards(params: ScorecardListIn, ctx: ActionContext) -> ScorecardListOut:
    period_key = params.period_key or current_period_key(ctx.org_id)
    in_force = roster.assignments_in_force(ctx.org_id, period_key)
    if params.profile_code:
        in_force = in_force.filter(profile_code=params.profile_code)
    visible = [
        str(s)
        for s in in_force.values_list("subject_id", flat=True)
        if ctx.visible_subjects.contains(str(s))
    ]
    scores: list[SubjectScore] = score_period(ctx.org_id, period_key, visible) if visible else []
    people = {
        str(s.subject_id): s
        for s in Subject.objects.filter(subject_id__in=[x.subject_id for x in scores])
    }
    rows = [
        ScorecardRowOut(
            subject_id=uuid.UUID(s.subject_id),
            staff_no=people[s.subject_id].staff_no,
            full_name=people[s.subject_id].full_name,
            profile_code=s.profile_code,
            total_score=s.total_score,
            graded_score=s.graded_score,
            achievement_pct=s.achievement_pct,
            metrics_scored=s.metrics_scored,
            metrics_total=s.metrics_total,
            not_reported=s.not_reported,
            no_target=s.no_target,
            band=_band(s.band),
        )
        for s in scores
    ]
    return ScorecardListOut(
        period_key=period_key,
        phase=period_phase(ctx.org_id, period_key),
        source="live",
        rows=rows[: params.limit],
        total=len(rows),
    )
