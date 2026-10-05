"""Campaign authoring and management (PRD CM-1–CM-6; App Flow §5.1, §5.2)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from kpigo.action import Conflict, InvalidInput, NotFound, PermissionDenied, invoke, registry
from kpigo.action.context import DataScopeGrant
from kpigo.action.identity import build_context
from kpigo.action.pipeline import Proposal
from kpigo.campaigns import authoring
from kpigo.campaigns.actions.events import shifted
from kpigo.campaigns.models import CampaignEvent
from kpigo.platform.models import AuditLog
from tests.campaign_support import admin, campaign, event, world
from tests.conftest import role_ctx, run

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 10)


@pytest.fixture(autouse=True)
def org(monkeypatch: pytest.MonkeyPatch) -> None:
    world()
    monkeypatch.setattr(authoring, "today", lambda: TODAY)


def publish(out: Any, n: int = 0) -> Any:
    return run("campaign.event.publish", admin(), event_id=out.events[n].event_id)


def as_user(user: Any, action_name: str, /, **payload: Any) -> Any:
    return invoke(registry.get(action_name), payload, build_context(user, caller="http"))


# ── create ───────────────────────────────────────────────────────────────────


def test_create_authors_a_campaign_with_draft_events() -> None:
    out = campaign()
    assert out.status == "draft"
    assert out.objective == "deposit_growth"
    (e,) = out.events
    assert e.state == "draft" and e.status == "draft"
    # The window comes from the objective: deposit growth runs 30 days.
    assert e.attribution_window_days == 30
    assert e.window_end == date(2026, 11, 30)
    assert e.budget_amount == "25000.00"
    assert [(c.dimension_type, c.member_code) for c in e.audience] == [("segment", "retail")]
    assert out.budget_needs_approval is True


def test_create_refuses_unknown_members_currencies_and_reused_codes() -> None:
    with pytest.raises(InvalidInput, match="segment=nobody"):
        campaign(events=[event(audience=[{"dimension_type": "segment", "member_code": "nobody"}])])
    with pytest.raises(InvalidInput, match="No dimension called channel_mix"):
        campaign(events=[event(audience=[{"dimension_type": "channel_mix", "member_code": "x"}])])
    with pytest.raises(InvalidInput, match="not an active currency"):
        campaign(events=[event(budget_currency="USD")])
    with pytest.raises(InvalidInput, match="product dimension"):
        campaign(product_code="mortgages")
    campaign()
    with pytest.raises(Conflict, match="already uses that code"):
        campaign()


def test_objective_windows_and_outcome_metrics_are_configurable() -> None:
    run(
        "metric.register",
        display_name="Campaign deposits",
        metric_code="cmp_deposit_value",
        direction="higher_is_better",
        aggregation="sum",
        unit="currency",
        products=["campaign"],
        effective_from="2024-01-01",
    )
    listed = run("campaign.objective.list", admin())
    defaults = {o.objective: o.default_window_days for o in listed.objectives}
    assert defaults["acquisition"] == 45 and defaults["attrition_winback"] == 90
    assert [c.metric_code for c in listed.candidates] == ["cmp_deposit_value"]
    with pytest.raises(InvalidInput, match="campaign product"):
        run(
            "campaign.objective.set",
            admin(),
            objective="deposit_growth",
            default_window_days=21,
            outcome_metric_codes=["nope"],
        )
    out = run(
        "campaign.objective.set",
        admin(),
        objective="deposit_growth",
        default_window_days=21,
        outcome_metric_codes=["cmp_deposit_value"],
    )
    row = next(o for o in out.objectives if o.objective == "deposit_growth")
    assert row.configured and row.outcome_metric_codes == ["cmp_deposit_value"]
    assert campaign().events[0].attribution_window_days == 21


# ── lifecycle ────────────────────────────────────────────────────────────────


def test_publish_needs_an_audience_then_status_follows_the_dates() -> None:
    out = campaign(
        events=[
            event(audience=[]),
            event(event_name="Nov", period_start="2026-11-01", period_end="2026-11-30"),
        ]
    )
    with pytest.raises(InvalidInput, match="audience"):
        publish(out, 0)
    out = publish(out, 1)
    assert out.events[1].status == "scheduled"
    assert out.status == "scheduled"
    run(
        "campaign.event.update",
        admin(),
        event_id=out.events[0].event_id,
        audience=[{"dimension_type": "region", "member_code": "GA"}],
    )
    out = publish(out, 0)
    assert out.events[0].status == "running" and out.status == "running"
    # Running lasts until the attribution window closes, not the last contact day.
    october = CampaignEvent.objects.get(event_id=out.events[0].event_id)
    assert authoring.event_status(october, date(2026, 11, 30)) == "running"
    assert authoring.event_status(october, date(2026, 12, 1)) == "closed"


def test_pause_resume_close() -> None:
    out = publish(campaign())
    eid = out.events[0].event_id
    out = run("campaign.event.status", admin(), event_id=eid, to="pause")
    assert out.events[0].status == "paused" and out.status == "paused"
    with pytest.raises(Conflict, match="paused cannot pause"):
        run("campaign.event.status", admin(), event_id=eid, to="pause")
    out = run("campaign.event.status", admin(), event_id=eid, to="resume")
    assert out.events[0].status == "running"
    out = run("campaign.event.status", admin(), event_id=eid, to="close")
    assert out.events[0].status == "closed" and out.status == "closed"
    with pytest.raises(Conflict, match="closed"):
        run("campaign.event.update", admin(), event_id=eid, event_name="Again")
    history = run("campaign.event.history", admin(), event_id=eid)
    assert [v.change for v in history.versions] == ["published", "paused", "resumed", "closed"]


def test_close_campaign_closes_every_event() -> None:
    out = campaign(
        events=[
            event(),
            event(event_name="Nov", period_start="2026-11-01", period_end="2026-11-30"),
        ]
    )
    publish(out)
    out = run("campaign.close", admin(), campaign_id=out.campaign_id)
    assert out.status == "closed"
    assert {e.state for e in out.events} == {"closed"}
    with pytest.raises(Conflict, match="closed"):
        run("campaign.event.add", admin(), campaign_id=out.campaign_id, **event())


def test_only_drafts_are_deleted() -> None:
    out = campaign(events=[event(), event(event_name="Spare")])
    out = run("campaign.event.remove", admin(), event_id=out.events[1].event_id)
    assert [e.event_name for e in out.events] == ["October wave"]
    publish(out)
    with pytest.raises(Conflict, match="Only a draft"):
        run("campaign.event.remove", admin(), event_id=out.events[0].event_id)


# ── managing a live event ────────────────────────────────────────────────────


def test_a_draft_budget_edits_freely_a_live_one_waits_for_approval(make_user: Any) -> None:
    maker = make_user("campaign_manager")
    checker = make_user("admin")
    out = campaign(owner_user_id=maker.pk)
    eid = out.events[0].event_id
    out = as_user(maker, "campaign.event.update", event_id=eid, budget_amount="30000")
    assert out.events[0].budget_amount == "30000.00"
    as_user(maker, "campaign.event.publish", event_id=eid)
    with pytest.raises(Conflict, match="budget change"):
        as_user(maker, "campaign.event.update", event_id=eid, budget_amount="1")

    proposal = as_user(maker, "campaign.event.budget.set", event_id=eid, budget_amount="45000")
    assert isinstance(proposal, Proposal)
    shown = as_user(maker, "campaign.get", campaign_id=out.campaign_id).events[0]
    assert shown.budget_amount == "30000.00"
    assert shown.pending_budget is not None and shown.pending_budget.budget_amount == "45000"

    with pytest.raises(PermissionDenied):
        as_user(
            maker,
            "platform.approval.approve",
            approval_request_id=proposal.approval_request_id,
        )
    as_user(checker, "platform.approval.approve", approval_request_id=proposal.approval_request_id)
    shown = as_user(maker, "campaign.get", campaign_id=out.campaign_id).events[0]
    assert shown.budget_amount == "45000.00" and shown.pending_budget is None
    history = as_user(maker, "campaign.event.history", event_id=eid)
    last = history.versions[-1]
    assert last.change == "budget" and last.approval_request_id == proposal.approval_request_id
    assert last.snapshot["budget_amount"] == "45000.00"
    assert AuditLog.objects.filter(event="campaign.budget.change_detail").exists()


def test_a_client_can_switch_budget_approval_off() -> None:
    out = publish(campaign())
    run("approval.policy.set", admin(), approval_class="budget", enabled=False)
    policies = {p.approval_class: p.enabled for p in run("approval.policy.list").policies}
    assert policies["budget"] is False
    out = run(
        "campaign.event.budget.set", admin(), event_id=out.events[0].event_id, budget_amount="1"
    )
    assert out.events[0].budget_amount == "1.00"
    assert out.budget_needs_approval is False


def test_changing_dates_or_audience_reopens_attribution_and_versions() -> None:
    out = publish(campaign())
    eid = out.events[0].event_id
    assert out.events[0].reattribute_from is None
    with pytest.raises(Conflict, match="has started"):
        run("campaign.event.update", admin(), event_id=eid, period_start="2026-10-05")
    out = run("campaign.event.update", admin(), event_id=eid, period_end="2026-11-15")
    e = out.events[0]
    assert e.window_end == date(2026, 12, 15)
    assert e.reattribute_from == date(2026, 10, 1)
    out = run(
        "campaign.event.update",
        admin(),
        event_id=eid,
        audience=[
            {"dimension_type": "segment", "member_code": "retail"},
            {"dimension_type": "region", "member_code": "GA"},
        ],
    )
    with pytest.raises(InvalidInput, match="at least one"):
        run("campaign.event.update", admin(), event_id=eid, audience=[])
    history = run("campaign.event.history", admin(), event_id=eid)
    assert [v.change for v in history.versions] == ["published", "updated", "audience"]
    assert len(history.versions[-1].snapshot["audience"]) == 2
    assert out.events[0].version == 3


def test_objective_is_fixed_once_an_event_is_published() -> None:
    out = campaign()
    run("campaign.update", admin(), campaign_id=out.campaign_id, objective="cross_sell")
    publish(out)
    with pytest.raises(Conflict, match="objective"):
        run("campaign.update", admin(), campaign_id=out.campaign_id, objective="activation")
    out = run("campaign.update", admin(), campaign_id=out.campaign_id, priority=2, name="Renamed")
    assert out.priority == 2 and out.name == "Renamed"


# ── recurrence ───────────────────────────────────────────────────────────────


def test_repeat_copies_an_event_on_following_months() -> None:
    out = campaign()
    out = run("campaign.event.repeat", admin(), event_id=out.events[0].event_id, count=3)
    runs = [(e.sequence_no, e.period_start, e.period_end, e.state) for e in out.events]
    assert runs == [
        (1, date(2026, 10, 1), date(2026, 10, 31), "draft"),
        (2, date(2026, 11, 1), date(2026, 11, 30), "draft"),
        (3, date(2026, 12, 1), date(2026, 12, 31), "draft"),
        (4, date(2027, 1, 1), date(2027, 1, 31), "draft"),
    ]
    assert out.events[1].event_name == "October wave (Nov 2026)"
    assert all(len(e.audience) == 1 for e in out.events)


def test_shifted_keeps_whole_months_and_clamps_days() -> None:
    assert shifted(date(2026, 1, 31), date(2026, 1, 31), "month", 1) == (
        date(2026, 2, 28),
        date(2026, 2, 28),
    )
    assert shifted(date(2026, 1, 15), date(2026, 2, 14), "quarter", 1) == (
        date(2026, 4, 15),
        date(2026, 5, 14),
    )
    assert shifted(date(2026, 1, 1), date(2026, 1, 7), "week", 2) == (
        date(2026, 1, 15),
        date(2026, 1, 21),
    )


# ── scope ────────────────────────────────────────────────────────────────────


def scoped(dimension: str, member: str) -> Any:
    return role_ctx(
        "campaign_manager",
        data_scopes=(
            DataScopeGrant(module="campaign", dimension_type=dimension, member_code=member),
        ),
    )


def test_no_grant_means_no_campaigns() -> None:
    out = campaign()
    nobody = role_ctx("campaign_manager")
    listed = run("campaign.list", nobody)
    assert listed.campaigns == [] and listed.scoped is False
    with pytest.raises(NotFound):
        run("campaign.get", nobody, campaign_id=out.campaign_id)
    with pytest.raises(NotFound):
        run("campaign.event.publish", nobody, event_id=out.events[0].event_id)


def test_grants_match_by_code_product_and_audience_below_a_member() -> None:
    campaign()
    campaign(
        code="CARDS",
        product_code="cards",
        events=[event(audience=[{"dimension_type": "segment", "member_code": "affluent"}])],
    )
    campaign(
        code="SME",
        product_code="cards",
        events=[event(audience=[{"dimension_type": "segment", "member_code": "sme"}])],
    )

    def codes(ctx: Any) -> list[str]:
        return [c.code for c in run("campaign.list", ctx).campaigns]

    assert codes(scoped("campaign", "SME")) == ["SME"]
    assert codes(scoped("product", "cards")) == ["CARDS", "SME"]
    # A grant on retail reaches campaigns aimed at affluent, which sits below it.
    assert codes(scoped("segment", "retail")) == ["CARDS", "SAVE-Q4"]
    assert codes(scoped("segment", "affluent")) == ["CARDS"]
    assert codes(scoped("region", "*")) == ["CARDS", "SAVE-Q4", "SME"]


def test_an_owner_sees_their_campaign_and_cannot_move_it_out_of_reach(make_user: Any) -> None:
    owner = make_user("campaign_manager")
    out = as_user(
        owner,
        "campaign.create",
        code="MINE",
        name="Mine",
        campaign_type="pilot",
        objective="activation",
        events=[event()],
    )
    assert [c.code for c in as_user(owner, "campaign.list").campaigns] == ["MINE"]
    other = make_user("campaign_manager")
    with pytest.raises(InvalidInput, match="outside your campaign scope"):
        as_user(owner, "campaign.update", campaign_id=out.campaign_id, owner_user_id=other.pk)


def test_list_tabs() -> None:
    campaign(code="DRAFT")
    publish(campaign(code="LIVE"))
    publish(
        campaign(
            code="LATER",
            events=[event(period_start="2026-12-01", period_end="2026-12-31")],
        )
    )

    def tab(name: str) -> list[str]:
        return [c.code for c in run("campaign.list", admin(), tab=name).campaigns]

    assert tab("running") == ["LIVE"]
    assert tab("scheduled") == ["LATER"]
    assert tab("draft") == ["DRAFT"]
    listed = run("campaign.list", admin(), tab="running").campaigns[0]
    assert listed.current_event is not None and listed.current_event.status == "running"
    assert [(b.currency, b.amount) for b in listed.budgets] == [("GHS", "25000.00")]


# ── the builder ──────────────────────────────────────────────────────────────


def test_builder_reference_offers_what_the_form_needs(make_user: Any) -> None:
    out = run("campaign.builder.reference", admin())
    assert out.currencies == ["GHS"] and out.default_currency == "GHS"
    segment = next(d for d in out.dimensions if d.dimension_type == "segment")
    assert [(m.member_code, m.parent_code) for m in segment.members][:2] == [
        ("affluent", "retail"),
        ("mass", "retail"),
    ]
    windows = {o.objective: o.default_window_days for o in out.objectives}
    assert windows["cross_sell"] == 60
    assert "whatsapp" in out.channels and out.budget_needs_approval is True


def test_outputs_name_the_owner(make_user: Any) -> None:
    owner = make_user("campaign_manager", username="kofi")
    campaign(owner_user_id=owner.pk)
    assert run("campaign.list", admin()).campaigns[0].owner_name == "kofi"
