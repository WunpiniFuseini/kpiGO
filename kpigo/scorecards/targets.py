"""The target workbench (Scope §6.7, App Flow §7.4, PRD SC-10, SC-11).

Upload or copy forward → validate → draft → publish as a batch → live. Drafts
are visible only here. Publishing makes a new version and supersedes the old; a
batch reverts as a unit while its periods are still in the future. Revising a
target for a period that has started is an override, never a new version, so
the reason and the approver are forced (App Flow §7.4).
"""

from __future__ import annotations

import contextlib
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db.models import Max, Q
from django.utils import timezone

from kpigo.action import Conflict, NotFound
from kpigo.hierarchy.models import Subject
from kpigo.metrics.models import Metric
from kpigo.scorecards import roster
from kpigo.scorecards.config import Settings, period_phase, settings_for
from kpigo.scorecards.models import (
    SERIES_TYPES,
    TARGET_TYPES,
    Target,
    TargetPublishBatch,
)

Key = tuple[str, str, str, str, str]  # metric_id, scope_type, scope_code, period_key, series

COLUMNS = (
    "metric_code",
    "scope_type",
    "scope_code",
    "period_key",
    "series_type",
    "target_value",
    "target_type",
    "weight",
    "cap",
    "currency_code",
)
# Products whose metrics take targets here: Scorecards, and Agent Performance's pacing.
TARGETED_PRODUCTS = ("scorecards", *roster.AGENT_PRODUCTS)
REQUIRED = ("metric_code", "scope_type", "scope_code", "period_key", "target_value")


@dataclass
class Finding:
    code: str
    message: str
    severity: str = "error"
    row_no: int | None = None
    column: str | None = None
    value: str | None = None


@dataclass
class Draft:
    """One validated row, resolved to the metric version and scope it targets."""

    row_no: int
    metric: Metric
    scope_type: str
    scope_code: str
    scope_label: str
    period_key: str
    series_type: str
    target_value: Decimal
    target_type: str
    weight: Decimal | None
    cap: Decimal | None
    currency_code: str | None

    @property
    def key(self) -> Key:
        return (
            str(self.metric.metric_id),
            self.scope_type,
            self.scope_code,
            self.period_key,
            self.series_type,
        )


@dataclass
class Checked:
    drafts: list[Draft] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]


class _Problems:
    """The findings for one row of a sheet."""

    def __init__(self, row_no: int, values: dict[str, str | None]) -> None:
        self.row_no = row_no
        self.values = values
        self.items: list[Finding] = []

    def add(
        self, code: str, message: str, column: str | None = None, severity: str = "error"
    ) -> None:
        value = self.values.get(column) if column else None
        self.items.append(Finding(code, message, severity, self.row_no, column, value))

    @property
    def failed(self) -> bool:
        return any(p.severity == "error" for p in self.items)


def _decimal(text: str | None) -> Decimal | None:
    if text is None or str(text).strip() == "":
        return None
    try:
        value = Decimal(str(text).strip().replace(",", ""))
    except InvalidOperation:
        raise ValueError(text) from None
    if not value.is_finite():
        raise ValueError(text)
    return value


def _subject_lookup(org_id: str, refs: Iterable[str]) -> dict[str, tuple[str, str]]:
    """Staff number or subject_id → (subject_id, "staff_no full name")."""
    refs = {r for r in refs if r}
    ids = set()
    for r in refs:
        with contextlib.suppress(ValueError):
            ids.add(str(uuid.UUID(r)))
    rows = Subject.objects.filter(org_id=org_id).filter(
        Q(staff_no__in=refs) | Q(subject_id__in=ids)
    )
    found: dict[str, tuple[str, str]] = {}
    for s in rows:
        label = f"{s.staff_no} {s.full_name}"
        found[s.staff_no] = (str(s.subject_id), label)
        found[str(s.subject_id)] = (str(s.subject_id), label)
    return found


def check_rows(org_id: str, rows: Sequence[dict[str, Any]], *, first_row_no: int = 1) -> Checked:
    """Validate a sheet before anything is written (Scope §6.7 step 1).

    Checks the metric exists and is bound to Scorecards or Agent Performance,
    the scope matches the metric's declared ``target_scope`` (§3.3), values,
    weights and caps are plausible (weights only for Scorecards metrics), the
    period accepts a new version, and no key repeats. Weight sums are checked
    at publish, over the whole profile.
    """
    settings = settings_for(org_id)
    out = Checked()
    subjects = _subject_lookup(
        org_id,
        (
            str(r.get("scope_code") or "").strip()
            for r in rows
            if str(r.get("scope_type") or "").strip() == "subject"
        ),
    )
    metric_cache: dict[tuple[str, str], Metric | None] = {}
    profile_cache: dict[str, dict[str, list[Metric]]] = {}
    phase_cache: dict[str, str] = {}
    seen: dict[Key, int] = {}

    for offset, raw in enumerate(rows):
        row_no = first_row_no + offset
        values = {k: (None if raw.get(k) is None else str(raw.get(k)).strip()) for k in COLUMNS}
        problems = _Problems(row_no, values)
        bad = problems.add

        for col in REQUIRED:
            if not values.get(col):
                bad("required", f"{col} is required.", col)
        if problems.failed:
            out.findings += problems.items
            continue

        period_key = values["period_key"] or ""
        if len(period_key) != 6 or not period_key.isdigit() or not 1 <= int(period_key[4:]) <= 12:
            bad("period_key", "period_key must be YYYYMM.", "period_key")
            out.findings += problems.items
            continue

        code = values["metric_code"] or ""
        if (code, period_key) not in metric_cache:
            metric_cache[(code, period_key)] = roster.metric_in_force(org_id, code, period_key)
        metric = metric_cache[(code, period_key)]
        if metric is None:
            bad("unknown_metric", f"No metric '{code}' is in force in {period_key}.", "metric_code")
            out.findings += problems.items
            continue
        if metric.status in ("inactive", "deprecated"):
            bad(
                "metric_inactive",
                f"'{code}' is {metric.status}; it takes no new targets.",
                "metric_code",
            )
        # Agent Performance paces against the same published targets (PRD AP-2);
        # only a Scorecards metric is weighted, so only it needs a weight and cap.
        bound = set(
            metric.bindings.filter(product__in=TARGETED_PRODUCTS, is_active=True).values_list(
                "product", flat=True
            )
        )
        if not bound:
            bad(
                "not_scorecards",
                f"'{code}' is not bound to Scorecards or Agent Performance.",
                "metric_code",
            )
        weighted = "scorecards" in bound

        scope_type = values["scope_type"] or ""
        if scope_type not in ("profile", "subject"):
            bad("scope_type", "scope_type must be profile or subject.", "scope_type")
        elif scope_type != metric.target_scope:
            bad(
                "scope_mismatch",
                f"'{code}' takes {metric.target_scope}-level targets, not {scope_type}-level.",
                "scope_type",
            )

        scope_code = values["scope_code"] or ""
        scope_label = scope_code
        if scope_type == "subject":
            hit = subjects.get(scope_code)
            if hit is None:
                bad(
                    "unknown_subject",
                    f"No subject with staff number or id '{scope_code}'.",
                    "scope_code",
                )
            else:
                scope_code, scope_label = hit
        elif scope_type == "profile":
            if period_key not in profile_cache:
                profile_cache[period_key] = roster.profile_metrics(org_id, period_key)
            on_card = profile_cache[period_key].get(scope_code, [])
            if not weighted:
                on_card = roster.agent_profile_metrics(org_id, period_key, scope_code)
            if all(m.metric_code != code for m in on_card):
                bad(
                    "not_on_profile",
                    f"'{code}' is not on profile {scope_code}'s scorecard in {period_key}; "
                    "the target is kept but scores nothing until it is.",
                    "scope_code",
                    severity="warning",
                )

        series = values["series_type"] or "target"
        if series not in SERIES_TYPES:
            bad(
                "series_type",
                f"series_type must be one of {', '.join(SERIES_TYPES)}.",
                "series_type",
            )
        target_type = values["target_type"] or "monthly"
        if target_type not in TARGET_TYPES:
            bad(
                "target_type",
                f"target_type must be one of {', '.join(TARGET_TYPES)}.",
                "target_type",
            )

        parsed: dict[str, Decimal | None] = {}
        for col in ("target_value", "weight", "cap"):
            try:
                parsed[col] = _decimal(values.get(col))
            except ValueError:
                bad("not_a_number", f"{col} is not a number.", col)
                parsed[col] = None
        value, weight, cap = parsed["target_value"], parsed["weight"], parsed["cap"]
        if value is not None and value <= 0 and series == "target":
            bad(
                "target_not_positive",
                "A target must be above zero: achievement divides by it.",
                "target_value",
            )
        if series == "target" and weighted:
            _check_weight_cap(settings, weight, cap, bad)

        currency = values.get("currency_code") or None
        if currency is not None and (
            len(currency) != 3 or not currency.isalpha() or not currency.isupper()
        ):
            bad("currency_code", "currency_code must be three capital letters.", "currency_code")

        if period_key not in phase_cache:
            phase_cache[period_key] = period_phase(org_id, period_key)
        phase = phase_cache[period_key]
        if phase == "locked":
            bad("period_locked", f"{period_key} is closed. Targets cannot change.", "period_key")

        if problems.failed:
            out.findings += problems.items
            continue
        assert value is not None
        draft = Draft(
            row_no=row_no,
            metric=metric,
            scope_type=scope_type,
            scope_code=scope_code,
            scope_label=scope_label,
            period_key=period_key,
            series_type=series,
            target_value=value,
            target_type=target_type,
            weight=weight,
            cap=cap,
            currency_code=currency,
        )
        if draft.key in seen:
            bad("duplicate", f"Repeats row {seen[draft.key]}.", "metric_code")
            out.findings += problems.items
            continue
        seen[draft.key] = row_no
        if phase == "open" and _live(draft.key) is not None:
            bad(
                "open_period_revision",
                f"{period_key} has started and already has a published target. "
                "Revise it with an override, which records a reason and an approver.",
                "period_key",
            )
            out.findings += problems.items
            continue
        out.findings += problems.items
        out.drafts.append(draft)
    return out


def _check_weight_cap(
    settings: Settings, weight: Decimal | None, cap: Decimal | None, bad: Any
) -> None:
    if weight is None:
        bad("weight_required", "A target needs a weight.", "weight")
    if cap is None:
        bad("cap_required", "A target needs a cap.", "cap")
    if weight is None or cap is None:
        return
    if weight > settings.weight_total:
        bad(
            "weight_too_large",
            f"A weight cannot exceed the total of {settings.weight_total}.",
            "weight",
        )
    if weight > 0 and cap < weight * settings.cap_min_ratio:
        bad(
            "cap_below_weight",
            f"Cap {cap} is below {settings.cap_min_ratio} × weight {weight}: "
            "a subject exactly on target could not earn the full weight.",
            "cap",
        )
    if weight > 0 and cap > weight * settings.cap_max_ratio:
        bad(
            "cap_implausible",
            f"Cap {cap} is more than {settings.cap_max_ratio} × weight {weight}.",
            "cap",
            "warning",
        )


def _key_filter(key: Key) -> Q:
    metric_id, scope_type, scope_code, period_key, series = key
    return Q(
        metric_id=metric_id,
        scope_type=scope_type,
        scope_code=scope_code,
        period_key=period_key,
        series_type=series,
    )


def _live(key: Key) -> Target | None:
    return Target.objects.filter(_key_filter(key), state="published").first()


def save_drafts(org_id: str, drafts: Iterable[Draft], *, source: str, user_id: int | None) -> int:
    """Write each row as the key's draft, replacing an earlier draft of it."""
    written = 0
    now = timezone.now()
    for d in drafts:
        existing = Target.objects.filter(_key_filter(d.key), state="draft").first()
        fields = {
            "target_value": d.target_value,
            "target_type": d.target_type,
            "weight": d.weight,
            "cap": d.cap,
            "currency_code": d.currency_code,
            "source": source,
            "updated_by": user_id,
            "updated_at": now,
        }
        if existing is not None:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.save()
        else:
            top = Target.objects.filter(_key_filter(d.key)).aggregate(v=Max("version"))["v"] or 0
            Target.objects.create(
                org_id=org_id,
                metric=d.metric,
                scope_type=d.scope_type,
                scope_code=d.scope_code,
                period_key=d.period_key,
                series_type=d.series_type,
                version=top + 1,
                state="draft",
                created_by=user_id,
                **fields,
            )
        written += 1
    return written


# ── weight sums (SC-10) ──────────────────────────────────────────────────────


@dataclass
class WeightCheck:
    profile_code: str
    period_key: str
    weight_sum: Decimal
    expected: Decimal
    complete: bool
    ok: bool
    missing: list[str] = field(default_factory=list)
    # Subject-scoped metrics carry their own weights: who sums wrong.
    subjects_off: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_code": self.profile_code,
            "period_key": self.period_key,
            "weight_sum": str(self.weight_sum),
            "expected": str(self.expected),
            "complete": self.complete,
            "ok": self.ok,
            "missing": self.missing,
            "subjects_off": self.subjects_off,
        }


def effective_targets(
    org_id: str, period_keys: Sequence[str], *, include_drafts: bool
) -> dict[Key, Target]:
    """The target each key would score against: live, or the draft over it when previewing."""
    rows = Target.objects.filter(
        org_id=org_id, period_key__in=list(period_keys), series_type="target"
    ).filter(Q(state="published") | Q(state="draft") if include_drafts else Q(state="published"))
    out: dict[Key, Target] = {}
    for t in rows.order_by("state"):  # "draft" sorts before "published"
        key = (str(t.metric_id), t.scope_type, t.scope_code, t.period_key, t.series_type)
        if key not in out:
            out[key] = t
    return out


def weight_checks(
    org_id: str,
    period_keys: Sequence[str],
    *,
    include_drafts: bool,
    profiles: Iterable[str] | None = None,
) -> list[WeightCheck]:
    settings = settings_for(org_id)
    live = effective_targets(org_id, period_keys, include_drafts=include_drafts)
    wanted = set(profiles) if profiles is not None else None
    checks: list[WeightCheck] = []
    for period_key in sorted(set(period_keys)):
        by_profile = roster.profile_metrics(org_id, period_key, wanted)
        members = roster.profile_members(org_id, period_key, wanted) if by_profile else {}
        for profile, metrics in sorted(by_profile.items()):
            base = Decimal(0)
            missing: list[str] = []
            subject_metrics: list[Metric] = []
            for m in metrics:
                if m.target_scope == "subject":
                    subject_metrics.append(m)
                    continue
                t = live.get((str(m.metric_id), "profile", profile, period_key, "target"))
                if t is None or t.weight is None:
                    missing.append(m.metric_code)
                else:
                    base += Decimal(t.weight)
            off: list[dict[str, Any]] = []
            sums = [base]
            if subject_metrics:
                sums = []
                for member in members.get(profile, []):
                    total = base
                    gaps = []
                    for m in subject_metrics:
                        t = live.get(
                            (str(m.metric_id), "subject", member.subject_id, period_key, "target")
                        )
                        if t is None or t.weight is None:
                            gaps.append(m.metric_code)
                        else:
                            total += Decimal(t.weight)
                    sums.append(total)
                    if gaps:
                        missing += [f"{c} for {member.staff_no}" for c in gaps]
                    elif (
                        not missing
                        and abs(total - settings.weight_total) > settings.weight_tolerance
                    ):
                        off.append({"staff_no": member.staff_no, "weight_sum": str(total)})
            complete = not missing
            weight_sum = max(sums) if sums else base
            ok = (
                complete
                and not off
                and all(abs(s - settings.weight_total) <= settings.weight_tolerance for s in sums)
            )
            checks.append(
                WeightCheck(
                    profile_code=profile,
                    period_key=period_key,
                    weight_sum=weight_sum,
                    expected=settings.weight_total,
                    complete=complete,
                    ok=ok,
                    missing=sorted(set(missing)),
                    subjects_off=off,
                )
            )
    return checks


# ── publish and revert ──────────────────────────────────────────────────────


def _profiles_touched(drafts: Iterable[Target], org_id: str) -> dict[str, set[str]]:
    """period_key → profiles whose weight sum the drafts change."""
    touched: dict[str, set[str]] = defaultdict(set)
    subjects: dict[str, set[str]] = defaultdict(set)
    for t in drafts:
        if t.scope_type == "profile":
            touched[t.period_key].add(t.scope_code)
        else:
            subjects[t.period_key].add(t.scope_code)
    for period_key, ids in subjects.items():
        rows = roster.assignments_in_force(org_id, period_key).filter(subject_id__in=ids)
        touched[period_key] |= set(rows.values_list("profile_code", flat=True))
    return touched


@dataclass
class Published:
    batch: TargetPublishBatch
    superseded: int
    checks: list[WeightCheck]


def publish(
    org_id: str,
    period_keys: Sequence[str],
    *,
    metric_codes: Sequence[str] | None,
    cycle_id: str | None,
    note: str,
    user_id: int | None,
) -> Published:
    drafts_q = Target.objects.select_for_update().filter(
        org_id=org_id, period_key__in=list(period_keys), state="draft"
    )
    if metric_codes:
        drafts_q = drafts_q.filter(metric__metric_code__in=list(metric_codes))
    drafts = list(drafts_q.select_related("metric"))
    if not drafts:
        raise Conflict("There are no drafts to publish for those periods.")

    blocked: list[dict[str, str]] = []
    for t in drafts:
        phase = period_phase(org_id, t.period_key)
        live = Target.objects.filter(
            metric_id=t.metric_id,
            scope_type=t.scope_type,
            scope_code=t.scope_code,
            period_key=t.period_key,
            series_type=t.series_type,
            state="published",
        ).exists()
        if phase == "locked" or (phase == "open" and live):
            blocked.append(
                {
                    "metric_code": t.metric.metric_code,
                    "scope_code": t.scope_code,
                    "period_key": t.period_key,
                    "reason": "closed" if phase == "locked" else "open_period_revision",
                }
            )
    if blocked:
        raise Conflict(
            "Some drafts revise a period that has started or closed; those changes are overrides.",
            detail={"blocked": blocked},
        )

    touched = _profiles_touched(drafts, org_id)
    checks: list[WeightCheck] = []
    for period_key, profiles in sorted(touched.items()):
        checks += weight_checks(org_id, [period_key], include_drafts=True, profiles=profiles)
    # Incomplete coverage is reported, not blocking; a complete profile must sum right.
    failing = [c for c in checks if c.complete and not c.ok]
    if failing:
        raise Conflict(
            "Weights do not sum to the expected total for some profiles; nothing was published.",
            detail={"weight_checks": [c.as_dict() for c in failing]},
        )

    now = timezone.now()
    batch = TargetPublishBatch.objects.create(
        org_id=org_id,
        cycle_id=cycle_id,
        period_keys=sorted({t.period_key for t in drafts}),
        published_at=now,
        published_by=user_id,
        weight_check_result={"checks": [c.as_dict() for c in checks]},
        row_count=len(drafts),
        note=note,
        created_by=user_id,
    )
    superseded = 0
    for t in drafts:
        superseded += Target.objects.filter(
            metric_id=t.metric_id,
            scope_type=t.scope_type,
            scope_code=t.scope_code,
            period_key=t.period_key,
            series_type=t.series_type,
            state="published",
        ).update(state="superseded", updated_by=user_id, updated_at=now)
    Target.objects.filter(target_id__in=[t.target_id for t in drafts]).update(
        state="published",
        batch=batch,
        published_at=now,
        published_by=user_id,
        updated_by=user_id,
        updated_at=now,
    )
    return Published(batch=batch, superseded=superseded, checks=checks)


def revert(org_id: str, batch_id: str, *, user_id: int | None) -> tuple[TargetPublishBatch, int]:
    """Undo a publish: its rows are superseded and the versions they replaced return."""
    batch = (
        TargetPublishBatch.objects.select_for_update()
        .filter(org_id=org_id, batch_id=batch_id)
        .first()
    )
    if batch is None:
        raise NotFound("No such publish batch.")
    if batch.status != "published":
        raise Conflict("This batch has already been reverted.")
    started = [p for p in batch.period_keys if period_phase(org_id, p) != "future"]
    if started:
        raise Conflict(
            "A batch can be reverted only while all its periods are in the future; "
            "revise a started period with an override.",
            detail={"started": started},
        )
    now = timezone.now()
    restored = 0
    rows = list(Target.objects.select_for_update().filter(batch=batch, state="published"))
    for t in rows:
        t.state = "superseded"
        t.updated_by = user_id
        t.updated_at = now
        t.save(update_fields=["state", "updated_by", "updated_at"])
        prior = (
            Target.objects.filter(
                metric_id=t.metric_id,
                scope_type=t.scope_type,
                scope_code=t.scope_code,
                period_key=t.period_key,
                series_type=t.series_type,
                state="superseded",
                version__lt=t.version,
            )
            .order_by("-version")
            .first()
        )
        if prior is not None:
            prior.state = "published"
            prior.updated_by = user_id
            prior.updated_at = now
            prior.save(update_fields=["state", "updated_by", "updated_at"])
            restored += 1
    batch.status = "reverted"
    batch.reverted_at = now
    batch.reverted_by = user_id
    batch.save(update_fields=["status", "reverted_at", "reverted_by"])
    return batch, restored


# ── copy forward (Scope §6.7 step 2) ─────────────────────────────────────────


def copy_forward_rows(
    org_id: str,
    source_periods: Sequence[str],
    *,
    months: int,
    uplift_pct: Decimal,
    metric_uplift: dict[str, Decimal],
) -> list[dict[str, Any]]:
    """Last cycle's live targets as rows for the periods ``months`` later, uplifted.

    The uplift multiplies the target value by ``1 + pct / 100``; a per-metric
    figure replaces the across-the-board one. Weights and caps carry over.
    """
    from kpigo.scorecards.cycles import shift

    live = (
        Target.objects.filter(org_id=org_id, period_key__in=list(source_periods), state="published")
        .select_related("metric")
        .order_by("period_key", "metric__metric_code", "scope_type", "scope_code", "series_type")
    )
    rows: list[dict[str, Any]] = []
    for t in live:
        code = t.metric.metric_code
        pct = metric_uplift.get(code, uplift_pct)
        value = (Decimal(t.target_value) * (1 + pct / 100)).quantize(Decimal("0.0001"))
        rows.append(
            {
                "metric_code": code,
                "scope_type": t.scope_type,
                "scope_code": t.scope_code,
                "period_key": shift(t.period_key, months),
                "series_type": t.series_type,
                "target_value": str(value),
                "target_type": t.target_type,
                "weight": None if t.weight is None else str(t.weight),
                "cap": None if t.cap is None else str(t.cap),
                "currency_code": t.currency_code,
            }
        )
    return rows


# ── coverage (Scope §6.7 step 4) ─────────────────────────────────────────────


@dataclass
class Cell:
    profile_code: str
    metric_code: str
    period_key: str
    target_scope: str
    state: str  # published | draft | revision | partial | missing
    expected: int
    published: int
    drafts: int


def coverage(
    org_id: str, period_keys: Sequence[str], profiles: Iterable[str] | None = None
) -> list[Cell]:
    """Profile × metric × period: what is set, what is missing, what is still draft."""
    wanted = set(profiles) if profiles is not None else None
    rows = Target.objects.filter(
        org_id=org_id,
        period_key__in=list(period_keys),
        series_type="target",
        state__in=("published", "draft"),
    ).values_list("metric_id", "scope_type", "scope_code", "period_key", "state")
    have: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for metric_id, scope_type, scope_code, period_key, state in rows:
        have[(str(metric_id), scope_type, scope_code, period_key)].add(state)

    cells: list[Cell] = []
    for period_key in sorted(set(period_keys)):
        by_profile = roster.profile_metrics(org_id, period_key, wanted)
        members = roster.profile_members(org_id, period_key, wanted) if by_profile else {}
        for profile, metrics in sorted(by_profile.items()):
            for m in metrics:
                if m.target_scope == "profile":
                    scopes = [("profile", profile)]
                else:
                    scopes = [("subject", member.subject_id) for member in members.get(profile, [])]
                published = drafts = covered = 0
                for scope_type, scope_code in scopes:
                    states = have.get((str(m.metric_id), scope_type, scope_code, period_key), set())
                    published += "published" in states
                    drafts += "draft" in states
                    covered += bool(states)
                expected = len(scopes)
                if expected and published == expected:
                    state = "revision" if drafts else "published"
                elif expected and covered == expected:
                    state = "draft"
                elif covered:
                    state = "partial"
                else:
                    state = "missing"
                cells.append(
                    Cell(
                        profile_code=profile,
                        metric_code=m.metric_code,
                        period_key=period_key,
                        target_scope=m.target_scope,
                        state=state,
                        expected=expected,
                        published=published,
                        drafts=drafts,
                    )
                )
    return cells
