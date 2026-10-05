"""Attribution and reach (PRD CM-7 to CM-13, CM-15; TDD §7.2).

The engine is exercised over outcome rows written directly, with fixed dates;
the feeds are exercised end to end through ``feed.dry_run``/``feed.run``, with
dates relative to today because the gates refuse the future.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import Q, Sum
from django.test import Client
from django.utils import timezone

from kpigo.action import InvalidInput
from kpigo.campaigns import attribution, authoring
from kpigo.campaigns.attribution import AttributionInvariantError, allocate, split
from kpigo.campaigns.models import (
    ATTRIBUTION_RULES,
    CampaignAttribution,
    CampaignContact,
    CampaignEvent,
    CampaignOutcome,
    CampaignPopulation,
)
from kpigo.ingestion import validator as v
from kpigo.ingestion.reference import build_reference
from kpigo.metrics.models import Metric
from tests.campaign_support import admin, campaign, event, world
from tests.conftest import ORG_ID, run
from tests.ingestion_support import dry, live, register_feed

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 10)
OUTCOME = [
    "customer_ref",
    "metric_code",
    "campaign_code",
    "activity_date",
    "activity_value",
    "currency_code",
    "source_ref",
    "segment_code",
    "product_code",
    "region_code",
]


@pytest.fixture(autouse=True)
def org(monkeypatch: pytest.MonkeyPatch) -> None:
    world()
    monkeypatch.setattr(authoring, "today", lambda: TODAY)
    for code, name in (("cmp_deposit_value", "Campaign deposits"), ("cmp_fees", "Fees")):
        run(
            "metric.register",
            display_name=name,
            metric_code=code,
            direction="higher_is_better",
            aggregation="sum",
            unit="currency",
            products=["campaign"],
            effective_from="2024-01-01",
        )
    objective(["cmp_deposit_value"])


def objective(codes: list[str], name: str = "deposit_growth") -> Any:
    return run(
        "campaign.objective.set",
        admin(),
        objective=name,
        default_window_days=30,
        outcome_metric_codes=codes,
    )


def metric_id(code: str = "cmp_deposit_value") -> uuid.UUID:
    return Metric.objects.get(metric_code=code).metric_id


def outcome(
    customer: str,
    day: date,
    value: str = "100",
    *,
    segment: str | None = "mass",
    product: str | None = "savings",
    region: str | None = "GA",
    tag: str | None = None,
    metric: str = "cmp_deposit_value",
) -> CampaignOutcome:
    from kpigo.campaigns.models import Campaign

    found = Campaign.objects.filter(code=tag).first() if tag else None
    return CampaignOutcome.objects.create(
        org_id=ORG_ID,
        customer_ref=customer,
        metric_id=metric_id(metric),
        campaign_code=tag,
        campaign=found,
        activity_date=day,
        activity_value=Decimal(value),
        currency_code="GHS",
        segment_code=segment,
        product_code=product,
        region_code=region,
        run_id=uuid.uuid4(),
        loaded_at=timezone.now(),
    )


def published(code: str = "SAVE-Q4", priority: int | None = None, **ev: Any) -> CampaignEvent:
    out = campaign(code=code, events=[event(**ev)], **({"priority": priority} if priority else {}))
    run("campaign.event.publish", admin(), event_id=out.events[0].event_id)
    return CampaignEvent.objects.get(event_id=out.events[0].event_id)


def credits(o: CampaignOutcome) -> dict[uuid.UUID, tuple[bool, Decimal, str]]:
    return {
        a.event_id: (a.credited, a.attributed_value, a.rule_applied)
        for a in CampaignAttribution.objects.filter(outcome=o)
    }


def redo() -> attribution.Summary:
    return attribution.attribute(ORG_ID, Q())


# ── matching ─────────────────────────────────────────────────────────────────


def test_window_runs_after_the_first_contact_day_to_the_end_of_the_window() -> None:
    e = published(period_start="2026-09-01", period_end="2026-09-10")  # window 30: to 10 Oct
    on_start = outcome("C1", date(2026, 9, 1))
    first = outcome("C2", date(2026, 9, 2))
    last = outcome("C3", date(2026, 10, 10))
    after = outcome("C4", date(2026, 10, 11))
    summary = redo()
    assert summary.outcomes == 4 and summary.attributed == 2 and summary.unattributed == 2
    assert credits(on_start) == {} and credits(after) == {}
    assert credits(first) == {e.event_id: (True, Decimal("100"), "single")}
    assert credits(last)[e.event_id][0] is True


def test_audience_matches_members_below_and_every_dimension_named() -> None:
    e = published(
        period_start="2026-09-01",
        audience=[
            {"dimension_type": "segment", "member_code": "retail"},
            {"dimension_type": "region", "member_code": "GA"},
        ],
    )
    day = date(2026, 9, 15)
    below = outcome("C1", day, segment="affluent")
    other_segment = outcome("C2", day, segment="sme")
    other_region = outcome("C3", day, region="AS")
    no_region = outcome("C4", day, region=None)
    # The campaign is for savings: a cards outcome is not its doing.
    other_product = outcome("C5", day, product="cards")
    redo()
    assert e.event_id in credits(below)
    for o in (other_segment, other_region, no_region, other_product):
        assert credits(o) == {}


def test_the_clients_tag_links_an_outcome_whatever_the_audience() -> None:
    e = published(period_start="2026-09-01")
    tagged = outcome("C1", date(2026, 9, 15), segment="sme", product="cards", tag="SAVE-Q4")
    redo()
    assert credits(tagged)[e.event_id][0] is True
    assert CampaignAttribution.objects.get(outcome=tagged).via == "tag"


def test_only_the_objectives_outcome_metrics_count() -> None:
    e = published(period_start="2026-09-01")
    fee = outcome("C1", date(2026, 9, 15), metric="cmp_fees")
    redo()
    assert credits(fee) == {}
    # Naming the metric for the objective weighs every outcome again.
    objective(["cmp_deposit_value", "cmp_fees"])
    assert credits(fee)[e.event_id][0] is True


def test_drafts_attribute_nothing_and_publishing_credits_what_is_loaded() -> None:
    out = campaign(events=[event(period_start="2026-09-01")])
    o = outcome("C1", date(2026, 9, 15))
    redo()
    assert credits(o) == {}
    run("campaign.event.publish", admin(), event_id=out.events[0].event_id)
    assert credits(o)[uuid.UUID(out.events[0].event_id)][0] is True


def test_changing_a_live_events_dates_or_audience_reattributes_at_once() -> None:
    e = published(period_start="2026-09-01", period_end="2026-09-30")
    late = outcome("C1", date(2026, 10, 20))
    redo()
    assert credits(late)[e.event_id][0] is True
    run("campaign.event.update", admin(), event_id=str(e.event_id), period_end="2026-09-10")
    assert credits(late) == {}
    run(
        "campaign.event.update",
        admin(),
        event_id=str(e.event_id),
        period_end="2026-09-30",
        audience=[{"dimension_type": "segment", "member_code": "sme"}],
    )
    assert credits(late) == {}
    e.refresh_from_db()
    assert e.reattribute_from is None


# ── collisions ───────────────────────────────────────────────────────────────


def two_events() -> tuple[CampaignEvent, CampaignEvent, CampaignOutcome]:
    early = published("EARLY", priority=1, period_start="2026-09-01")
    late = published("LATE", priority=2, period_start="2026-09-10")
    return early, late, outcome("C1", date(2026, 9, 20), "100.0001")


def test_last_touch_is_the_default_and_the_loser_still_counts_the_customer_as_reached() -> None:
    early, late, o = two_events()
    summary = redo()
    assert summary.rule == "last_touch" and summary.collisions == 1
    assert credits(o) == {
        late.event_id: (True, Decimal("100.0001"), "last_touch"),
        early.event_id: (False, Decimal("0"), "last_touch"),
    }
    reach = run("campaign.reach", admin(), campaign_id=str(early.campaign_id))
    (er,) = reach.events
    assert er.matched_customers == 1 and er.converted_customers == 0 and er.attributed == []


@pytest.mark.parametrize(
    ("rule", "winner"), [("first_touch", "early"), ("priority", "early"), ("last_touch", "late")]
)
def test_collision_rules_pick_one_event(rule: str, winner: str) -> None:
    early, late, o = two_events()
    summary = run("campaign.attribution.rule.set", admin(), rule=rule, reason="Onboarding")
    assert summary.rule == rule and summary.attributed == 1
    won = {"early": early, "late": late}[winner]
    assert [k for k, c in credits(o).items() if c[0]] == [won.event_id]


def test_split_even_shares_the_value_exactly() -> None:
    early, late, o = two_events()
    third = published("THIRD", period_start="2026-09-05")
    run("campaign.attribution.rule.set", admin(), rule="split_even")
    found = credits(o)
    assert all(c[0] for c in found.values()) and len(found) == 3
    assert sum(c[1] for c in found.values()) == Decimal("100.0001")
    # The remainder goes to the most recent touch.
    assert found[late.event_id][1] == Decimal("33.3335")
    assert found[early.event_id][1] == found[third.event_id][1] == Decimal("33.3333")


def test_a_campaigns_priority_change_settles_its_collisions_again() -> None:
    early, late, o = two_events()
    run("campaign.attribution.rule.set", admin(), rule="priority")
    assert credits(o)[early.event_id][0] is True
    run("campaign.update", admin(), campaign_id=str(late.campaign_id), priority=1)
    run("campaign.update", admin(), campaign_id=str(early.campaign_id), priority=3)
    assert credits(o)[late.event_id][0] is True


# ── the invariant ────────────────────────────────────────────────────────────


def test_split_and_allocate_never_credit_more_than_the_value() -> None:
    rng = random.Random(7)
    events = [
        attribution.EventSpec(
            event_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            priority=rng.randint(1, 3),
            sequence_no=1,
            period_start=date(2026, 9, rng.randint(1, 20)),
            period_end=date(2026, 9, 30),
            window_end=date(2026, 12, 31),
            metric_ids=frozenset(),
            products=None,
        )
        for _ in range(6)
    ]
    for _ in range(500):
        value = Decimal(rng.randint(0, 10**9)) / 10**4
        n = rng.randint(1, 6)
        assert sum(split(value, n)) == value
        for rule in ATTRIBUTION_RULES:
            found, _ = allocate(value, events[:n], rule)
            assert sum(c.value for c in found if c.credited) <= value


def test_a_violation_fails_the_run_and_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    e = published(period_start="2026-09-01")
    o = outcome("C1", date(2026, 9, 15))
    redo()
    before = credits(o)

    def greedy(value: Decimal, found: list[Any], rule: str) -> Any:
        return [attribution.Credit(found[0].event_id, True, value + 1, Decimal(1))], rule

    monkeypatch.setattr(attribution, "allocate", greedy)
    with pytest.raises(AttributionInvariantError, match="more than its value"):
        run("campaign.attribution.run", admin())
    assert credits(o) == before == {e.event_id: (True, Decimal("100"), "single")}


# ── feeds ────────────────────────────────────────────────────────────────────

REAL_TODAY = date.today()
START = REAL_TODAY - timedelta(days=40)


def live_event(**ev: Any) -> CampaignEvent:
    return published(
        period_start=START.isoformat(),
        period_end=(START + timedelta(days=10)).isoformat(),
        **ev,
    )


def outcome_row(customer: str, days_ago: int, value: Any = "50", **over: Any) -> list[Any]:
    row = {
        "customer_ref": customer,
        "metric_code": "cmp_deposit_value",
        "campaign_code": None,
        "activity_date": (REAL_TODAY - timedelta(days=days_ago)).isoformat(),
        "activity_value": value,
        "currency_code": "GHS",
        "source_ref": None,
        "segment_code": "mass",
        "product_code": "savings",
        "region_code": "GA",
    } | over
    return [row[c] for c in OUTCOME]


def load(feed: str, template: str, header: list[str], rows: list[list[Any]]) -> Any:
    if not any(f.name == feed for f in run("feed.list").feeds):
        register_feed(feed, template)
    checked = dry(feed, header, rows)
    assert checked.passed, checked.findings
    return live(feed, header, rows)


def rules(result: Any) -> dict[str, str]:
    return {f.rule: f.severity for f in result.findings}


def test_an_outcome_load_is_attributed_in_the_same_load_and_a_reload_replaces_it() -> None:
    e = live_event()
    out = load(
        "outcomes", "campaign_outcome", OUTCOME, [outcome_row("C1", 25), outcome_row("C2", 5)]
    )
    assert out.state == "committed"
    assert CampaignOutcome.objects.count() == 2
    credited = CampaignAttribution.objects.filter(event=e, credited=True)
    assert credited.aggregate(s=Sum("attributed_value"))["s"] == Decimal("100")
    # A changed reload of the same days replaces the facts and their attribution.
    load(
        "outcomes", "campaign_outcome", OUTCOME, [outcome_row("C1", 25, "70"), outcome_row("C2", 5)]
    )
    assert CampaignOutcome.objects.count() == 2
    assert credited.aggregate(s=Sum("attributed_value"))["s"] == Decimal("120")


def test_outcome_gates() -> None:
    live_event()
    register_feed("outcomes", "campaign_outcome")
    found = dry(
        "outcomes",
        OUTCOME,
        [
            outcome_row("C1", 5, "-1"),
            outcome_row("C2", 5, campaign_code="NOPE"),
            outcome_row("C3", 5, segment_code="vip"),
            outcome_row("C4", 5, metric_code="nobody"),
        ],
    )
    assert not found.passed
    assert rules(found) == {
        "negative_outcome": "error",
        "unknown_campaign_tag": "warning",
        "unknown_customer_member": "warning",
        "unknown_metric": "error",
    }


POPULATION = ["snapshot_date", "segment_code", "product_code", "region_code", "customer_count"]


def test_population_sizes_an_audience_and_says_when_it_cannot() -> None:
    criteria = ["segment:retail"]
    nothing = run("campaign.audience.estimate", admin(), audience=criteria)
    assert nothing.targeted is None and nothing.reason == "no_population"
    day = (REAL_TODAY - timedelta(days=1)).isoformat()
    rows = [
        [day, "mass", "savings", "GA", 1000],
        [day, "affluent", "savings", "AS", 200],
        [day, "sme", "cards", "GA", 50],
    ]
    load("population", "campaign_population", POPULATION, rows)
    retail = run("campaign.audience.estimate", admin(), audience=criteria)
    assert (retail.targeted, retail.population, retail.as_of) == (
        1200,
        1250,
        REAL_TODAY - timedelta(days=1),
    )
    narrowed = run(
        "campaign.audience.estimate",
        admin(),
        audience=[*criteria, "region:GA"],
    )
    assert narrowed.targeted == 1000
    # The population carries no branch breakdown, so it cannot size a branch audience.
    run("dimension.define", dimension_type="branch", display_name="Branch")
    run(
        "dimension.member.upsert",
        dimension_type="branch",
        members=[{"member_code": "B1", "member_name": "One"}],
    )
    branch = run(
        "campaign.audience.estimate",
        admin(),
        audience=["branch:B1"],
    )
    assert branch.targeted is None and branch.reason == "dimension_not_in_population"
    assert branch.dimensions == ["branch"]
    assert CampaignPopulation.objects.count() == 3


def test_the_estimate_is_a_plain_get(make_user: Callable[..., Any]) -> None:
    client = Client()
    client.force_login(make_user("admin", username="ops"))
    found = client.get(
        "/api/v1/actions/campaign.audience.estimate", {"audience": ["segment:retail", "region:GA"]}
    )
    assert found.status_code == 200, found.content
    assert found.json()["reason"] == "no_population"
    bad = client.get("/api/v1/actions/campaign.audience.estimate", {"audience": ["retail"]})
    assert bad.status_code == 422


def test_population_counts_are_whole_and_never_negative() -> None:
    register_feed("population", "campaign_population")
    day = (REAL_TODAY - timedelta(days=1)).isoformat()
    found = dry(
        "population", POPULATION, [[day, "mass", None, None, "1.5"], [day, "sme", None, None, -2]]
    )
    assert rules(found) == {"bad_count": "error"}


CONTACT = ["customer_ref", "campaign_code", "channel", "contact_date", "delivered", "responded"]


def test_contacts_fill_the_funnel_and_reach_sits_beside_the_estimate() -> None:
    e = live_event()
    load(
        "population",
        "campaign_population",
        POPULATION,
        [[(START - timedelta(days=1)).isoformat(), "mass", "savings", "GA", 500]],
    )
    on = (START + timedelta(days=2)).isoformat()
    out = load(
        "contacts",
        "campaign_contact",
        CONTACT,
        [
            ["C1", "SAVE-Q4", "sms", on, "Y", "Y"],
            ["C1", "SAVE-Q4", "app_push", on, "Y", "N"],
            ["C2", "SAVE-Q4", "sms", on, "Y", "N"],
            ["C3", "SAVE-Q4", "sms", on, "N", None],
            # After the last contact day: kept out of the funnel, with a warning.
            ["C4", "SAVE-Q4", "sms", (START + timedelta(days=20)).isoformat(), "Y", "Y"],
        ],
    )
    assert rules(out) == {"contact_outside_event": "warning"}
    assert CampaignContact.objects.count() == 4
    load("outcomes", "campaign_outcome", OUTCOME, [outcome_row("C1", 25), outcome_row("C9", 25)])
    reach = run("campaign.reach", admin(), campaign_id=str(e.campaign_id))
    assert reach.attribution_rule == "last_touch"
    assert reach.outcome_metric_codes == ["cmp_deposit_value"]
    (er,) = reach.events
    assert er.estimate.targeted == 500
    assert (er.contacted, er.delivered, er.responded) == (3, 2, 1)
    assert (er.matched_customers, er.converted_customers, er.credited_outcomes) == (2, 2, 2)
    assert [(a.currency, a.amount) for a in er.attributed] == [("GHS", "100.0000")]


def test_contact_gates() -> None:
    live_event()
    register_feed("contacts", "campaign_contact")
    on = (START + timedelta(days=2)).isoformat()
    found = dry(
        "contacts",
        CONTACT,
        [
            ["C1", "NOPE", "sms", on, "Y", "Y"],
            ["C2", "SAVE-Q4", "pigeon", on, "Y", "Y"],
            ["C3", "SAVE-Q4", "sms", on, "maybe", None],
        ],
    )
    assert rules(found) == {
        "unknown_campaign": "error",
        "unknown_channel": "error",
        "not_a_flag": "error",
    }


def test_reach_is_absent_not_zero_before_anything_is_fed() -> None:
    out = campaign(events=[event(period_start="2026-09-01")])
    reach = run("campaign.reach", admin(), campaign_id=out.campaign_id)
    assert not reach.outcomes_fed and not reach.contacts_fed
    (er,) = reach.events
    assert er.estimate.reason == "no_population"
    assert er.contacted is None and er.matched_customers is None and er.converted_customers is None


def test_the_contract_carries_campaigns_for_the_standalone_validator() -> None:
    e = live_event()
    ref = build_reference(ORG_ID, v.TEMPLATES["campaign_contact"], [])
    back = v.reference_from_json(v.reference_to_json(ref))
    (window,) = back.campaigns["SAVE-Q4"]
    assert window.event_id == str(e.event_id) and window.period_start == START


def test_the_rule_is_one_of_four() -> None:
    with pytest.raises(InvalidInput):
        run("campaign.attribution.rule.set", admin(), rule="linear")
