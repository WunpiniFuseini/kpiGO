"""Which campaigns a caller sees (PRD AD-2, Scope §10: explicit grant, no grant means no data).

Campaign data is scoped by ``data_scope_grant`` rows with ``module='campaign'``:

- ``member_code='*'`` (any dimension) sees every campaign;
- ``campaign`` grants name a campaign by its code;
- ``product`` grants match the campaign's product, or a member below it;
- any other dimension (``segment``, ``region``...) matches a campaign one of
  whose events is aimed at that member, or a member below it.

A campaign's owner always sees it: ownership is the other scope the brief names,
and an author must be able to see what they built. Nothing else does.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from django.db.models import Q, QuerySet

from kpigo.action import ActionContext, NotFound
from kpigo.campaigns.models import Campaign
from kpigo.hierarchy.models import DimMember

ALL = "*"


def descendants(org_id: str, dimension_type: str, codes: set[str]) -> set[str]:
    """The members named and every member below them in the dimension's tree."""
    children: dict[str, list[str]] = defaultdict(list)
    for code, parent in DimMember.objects.filter(
        org_id=org_id, dimension_type=dimension_type, parent_code__isnull=False
    ).values_list("member_code", "parent_code"):
        children[str(parent)].append(str(code))
    found = set(codes)
    frontier = list(codes)
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in found:
                found.add(child)
                frontier.append(child)
    return found


def scope_filter(ctx: ActionContext) -> Q | None:
    """A filter over ``Campaign`` for what ``ctx`` may see; ``None`` means everything."""
    grants = [g for g in ctx.data_scopes if g.module == "campaign"]
    if any(g.member_code == ALL for g in grants):
        return None
    # Matches nothing: a deny is the starting point, never a wildcard.
    allowed = Q(pk__in=[])
    if ctx.user_id is not None:
        allowed |= Q(owner_user_id=ctx.user_id)
    by_dimension: dict[str, set[str]] = defaultdict(set)
    for g in grants:
        by_dimension[g.dimension_type].add(g.member_code)
    for dimension, codes in by_dimension.items():
        if dimension == "campaign":
            allowed |= Q(code__in=sorted(codes))
            continue
        members = sorted(descendants(ctx.org_id, dimension, codes))
        if dimension == "product":
            allowed |= Q(product_code__in=members)
        allowed |= Q(
            events__audience__dimension_type=dimension, events__audience__member_code__in=members
        )
    return allowed


def visible(ctx: ActionContext) -> QuerySet[Campaign]:
    query = Campaign.objects.filter(org_id=ctx.org_id)
    found = scope_filter(ctx)
    if found is None:
        return query
    return Campaign.objects.filter(
        org_id=ctx.org_id, campaign_id__in=query.filter(found).values("campaign_id")
    )


def is_visible(ctx: ActionContext, campaign_id: uuid.UUID | str) -> bool:
    return visible(ctx).filter(campaign_id=campaign_id).exists()


def get_visible(ctx: ActionContext, campaign_id: str, *, lock: bool = False) -> Campaign:
    """The campaign, if ``ctx`` may see it. Out of scope reads as not found."""
    try:
        if not is_visible(ctx, campaign_id):
            raise Campaign.DoesNotExist
        query = Campaign.objects.filter(org_id=ctx.org_id)
        if lock:
            query = query.select_for_update()
        return query.get(campaign_id=campaign_id)
    except (Campaign.DoesNotExist, ValueError):
        raise NotFound("No such campaign in your scope.") from None
