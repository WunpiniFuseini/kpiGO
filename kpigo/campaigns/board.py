"""The tracking board: the quarter at a glance, and each campaign's figures (PRD CM-18, App Flow §5.4).

The board counts the events *in play* in the period: published events whose
span (first contact day, end of window] overlaps it. An event's value is its
whole value, read from ``kpigo.campaigns.value``; the board does not prorate an
event across quarters. Money is summed per budget currency and never converted.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from kpigo.campaigns import authoring as au
from kpigo.campaigns import reach as rc
from kpigo.campaigns import value as vl
from kpigo.campaigns import winbacks as wb
from kpigo.campaigns.models import Campaign, CampaignContact, CampaignEvent, CampaignWinback


def quarter_of(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def in_play(e: CampaignEvent, start: date, end: date) -> bool:
    """The event's span (period_start, window_end] overlaps [start, end]."""
    return e.state != "draft" and e.period_start < end and au.window_end(e) >= start


@dataclass
class Money:
    """One currency's totals. Incremental is withheld when any measured event's is."""

    currency: str
    budget: Decimal = Decimal(0)
    spend: Decimal | None = None
    gross: Decimal | None = None
    measured_events: int = 0
    withheld_events: int = 0
    _incremental: Decimal = Decimal(0)

    def add(self, v: vl.EventValue) -> None:
        self.budget += v.budget
        if v.spend is not None:
            self.spend = (self.spend or Decimal(0)) + v.spend
        if v.gross is None:
            return
        self.measured_events += 1
        self.gross = (self.gross or Decimal(0)) + v.gross
        if v.incremental is None:
            self.withheld_events += 1
        else:
            self._incremental += v.incremental

    @property
    def incremental(self) -> Decimal | None:
        if not self.measured_events or self.withheld_events:
            return None
        return self._incremental

    @property
    def roi(self) -> Decimal | None:
        found = self.incremental
        return vl.ratio(None if found is None else found - self.budget, self.budget)

    @property
    def gross_roi(self) -> Decimal | None:
        return vl.ratio(None if self.gross is None else self.gross - self.budget, self.budget)


@dataclass
class Row:
    campaign_id: str
    events_in_play: int = 0
    contacted: int | None = None
    converted: int | None = None
    money: dict[str, Money] = field(default_factory=dict)
    winbacks: wb.Counts | None = None


@dataclass
class Board:
    start: date
    end: date
    basis: str
    outcomes_fed: bool
    contacts_fed: bool
    money: dict[str, Money] = field(default_factory=dict)
    winbacks: wb.Counts | None = None
    rows: list[Row] = field(default_factory=list)


def build(org_id: str, campaigns: list[Campaign], start: date, end: date) -> Board:
    basis, _ = vl.basis_for(org_id)
    fed = rc.Fed.of(org_id)
    board = Board(start, end, basis, fed.outcomes, fed.contacts)
    events: dict[object, list[CampaignEvent]] = defaultdict(list)
    for e in CampaignEvent.objects.filter(campaign__in=campaigns).order_by("sequence_no"):
        if in_play(e, start, end):
            events[e.campaign_id].append(e)
    days = wb.retention_days(org_id)
    winbacks_fed = CampaignWinback.objects.filter(org_id=org_id).exists()
    all_ids = [e.event_id for found in events.values() for e in found]
    if winbacks_fed and all_ids:
        board.winbacks = wb.counts(CampaignWinback.objects.filter(event_id__in=all_ids), days, end)
    for c in campaigns:
        mine = events.get(c.campaign_id, [])
        row = Row(str(c.campaign_id), events_in_play=len(mine))
        if mine and fed.contacts:
            contacts = CampaignContact.objects.filter(event__in=mine, holdout=False)
            row.contacted = contacts.values("customer_ref").distinct().count()
        for e in mine:
            v = vl.event_value(e, basis, fed.outcomes, fed.contacts)
            for target in (row.money, board.money):
                target.setdefault(v.currency, Money(v.currency)).add(v)
            if v.converted_customers is not None:
                row.converted = (row.converted or 0) + v.converted_customers
        if mine and winbacks_fed and c.objective == wb.OBJECTIVE:
            row.winbacks = wb.counts(CampaignWinback.objects.filter(event__in=mine), days, end)
        board.rows.append(row)
    return board
