"""Your own saved views of the Executive dashboard (PRD EX-9, Scope §10.5).

Anyone who can read the dashboard (``executive.view``) can keep their own named
snapshots of its filter state and recall them. The views are personal: the
actions only ever touch the signed-in reader's own rows, so no approval and no
config version are involved — saving a view changes nothing anyone else sees.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from kpigo.action import ActionContext, action
from kpigo.executive import saved_views as sv
from kpigo.executive.models import ExecutiveSavedView


class SavedViewOut(BaseModel):
    name: str
    state: sv.SavedViewState
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, row: ExecutiveSavedView) -> SavedViewOut:
        return cls(
            name=row.name,
            state=sv.SavedViewState.model_validate(row.state),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SavedViewSaveIn(BaseModel):
    name: sv.ViewName
    state: sv.SavedViewState


@action(
    name="executive.view.save",
    summary="Save or replace one of your named views of the Executive dashboard.",
    schema=SavedViewSaveIn,
    output=SavedViewOut,
    permission="executive.view",
    read_only=False,
    module="executive",
    example={"name": "Q4 by region", "state": {"period_key": "202610", "drill": {}}},
)
def save_view(params: SavedViewSaveIn, ctx: ActionContext) -> SavedViewOut:
    return SavedViewOut.of(sv.save(ctx, params.name, params.state))


class SavedViewListIn(BaseModel):
    pass


class SavedViewListOut(BaseModel):
    views: list[SavedViewOut]


@action(
    name="executive.view.list",
    summary="Your saved views of the Executive dashboard, by name.",
    schema=SavedViewListIn,
    output=SavedViewListOut,
    permission="executive.view",
    read_only=True,
    module="executive",
    example={},
)
def list_views(params: SavedViewListIn, ctx: ActionContext) -> SavedViewListOut:
    return SavedViewListOut(views=[SavedViewOut.of(row) for row in sv.listed(ctx)])


class SavedViewDeleteIn(BaseModel):
    name: sv.ViewName


class SavedViewDeleteOut(BaseModel):
    name: str
    deleted: bool


@action(
    name="executive.view.delete",
    summary="Delete one of your saved views of the Executive dashboard.",
    schema=SavedViewDeleteIn,
    output=SavedViewDeleteOut,
    permission="executive.view",
    read_only=False,
    module="executive",
    example={"name": "Q4 by region"},
)
def delete_view(params: SavedViewDeleteIn, ctx: ActionContext) -> SavedViewDeleteOut:
    sv.remove(ctx, params.name)
    return SavedViewDeleteOut(name=params.name, deleted=True)
