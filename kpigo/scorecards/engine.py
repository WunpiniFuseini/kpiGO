"""The scoring arithmetic (Scope §6.2, §6.3, §6.6; TDD §4).

Pure functions over resolved inputs: nothing here reads the database. The
per-subject path (``scoring``) feeds these with ORM rows; the bulk path
(``bulk``) reimplements the same rules as Polars expressions, and the golden-case
suite asserts the two agree to the last stored digit.

Units: a target's ``weight`` and ``cap`` are points out of the configured weight
total (normally 100). ``pct_achieved`` is a ratio (1.0 is exactly on target).
A metric's ``score`` is ``MIN(pct × weight, cap) ÷ 100``, so a scorecard with
every metric on target and weights summing to 100 totals 1.0, the scale the
rating bands use.

Absent is not zero. A metric with no actual is ``not_reported`` and leaves the
denominator; one with no target is ``no_target`` (a configuration gap); an
explicit ``0`` is ``zero_actual`` and scores. Under the default ``reduced``
policy the total is the sum of what was scored and the grade is read from the
total scaled back up by the weight that is still awaiting data, so a missing feed
never drags a good performer down a band. ``redistribute`` (an explicit client
setting) instead scales each scored metric's weight and cap by the same factor,
which yields the same grade but different per-metric points.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from kpigo.scorecards.bands import Band, lookup

State = Literal["scored", "zero_actual", "not_reported", "no_target", "no_fx_rate"]
STATES: tuple[State, ...] = ("scored", "zero_actual", "not_reported", "no_target", "no_fx_rate")
COUNTED: frozenset[str] = frozenset({"scored", "zero_actual"})
# Excluded because the data has not arrived, rather than because configuration is missing.
AWAITING: frozenset[str] = frozenset({"not_reported"})

SCOPE_RANK = {"subject": 1, "profile": 2, "dimension": 3}

VALUE_DP = Decimal("0.0001")  # numeric(18,4), as targets and actuals are stored
RATIO_DP = Decimal("0.000001")  # pct achieved, scores, totals


def q_value(x: Decimal | float | None) -> Decimal | None:
    return None if x is None else _dec(x).quantize(VALUE_DP, rounding=ROUND_HALF_UP)


def q_ratio(x: Decimal | float | None) -> Decimal | None:
    return None if x is None else _dec(x).quantize(RATIO_DP, rounding=ROUND_HALF_UP)


def _dec(x: Decimal | float) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(repr(float(x)))


@dataclass(frozen=True)
class AppliedOverride:
    override_id: str
    change_type: str
    scope_type: str
    scope_code: str
    value: Decimal | None
    text: str | None
    reason: str


@dataclass(frozen=True)
class TargetIn:
    target_id: str | None
    version: int | None
    scope_type: str
    value: Decimal
    target_type: str
    weight: Decimal | None
    cap: Decimal | None
    currency_code: str | None


@dataclass(frozen=True)
class ActualIn:
    value: Decimal
    currency_code: str | None
    run_id: str | None


@dataclass(frozen=True)
class CyclePosition:
    months: int
    months_elapsed: int
    quarters_elapsed: int


@dataclass(frozen=True)
class MetricIn:
    metric_id: str
    metric_code: str
    display_name: str
    direction: str
    unit: str
    decimal_places: int
    target: TargetIn | None
    actual: ActualIn | None
    # The winning approved override per change type, after precedence.
    overrides: dict[str, AppliedOverride] = field(default_factory=dict)
    # Units of the target currency per unit of the actual's; None when it is needed but missing.
    fx_rate: Decimal | None = Decimal(1)


@dataclass(frozen=True)
class MetricScore:
    metric_id: str
    metric_code: str
    display_name: str
    direction: str
    unit: str
    decimal_places: int
    state: State
    target_id: str | None
    target_version: int | None
    target_scope: str | None
    base_target: Decimal | None
    target_type: str | None
    target_currency: str | None
    target_value: Decimal | None
    reported_actual: Decimal | None
    actual_currency: str | None
    fx_rate: Decimal | None
    actual_value: Decimal | None
    run_id: str | None
    weight: Decimal | None
    cap: Decimal | None
    pct_achieved: Decimal | None
    score: Decimal | None
    overrides: tuple[AppliedOverride, ...]


@dataclass(frozen=True)
class SubjectScore:
    subject_id: str
    assignment_id: str
    profile_code: str
    period_key: str
    cycle: CyclePosition
    policy: str
    metrics: tuple[MetricScore, ...]
    total_score: Decimal
    total_cap: Decimal
    weight_scored: Decimal
    weight_expected: Decimal
    graded_score: Decimal | None
    achievement_pct: Decimal | None
    metrics_scored: int
    metrics_total: int
    not_reported: int
    no_target: int
    band: Band | None

    @property
    def statement(self) -> str:
        """The reduced-denominator line the scorecard shows (SC-7)."""
        if self.metrics_total == 0:
            return "No metrics on this profile's scorecard."
        text = f"{self.metrics_scored} of {self.metrics_total} metrics scored"
        if self.not_reported:
            text += f" · {self.not_reported} awaiting data"
        gaps = self.metrics_total - self.metrics_scored - self.not_reported
        if gaps:
            text += f" · {gaps} without a target"
        return text


def adjust_target(value: Decimal, target_type: str, pos: CyclePosition) -> Decimal:
    """The period's target from the stored value (Scope §6.2)."""
    if target_type == "yearly":
        return value / pos.months
    if target_type == "cumulative":
        return value * pos.months_elapsed
    if target_type == "quarterly":
        return value * pos.quarters_elapsed
    if target_type == "prorated":
        return value / pos.months * pos.months_elapsed
    return value


def winning(candidates: Sequence[tuple[AppliedOverride, int]]) -> dict[str, AppliedOverride]:
    """Subject beats profile beats dimension; within a scope the latest approval wins.

    ``candidates`` pairs each applicable override with its approval time in epoch
    microseconds; ties fall to the override id so both paths pick the same row.
    """
    ordered = sorted(
        candidates, key=lambda c: (SCOPE_RANK[c[0].scope_type], -c[1], c[0].override_id)
    )
    out: dict[str, AppliedOverride] = {}
    for ov, _ in ordered:
        out.setdefault(ov.change_type, ov)
    return out


def score_metric(m: MetricIn, pos: CyclePosition) -> MetricScore:
    ov = m.overrides
    t = m.target

    target_type = ov["target_type"].text if "target_type" in ov else (t.target_type if t else None)
    target_value: Decimal | None = None
    if t is not None:
        target_value = adjust_target(t.value, target_type or "monthly", pos)
    if "target" in ov:
        target_value = ov["target"].value
    weight = ov["weight"].value if "weight" in ov else (t.weight if t else None)
    cap = ov["cap"].value if "cap" in ov else (t.cap if t else None)

    reported = m.actual.value if m.actual else None
    actual: Decimal | None = None
    fx: Decimal | None = None
    if "actual" in ov:
        actual = ov["actual"].value  # stated in the target's terms
    elif m.actual is not None:
        fx = m.fx_rate
        actual = None if fx is None else m.actual.value * fx

    state: State
    if "actual" not in ov and m.actual is None:
        state = "not_reported"
    elif target_value is None or weight is None or cap is None or target_value <= 0:
        state = "no_target"
    elif actual is None:
        state = "no_fx_rate"
    elif actual == 0:
        state = "zero_actual"
    else:
        state = "scored"

    pct: Decimal | None = None
    score: Decimal | None = None
    if state in COUNTED:
        assert actual is not None and target_value is not None
        assert weight is not None and cap is not None
        if m.direction == "lower_is_better":
            # Nothing of a lower-is-better measure is the best result: full cap.
            pct = None if actual == 0 else target_value / actual
        else:
            pct = actual / target_value
        raw = cap if pct is None else min(max(pct, Decimal(0)) * weight, cap)
        score = raw / 100

    applied = tuple(ov[k] for k in sorted(ov))
    return MetricScore(
        metric_id=m.metric_id,
        metric_code=m.metric_code,
        display_name=m.display_name,
        direction=m.direction,
        unit=m.unit,
        decimal_places=m.decimal_places,
        state=state,
        target_id=t.target_id if t else None,
        target_version=t.version if t else None,
        target_scope=t.scope_type if t else None,
        base_target=q_value(t.value) if t else None,
        target_type=target_type,
        target_currency=t.currency_code if t else None,
        target_value=q_value(target_value),
        reported_actual=q_value(reported),
        actual_currency=m.actual.currency_code if m.actual else None,
        fx_rate=q_ratio(fx) if fx is not None and fx != 1 else None,
        actual_value=q_value(actual),
        run_id=m.actual.run_id if m.actual and "actual" not in ov else None,
        weight=q_value(weight),
        cap=q_value(cap),
        pct_achieved=q_ratio(pct),
        score=score,  # unrounded until totals are taken; see total()
        overrides=applied,
    )


def total(
    *,
    subject_id: str,
    assignment_id: str,
    profile_code: str,
    period_key: str,
    pos: CyclePosition,
    policy: str,
    metrics: Sequence[MetricScore],
    bands: list[Band],
) -> SubjectScore:
    counted = [m for m in metrics if m.state in COUNTED]
    expected = [m for m in metrics if m.state in COUNTED or m.state in AWAITING]
    raw_scores = [m.score or Decimal(0) for m in counted]
    weight_scored = sum((m.weight or Decimal(0) for m in counted), Decimal(0))
    weight_expected = sum((_weight(m) for m in expected), Decimal(0))
    raw_total = sum(raw_scores, Decimal(0))
    raw_cap = sum((m.cap or Decimal(0) for m in counted), Decimal(0)) / 100

    factor = weight_expected / weight_scored if weight_scored > 0 else None
    graded = raw_total * factor if factor is not None else None
    if policy == "redistribute" and factor is not None:
        metrics = [_scaled(m, factor) for m in metrics]
        raw_total, raw_cap = graded or Decimal(0), raw_cap * factor

    achievement = raw_total / raw_cap if raw_cap > 0 else None
    graded_q = q_ratio(graded)
    return SubjectScore(
        subject_id=subject_id,
        assignment_id=assignment_id,
        profile_code=profile_code,
        period_key=period_key,
        cycle=pos,
        policy=policy,
        metrics=tuple(_rounded(m) for m in metrics),
        total_score=q_ratio(raw_total) or Decimal(0),
        total_cap=q_ratio(raw_cap) or Decimal(0),
        weight_scored=q_value(weight_scored) or Decimal(0),
        weight_expected=q_value(weight_expected) or Decimal(0),
        graded_score=graded_q,
        achievement_pct=q_ratio(achievement),
        metrics_scored=len(counted),
        metrics_total=len(metrics),
        not_reported=sum(1 for m in metrics if m.state in AWAITING),
        no_target=sum(1 for m in metrics if m.state not in COUNTED and m.state not in AWAITING),
        band=lookup(bands, graded_q),
    )


def _weight(m: MetricScore) -> Decimal:
    return m.weight if m.weight is not None else Decimal(0)


def _scaled(m: MetricScore, factor: Decimal) -> MetricScore:
    if m.state not in COUNTED or m.score is None:
        return m
    return replace(m, score=m.score * factor)


def _rounded(m: MetricScore) -> MetricScore:
    return replace(m, score=q_ratio(m.score))
