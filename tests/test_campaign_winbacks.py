"""Win-backs with retention qualification (PRD CM-16, Scope §9.4).

Dates are relative to today, because the gates refuse the future. The win-back
event's contact days start 40 days ago, with a 90-day window.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from kpigo.action import InvalidInput
from kpigo.campaigns.models import CampaignEvent, CampaignWinback
from tests.campaign_support import admin, campaign, event
from tests.conftest import run
from tests.ingestion_support import dry, register_feed
from tests.test_campaign_attribution import (
    CONTACT,
    REAL_TODAY,
    START,
    live_event,
    load,
    org,  # noqa: F401  (the autouse world)
    rules,
)

pytestmark = pytest.mark.django_db

WINBACK = [
    "customer_ref",
    "campaign_code",
    "qualified_at",
    "winback_flag",
    "retention_confirmed_at",
    "segment_code",
    "product_code",
    "region_code",
]
HELD = [*CONTACT, "holdout"]


def day(n: int) -> str:
    return (START + timedelta(days=n)).isoformat()


def winback_event(code: str = "WIN-Q3", **ev: Any) -> CampaignEvent:
    out = campaign(
        code=code,
        objective="attrition_winback",
        events=[event(period_start=day(0), period_end=day(10), attribution_window_days=90, **ev)],
    )
    run("campaign.event.publish", admin(), event_id=out.events[0].event_id)
    return CampaignEvent.objects.get(event_id=out.events[0].event_id)


def row(customer: str, qualified: int, flag: str = "Y", **over: Any) -> list[Any]:
    values = {
        "customer_ref": customer,
        "campaign_code": None,
        "qualified_at": day(qualified),
        "winback_flag": flag,
        "retention_confirmed_at": None,
        "segment_code": "mass",
        "product_code": "savings",
        "region_code": "GA",
    } | over
    return [values[c] for c in WINBACK]


def counts(e: CampaignEvent) -> Any:
    out = run("campaign.winbacks", admin(), campaign_id=str(e.campaign_id))
    return out, next(x.counts for x in out.events if x.event_id == str(e.event_id))


def matched() -> dict[str, tuple[Any, str | None, str | None]]:
    return {
        w.customer_ref: (w.event_id, w.via, w.rule_applied) for w in CampaignWinback.objects.all()
    }


def test_a_win_back_is_earned_by_tag_contact_or_audience_but_never_by_a_held_out_customer() -> None:
    e = winback_event(holdout_pct=10)
    load(
        "contacts",
        "campaign_contact",
        HELD,
        [
            ["C1", "WIN-Q3", "call", day(2), "Y", None, None],
            ["C2", "WIN-Q3", None, day(2), None, None, "Y"],
        ],
    )
    load(
        "winbacks",
        "campaign_winback",
        WINBACK,
        [
            row("C1", 5, segment_code=None),  # contacted, though the feed has no segment
            row("C2", 5),  # the control group
            row("C3", 6, campaign_code="WIN-Q3", segment_code="sme"),  # the client's tag
            row("C4", 7),  # in the audience
            row("C5", -1),  # before the first contact day
        ],
    )
    assert matched() == {
        "C1": (e.event_id, "contact", "single"),
        "C2": (None, None, None),
        "C3": (e.event_id, "tag", "single"),
        "C4": (e.event_id, "criteria", "single"),
        "C5": (None, None, None),
    }
    out, c = counts(e)
    assert out.retention_days == 90 and out.fed and out.earns_winbacks
    # Qualified 33 to 35 days ago: all still inside the 90-day window.
    assert (c.qualified, c.provisional, c.confirmed, c.lapsed) == (3, 3, 0, 0)
    assert c.next_confirmation == START + timedelta(days=5 + 90)


def test_retention_confirms_lapses_and_moves_with_the_window() -> None:
    e = winback_event()
    load(
        "winbacks",
        "campaign_winback",
        WINBACK,
        [
            row("C1", 5),
            row("C2", 6, retention_confirmed_at=day(30)),  # the client confirmed it already
            row("C3", 7),
        ],
    )
    _, c = counts(e)
    assert (c.provisional, c.confirmed, c.lapsed) == (2, 1, 0)
    # A later load of changes only: C3 no longer qualifies.
    load("winbacks", "campaign_winback", WINBACK, [row("C3", 7, "N")])
    _, c = counts(e)
    assert (c.qualified, c.provisional, c.confirmed, c.lapsed) == (3, 1, 1, 1)
    # 35 days since C1 qualified: a 30-day window confirms it at once.
    out = run("campaign.winback.retention.set", admin(), days=30, reason="Policy review")
    assert out.retention_days == 30 and out.counts.confirmed == 2
    _, c = counts(e)
    assert (c.provisional, c.confirmed, c.next_confirmation) == (0, 2, None)
    with pytest.raises(InvalidInput):
        run("campaign.winback.retention.set", admin(), days=0, reason="No")


def test_publishing_matches_win_backs_loaded_while_the_event_was_a_draft() -> None:
    out = campaign(
        code="WIN-Q3",
        objective="attrition_winback",
        events=[event(period_start=day(0), period_end=day(10), attribution_window_days=90)],
    )
    load("winbacks", "campaign_winback", WINBACK, [row("C1", 5)])
    assert matched()["C1"][0] is None
    run("campaign.event.publish", admin(), event_id=out.events[0].event_id)
    assert str(matched()["C1"][0]) == out.events[0].event_id


def test_only_attrition_win_back_events_earn_win_backs() -> None:
    e = live_event()
    load("winbacks", "campaign_winback", WINBACK, [row("C1", 5, campaign_code="SAVE-Q4")])
    assert matched()["C1"][0] is None
    out, c = counts(e)
    assert not out.earns_winbacks and c is None


def test_win_backs_are_absent_not_zero_before_any_is_fed() -> None:
    e = winback_event()
    out, c = counts(e)
    assert not out.fed and c is None


def test_win_back_gates() -> None:
    winback_event()
    register_feed("winbacks", "campaign_winback")
    found = dry(
        "winbacks",
        WINBACK,
        [
            row("C1", 5, retention_confirmed_at=day(4)),
            row("C2", 5, campaign_code="NOPE"),
            row("C3", 5, "maybe"),
            row("C4", 5, qualified_at=(REAL_TODAY + timedelta(days=1)).isoformat()),
        ],
    )
    assert rules(found) == {
        "retention_before_qualified": "error",
        "unknown_campaign_tag": "warning",
        "not_a_flag": "error",
        "future_date": "error",
    }
