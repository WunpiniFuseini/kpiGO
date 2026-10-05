"""Win-backs: who the client counts as won back, which event earned it, and whether it held (Scope §9.4).

What "won back" means is the client's own definition, fed by their DE team. kpiGo
adds two things:

- **Matching.** A win-back is earned by a published attrition win-back event
  whose window holds the day the customer qualified: the event the client's tag
  names, else one that contacted the customer or had them in its audience. A
  customer held out as an event's control group is never credited to it. When
  several events could claim it, the org's collision rule picks one (an even
  split cannot halve a customer, so it falls back to the latest touch).
- **Retention.** A win-back is *provisional* until it is confirmed: by the
  client's own ``retention_confirmed_at``, or once the org's retention window
  (90 days by default) has passed since the customer qualified. A later load
  that says the customer no longer qualifies makes it *lapsed*. The state is
  worked out when asked, so changing the window moves win-backs between
  provisional and confirmed at once.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from django.db.models import Count, Min, Q, QuerySet
from django.utils import timezone

from kpigo.campaigns.models import (
    DEFAULT_RETENTION_DAYS,
    CampaignContact,
    CampaignEvent,
    CampaignWinback,
)
from kpigo.platform.models import OrgSettings

if TYPE_CHECKING:
    from kpigo.campaigns.attribution import EventSpec

OBJECTIVE = "attrition_winback"
BATCH = 2000


def retention_days(org_id: str) -> int:
    found = (
        OrgSettings.objects.filter(org_id=org_id)
        .values_list("winback_retention_days", flat=True)
        .first()
    )
    return int(found) if found is not None else DEFAULT_RETENTION_DAYS


def match(org_id: str, which: Q, events: list[EventSpec], *, now: datetime | None = None) -> int:
    """Match the org's win-backs selected by ``which`` to events again; how many found one."""
    from kpigo.campaigns import attribution

    now = now or timezone.now()
    rows = CampaignWinback.objects.filter(org_id=org_id).filter(which)
    eligible = [e for e in events if e.objective == OBJECTIVE]
    if not eligible:
        rows.exclude(event=None).update(event=None, via=None, rule_applied=None, candidates=0)
        rows.update(matched_at=now)
        return 0
    rule = attribution.rule_for(org_id)
    pick = "last_touch" if rule == "split_even" else rule
    ids = [e.event_id for e in eligible]
    held = attribution.holdouts(org_id)
    contacted: dict[object, set[str]] = defaultdict(set)
    for event_id, customer in CampaignContact.objects.filter(
        event_id__in=ids, holdout=False, customer_ref__in=rows.values("customer_ref")
    ).values_list("event_id", "customer_ref"):
        contacted[event_id].add(customer)
    matched = 0
    batch: list[CampaignWinback] = []
    for w in rows.order_by("winback_id").iterator(chunk_size=BATCH):
        open_ = [
            e
            for e in eligible
            if e.in_window(w.qualified_at) and w.customer_ref not in held.get(e.event_id, ())
        ]
        if w.campaign_id is not None:
            found = [e for e in open_ if e.campaign_id == w.campaign_id]
            via = "tag"
        else:
            touched = [e for e in open_ if w.customer_ref in contacted[e.event_id]]
            found = touched or [e for e in open_ if e.in_audience(w)]
            via = "contact" if touched else "criteria"
        best = attribution.winner(found, pick) if found else None
        w.event_id = best.event_id if best else None
        w.via = via if best else None
        w.rule_applied = None if best is None else ("single" if len(found) == 1 else pick)
        w.candidates = len(found)
        w.matched_at = now
        matched += best is not None
        batch.append(w)
        if len(batch) >= BATCH:
            _save(batch)
            batch = []
    _save(batch)
    return matched


def _save(batch: list[CampaignWinback]) -> None:
    if batch:
        CampaignWinback.objects.bulk_update(
            batch, ["event", "via", "rule_applied", "candidates", "matched_at"]
        )


# ── retention ────────────────────────────────────────────────────────────────


def confirmed_by(days: int, today: date) -> Q:
    """Still qualifying, and confirmed by the client or past the retention window."""
    return Q(winback_flag=True) & (
        Q(retention_confirmed_at__isnull=False) | Q(qualified_at__lte=today - timedelta(days=days))
    )


def provisional_by(days: int, today: date) -> Q:
    return Q(winback_flag=True, retention_confirmed_at__isnull=True) & Q(
        qualified_at__gt=today - timedelta(days=days)
    )


@dataclass
class Counts:
    qualified: int = 0
    provisional: int = 0
    confirmed: int = 0
    lapsed: int = 0
    # The day the earliest provisional win-back confirms, if nothing changes.
    next_confirmation: date | None = None


def counts(query: QuerySet[CampaignWinback], days: int, today: date) -> Counts:
    found = query.aggregate(
        qualified=Count("winback_id"),
        provisional=Count("winback_id", filter=provisional_by(days, today)),
        confirmed=Count("winback_id", filter=confirmed_by(days, today)),
        lapsed=Count("winback_id", filter=Q(winback_flag=False)),
        first=Min("qualified_at", filter=provisional_by(days, today)),
    )
    first = found.pop("first")
    return Counts(
        **found, next_confirmation=None if first is None else first + timedelta(days=days)
    )


def by_event(events: list[CampaignEvent], days: int, today: date) -> dict[object, Counts]:
    return {
        e.event_id: counts(CampaignWinback.objects.filter(event=e), days, today) for e in events
    }
