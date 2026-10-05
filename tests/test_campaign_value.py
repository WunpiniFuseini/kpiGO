"""Baseline, incremental value, control groups and ROI (Scope §9.1b, §9.2; PRD CM-14, CM-16).

The event under test runs 1-10 Sep 2026 with a 30-day window, so its span is
(1 Sep, 10 Oct], 39 days, and its baseline window is (24 Jul, 1 Sep].
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from kpigo.action import Conflict, InvalidInput
from kpigo.campaigns.models import CampaignAttribution, CampaignBaseline, CampaignEvent
from kpigo.platform.models import AuditLog
from tests.campaign_support import admin, campaign, event
from tests.conftest import run
from tests.ingestion_support import dry
from tests.test_campaign_attribution import (
    CONTACT,
    OUTCOME,
    START,
    credits,
    live_event,
    load,
    org,  # noqa: F401  (the autouse world)
    outcome,
    outcome_row,
    published,
    redo,
    rules,
)

pytestmark = pytest.mark.django_db

DATES: dict[str, Any] = {"period_start": "2026-09-01", "period_end": "2026-09-10"}


def history() -> None:
    """An outcome at the start of the baseline window, so the feed's history covers it."""
    outcome("C0", date(2026, 7, 25), "1")


def baselines(e: CampaignEvent) -> dict[str, tuple[str, Decimal | None, Decimal | None]]:
    return {
        b.customer_ref: (b.confidence, b.baseline, b.incremental)
        for b in CampaignBaseline.objects.filter(event=e)
    }


def value(e: CampaignEvent) -> Any:
    report = run("campaign.value", admin(), campaign_id=str(e.campaign_id))
    return report, next(v for v in report.events if v.event_id == str(e.event_id))


def test_incremental_is_gross_less_the_same_customers_pre_period() -> None:
    e = published(**DATES)
    history()
    outcome("C1", date(2026, 8, 15), "40")
    outcome("C1", date(2026, 9, 5), "100")
    outcome("C2", date(2026, 9, 6), "70")  # nothing before: a new customer
    outcome("C3", date(2026, 8, 1), "300")
    outcome("C3", date(2026, 9, 7), "100")  # a worse month reads as negative
    redo()
    assert baselines(e) == {
        "C1": ("high", Decimal("40.0000"), Decimal("60.0000")),
        "C2": ("new_customer", None, Decimal("70.0000")),
        "C3": ("high", Decimal("300.0000"), Decimal("-200.0000")),
    }
    report, ev = value(e)
    assert report.basis == "incremental" and report.basis_changed_at is None
    assert (ev.baseline_start, ev.baseline_end) == (date(2026, 7, 24), date(2026, 9, 1))
    assert (ev.gross, ev.baseline, ev.incremental, ev.withheld) == (
        "270.0000",
        "340.0000",
        "-70.0000",
        None,
    )
    assert (ev.converted_customers, ev.new_customers, ev.contaminated_customers) == (3, 1, 0)
    # (incremental - budget) / budget, with gross beside it.
    assert ev.roi == str(((Decimal("-70") - 25000) / 25000).quantize(Decimal("0.0001")))
    assert ev.gross_roi == str(((Decimal("270") - 25000) / 25000).quantize(Decimal("0.0001")))
    assert ev.cost_per_outcome == "8333.33"
    assert ev.utilisation is None  # no spend fed


def test_a_shared_outcome_subtracts_only_the_events_share_of_the_baseline() -> None:
    a = published("SPLIT-A", **DATES)
    b = published("SPLIT-B", **DATES)
    history()
    outcome("C1", date(2026, 8, 15), "40")
    outcome("C1", date(2026, 9, 5), "100")
    run("campaign.attribution.rule.set", admin(), rule="split_even")
    for e in (a, b):
        # Events starting the same day do not contaminate each other's baseline.
        assert baselines(e) == {"C1": ("high", Decimal("20.0000"), Decimal("30.0000"))}


def test_a_baseline_another_event_reached_withholds_incremental_but_not_gross() -> None:
    e = published(**DATES)
    rival = published("EARLIER", period_start="2026-08-01", period_end="2026-08-10")
    history()
    outcome("C1", date(2026, 8, 15), "40")
    outcome("C1", date(2026, 9, 5), "100")
    redo()
    assert credits(CampaignAttribution.objects.get(event=e, credited=True).outcome)
    row = CampaignBaseline.objects.get(event=e)
    assert row.confidence == "low_contaminated_baseline" and row.incremental is None
    assert row.contaminated_by_id == rival.event_id
    _, ev = value(e)
    assert ev.withheld == "contaminated_baseline" and ev.contaminated_customers == 1
    assert ev.incremental is None and ev.roi is None and ev.roi_reason == "contaminated_baseline"
    assert ev.gross == "100.0000" and ev.gross_roi is not None


def test_a_history_that_starts_inside_the_baseline_window_withholds_incremental() -> None:
    e = published(**DATES)
    outcome("C1", date(2026, 8, 15), "40")
    outcome("C1", date(2026, 9, 5), "100")
    redo()
    _, ev = value(e)
    assert ev.withheld == "history_too_short" and ev.incremental is None and ev.gross == "100.0000"


def test_value_is_absent_before_anything_is_fed_and_for_a_draft() -> None:
    out = campaign(events=[event(**DATES)])
    _, ev = value(CampaignEvent.objects.get(event_id=out.events[0].event_id))
    assert ev.withheld == ev.roi_reason == ev.control.reason == "not_published"
    e = published("LIVE", **DATES)
    _, ev = value(e)
    assert ev.gross is None and ev.withheld == "no_outcomes_fed"
    assert ev.control.reason == "no_contacts_fed" and ev.control.treated is None


def test_the_basis_is_changed_through_approval_and_leaves_a_banner() -> None:
    e = published(**DATES)
    history()
    outcome("C1", date(2026, 9, 5), "100")
    redo()
    with pytest.raises(InvalidInput):
        run("campaign.value_basis.set", admin(), basis="gross", reason="")
    out = run("campaign.value_basis.set", admin(), basis="gross", reason="Board reports gross")
    assert out.basis == "gross" and out.basis_changed_at is not None
    report, ev = value(e)
    assert report.basis == "gross" and report.basis_changed_at == out.basis_changed_at
    assert ev.roi == ev.gross_roi
    assert AuditLog.objects.filter(event="campaign.value_basis.detail").exists()


# ── control groups, through the feeds ────────────────────────────────────────

HELD = [*CONTACT, "holdout"]


def test_held_out_customers_are_never_credited_and_lift_compares_the_groups() -> None:
    e = live_event(holdout_pct=10)
    on = (START + timedelta(days=2)).isoformat()
    load(
        "contacts",
        "campaign_contact",
        HELD,
        [
            ["C1", "SAVE-Q4", "sms", on, "Y", None, None],
            ["C3", "SAVE-Q4", "sms", on, "Y", None, "N"],
            ["C2", "SAVE-Q4", None, on, None, None, "Y"],
            ["C4", "SAVE-Q4", None, on, None, None, "Y"],
        ],
    )
    load(
        "outcomes",
        "campaign_outcome",
        OUTCOME,
        [outcome_row("C1", 25, "100"), outcome_row("C2", 25, "50")],
    )
    held = CampaignAttribution.objects.get(event=e, outcome__customer_ref="C2")
    assert (held.credited, held.rule_applied) == (False, "holdout")
    reach = run("campaign.reach", admin(), campaign_id=str(e.campaign_id)).events[0]
    assert (reach.contacted, reach.held_out, reach.converted_customers) == (2, 2, 1)
    _, ev = value(e)
    c = ev.control
    assert (c.planned_pct, c.treated, c.control, c.actual_pct) == (10, 2, 2, "50.0")
    assert (c.treated_rate, c.control_rate, c.lift_points) == ("0.5000", "0.5000", "0.00")
    # (100/2 - 50/2) x 2 contacted.
    assert c.incremental == "50.00" and c.small and c.reason is None


def test_a_contact_row_has_a_channel_exactly_when_it_was_contacted() -> None:
    live_event()
    on = (START + timedelta(days=2)).isoformat()
    from tests.ingestion_support import register_feed

    register_feed("contacts", "campaign_contact")
    found = dry("contacts", HELD, [["C1", "SAVE-Q4", None, on, "Y", None, "N"]])
    assert rules(found) == {"channel_required": "error"}
    found = dry("contacts", HELD, [["C1", "SAVE-Q4", "sms", on, None, None, "Y"]])
    assert rules(found) == {"holdout_contacted": "error"}


def test_the_planned_control_share_is_bounded_and_fixed_once_the_event_starts() -> None:
    with pytest.raises(InvalidInput):
        campaign(events=[event(holdout_pct=60)])
    out = campaign(events=[event(**DATES, holdout_pct=10)])
    event_id = out.events[0].event_id
    cleared = run("campaign.event.update", admin(), event_id=event_id, holdout_pct=0)
    assert cleared.events[0].holdout_pct is None
    run("campaign.event.update", admin(), event_id=event_id, holdout_pct=15)
    run("campaign.event.publish", admin(), event_id=event_id)
    with pytest.raises(Conflict):
        run("campaign.event.update", admin(), event_id=event_id, holdout_pct=20)
