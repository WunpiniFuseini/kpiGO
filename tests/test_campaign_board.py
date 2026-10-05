"""The tracking board and the reconciliation report (PRD CM-18, CM-20).

Engineering Plan R3 exit: a two-event campaign reconciles against source
totals, with the invariant holding under every collision rule.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from kpigo.action import InvalidInput
from kpigo.campaigns.models import ATTRIBUTION_RULES, CampaignEvent
from tests.campaign_support import admin, campaign, event
from tests.conftest import run
from tests.test_campaign_attribution import (
    org,  # noqa: F401  (the autouse world; today is 10 Oct 2026)
    outcome,
    published,
    redo,
)

pytestmark = pytest.mark.django_db


def two_event_campaign() -> tuple[CampaignEvent, CampaignEvent]:
    out = campaign(
        code="SAVE-Q4",
        events=[
            event(event_name="First wave", period_start="2026-09-01", period_end="2026-09-10"),
            event(event_name="Second wave", period_start="2026-09-15", period_end="2026-09-25"),
        ],
    )
    for e in out.events:
        run("campaign.event.publish", admin(), event_id=e.event_id)
    first, second = (CampaignEvent.objects.get(event_id=e.event_id) for e in out.events)
    return first, second


def reconcile(e: CampaignEvent) -> Any:
    return run("campaign.reconciliation", admin(), campaign_id=str(e.campaign_id))


@pytest.mark.parametrize("rule", ATTRIBUTION_RULES)
def test_a_two_event_campaign_reconciles_against_source_under_every_rule(rule: str) -> None:
    first, _ = two_event_campaign()
    published("OTHER", period_start="2026-09-05", period_end="2026-09-12")
    outcome("C1", date(2026, 9, 3), "100")  # the first wave only
    outcome("C2", date(2026, 9, 8), "50")  # the first wave and OTHER
    outcome("C3", date(2026, 9, 20), "70.0001")  # both waves and OTHER
    outcome("C4", date(2026, 9, 4), "20", segment=None)  # no audience matches it
    outcome("C5", date(2026, 8, 20), "30")  # before the campaign's span
    outcome("C6", date(2026, 9, 6), "40", metric="cmp_fees")  # not an objective metric
    run("campaign.attribution.rule.set", admin(), rule=rule)
    out = reconcile(first)
    assert (out.span_start, out.span_end) == (date(2026, 9, 1), date(2026, 10, 25))
    (line,) = out.lines
    assert (line.metric_code, line.currency, line.source_outcomes) == (
        "cmp_deposit_value",
        "GHS",
        4,
    )
    assert Decimal(line.source_total) == Decimal("240.0001")
    here, there, none = (
        Decimal(line.credited_here),
        Decimal(line.credited_elsewhere),
        Decimal(line.unattributed),
    )
    assert here + there + none == Decimal(line.source_total)
    assert none == Decimal("20") and out.invariant_holds
    credited = sum((Decimal(e.credited) for e in out.events), Decimal(0))
    assert credited == here


def test_reconciliation_says_what_each_event_won_and_lost() -> None:
    first, _ = two_event_campaign()
    other = published("OTHER", period_start="2026-09-05", period_end="2026-09-12")
    outcome("C1", date(2026, 9, 3), "100")
    outcome("C2", date(2026, 9, 8), "50")
    outcome("C3", date(2026, 9, 20), "70")
    redo()
    out = reconcile(first)
    (line,) = out.lines
    # Last touch: OTHER started after the first wave, the second wave after both.
    assert (line.credited_here, line.credited_elsewhere, line.unattributed) == (
        "170.0000",
        "50.0000",
        "0.0000",
    )
    a, b = out.events
    assert (a.event_id, a.credited, a.credited_outcomes) == (str(first.event_id), "100.0000", 1)
    assert (a.lost, a.lost_outcomes) == ("120.0000", 2)
    assert a.by_rule == {"single": 1, "last_touch": 2}
    assert (b.credited, b.lost, b.by_rule) == ("70.0000", "0.0000", {"last_touch": 1})
    assert reconcile(other).lines[0].credited_here == "50.0000"


def test_reconciliation_lists_customers_whose_baseline_was_contaminated() -> None:
    e = published(period_start="2026-09-01", period_end="2026-09-10")
    rival = published("EARLIER", period_start="2026-08-01", period_end="2026-08-10")
    outcome("C0", date(2026, 7, 25), "1")
    outcome("C1", date(2026, 8, 15), "40")
    outcome("C1", date(2026, 9, 5), "100")
    redo()
    out = reconcile(e)
    assert out.contaminated_total == 1
    (c,) = out.contaminated
    assert (c.customer_ref, c.contaminated_by) == ("C1", str(rival.event_id))


def test_reconciliation_of_a_campaign_with_nothing_published_is_empty() -> None:
    out = campaign(code="DRAFT-ONLY", events=[event()])
    found = run("campaign.reconciliation", admin(), campaign_id=out.campaign_id)
    assert found.span_start is None and found.lines == [] and found.events == []


# ── the board ────────────────────────────────────────────────────────────────


def test_the_board_sums_the_events_in_play_this_quarter() -> None:
    live = published(period_start="2026-09-01", period_end="2026-09-10")  # window to 10 Oct
    done = published("SUMMER", period_start="2026-06-01", period_end="2026-06-10")
    outcome("C0", date(2026, 7, 25), "1")
    outcome("C1", date(2026, 8, 15), "40")
    outcome("C1", date(2026, 9, 5), "100")
    redo()
    board = run("campaign.board", admin())
    assert (board.start, board.end, board.basis) == (
        date(2026, 10, 1),
        date(2026, 10, 10),
        "incremental",
    )
    (money,) = board.money
    assert (money.currency, money.budget, money.gross, money.incremental) == (
        "GHS",
        "25000.00",
        "100.0000",
        "60.0000",
    )
    assert money.roi == str(((Decimal("60") - 25000) / 25000).quantize(Decimal("0.0001")))
    rows = {r.campaign_id: r for r in board.campaigns}
    assert rows[str(live.campaign_id)].events_in_play == 1
    assert rows[str(live.campaign_id)].converted == 1
    assert rows[str(live.campaign_id)].contacted is None  # no contact feed
    assert rows[str(done.campaign_id)].events_in_play == 0
    assert rows[str(done.campaign_id)].money == []
    assert board.winbacks is None


def test_the_board_can_look_at_an_earlier_quarter_but_not_ahead() -> None:
    published("SUMMER", period_start="2026-06-01", period_end="2026-06-10")
    board = run("campaign.board", admin(), on="2026-08-15")
    assert (board.start, board.end) == (date(2026, 7, 1), date(2026, 9, 30))
    assert board.campaigns[0].events_in_play == 1
    with pytest.raises(InvalidInput):
        run("campaign.board", admin(), on="2026-12-01")
