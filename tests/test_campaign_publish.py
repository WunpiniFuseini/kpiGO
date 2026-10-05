"""Publishing a campaign result as a registry metric (PRD CM-19, Scope §9.5).

Publishing is explicit: a client picks a result and the products, and gets a
metric in the registry with a lineage link back to the campaign. Withdrawing it
takes the metric inactive and frees the result to be published again, reactivating
the same metric so its code stays stable.
"""

from __future__ import annotations

from typing import Any

import pytest

from kpigo.action import Conflict
from kpigo.campaigns.models import Campaign
from kpigo.metrics.models import Metric
from tests.campaign_support import admin, campaign, event
from tests.conftest import run
from tests.test_campaign_attribution import org  # noqa: F401  (the autouse world)

pytestmark = pytest.mark.django_db


def a_campaign(code: str = "SAVE-Q4") -> Campaign:
    out = campaign(code=code, name="Save more this quarter", events=[event()])
    return Campaign.objects.get(campaign_id=out.campaign_id)


def publish(c: Campaign, result_kind: str = "attributed_value", **over: Any) -> Any:
    payload = {
        "campaign_id": str(c.campaign_id),
        "result_kind": result_kind,
        "products": ["scorecards"],
        **over,
    }
    return run("campaign.metric.publish", admin(), **payload)


def listed(c: Campaign) -> Any:
    return run("campaign.metrics", admin(), campaign_id=str(c.campaign_id)).published


def test_publishing_a_result_registers_a_metric_bound_to_the_products_with_lineage() -> None:
    c = a_campaign()
    pub = publish(c)
    assert pub.result_kind == "attributed_value" and pub.status == "active"
    assert (pub.unit, pub.direction, pub.aggregation) == ("currency", "higher_is_better", "sum")
    assert pub.products == ["scorecards"] and pub.campaign_code == "SAVE-Q4"

    metric = Metric.objects.get(metric_code=pub.metric_code)
    assert metric.status == "active"
    assert sorted(b.product for b in metric.bindings.all()) == ["scorecards"]
    assert "SAVE-Q4" in metric.computation_note

    rows = listed(c)
    assert [r.published_id for r in rows] == [pub.published_id]
    assert rows[0].label == "Attributed campaign value"


def test_a_rate_result_publishes_as_an_averaged_percentage() -> None:
    c = a_campaign()
    pub = publish(c, result_kind="conversion_rate", products=["executive"])
    assert (pub.unit, pub.aggregation, pub.is_percentage) == ("percent", "average", True)
    assert Metric.objects.get(metric_code=pub.metric_code).is_percentage is True


def test_several_results_from_one_campaign_coexist() -> None:
    c = a_campaign()
    publish(c, result_kind="attributed_value")
    publish(c, result_kind="conversions")
    assert {r.result_kind for r in listed(c)} == {"attributed_value", "conversions"}


def test_a_result_cannot_be_published_twice_while_active() -> None:
    c = a_campaign()
    publish(c)
    with pytest.raises(Conflict):
        publish(c)


def test_withdrawing_frees_the_slot_and_reactivates_the_same_metric() -> None:
    c = a_campaign()
    pub = publish(c)
    code = pub.metric_code

    w = run("campaign.metric.withdraw", admin(), published_id=pub.published_id)
    assert w.status == "withdrawn" and w.withdrawn_at is not None
    assert Metric.objects.get(metric_code=code).status == "inactive"

    again = publish(c)
    # The same lineage row and metric come back, not a second one.
    assert again.published_id == pub.published_id and again.metric_code == code
    assert again.status == "active"
    assert Metric.objects.filter(metric_code=code).count() == 1
    assert Metric.objects.get(metric_code=code).status == "active"


def test_withdrawing_twice_is_refused() -> None:
    c = a_campaign()
    pub = publish(c)
    run("campaign.metric.withdraw", admin(), published_id=pub.published_id)
    with pytest.raises(Conflict):
        run("campaign.metric.withdraw", admin(), published_id=pub.published_id)


def test_a_campaign_with_nothing_published_lists_empty() -> None:
    c = a_campaign()
    assert listed(c) == []


def test_a_chosen_name_is_kept() -> None:
    c = a_campaign()
    pub = publish(c, display_name="RM campaign revenue")
    assert pub.display_name == "RM campaign revenue"
