"""Attribution: which event an outcome is credited to (TDD §7.2, PRD CM-8 to CM-13).

An outcome is a candidate for a published event when:

1. its metric is one of the outcome metrics of the campaign's objective;
2. it happened after the first contact day and within the window:
   ``period_start < activity_date <= period_end + attribution_window_days``;
3. either the client's own tag names the event's campaign (``campaign_code``),
   or, untagged, the customer is in the event's audience (each criterion
   dimension matches, a member matching every member below it) and the
   campaign's product, where it has one.

No candidate leaves the outcome unattributed. One is credited in full. Several
go through the org's collision rule: ``last_touch`` (default: the event whose
contacts began most recently), ``first_touch``, ``priority`` (the campaign with
the lowest priority rank) or ``split_even``. Ties fall to priority, then the
event's sequence, so the result never depends on read order.

Invariant (CM-13), asserted before writing and checked in the database after:
per outcome, the credited values sum to at most the outcome's value. A
violation raises and nothing is written.

Attribution runs in the transaction of whatever changed its inputs: an outcome
load, publishing or re-dating an event, a change to its audience, a campaign's
priority or product, an objective's outcome metrics, or the org's rule.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

from django.db import connection
from django.db.models import Max, Min, Q
from django.utils import timezone

from kpigo.campaigns import baseline, winbacks
from kpigo.campaigns.authoring import window_end
from kpigo.campaigns.models import (
    ATTRIBUTION_RULES,
    Campaign,
    CampaignAttribution,
    CampaignAudience,
    CampaignContact,
    CampaignEvent,
    CampaignObjective,
    CampaignOutcome,
    CustomerDims,
)
from kpigo.hierarchy.models import DimMember
from kpigo.ingestion.validator import CUSTOMER_DIMENSIONS
from kpigo.metrics.models import Metric
from kpigo.platform.models import OrgSettings

DEFAULT_RULE = "last_touch"
VALUE_QUANTUM = Decimal("0.0001")
SHARE_QUANTUM = Decimal("0.00000001")
BATCH = 5000
# A campaign with no priority ranks after every ranked one.
UNRANKED = 1 << 30


class AttributionInvariantError(RuntimeError):
    """Credited value would exceed an outcome's value. Fails the job; nothing is published."""


def rule_for(org_id: str) -> str:
    found = (
        OrgSettings.objects.filter(org_id=org_id).values_list("attribution_rule", flat=True).first()
    )
    return found or DEFAULT_RULE


# ── the events, as attribution sees them ────────────────────────────────────


@dataclass(frozen=True)
class EventSpec:
    event_id: uuid.UUID
    campaign_id: uuid.UUID
    priority: int
    sequence_no: int
    period_start: date
    # The last contact day; outcomes count until window_end.
    period_end: date
    window_end: date
    metric_ids: frozenset[str]
    # The campaign's product and every member below it; None when it has none.
    products: frozenset[str] | None
    # Audience: dimension -> the members named and every member below them.
    criteria: dict[str, frozenset[str]] = field(default_factory=dict)
    objective: str = ""

    def in_window(self, day: date) -> bool:
        return self.period_start < day <= self.window_end

    def in_audience(self, outcome: CustomerDims) -> bool:
        if not self.criteria:
            return False
        if self.products is not None and outcome.product_code not in self.products:
            return False
        for dimension, members in self.criteria.items():
            column = CUSTOMER_DIMENSIONS.get(dimension)
            # A dimension the outcome feed does not describe cannot be matched.
            if column is None or getattr(outcome, column) not in members:
                return False
        return True


class Trees:
    """Each dimension's member tree, read once per run."""

    def __init__(self, org_id: str) -> None:
        self.children: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for dim, code, parent in DimMember.objects.filter(
            org_id=org_id, parent_code__isnull=False
        ).values_list("dimension_type", "member_code", "parent_code"):
            self.children[dim][str(parent)].append(str(code))

    def below(self, dimension: str, codes: Iterable[str]) -> frozenset[str]:
        found = set(codes)
        frontier = list(found)
        tree = self.children.get(dimension, {})
        while frontier:
            for child in tree.get(frontier.pop(), ()):
                if child not in found:
                    found.add(child)
                    frontier.append(child)
        return frozenset(found)


def outcome_metric_ids(org_id: str) -> dict[str, frozenset[str]]:
    """Objective -> the ids of every version of its outcome metrics."""
    codes = {
        o.objective: list(o.outcome_metric_codes)
        for o in CampaignObjective.objects.filter(org_id=org_id)
    }
    ids: dict[str, set[str]] = defaultdict(set)
    wanted = {c for found in codes.values() for c in found}
    for metric_id, code in Metric.objects.filter(
        org_id=org_id, metric_code__in=sorted(wanted)
    ).values_list("metric_id", "metric_code"):
        ids[code].add(str(metric_id))
    return {
        objective: frozenset(i for c in found for i in ids.get(c, ()))
        for objective, found in codes.items()
    }


def events_for(org_id: str, trees: Trees | None = None) -> list[EventSpec]:
    """Every published event (live, paused or closed). Drafts attribute nothing."""
    trees = trees or Trees(org_id)
    metrics = outcome_metric_ids(org_id)
    events = list(
        CampaignEvent.objects.filter(org_id=org_id)
        .exclude(state="draft")
        .select_related("campaign")
        .order_by("period_start", "sequence_no")
    )
    audience: dict[uuid.UUID, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for event_id, dim, code in CampaignAudience.objects.filter(
        event_id__in=[e.event_id for e in events]
    ).values_list("event_id", "dimension_type", "member_code"):
        audience[event_id][dim].add(code)
    specs = []
    for e in events:
        c: Campaign = e.campaign
        specs.append(
            EventSpec(
                event_id=e.event_id,
                campaign_id=c.campaign_id,
                priority=c.priority if c.priority is not None else UNRANKED,
                sequence_no=e.sequence_no,
                period_start=e.period_start,
                period_end=e.period_end,
                window_end=window_end(e),
                metric_ids=metrics.get(c.objective, frozenset()),
                products=trees.below("product", [c.product_code]) if c.product_code else None,
                criteria={
                    dim: trees.below(dim, codes) for dim, codes in audience[e.event_id].items()
                },
                objective=c.objective,
            )
        )
    return specs


# ── matching and the collision rule ──────────────────────────────────────────


def candidates(outcome: CampaignOutcome, events: list[EventSpec]) -> tuple[list[EventSpec], str]:
    metric = str(outcome.metric_id)
    day = outcome.activity_date
    if outcome.campaign_id is not None:
        tagged = [
            e
            for e in events
            if e.campaign_id == outcome.campaign_id and metric in e.metric_ids and e.in_window(day)
        ]
        return tagged, "tag"
    return [
        e for e in events if metric in e.metric_ids and e.in_window(day) and e.in_audience(outcome)
    ], "criteria"


def _tie(e: EventSpec) -> tuple[int, int, str]:
    return (e.priority, e.sequence_no, str(e.event_id))


def winner(found: list[EventSpec], rule: str) -> EventSpec:
    if rule == "first_touch":
        return min(found, key=lambda e: (e.period_start.toordinal(), *_tie(e)))
    if rule == "priority":
        return min(found, key=lambda e: (e.priority, -e.period_start.toordinal(), *_tie(e)[1:]))
    return min(found, key=lambda e: (-e.period_start.toordinal(), *_tie(e)))


def split(value: Decimal, n: int) -> list[Decimal]:
    """``value`` in ``n`` parts that sum to it exactly; the remainder goes to the first."""
    base = (value / n).quantize(VALUE_QUANTUM, rounding=ROUND_DOWN)
    parts = [base] * n
    parts[0] += value - base * n
    return parts


@dataclass(frozen=True)
class Credit:
    event_id: uuid.UUID
    credited: bool
    value: Decimal
    share: Decimal


def allocate(value: Decimal, found: list[EventSpec], rule: str) -> tuple[list[Credit], str]:
    """Each candidate's credit and the rule that decided it."""
    if len(found) == 1:
        return [Credit(found[0].event_id, True, value, Decimal(1))], "single"
    if rule == "split_even":
        ordered = sorted(found, key=lambda e: (-e.period_start.toordinal(), *_tie(e)))
        share = (Decimal(1) / len(found)).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)
        return [
            Credit(e.event_id, True, part, share)
            for e, part in zip(ordered, split(value, len(found)), strict=True)
        ], rule
    best = winner(found, rule)
    return [
        Credit(e.event_id, True, value, Decimal(1))
        if e is best
        else Credit(e.event_id, False, Decimal(0), Decimal(0))
        for e in found
    ], rule


# ── running it ───────────────────────────────────────────────────────────────


@dataclass
class Summary:
    rule: str
    outcomes: int = 0
    attributed: int = 0
    unattributed: int = 0
    collisions: int = 0
    events: set[uuid.UUID] = field(default_factory=set)


def attribute(
    org_id: str,
    outcomes: Q,
    *,
    now: datetime | None = None,
    days: tuple[date, date] | None = None,
) -> Summary:
    """Re-attribute the org's outcomes matching ``outcomes``, replacing what they had.

    Then the baselines of every event whose span or baseline window touches the
    days involved (``days``, else the selected outcomes' first and last day).
    """
    now = now or timezone.now()
    rule = rule_for(org_id)
    if rule not in ATTRIBUTION_RULES:
        raise ValueError(f"unknown attribution rule {rule!r}")
    events = events_for(org_id)
    summary = Summary(rule=rule)
    selected = CampaignOutcome.objects.filter(org_id=org_id).filter(outcomes)
    if days is None:
        bounds = selected.aggregate(lo=Min("activity_date"), hi=Max("activity_date"))
        days = None if bounds["lo"] is None else (bounds["lo"], bounds["hi"])
    held = holdouts(org_id)
    CampaignAttribution.objects.filter(
        org_id=org_id, outcome_id__in=selected.values("outcome_id")
    ).delete()
    # Win-backs are matched to events by the same window and audience: a full run
    # matches every one, a run over days those that qualified in them.
    won_back = Q() if not outcomes else None
    if won_back is None and days is not None:
        won_back = Q(qualified_at__gte=days[0], qualified_at__lte=days[1])
    if not events:
        summary.outcomes = selected.count()
        summary.unattributed = summary.outcomes
        if won_back is not None:
            winbacks.match(org_id, won_back, events, now=now)
        return summary
    rows: list[CampaignAttribution] = []
    for outcome in selected.order_by("outcome_id").iterator(chunk_size=BATCH):
        summary.outcomes += 1
        found, via = candidates(outcome, events)
        if not found:
            summary.unattributed += 1
            continue
        # A customer held out of an event as its control group cannot be credited
        # to it; the outcome still counts toward that event's control conversions.
        control = [e for e in found if outcome.customer_ref in held.get(e.event_id, ())]
        open_ = [e for e in found if e not in control]
        credits: list[Credit] = [Credit(e.event_id, False, Decimal(0), Decimal(0)) for e in control]
        applied = {e.event_id: "holdout" for e in control}
        if open_:
            won, rule_used = allocate(outcome.activity_value, open_, rule)
            credits += won
            applied |= {c.event_id: rule_used for c in won}
        total = sum((c.value for c in credits if c.credited), Decimal(0))
        if total > outcome.activity_value:
            raise AttributionInvariantError(
                f"Outcome {outcome.outcome_id} would be credited {total}, more than its "
                f"value {outcome.activity_value}."
            )
        summary.attributed += bool(open_)
        summary.unattributed += not open_
        summary.collisions += len(open_) > 1
        for c in credits:
            summary.events.add(c.event_id)
            rows.append(
                CampaignAttribution(
                    org_id=org_id,
                    outcome_id=outcome.outcome_id,
                    event_id=c.event_id,
                    credited=c.credited,
                    attributed_value=c.value,
                    share=c.share,
                    rule_applied=applied[c.event_id],
                    candidates=len(found),
                    via=via,
                    computed_at=now,
                )
            )
        if len(rows) >= BATCH:
            CampaignAttribution.objects.bulk_create(rows)
            rows = []
    if rows:
        CampaignAttribution.objects.bulk_create(rows)
    check_invariant(org_id)
    if days is not None:
        baseline.refresh(org_id, events, *days, now=now)
    if won_back is not None:
        winbacks.match(org_id, won_back, events, now=now)
    return summary


def holdouts(org_id: str) -> dict[uuid.UUID, set[str]]:
    """Each event's control group, as the contact feed named it."""
    held: dict[uuid.UUID, set[str]] = defaultdict(set)
    for event_id, customer in CampaignContact.objects.filter(
        org_id=org_id, holdout=True
    ).values_list("event_id", "customer_ref"):
        held[event_id].add(customer)
    return held


def check_invariant(org_id: str) -> None:
    """The database's own word that no outcome is credited more than its value."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT a.outcome_id, SUM(a.attributed_value), MIN(o.activity_value) "
            "FROM campaign_attribution a JOIN campaign_outcome o ON o.outcome_id = a.outcome_id "
            "WHERE a.org_id = %s AND a.credited GROUP BY a.outcome_id "
            "HAVING SUM(a.attributed_value) > MIN(o.activity_value) LIMIT 1",
            [org_id],
        )
        broken = cur.fetchone()
    if broken is not None:
        raise AttributionInvariantError(
            f"Outcome {broken[0]} is credited {broken[1]}, more than its value {broken[2]}."
        )


def reattribute(
    org_id: str, since: date, until: date, *, events: Iterable[CampaignEvent] = ()
) -> Summary:
    """Redo attribution for outcomes in ``(since, until]`` and clear the events' marks.

    Callers pass the union of where the changed events were and are now, so an
    outcome a change moved out of an event's reach loses its credit too.
    """
    summary = attribute(
        org_id, Q(activity_date__gt=since, activity_date__lte=until), days=(since, until)
    )
    for e in events:
        if e.reattribute_from is not None:
            e.reattribute_from = None
            e.save(update_fields=["reattribute_from"])
    return summary


def span(
    events: Iterable[CampaignEvent], *, with_baseline: bool = False
) -> tuple[date, date] | None:
    """The days the published events among ``events`` can attribute: (first start, last end].

    ``with_baseline`` reaches back over each event's baseline window too, for a
    change (a control group, a contact) that alters baselines as well as credit.
    """
    published = [e for e in events if e.state != "draft"]
    if not published:
        return None
    starts = [
        baseline.window(e.period_start, window_end(e))[0] if with_baseline else e.period_start
        for e in published
    ]
    starts += [e.reattribute_from for e in published if e.reattribute_from is not None]
    return min(starts), max(window_end(e) for e in published)
