"""Product lines (Schema §5). Ingestion registers unseen codes as ``available``.

Naming, grouping and activating lines is Agent Performance work (PRD AP-8, R2);
until then the list shows what feeds have detected and when.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from kpigo.action import ActionContext, action
from kpigo.hierarchy.models import ProductLine


class ProductLineOut(BaseModel):
    line_id: uuid.UUID
    code: str
    display_name: str
    status: str
    group_code: str | None
    source_member_code: str | None
    first_detected_at: datetime | None
    effective_from: date
    effective_to: date | None


class ProductLineListIn(BaseModel):
    status: Literal["available", "active", "retired"] | None = None


class ProductLineListOut(BaseModel):
    product_lines: list[ProductLineOut]


@action(
    name="product_line.list",
    summary="Product lines, including those feeds detected that await an Admin.",
    schema=ProductLineListIn,
    output=ProductLineListOut,
    permission="dimension.view",
    read_only=True,
    example={"status": "available"},
)
def list_product_lines(params: ProductLineListIn, ctx: ActionContext) -> ProductLineListOut:
    rows = ProductLine.objects.filter(org_id=ctx.org_id).select_related("group")
    if params.status is not None:
        rows = rows.filter(status=params.status)
    return ProductLineListOut(
        product_lines=[
            ProductLineOut(
                line_id=line.line_id,
                code=line.code,
                display_name=line.display_name,
                status=line.status,
                group_code=line.group.code if line.group is not None else None,
                source_member_code=line.source_member_code,
                first_detected_at=line.first_detected_at,
                effective_from=line.effective_from,
                effective_to=line.effective_to,
            )
            for line in rows.order_by("sort_order", "code")
        ]
    )
