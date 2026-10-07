"""Saving and recalling a reader's own dashboard filter state (PRD EX-9, Scope §10.5).

The one Executive dashboard is shared; how a reader looks at it — the period and
how far each breakdown widget is drilled — is personal. A saved view keeps that
filter state under a name, so a reader returns to the same vantage without
re-setting it. What one reader saves binds nobody else, exactly as a view
preference does: the rows are keyed by the signed-in account, never by role or
scope.

The ``state`` payload is opaque to the registry on purpose: it names a period and
a per-widget drill path (``widget_key`` → ``member_code``), nothing the registry
owns, so a view survives a widget being renamed or removed — the dashboard drops
a key it no longer has rather than failing. The logic here only bounds the shape
(a valid period, sane keys, a modest size); it never resolves the widgets, since a
view is a convenience, not a grant.
"""

from __future__ import annotations

from typing import Annotated

from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints

from kpigo.access.identity import app_user_for
from kpigo.access.models import AppUser
from kpigo.action import ActionContext, Conflict, NotFound
from kpigo.executive.models import WIDGET_KEY_PATTERN, ExecutiveSavedView
from kpigo.platform.vocab import PeriodKey

ViewName = Annotated[str, StringConstraints(min_length=1, max_length=80)]
WidgetKey = Annotated[str, StringConstraints(pattern=WIDGET_KEY_PATTERN)]
MemberCode = Annotated[str, StringConstraints(min_length=1, max_length=64)]
# A dashboard holds a handful of widgets; a view that drills every one is still small.
MAX_DRILL = 64


class SavedViewState(BaseModel):
    """The dashboard's filter state: the chosen period and each widget's drill path."""

    model_config = {"extra": "forbid"}

    period_key: PeriodKey
    # widget_key -> the member the widget is drilled into; absent means top level.
    drill: dict[WidgetKey, MemberCode] = Field(default_factory=dict, max_length=MAX_DRILL)


def account(ctx: ActionContext) -> AppUser:
    found = app_user_for(ctx.user, ctx.org_id)
    if found is None:
        raise Conflict("A saved view belongs to a signed-in user.")
    return found


def save(ctx: ActionContext, name: str, state: SavedViewState) -> ExecutiveSavedView:
    """Create or replace this reader's view of that name; the latest state wins."""
    owner = account(ctx)
    payload = state.model_dump()
    row, created = ExecutiveSavedView.objects.get_or_create(
        app_user=owner,
        name=name,
        defaults={
            "org_id": ctx.org_id,
            "state": payload,
            "created_by": ctx.user_id,
            "updated_by": ctx.user_id,
        },
    )
    if not created:
        row.state = payload
        row.updated_by = ctx.user_id
        row.updated_at = timezone.now()
        row.save(update_fields=["state", "updated_by", "updated_at"])
    return row


def listed(ctx: ActionContext) -> list[ExecutiveSavedView]:
    return list(ExecutiveSavedView.objects.filter(app_user=account(ctx)).order_by("name"))


def remove(ctx: ActionContext, name: str) -> None:
    deleted, _ = ExecutiveSavedView.objects.filter(app_user=account(ctx), name=name).delete()
    if not deleted:
        raise NotFound(f"You have no saved view named '{name}'.")
