"""The Sales pipeline: stages read from snapshot metrics in the daily feed (Scope §8.2)."""

from __future__ import annotations

from typing import Any

import pytest

from kpigo.action import InvalidInput, NotFound, PermissionDenied
from kpigo.action.identity import build_context
from tests.agent_support import PRODUCT, SINCE, day, load, targets, world
from tests.conftest import role_ctx, run

pytestmark = pytest.mark.django_db

SNAPSHOTS = {
    "pipeline_lead_value": ("Leads value", "currency"),
    "pipeline_lead_count": ("Leads", "count"),
    "pipeline_proposal_value": ("Proposals value", "currency"),
    "pipeline_proposal_count": ("Proposals", "count"),
    "pipeline_booked_value": ("Booked value", "currency"),
}


@pytest.fixture
def org() -> dict[str, str]:
    ids = world()
    targets()
    for code, (name, unit) in SNAPSHOTS.items():
        run(
            "metric.register",
            display_name=name,
            metric_code=code,
            direction="higher_is_better",
            aggregation="latest",
            unit=unit,
            target_scope="profile",
            products=[PRODUCT],
            effective_from=SINCE,
            acknowledge_similar=True,
        )
    load(
        [
            ["pipeline_lead_value", "A1", day(2), None, "1000", "GHS"],
            ["pipeline_lead_count", "A1", day(2), None, "10", None],
            ["pipeline_lead_value", "A1", day(5), None, "1200", "GHS"],
            ["pipeline_lead_count", "A1", day(5), None, "12", None],
            ["pipeline_proposal_value", "A1", day(5), None, "400", "GHS"],
            ["pipeline_proposal_count", "A1", day(5), None, "4", None],
            ["pipeline_lead_value", "A2", day(3), None, "500", "GHS"],
            ["pipeline_lead_count", "A2", day(3), None, "5", None],
        ]
    )
    return ids


def stages() -> None:
    run(
        "pipeline.stage.set",
        product=PRODUCT,
        code="lead",
        display_name="Lead",
        value_metric_code="pipeline_lead_value",
        count_metric_code="pipeline_lead_count",
    )
    run(
        "pipeline.stage.set",
        product=PRODUCT,
        code="proposal",
        display_name="Proposal",
        value_metric_code="pipeline_proposal_value",
        count_metric_code="pipeline_proposal_count",
    )
    run(
        "pipeline.stage.set",
        product=PRODUCT,
        code="booked",
        display_name="Booked",
        value_metric_code="pipeline_booked_value",
    )


def cells(row: Any) -> list[tuple[Any, ...]]:
    def s(x: Any) -> Any:
        return None if x is None else str(x)

    return [
        (s(c.value), s(c.count), s(c.value_change), s(c.count_change), c.reported, s(c.conversion))
        for c in row.cells
    ]


def pipeline(ctx: Any = None, **params: Any) -> Any:
    return run("agent.pipeline", ctx or role_ctx(), product=PRODUCT, as_of=day(17), **params)


def test_with_no_stages_the_pipeline_says_so(org: dict[str, str]) -> None:
    out = pipeline()
    assert out.stages == [] and out.rows == [] and out.total is None
    listed = run("pipeline.stage.list", product=PRODUCT)
    # Only snapshot metrics bound to the module can be stages.
    assert [m.metric_code for m in listed.candidates] == [
        "pipeline_booked_value",
        "pipeline_lead_count",
        "pipeline_lead_value",
        "pipeline_proposal_count",
        "pipeline_proposal_value",
    ]


def test_stages_sum_agents_latest_snapshots_and_convert(org: dict[str, str]) -> None:
    stages()
    out = pipeline()
    assert [s.code for s in out.stages] == ["lead", "proposal", "booked"]
    assert [(r.key, r.agents, r.drill_level) for r in out.rows] == [
        ("AS", 1, "branch"),
        ("GA", 2, "branch"),
    ]
    ga = out.rows[1]
    # Leads: A1's latest (1200, 12) plus A2's (500, 5); A1 grew 200 and 2 since the 2nd.
    # Proposals: only A1, 4 of the 17 leads. Booked: nobody yet (absent, not zero).
    assert cells(ga) == [
        ("1700.0000", "17.0000", "200.0000", "2.0000", 2, None),
        ("400.0000", "4.0000", "0.0000", "0.0000", 1, "0.2353"),
        (None, None, None, None, 0, None),
    ]
    assert ga.cells[0].currency_code == "GHS"
    # Ashanti's one agent has nothing in the pipeline.
    assert cells(out.rows[0])[0] == (None, None, None, None, 0, None)
    assert out.total is not None and cells(out.total)[0][0] == "1700.0000"


def test_the_pipeline_drills_to_rms(org: dict[str, str]) -> None:
    stages()
    rms = pipeline(level="rm", branch_code="GA-01")
    assert [(r.name, r.drill_level) for r in rms.rows] == [("Agent A1", None), ("Agent A2", None)]
    assert [c.level for c in rms.breadcrumb] == ["all", "region", "branch"]
    with pytest.raises(NotFound):
        pipeline(level="branch", region_code="NOWHERE")
    with pytest.raises(InvalidInput):
        pipeline(level="rm")


def test_the_pipeline_respects_who_sees_whom(org: dict[str, str], make_user: Any) -> None:
    stages()
    run(
        "agent.visibility.set",
        product=PRODUCT,
        applies_to="role",
        applies_code="staff",
        scope="self",
    )
    a2 = build_context(make_user("staff", email="a2@bank.example"), caller="cli")
    out = pipeline(a2)
    assert out.visibility.restricted
    assert out.total is not None and cells(out.total)[0][0] == "500.0000"


def test_stages_only_read_snapshot_metrics_and_admins_keep_them(org: dict[str, str]) -> None:
    with pytest.raises(InvalidInput):
        run(
            "pipeline.stage.set",
            product=PRODUCT,
            code="lead",
            display_name="Lead",
            value_metric_code="value_booked",  # a sum, not a snapshot
        )
    with pytest.raises(InvalidInput):
        run(
            "pipeline.stage.set",
            product=PRODUCT,
            code="lead",
            display_name="Lead",
            count_metric_code="pipeline_lead_value",  # not a count
        )
    with pytest.raises(InvalidInput):
        run("pipeline.stage.set", product=PRODUCT, code="lead", display_name="Lead")
    stages()
    # Changing a stage keeps its place; removing one leaves the others in order.
    run(
        "pipeline.stage.set",
        product=PRODUCT,
        code="lead",
        display_name="Leads",
        value_metric_code="pipeline_lead_value",
    )
    run("pipeline.stage.remove", product=PRODUCT, code="proposal")
    listed = run("pipeline.stage.list", product=PRODUCT).stages
    assert [(s.code, s.display_name, s.count_metric_code) for s in listed] == [
        ("lead", "Leads", None),
        ("booked", "Booked", None),
    ]
    with pytest.raises(NotFound):
        run("pipeline.stage.remove", product=PRODUCT, code="proposal")
    with pytest.raises(PermissionDenied):
        run(
            "pipeline.stage.set",
            role_ctx("agent_supervisor"),
            product=PRODUCT,
            code="x",
            display_name="X",
            value_metric_code="pipeline_lead_value",
        )
