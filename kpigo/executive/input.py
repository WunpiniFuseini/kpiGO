"""Entering an independent executive metric's actual by hand (PRD MI-4, TDD §5.5).

Independent-mode executive metrics (cost-to-income, NPS, capital ratios) are fed
via ``tmpl_actual_dimensional`` or entered here. A value is recorded with its
provenance (who, when, the note) and conformed to ``fact_actual_dimensional`` for
its slice — the whole organisation, or one member of a declared dimension — so
``widget.data`` reads it exactly as it reads a feed. Values are freely editable
until the manual-input deadline (MI-7); a change after it is a restatement, a new
version with the prior one kept (MI-5).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from kpigo.executive.models import ExecutiveManualInput
from kpigo.hierarchy.models import Dimension, DimMember
from kpigo.ingestion.models import FactActualDimensional
from kpigo.metrics.models import Metric
from kpigo.scorecards import inputs as sc_inputs


def slot_problem(org_id: str, dimension_type: str, member_code: str) -> str | None:
    """Why this slice is not a valid slot, or ``None``. '' / '' is the organisation."""
    if not dimension_type and not member_code:
        return None
    if not dimension_type or not member_code:
        return "A slice is the whole organisation (no dimension) or one member of one dimension."
    if not Dimension.objects.filter(org_id=org_id, dimension_type=dimension_type).exists():
        return f"No dimension '{dimension_type}' is defined."
    if not DimMember.objects.filter(
        org_id=org_id, dimension_type=dimension_type, member_code=member_code
    ).exists():
        return f"No member '{member_code}' in dimension '{dimension_type}'."
    return None


def current(
    org_id: str, metric_code: str, dimension_type: str, member_code: str, period_key: str
) -> ExecutiveManualInput | None:
    return ExecutiveManualInput.objects.filter(
        org_id=org_id,
        metric_code=metric_code,
        dimension_type=dimension_type,
        member_code=member_code,
        period_key=period_key,
        is_current=True,
    ).first()


def set_value(
    *,
    org_id: str,
    metric: Metric,
    dimension_type: str,
    member_code: str,
    period_key: str,
    value: Decimal,
    currency_code: str | None,
    note: str,
    user_id: int | None,
    now: datetime,
) -> ExecutiveManualInput:
    """Record the value with its provenance and conform it to ``fact_actual_dimensional``.

    Before the deadline the current row is edited in place; after it, the change
    is a new ``restated`` version and the prior row is kept (``is_current`` off).
    """
    existing = current(org_id, metric.metric_code, dimension_type, member_code, period_key)
    restating = sc_inputs.locked(org_id, period_key, now)
    if existing is not None and not restating:
        existing.value = value
        existing.currency_code = currency_code
        existing.note = note
        existing.submitted_by = user_id
        existing.submitted_at = now
        existing.updated_by = user_id
        existing.updated_at = now
        existing.save(
            update_fields=[
                "value",
                "currency_code",
                "note",
                "submitted_by",
                "submitted_at",
                "updated_by",
                "updated_at",
            ]
        )
        row = existing
    else:
        if existing is not None:
            ExecutiveManualInput.objects.filter(pk=existing.pk).update(is_current=False)
        row = ExecutiveManualInput.objects.create(
            org_id=org_id,
            metric=metric,
            metric_code=metric.metric_code,
            dimension_type=dimension_type,
            member_code=member_code,
            period_key=period_key,
            value=value,
            currency_code=currency_code,
            note=note,
            change="restated" if existing is not None else "entered",
            submitted_by=user_id,
            submitted_at=now,
            version=(existing.version + 1) if existing is not None else 1,
            is_current=True,
            created_by=user_id,
            updated_by=user_id,
        )
    _conform(row, now)
    return row


def _conform(row: ExecutiveManualInput, now: datetime) -> None:
    """Write the value as the slice's dimensional actual; manual run_id is null (MI-11)."""
    FactActualDimensional.objects.update_or_create(
        metric=row.metric,
        dimension_type=row.dimension_type,
        member_code=row.member_code,
        period_key=row.period_key,
        defaults={
            "org_id": str(row.org_id),
            "actual_value": row.value,
            "currency_code": row.currency_code,
            "run_id": None,
            "loaded_at": now,
            "created_by": row.submitted_by,
        },
    )
