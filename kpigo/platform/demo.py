"""Demo mode (PRD OP-9): a synthetic Scorecards world an evaluator can explore, and a
clean way to remove it.

``seed_demo`` builds a small retail-banking relationship-management world — a manager with
a team of six RMs on one profile, four metrics, published monthly targets and actuals for
the last three months, and a default rating-band set when the org has none — so the live
scorecard, history and roll-up screens light up without closing a period. Everything it
creates is recorded in :class:`~kpigo.platform.models.DemoArtifact` and then the period's
visibility closure is rebuilt, so the data behaves exactly like the real thing.

``reset_demo`` reads that ledger and deletes only those rows, youngest batch first and in
reverse dependency order, then rebuilds the affected periods' closures. It never looks at a
row it did not create, so a client's own subjects, metrics and targets are untouchable.

The world is deliberately namespaced — subjects, profile and metric codes all carry a
``demo_`` / ``DEMO`` marker — so it reads as a demonstration and cannot collide with a
client's registry. Everything runs inside the action's transaction (and a dry run rolls it
all back), because the loader does no commits of its own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import models
from django.utils import timezone

from kpigo.action import ActionContext
from kpigo.hierarchy import closure
from kpigo.hierarchy.models import Assignment, ReportingEdge, Subject
from kpigo.ingestion.models import FactActualMonthly
from kpigo.metrics.models import Metric, MetricBinding, MetricFamily, MetricProfileAssignment
from kpigo.metrics.naming import normalise_name
from kpigo.platform.models import DemoArtifact
from kpigo.scorecards.models import RatingBand, Target

PROFILE = "demo_rm"
ROLE = "rm"
PRODUCT = "scorecards"
MONTHS_BACK = 3  # current period and the two before it

# The manager and their six relationship managers. The manager carries the same profile so
# they have a scorecard of their own as well as a team that rolls up to them.
MANAGER = ("DEMO-M1", "Dana Mensah (Demo Area Manager)")
TEAM = [
    ("DEMO-E1", "Ama Boateng (Demo)"),
    ("DEMO-E2", "Kwame Owusu (Demo)"),
    ("DEMO-E3", "Efua Addo (Demo)"),
    ("DEMO-E4", "Yaw Darko (Demo)"),
    ("DEMO-E5", "Akua Mensima (Demo)"),
    ("DEMO-E6", "Kofi Asante (Demo)"),
]

# metric_code → (name, unit, aggregation, direction, target_scope, weight, cap)
METRICS: dict[str, tuple[str, str, str, str, str, int, int]] = {
    "demo_casa_growth": (
        "Demo CASA balance growth",
        "currency",
        "sum",
        "higher_is_better",
        "profile",
        40,
        60,
    ),
    "demo_ntb_accounts": (
        "Demo new-to-bank accounts",
        "count",
        "sum",
        "higher_is_better",
        "profile",
        20,
        30,
    ),
    "demo_service_tat": (
        "Demo service turnaround",
        "days",
        "average",
        "lower_is_better",
        "profile",
        15,
        22,
    ),
    "demo_fee_income": (
        "Demo fee and commission income",
        "currency",
        "sum",
        "higher_is_better",
        "subject",
        25,
        37,
    ),
}

# Default rating bands, used only when the org has none of its own (threshold → label).
DEFAULT_BANDS = [
    ("Needs Focus", "0", 1),
    ("Gaining Momentum", "0.75", 3),
    ("On Target", "1.00", 5),
    ("Exemplary", "1.20", 7),
]


@dataclass
class DemoReport:
    batch_id: str = ""
    subjects: int = 0
    metrics: int = 0
    targets: int = 0
    actuals: int = 0
    periods: list[str] = field(default_factory=list)
    bands_applied: bool = False
    note: str = ""


def _period_key(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}"


def _shift(period_key: str, months: int) -> str:
    index = int(period_key[:4]) * 12 + int(period_key[4:]) - 1 + months
    return f"{index // 12:04d}{index % 12 + 1:02d}"


def recent_periods(today: date | None = None) -> list[str]:
    """The current period and the ``MONTHS_BACK - 1`` before it, oldest first."""
    current = _period_key(today or timezone.now().date())
    keys = [_shift(current, -n) for n in range(MONTHS_BACK)]
    return list(reversed(keys))


def has_demo(org_id: str) -> bool:
    return DemoArtifact.objects.filter(org_id=org_id).exists()


class _Ledger:
    """Records every row a seed run creates against one batch id."""

    def __init__(self, org_id: str, batch_id: str) -> None:
        self.org_id = org_id
        self.batch_id = batch_id
        self._rows: list[DemoArtifact] = []

    def track(self, obj: object, note: str = "") -> None:
        meta = obj._meta  # type: ignore[attr-defined]
        self._rows.append(
            DemoArtifact(
                org_id=self.org_id,
                batch_id=self.batch_id,
                model_label=f"{meta.app_label}.{meta.object_name}",
                object_pk=str(obj.pk),  # type: ignore[attr-defined]
                note=note,
            )
        )

    def flush(self) -> None:
        DemoArtifact.objects.bulk_create(self._rows)
        self._rows.clear()


def _effective_from(periods: list[str]) -> date:
    """The first day of the oldest demo period, so assignments and metrics are in force
    for every period that has data."""
    oldest = periods[0]
    return date(int(oldest[:4]), int(oldest[4:]), 1)


def _make_subjects(ctx: ActionContext, led: _Ledger, since: date) -> dict[str, Subject]:
    subjects: dict[str, Subject] = {}
    for staff_no, name in [MANAGER, *TEAM]:
        subject = Subject.objects.create(
            org_id=ctx.org_id,
            staff_no=staff_no,
            full_name=name,
            email=f"{staff_no.lower().replace('-', '.')}@demo.example",
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        led.track(subject, "subject")
        assignment = Assignment.objects.create(
            org_id=ctx.org_id,
            subject=subject,
            role_code=ROLE,
            profile_code=PROFILE,
            effective_from=since,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        led.track(assignment, "assignment")
        subjects[staff_no] = subject
    # Every RM reports solid-line to the manager, so the team rolls up.
    manager = subjects[MANAGER[0]]
    for staff_no, _ in TEAM:
        edge = ReportingEdge.objects.create(
            org_id=ctx.org_id,
            subject=subjects[staff_no],
            manager=manager,
            relationship_type="solid",
            counts_toward_rollup=True,
            effective_from=since,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        led.track(edge, "reporting edge")
    return subjects


def _make_metrics(ctx: ActionContext, led: _Ledger, since: date) -> dict[str, Metric]:
    metrics: dict[str, Metric] = {}
    for code, (name, unit, agg, direction, scope, _w, _c) in METRICS.items():
        family = MetricFamily.objects.create(
            org_id=ctx.org_id,
            display_name=name,
            normalised_name=normalise_name(name),
            description="",
            owner_user_id=ctx.user_id,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        led.track(family, "metric family")
        metric = Metric.objects.create(
            org_id=ctx.org_id,
            family=family,
            metric_code=code,
            display_name=name,
            direction=direction,
            aggregation=agg,
            unit=unit,
            target_scope=scope,
            collection_method="feed",
            status="active",
            computation_note="",
            effective_from=since,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        led.track(metric, "metric")
        binding = MetricBinding.objects.create(
            metric=metric, product=PRODUCT, created_by=ctx.user_id
        )
        led.track(binding, "metric binding")
        profile = MetricProfileAssignment.objects.create(
            metric=metric,
            profile_code=PROFILE,
            product=PRODUCT,
            effective_from=since,
            created_by=ctx.user_id,
        )
        led.track(profile, "profile metric")
        metrics[code] = metric
    return metrics


def _make_bands(ctx: ActionContext, led: _Ledger) -> bool:
    """Adopt a default band set only when the org has none — never replace a client's."""
    if RatingBand.objects.filter(org_id=ctx.org_id, product=PRODUCT).exists():
        return False
    for order, (label, threshold, ramp) in enumerate(DEFAULT_BANDS):
        band = RatingBand.objects.create(
            org_id=ctx.org_id,
            product=PRODUCT,
            label=label,
            threshold=Decimal(threshold),
            ramp_position=ramp,
            sort_order=order,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
        led.track(band, "rating band")
    return True


def _actual_value(code: str, subject_index: int, month_index: int) -> Decimal:
    """A spread of deterministic figures so scorecards land across the bands and improve
    over the months. Target value is 100 everywhere; these land roughly 70%–135% of it."""
    base = 70 + (subject_index * 9) % 60  # 70..124 across the team
    drift = month_index * 6  # each later month a little stronger
    if code == "demo_service_tat":
        # Lower is better: express as a smaller-is-better figure around the 100 target.
        return Decimal(max(40, 150 - base - drift))
    return Decimal(base + drift)


def _make_targets_and_actuals(
    ctx: ActionContext,
    led: _Ledger,
    metrics: dict[str, Metric],
    subjects: dict[str, Subject],
    periods: list[str],
) -> tuple[int, int]:
    now = timezone.now()
    assignments = {
        staff_no: Assignment.objects.get(org_id=ctx.org_id, subject=subject)
        for staff_no, subject in subjects.items()
    }
    team_order = {staff_no: i for i, (staff_no, _) in enumerate([MANAGER, *TEAM])}
    targets = 0
    actuals = 0
    for period in periods:
        month_index = periods.index(period)
        for code, (_n, _u, _a, _d, scope, weight, cap) in METRICS.items():
            metric = metrics[code]
            # One profile-scoped target, or one per subject for subject-scoped metrics.
            if scope == "profile":
                target = Target.objects.create(
                    org_id=ctx.org_id,
                    metric=metric,
                    scope_type="profile",
                    scope_code=PROFILE,
                    period_key=period,
                    target_value=Decimal("100"),
                    weight=Decimal(weight),
                    cap=Decimal(max(cap, weight)),
                    version=1,
                    state="published",
                )
                led.track(target, f"target {period}")
                targets += 1
            else:
                for staff_no in subjects:
                    target = Target.objects.create(
                        org_id=ctx.org_id,
                        metric=metric,
                        scope_type="subject",
                        scope_code=str(subjects[staff_no].subject_id),
                        period_key=period,
                        target_value=Decimal("100"),
                        weight=Decimal(weight),
                        cap=Decimal(max(cap, weight)),
                        version=1,
                        state="published",
                    )
                    led.track(target, f"target {period}")
                    targets += 1
            # Actuals for everyone.
            for staff_no, subject in subjects.items():
                fact = FactActualMonthly.objects.create(
                    org_id=ctx.org_id,
                    metric=metric,
                    subject_id=subject.subject_id,
                    assignment=assignments[staff_no],
                    period_key=period,
                    actual_value=_actual_value(code, team_order[staff_no], month_index),
                    loaded_at=now,
                )
                led.track(fact, f"actual {period}")
                actuals += 1
    return targets, actuals


def seed_demo(ctx: ActionContext) -> DemoReport:
    """Build the demo world. Caller's transaction governs persistence (and dry-run rollback)."""
    batch_id = str(uuid.uuid4())
    led = _Ledger(ctx.org_id, batch_id)
    periods = recent_periods()
    since = _effective_from(periods)

    subjects = _make_subjects(ctx, led, since)
    metrics = _make_metrics(ctx, led, since)
    bands_applied = _make_bands(ctx, led)
    targets, actuals = _make_targets_and_actuals(ctx, led, metrics, subjects, periods)
    led.flush()

    # Rebuild each period's visibility closure so the manager sees the team and roll-ups work.
    for period in periods:
        closure.build(ctx.org_id, period, ctx.user_id)

    return DemoReport(
        batch_id=batch_id,
        subjects=len(subjects),
        metrics=len(metrics),
        targets=targets,
        actuals=actuals,
        periods=periods,
        bands_applied=bands_applied,
        note=(
            f"Seeded a demo team of {len(subjects)} on {len(metrics)} metrics across "
            f"{len(periods)} periods ({periods[0]}–{periods[-1]}). Periods are left open so "
            "the live scorecards compute from this data."
        ),
    )


# Single-UUID-PK models, deleted by the primary keys the ledger recorded. Children first.
_PK_DELETE_ORDER: list[tuple[str, type[models.Model]]] = [
    ("scorecards.Target", Target),
    ("metrics.Metric", Metric),
    ("metrics.MetricFamily", MetricFamily),
    ("hierarchy.ReportingEdge", ReportingEdge),
    ("hierarchy.Assignment", Assignment),
    ("hierarchy.Subject", Subject),
    ("scorecards.RatingBand", RatingBand),
]

# Composite-PK children have no single pk to match on, so they are removed through their FK
# to the demo metrics (nothing but the demo references those metrics). Deleted before the
# metrics themselves, which PROTECT against a dangling profile assignment.
_FK_BY_METRIC: list[type[models.Model]] = [
    FactActualMonthly,
    MetricProfileAssignment,
    MetricBinding,
]


@dataclass
class ResetReport:
    batches: int = 0
    deleted: int = 0
    periods: list[str] = field(default_factory=list)
    note: str = ""


def reset_demo(ctx: ActionContext) -> ResetReport:
    """Delete every row the demo seeder created for this org, then rebuild the closures it
    touched. Rows it never created are not looked at."""
    arts = list(DemoArtifact.objects.filter(org_id=ctx.org_id))
    if not arts:
        return ResetReport(note="No demo data to remove.")

    batch_ids = {a.batch_id for a in arts}
    # Which periods had demo data, so we can rebuild their closures after the subjects go.
    periods = sorted(
        {a.note.split()[-1] for a in arts if a.note.startswith(("target ", "actual "))}
    )

    pks_by_model: dict[str, list[str]] = {}
    for art in arts:
        pks_by_model.setdefault(art.model_label, []).append(art.object_pk)
    metric_pks = pks_by_model.get("metrics.Metric", [])

    deleted = 0
    # Composite-PK children first, via the demo metrics they hang off.
    for model in _FK_BY_METRIC:
        if metric_pks:
            count, _ = model._default_manager.filter(metric_id__in=metric_pks).delete()
            deleted += count
    # Then the single-PK rows, children before their parents.
    for label, model in _PK_DELETE_ORDER:
        pks = pks_by_model.get(label)
        if not pks:
            continue
        count, _ = model._default_manager.filter(pk__in=pks).delete()
        deleted += count

    DemoArtifact.objects.filter(org_id=ctx.org_id, batch_id__in=batch_ids).delete()

    for period in periods:
        closure.build(ctx.org_id, period, ctx.user_id)

    return ResetReport(
        batches=len(batch_ids),
        deleted=deleted,
        periods=periods,
        note=f"Removed the demo data ({deleted} rows) and rebuilt {len(periods)} period(s).",
    )
