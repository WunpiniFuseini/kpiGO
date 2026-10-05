"""The bulk scoring path (TDD §4.3): a whole period in a handful of frames.

Period close, leaderboards and team views score thousands of subjects at once.
This loads the period's assignments, profile metrics, published targets, facts
and approved overrides into Polars frames and resolves them with joins and
expressions: the same rules as ``engine.score_metric`` and ``engine.total``,
written a second time on purpose. ``tests/test_scoring_golden.py`` runs both
paths over the same cases and asserts identical output.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import polars as pl

from kpigo.ingestion.models import FactActualMonthly
from kpigo.platform.config import MissingRate, fx_rate
from kpigo.scorecards import roster
from kpigo.scorecards.bands import bands_for, lookup
from kpigo.scorecards.config import settings_for
from kpigo.scorecards.engine import (
    SCOPE_RANK,
    AppliedOverride,
    CyclePosition,
    MetricScore,
    SubjectScore,
    q_ratio,
    q_value,
)
from kpigo.scorecards.models import (
    OVERRIDE_CHANGE_TYPES,
    OVERRIDE_DIMENSIONS,
    Override,
    ScoreExclusion,
    Target,
)
from kpigo.scorecards.scoring import applicable_overrides, micros, position

F = pl.Float64
S = pl.Utf8


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def score_period(
    org_id: str,
    period_key: str,
    subject_ids: Iterable[str] | None = None,
    *,
    hide_manual: bool = False,
) -> list[SubjectScore]:
    """Every subject with an assignment in force, or only ``subject_ids``; by staff number.

    ``hide_manual`` leaves manual-input values out until close (MI-10).
    """
    assignments = roster.assignments_in_force(org_id, period_key).select_related("subject")
    if subject_ids is not None:
        assignments = assignments.filter(subject_id__in=list(subject_ids))
    member_rows = []
    cycles: dict[str | None, CyclePosition] = {}
    for a in assignments.order_by("subject__staff_no"):
        cycle_id = str(a.cycle_id) if a.cycle_id else None
        if cycle_id not in cycles:
            cycles[cycle_id] = position(org_id, period_key, cycle_id)
        pos = cycles[cycle_id]
        member_rows.append(
            {
                "subject_id": str(a.subject_id),
                "assignment_id": str(a.assignment_id),
                "profile_code": a.profile_code,
                "staff_no": a.subject.staff_no,
                "months": pos.months,
                "months_elapsed": pos.months_elapsed,
                "quarters_elapsed": pos.quarters_elapsed,
                "dims": [
                    f"{d}:{getattr(a, f'{d}_code')}"
                    for d in OVERRIDE_DIMENSIONS
                    if getattr(a, f"{d}_code")
                ],
            }
        )
    if not member_rows:
        return []
    members = pl.DataFrame(
        member_rows,
        schema={
            "subject_id": S,
            "assignment_id": S,
            "profile_code": S,
            "staff_no": S,
            "months": pl.Int64,
            "months_elapsed": pl.Int64,
            "quarters_elapsed": pl.Int64,
            "dims": pl.List(S),
        },
    )

    by_profile = roster.profile_metrics(
        org_id, period_key, members["profile_code"].unique().to_list()
    )
    metric_rows = [
        {
            "profile_code": profile,
            "metric_id": str(m.metric_id),
            "metric_code": m.metric_code,
            "display_name": m.display_name,
            "direction": m.direction,
            "unit": m.unit,
            "decimal_places": m.decimal_places,
            "target_scope": m.target_scope,
        }
        for profile, metrics in by_profile.items()
        for m in metrics
    ]
    metrics = pl.DataFrame(
        metric_rows,
        schema={
            "profile_code": S,
            "metric_id": S,
            "metric_code": S,
            "display_name": S,
            "direction": S,
            "unit": S,
            "decimal_places": pl.Int64,
            "target_scope": S,
        },
    )
    metric_ids = metrics["metric_id"].unique().to_list()

    targets = pl.DataFrame(
        [
            {
                "metric_id": str(t["metric_id"]),
                "t_scope": t["scope_type"],
                "scope_key": t["scope_code"],
                "target_id": str(t["target_id"]),
                "target_version": t["version"],
                "base_target": _f(t["target_value"]),
                "base_type": t["target_type"],
                "base_weight": _f(t["weight"]),
                "base_cap": _f(t["cap"]),
                "target_currency": t["currency_code"],
            }
            for t in Target.objects.filter(
                org_id=org_id,
                metric_id__in=metric_ids,
                period_key=period_key,
                series_type="target",
                product_line_code="",
                state="published",
            ).values(
                "metric_id",
                "scope_type",
                "scope_code",
                "target_id",
                "version",
                "target_value",
                "target_type",
                "weight",
                "cap",
                "currency_code",
            )
        ],
        schema={
            "metric_id": S,
            "t_scope": S,
            "scope_key": S,
            "target_id": S,
            "target_version": pl.Int64,
            "base_target": F,
            "base_type": S,
            "base_weight": F,
            "base_cap": F,
            "target_currency": S,
        },
    )
    facts = pl.DataFrame(
        [
            {
                "metric_id": str(f["metric_id"]),
                "subject_id": str(f["subject_id"]),
                "reported": _f(f["actual_value"]),
                "actual_currency": f["currency_code"],
                "run_id": str(f["run_id"]) if f["run_id"] else None,
            }
            for f in FactActualMonthly.objects.filter(
                org_id=org_id, period_key=period_key, metric_id__in=metric_ids
            )
            .exclude(**({"metric__collection_method": "manual_input"} if hide_manual else {}))
            .values("metric_id", "subject_id", "actual_value", "currency_code", "run_id")
        ],
        schema={
            "metric_id": S,
            "subject_id": S,
            "reported": F,
            "actual_currency": S,
            "run_id": S,
        },
    )
    overrides = pl.DataFrame(
        [
            {
                "override_id": str(o.override_id),
                "metric_code": o.metric.metric_code,
                "o_scope": o.scope_type,
                "o_code": o.scope_code,
                "change_type": o.change_type,
                "value": _f(o.override_value),
                "value_text": str(o.override_value) if o.override_value is not None else None,
                "text": o.override_text,
                "reason": o.reason,
                "rank": SCOPE_RANK[o.scope_type],
                "approved": micros(o.approved_at),
            }
            for o in Override.objects.filter(applicable_overrides(org_id, period_key))
            .filter(metric__metric_code__in=metrics["metric_code"].unique().to_list())
            .select_related("metric")
        ],
        schema={
            "override_id": S,
            "metric_code": S,
            "o_scope": S,
            "o_code": S,
            "change_type": S,
            "value": F,
            "value_text": S,
            "text": S,
            "reason": S,
            "rank": pl.Int64,
            "approved": pl.Int64,
        },
    )

    excl = pl.DataFrame(
        [
            {
                "metric_id": str(e["metric_id"]),
                "subject_id": str(e["subject_id"]) if e["subject_id"] else None,
                "reason": e["reason"],
            }
            for e in ScoreExclusion.objects.filter(
                org_id=org_id, product="scorecards", period_key=period_key, metric_id__in=metric_ids
            ).values("metric_id", "subject_id", "reason")
        ],
        schema={"metric_id": S, "subject_id": S, "reason": S},
    )

    # One row per (subject, metric) on the subject's profile.
    rows = members.join(metrics, on="profile_code", how="inner").with_columns(
        pl.when(pl.col("target_scope") == "subject")
        .then(pl.col("subject_id"))
        .otherwise(pl.col("profile_code"))
        .alias("scope_key")
    )
    rows = rows.join(
        targets,
        left_on=["metric_id", "target_scope", "scope_key"],
        right_on=["metric_id", "t_scope", "scope_key"],
        how="left",
    ).join(facts, on=["metric_id", "subject_id"], how="left")
    rows = (
        rows.join(
            excl.filter(pl.col("subject_id").is_not_null()).rename({"reason": "excl_own"}),
            on=["metric_id", "subject_id"],
            how="left",
        )
        .join(
            excl.filter(pl.col("subject_id").is_null()).select(
                "metric_id", pl.col("reason").alias("excl_all")
            ),
            on="metric_id",
            how="left",
        )
        .with_columns(pl.coalesce("excl_own", "excl_all").alias("exclusion_reason"))
    )

    rows = _with_overrides(rows, overrides)
    rows = _with_fx(org_id, period_key, rows)
    rows = _score(rows)
    return _assemble(org_id, period_key, members, rows)


def _with_overrides(rows: pl.DataFrame, overrides: pl.DataFrame) -> pl.DataFrame:
    """The winning override per change type: subject > profile > dimension, latest first."""
    keys = pl.concat(
        [
            rows.select(
                "subject_id",
                "metric_code",
                pl.lit("subject").alias("o_scope"),
                pl.col("subject_id").alias("o_code"),
            ),
            rows.select(
                "subject_id",
                "metric_code",
                pl.lit("profile").alias("o_scope"),
                pl.col("profile_code").alias("o_code"),
            ),
            rows.select(
                "subject_id",
                "metric_code",
                pl.lit("dimension").alias("o_scope"),
                pl.col("dims").alias("o_code"),
            ).explode("o_code", empty_as_null=False),
        ]
    ).drop_nulls("o_code")
    winners = (
        keys.join(overrides, on=["metric_code", "o_scope", "o_code"], how="inner")
        .sort(["rank", "approved", "override_id"], descending=[False, True, False])
        .group_by(["subject_id", "metric_code", "change_type"], maintain_order=True)
        .first()
    )
    for change in OVERRIDE_CHANGE_TYPES:
        part = winners.filter(pl.col("change_type") == change).select(
            "subject_id",
            "metric_code",
            pl.col("override_id").alias(f"ov_{change}_id"),
            pl.col("value").alias(f"ov_{change}"),
            pl.col("value_text").alias(f"ov_{change}_value_text"),
            pl.col("text").alias(f"ov_{change}_text"),
            pl.col("reason").alias(f"ov_{change}_reason"),
            pl.col("o_scope").alias(f"ov_{change}_scope"),
            pl.col("o_code").alias(f"ov_{change}_code"),
        )
        rows = rows.join(part, on=["subject_id", "metric_code"], how="left")
    return rows


def _with_fx(org_id: str, period_key: str, rows: pl.DataFrame) -> pl.DataFrame:
    need = (
        pl.col("reported").is_not_null()
        & pl.col("ov_actual_id").is_null()
        & pl.col("actual_currency").is_not_null()
        & pl.col("target_currency").is_not_null()
        & (pl.col("actual_currency") != pl.col("target_currency"))
    )
    rows = rows.with_columns(need.fill_null(False).alias("needs_fx"))
    pairs = rows.filter(pl.col("needs_fx")).select("actual_currency", "target_currency").unique()
    rates: list[tuple[str, str, float | None]] = []
    for src, dst in pairs.iter_rows():
        try:
            rates.append((src, dst, float(fx_rate(org_id, src, dst, period_key, "average"))))
        except MissingRate:
            rates.append((src, dst, None))
    rate_frame = pl.DataFrame(
        rates,
        schema={"actual_currency": S, "target_currency": S, "fx": F},
        orient="row",
    )
    return rows.join(
        rate_frame, on=["actual_currency", "target_currency"], how="left"
    ).with_columns(pl.when(pl.col("needs_fx")).then(pl.col("fx")).otherwise(None).alias("fx"))


def _score(rows: pl.DataFrame) -> pl.DataFrame:
    c = pl.col
    target_type = pl.coalesce(c("ov_target_type_text"), c("base_type"))
    v, months = c("base_target"), c("months").cast(F)
    adjusted = (
        pl.when(target_type == "yearly")
        .then(v / months)
        .when(target_type == "cumulative")
        .then(v * c("months_elapsed"))
        .when(target_type == "quarterly")
        .then(v * c("quarters_elapsed"))
        .when(target_type == "prorated")
        .then(v / months * c("months_elapsed"))
        .otherwise(v)
    )
    rows = rows.with_columns(
        target_type.alias("target_type"),
        pl.coalesce(c("ov_target"), adjusted).alias("target_value"),
        pl.coalesce(c("ov_weight"), c("base_weight")).alias("weight"),
        pl.coalesce(c("ov_cap"), c("base_cap")).alias("cap"),
        pl.when(c("ov_actual_id").is_not_null())
        .then(c("ov_actual"))
        .when(c("needs_fx"))
        .then(c("reported") * c("fx"))
        .otherwise(c("reported"))
        .alias("actual_value"),
    )
    no_target = (
        c("target_value").is_null()
        | c("weight").is_null()
        | c("cap").is_null()
        | (c("target_value") <= 0)
    )
    state = (
        pl.when(c("ov_actual_id").is_null() & c("reported").is_null())
        .then(pl.lit("not_reported"))
        .when(no_target)
        .then(pl.lit("no_target"))
        .when(c("actual_value").is_null())
        .then(pl.lit("no_fx_rate"))
        .when(c("actual_value") == 0)
        .then(pl.lit("zero_actual"))
        .otherwise(pl.lit("scored"))
    )
    rows = rows.with_columns(state.alias("state")).with_columns(
        pl.when(~c("state").is_in(["scored", "zero_actual"]) & c("exclusion_reason").is_not_null())
        .then(pl.lit("excluded"))
        .otherwise(c("state"))
        .alias("state")
    )
    counted = c("state").is_in(["scored", "zero_actual"])
    lower = c("direction") == "lower_is_better"
    pct = (
        pl.when(counted & lower & (c("actual_value") == 0))
        .then(None)
        .when(counted & lower)
        .then(c("target_value") / c("actual_value"))
        .when(counted)
        .then(c("actual_value") / c("target_value"))
        .otherwise(None)
    )
    rows = rows.with_columns(pct.alias("pct"))
    score = (
        pl.when(counted & c("pct").is_null())
        .then(c("cap") / 100)
        .when(counted)
        .then(
            pl.min_horizontal(pl.max_horizontal(c("pct"), pl.lit(0.0)) * c("weight"), c("cap"))
            / 100
        )
        .otherwise(None)
    )
    return rows.with_columns(score.alias("score"), counted.alias("counted"))


def _assemble(
    org_id: str, period_key: str, members: pl.DataFrame, rows: pl.DataFrame
) -> list[SubjectScore]:
    c = pl.col
    policy = settings_for(org_id).denominator_policy
    bands = bands_for(org_id)
    awaiting = c("state") == "not_reported"
    totals = rows.group_by("subject_id").agg(
        c("score").filter(c("counted")).sum().alias("raw_total"),
        (c("cap").filter(c("counted")).sum() / 100).alias("raw_cap"),
        c("weight").filter(c("counted")).fill_null(0).sum().alias("weight_scored"),
        c("weight").filter(c("counted") | awaiting).fill_null(0).sum().alias("weight_expected"),
        c("counted").sum().alias("metrics_scored"),
        pl.len().alias("metrics_total"),
        awaiting.sum().alias("not_reported"),
        (c("state") == "excluded").sum().alias("excluded"),
    )
    totals = totals.with_columns(
        pl.when(c("weight_scored") > 0)
        .then(c("weight_expected") / c("weight_scored"))
        .otherwise(None)
        .alias("factor")
    ).with_columns((c("raw_total") * c("factor")).alias("graded"))
    if policy == "redistribute":
        totals = totals.with_columns(
            pl.when(c("factor").is_not_null())
            .then(c("graded"))
            .otherwise(c("raw_total"))
            .alias("raw_total"),
            pl.when(c("factor").is_not_null())
            .then(c("raw_cap") * c("factor"))
            .otherwise(c("raw_cap"))
            .alias("raw_cap"),
        )
        rows = rows.join(
            totals.select("subject_id", "factor"), on="subject_id", how="left"
        ).with_columns(
            pl.when(c("counted") & c("factor").is_not_null())
            .then(c("score") * c("factor"))
            .otherwise(c("score"))
            .alias("score")
        )
    totals = totals.with_columns(
        pl.when(c("raw_cap") > 0)
        .then(c("raw_total") / c("raw_cap"))
        .otherwise(None)
        .alias("achievement")
    )

    by_subject: dict[str, list[MetricScore]] = {}
    for r in rows.sort(["staff_no", "metric_code"]).iter_rows(named=True):
        by_subject.setdefault(r["subject_id"], []).append(_metric(r))
    total_rows = {r["subject_id"]: r for r in totals.iter_rows(named=True)}

    out: list[SubjectScore] = []
    for m in members.iter_rows(named=True):  # already in staff-number order
        subject_id = m["subject_id"]
        t = total_rows.get(subject_id, _EMPTY)
        graded = q_ratio(t["graded"])
        scored = by_subject.get(subject_id, [])
        out.append(
            SubjectScore(
                subject_id=subject_id,
                assignment_id=m["assignment_id"],
                profile_code=m["profile_code"],
                period_key=period_key,
                cycle=CyclePosition(
                    months=m["months"],
                    months_elapsed=m["months_elapsed"],
                    quarters_elapsed=m["quarters_elapsed"],
                ),
                policy=policy,
                metrics=tuple(scored),
                total_score=q_ratio(t["raw_total"] or 0) or Decimal(0),
                total_cap=q_ratio(t["raw_cap"] or 0) or Decimal(0),
                weight_scored=q_value(t["weight_scored"] or 0) or Decimal(0),
                weight_expected=q_value(t["weight_expected"] or 0) or Decimal(0),
                graded_score=graded,
                achievement_pct=q_ratio(t["achievement"]),
                metrics_scored=t["metrics_scored"],
                metrics_total=t["metrics_total"],
                not_reported=t["not_reported"],
                no_target=t["metrics_total"]
                - t["metrics_scored"]
                - t["not_reported"]
                - t["excluded"],
                excluded=t["excluded"],
                band=lookup(bands, graded),
            )
        )
    return out


# A subject whose profile has no scorecard metrics.
_EMPTY: dict[str, Any] = {
    "raw_total": 0,
    "raw_cap": 0,
    "weight_scored": 0,
    "weight_expected": 0,
    "graded": None,
    "achievement": None,
    "metrics_scored": 0,
    "metrics_total": 0,
    "not_reported": 0,
    "excluded": 0,
}


def _metric(r: dict[str, Any]) -> MetricScore:
    applied = []
    for change in sorted(OVERRIDE_CHANGE_TYPES):
        if r.get(f"ov_{change}_id") is None:
            continue
        text = r[f"ov_{change}_value_text"]
        applied.append(
            AppliedOverride(
                override_id=r[f"ov_{change}_id"],
                change_type=change,
                scope_type=r[f"ov_{change}_scope"],
                scope_code=r[f"ov_{change}_code"],
                value=Decimal(text) if text is not None else None,
                text=r[f"ov_{change}_text"],
                reason=r[f"ov_{change}_reason"],
            )
        )
    has_target = r["target_id"] is not None
    fx = r["fx"]
    return MetricScore(
        metric_id=r["metric_id"],
        metric_code=r["metric_code"],
        display_name=r["display_name"],
        direction=r["direction"],
        unit=r["unit"],
        decimal_places=r["decimal_places"],
        state=r["state"],
        target_id=r["target_id"],
        target_version=r["target_version"],
        target_scope=r["target_scope"] if has_target else None,
        base_target=q_value(r["base_target"]),
        target_type=r["target_type"],
        target_currency=r["target_currency"],
        target_value=q_value(r["target_value"]),
        reported_actual=q_value(r["reported"]),
        actual_currency=r["actual_currency"],
        fx_rate=q_ratio(fx) if fx is not None and fx != 1 else None,
        actual_value=q_value(r["actual_value"]),
        run_id=r["run_id"] if r["ov_actual_id"] is None else None,
        weight=q_value(r["weight"]),
        cap=q_value(r["cap"]),
        pct_achieved=q_ratio(r["pct"]),
        score=q_ratio(r["score"]),
        overrides=tuple(applied),
        exclusion_reason=r["exclusion_reason"] if r["state"] == "excluded" else None,
    )
