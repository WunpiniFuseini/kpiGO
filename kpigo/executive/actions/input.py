"""Hand-entering an independent executive metric's actual (PRD MI-4, TDD §5.5).

``executive.input.set`` records one value for a slice — the whole organisation,
or one member of a declared dimension — for a period, and conforms it to
``fact_actual_dimensional`` so ``widget.data`` reads it like a feed. The value
carries its contributor, moment and note as provenance (MI-11); a change after
the input deadline is a restatement (MI-5). ``executive.input.list`` shows what
has been entered for a period.

Maker-checker is available through the ``manual_input`` approval class, honoured
only where the client turns it on (MI-6).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints

from kpigo.action import ActionContext, InvalidInput, NotFound, action
from kpigo.executive import input as ex_input
from kpigo.executive.models import ExecutiveManualInput
from kpigo.ingestion.reference import org_today
from kpigo.metrics.models import Metric
from kpigo.platform.vocab import PeriodKey
from kpigo.scorecards import inputs as sc_inputs

MetricCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
MemberCode = Annotated[str, StringConstraints(max_length=64)]
DimensionType = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$|^$", max_length=64)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class ExecutiveInputOut(BaseModel):
    metric_code: str
    display_name: str
    dimension_type: str
    member_code: str
    period_key: str
    value: Decimal
    currency_code: str | None
    note: str
    version: int
    submitted_at: datetime


class ExecutiveInputSetIn(BaseModel):
    metric_code: MetricCode
    period_key: PeriodKey
    # Both omitted: an organisation-level value. Both given: one dimension member.
    dimension_type: DimensionType = ""
    member_code: MemberCode = ""
    value: Decimal = Field(ge=Decimal("-1e14"), le=Decimal("1e14"))
    currency_code: Currency | None = None
    note: Note = ""


def _out(row: ExecutiveManualInput, display_name: str) -> ExecutiveInputOut:
    return ExecutiveInputOut(
        metric_code=row.metric_code,
        display_name=display_name,
        dimension_type=row.dimension_type,
        member_code=row.member_code,
        period_key=row.period_key,
        value=row.value,
        currency_code=row.currency_code,
        note=row.note,
        version=row.version,
        submitted_at=row.submitted_at,
    )


def _metric(ctx: ActionContext, code: str, today: date) -> Metric:
    metric = (
        Metric.objects.filter(org_id=ctx.org_id, metric_code=code, effective_from__lte=today)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=today))
        .prefetch_related("bindings")
        .first()
    )
    if metric is None:
        raise NotFound(f"No metric '{code}' is in force on {today.isoformat()}.")
    if metric.collection_method != "manual_input":
        raise InvalidInput(
            f"'{code}' is collected by a feed. Set its collection to manual input in the "
            "metric registry first."
        )
    if not any(b.product == "executive" and b.is_active for b in metric.bindings.all()):
        raise InvalidInput(f"'{code}' is not bound to Executive; bind it in the registry first.")
    return metric


@action(
    name="executive.input.set",
    summary="Enter an executive metric's actual by hand for a period, org-level or by member.",
    schema=ExecutiveInputSetIn,
    output=ExecutiveInputOut,
    permission="executive.input",
    read_only=False,
    module="executive",
    requires_approval="manual_input",
    audit="executive.input.submitted",
    example={"metric_code": "ex_cti", "period_key": "202610", "value": "0.47"},
)
def set_input(params: ExecutiveInputSetIn, ctx: ActionContext) -> ExecutiveInputOut:
    today = org_today(ctx.org_id)
    if params.period_key > _current_period(ctx.org_id):
        raise InvalidInput(f"{params.period_key} has not started; a value is entered once it does.")
    if problem := sc_inputs.check_value(params.value):
        raise InvalidInput(problem)
    if slot := ex_input.slot_problem(ctx.org_id, params.dimension_type, params.member_code):
        raise InvalidInput(slot)
    metric = _metric(ctx, params.metric_code, today)
    with transaction.atomic():
        row = ex_input.set_value(
            org_id=str(ctx.org_id),
            metric=metric,
            dimension_type=params.dimension_type,
            member_code=params.member_code,
            period_key=params.period_key,
            value=params.value,
            currency_code=params.currency_code,
            note=params.note,
            user_id=ctx.user_id,
            now=timezone.now(),
        )
        ctx.audit(
            "executive.input.submitted.detail",
            metric_code=row.metric_code,
            dimension_type=row.dimension_type,
            member_code=row.member_code,
            period_key=row.period_key,
            version=row.version,
        )
    return _out(row, metric.display_name)


class ExecutiveInputListIn(BaseModel):
    period_key: PeriodKey


class ExecutiveInputListOut(BaseModel):
    period_key: str
    inputs: list[ExecutiveInputOut]


@action(
    name="executive.input.list",
    summary="The hand-entered executive values current for a period.",
    schema=ExecutiveInputListIn,
    output=ExecutiveInputListOut,
    permission="executive.input",
    read_only=True,
    module="executive",
    example={"period_key": "202610"},
)
def list_inputs(params: ExecutiveInputListIn, ctx: ActionContext) -> ExecutiveInputListOut:
    rows = list(
        ExecutiveManualInput.objects.filter(
            org_id=ctx.org_id, period_key=params.period_key, is_current=True
        ).order_by("metric_code", "dimension_type", "member_code")
    )
    names = dict(
        Metric.objects.filter(
            org_id=ctx.org_id, metric_code__in=sorted({r.metric_code for r in rows})
        ).values_list("metric_code", "display_name")
    )
    return ExecutiveInputListOut(
        period_key=params.period_key,
        inputs=[_out(r, names.get(r.metric_code, r.metric_code)) for r in rows],
    )


def _current_period(org_id: str) -> str:
    from kpigo.hierarchy.scope import current_period_key

    return current_period_key(org_id)
