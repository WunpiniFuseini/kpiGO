"""Executive widget definitions (PRD EX-5–EX-10, Scope §10.4, App Flow §6).

An Admin places a metric on the dashboard and picks its type from the bundle;
combinations a type cannot draw are refused at placement, never at draw time;
every change is a new version of a declarative definition; a threshold override
is recorded on the widget and can go through maker-checker.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User

from kpigo.action import Conflict, InvalidInput, NotFound, Proposal, invoke, registry
from kpigo.action.identity import build_context
from kpigo.executive.models import WidgetDefinition
from kpigo.platform.models import AuditLog
from tests.campaign_support import admin as campaign_admin
from tests.campaign_support import campaign
from tests.conftest import role_ctx, run
from tests.executive_support import place, world

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def org() -> None:
    world()


def problems(exc: pytest.ExceptionInfo[InvalidInput]) -> str:
    return " ".join(exc.value.detail["problems"])


def test_placing_a_widget_versions_its_definition_and_shows_it_to_executives() -> None:
    out = place()
    assert (out.widget_key, out.version, out.state, out.change) == (
        "revenue",
        1,
        "placed",
        "placed",
    )
    assert out.title == "Total revenue"  # one metric: its name unless given another
    assert [(m.metric_code, m.source) for m in out.metrics] == [("ex_revenue", "independent")]
    assert out.series == ["actual", "target"] and out.thresholds.source == "metric"
    assert out.layout is not None and (out.layout.x, out.layout.y, out.layout.w) == (0, 0, 3)

    second = place(widget_key="cti", widget_type="gauge", metrics=[{"metric_code": "ex_cti"}])
    assert second.layout is not None and second.layout.y == 2  # under the first

    board = run("widget.dashboard", role_ctx("executive"))
    assert [w.widget_key for w in board.widgets] == ["revenue", "cti"]
    assert {t.type for t in board.types} == {
        "kpi_card",
        "bullet",
        "gauge",
        "bar",
        "line",
        "pie",
        "ranked_list",
        "table",
        "funnel",
    }
    assert AuditLog.objects.filter(event="widget.placed").count() == 2


def test_source_follows_the_metric_bindings_unless_chosen() -> None:
    rolled = place(
        widget_key="casa",
        widget_type="bar",
        metrics=[{"metric_code": "ex_casa"}],
        dimension="region",
    )
    assert rolled.metrics[0].source == "rollup"
    fed = place(
        widget_key="casa_fed",
        widget_type="kpi_card",
        metrics=[{"metric_code": "ex_casa", "source": "independent"}],
    )
    assert fed.metrics[0].source == "independent"
    with pytest.raises(InvalidInput) as exc:
        place(widget_key="rev_rollup", metrics=[{"metric_code": "ex_revenue", "source": "rollup"}])
    assert "nothing to roll up" in problems(exc)


def test_a_published_campaign_result_is_drawn_from_its_campaign() -> None:
    from kpigo.campaigns.models import Campaign

    run("dimension.define", dimension_type="segment", display_name="Segment")
    run(
        "dimension.member.upsert",
        dimension_type="segment",
        members=[{"member_code": "retail", "member_name": "Retail"}],
    )
    run("dimension.define", dimension_type="product", display_name="Product")
    run(
        "dimension.member.upsert",
        dimension_type="product",
        members=[{"member_code": "savings", "member_name": "Savings"}],
    )
    created = campaign()
    c = Campaign.objects.get(campaign_id=created.campaign_id)
    pub = run(
        "campaign.metric.publish",
        campaign_admin(),
        campaign_id=str(c.campaign_id),
        result_kind="incremental_value",
        products=["executive"],
    )
    out = place(widget_key="campaigns", metrics=[{"metric_code": pub.metric_code}])
    assert out.metrics[0].source == "campaign"
    with pytest.raises(InvalidInput) as exc:
        place(
            widget_key="campaigns_by_channel",
            widget_type="ranked_list",
            metrics=[{"metric_code": pub.metric_code}],
            dimension="channel",
        )
    assert "describe customers by" in problems(exc)


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        (
            {
                "widget_type": "gauge",
                "metrics": [{"metric_code": "ex_cti"}, {"metric_code": "ex_revenue"}],
            },
            "exactly 1 metric",
        ),
        ({"widget_type": "kpi_card", "dimension": "region"}, "takes no dimension"),
        ({"widget_type": "ranked_list"}, "needs a dimension"),
        ({"widget_type": "table", "metrics": [{"metric_code": "ex_revenue"}]}, "needs a dimension"),
        (
            {"widget_type": "pie", "metrics": [{"metric_code": "ex_cti"}], "dimension": "region"},
            "sum or a count",
        ),
        (
            {
                "widget_type": "line",
                "metrics": [{"metric_code": "ex_revenue"}, {"metric_code": "ex_cti"}],
            },
            "share a unit",
        ),
        ({"widget_type": "funnel"}, "2 to 8 metrics"),
        ({"widget_type": "bullet", "series": ["actual"]}, "needs a comparison"),
        (
            {
                "widget_type": "bar",
                "metrics": [{"metric_code": "ex_revenue"}, {"metric_code": "ex_revenue"}],
            },
            "more than once",
        ),
    ],
)
def test_combinations_a_type_cannot_draw_are_refused_at_placement(
    over: dict[str, Any], reason: str
) -> None:
    with pytest.raises(InvalidInput) as exc:
        place(**over)
    assert reason in problems(exc)
    assert not WidgetDefinition.objects.exists()


def test_composition_types_draw_the_actual_only() -> None:
    out = place(
        widget_key="mix",
        widget_type="pie",
        metrics=[{"metric_code": "ex_accounts"}],
        dimension="channel",
        series=["actual", "target", "forecast"],
    )
    assert out.series == ["actual"]
    funnel = place(
        widget_key="funnel",
        widget_type="funnel",
        metrics=[{"metric_code": "ex_leads"}, {"metric_code": "ex_accounts"}],
        title="Pipeline",
    )
    assert funnel.title == "Pipeline"


@pytest.mark.parametrize(
    ("metrics", "dimension", "reason"),
    [
        ([{"metric_code": "sc_only"}], None, "not bound to Executive"),
        ([{"metric_code": "ex_draft"}], None, "draft, not active"),
        ([{"metric_code": "nope"}], None, "No metric 'nope'"),
        ([{"metric_code": "ex_casa"}], "channel", "not 'channel'"),
    ],
)
def test_metrics_must_be_active_executive_metrics_fit_for_their_source(
    metrics: list[dict[str, str]], dimension: str | None, reason: str
) -> None:
    with pytest.raises(InvalidInput) as exc:
        place(widget_type="bar", metrics=metrics, dimension=dimension or "region")
    assert reason in problems(exc)


def test_an_unknown_dimension_is_refused() -> None:
    with pytest.raises(InvalidInput) as exc:
        place(widget_type="ranked_list", dimension="galaxy")
    assert "No dimension 'galaxy'" in problems(exc)


def test_several_metrics_need_a_title() -> None:
    with pytest.raises(InvalidInput, match="title"):
        place(
            widget_type="bar",
            metrics=[{"metric_code": "ex_revenue"}, {"metric_code": "ex_casa"}],
        )


def test_changing_the_type_is_global_and_versioned() -> None:
    place()
    changed = run("widget.update", widget_key="revenue", widget_type="line", expected_version=1)
    assert (changed.version, changed.change, changed.widget_type) == (2, "type_changed", "line")
    assert run("widget.dashboard", role_ctx("executive")).widgets[0].widget_type == "line"

    moved = run("widget.update", widget_key="revenue", layout={"x": 6, "y": 0, "w": 6, "h": 4})
    assert (moved.version, moved.change) == (3, "updated")
    same = run("widget.update", widget_key="revenue", title="Total revenue")
    assert same.version == 3  # nothing changed, nothing written

    with pytest.raises(Conflict, match="changed since"):
        run("widget.update", widget_key="revenue", widget_type="kpi_card", expected_version=1)
    with pytest.raises(InvalidInput):
        run(
            "widget.update",
            widget_key="revenue",
            widget_type="gauge",
            metrics=[{"metric_code": "ex_revenue"}, {"metric_code": "ex_casa"}],
        )

    history = run("widget.history", widget_key="revenue").versions
    assert [(h.version, h.change) for h in history] == [
        (3, "updated"),
        (2, "type_changed"),
        (1, "placed"),
    ]
    assert WidgetDefinition.objects.filter(widget_key="revenue", is_current=True).count() == 1


def test_a_breakdown_can_be_added_and_cleared() -> None:
    place(
        widget_key="casa",
        widget_type="bar",
        metrics=[{"metric_code": "ex_casa"}, {"metric_code": "ex_revenue"}],
        dimension="region",
        title="CASA and revenue",
    )
    cleared = run("widget.update", widget_key="casa", clear_dimension=True)
    assert cleared.dimension is None
    with pytest.raises(InvalidInput, match="needs a dimension"):
        run("widget.update", widget_key="casa", metrics=[{"metric_code": "ex_casa"}])


def test_threshold_overrides_are_recorded_on_the_widget_and_audited() -> None:
    place(widget_key="cti", widget_type="gauge", metrics=[{"metric_code": "ex_cti"}])
    override = {
        "source": "override",
        "basis": "value",
        "bands": [
            {"label": "Within tolerance", "threshold": "0"},
            {"label": "Above tolerance", "threshold": "0.035"},
        ],
        "note": "Board tolerance is 3.5%.",
    }
    out = run("widget.thresholds.set", widget_key="cti", thresholds=override)
    assert (out.version, out.change, out.thresholds.source) == (2, "thresholds_changed", "override")
    assert [b.label for b in out.thresholds.bands] == ["Within tolerance", "Above tolerance"]
    assert out.thresholds.note == "Board tolerance is 3.5%."
    entry = AuditLog.objects.get(event="widget.thresholds_changed.detail")
    assert entry.payload["before"] == {"source": "metric", "basis": None, "bands": [], "note": ""}

    back = run("widget.thresholds.set", widget_key="cti", thresholds={"source": "metric"})
    assert (back.version, back.thresholds.source) == (3, "metric")

    place(widget_key="trend", widget_type="line", metrics=[{"metric_code": "ex_revenue"}])
    with pytest.raises(InvalidInput, match="no threshold bands"):
        run("widget.thresholds.set", widget_key="trend", thresholds=override)
    # A type change that would strand an override is refused rather than dropping it.
    run("widget.thresholds.set", widget_key="cti", thresholds=override)
    with pytest.raises(InvalidInput, match="takes no override"):
        run("widget.update", widget_key="cti", widget_type="line")


@pytest.mark.parametrize(
    "thresholds",
    [
        {"source": "override", "basis": "value", "bands": [{"label": "Only", "threshold": "0"}]},
        {
            "source": "override",
            "bands": [{"label": "A", "threshold": "0"}, {"label": "B", "threshold": "1"}],
        },
        {
            "source": "override",
            "basis": "achievement",
            "bands": [{"label": "A", "threshold": "1"}, {"label": "B", "threshold": "0.5"}],
        },
        {"source": "metric", "bands": [{"label": "A", "threshold": "0"}]},
    ],
)
def test_malformed_overrides_are_refused(thresholds: dict[str, Any]) -> None:
    place(widget_key="cti", widget_type="gauge", metrics=[{"metric_code": "ex_cti"}])
    with pytest.raises(InvalidInput, match="Invalid input"):
        run("widget.thresholds.set", widget_key="cti", thresholds=thresholds)
    assert WidgetDefinition.objects.get(widget_key="cti", is_current=True).version == 1


def test_a_threshold_override_waits_for_a_checker_when_widget_change_is_on(
    make_user: Any,
) -> None:
    maker: User = make_user("admin")
    checker: User = make_user("admin")

    def as_user(user: User, name: str, /, **payload: Any) -> Any:
        return invoke(registry.get(name), payload, build_context(user, caller="http"))

    as_user(maker, "approval.policy.set", approval_class="widget_change", enabled=True)
    as_user(
        maker,
        "widget.place",
        widget_key="cti",
        widget_type="gauge",
        metrics=[{"metric_code": "ex_cti"}],
    )  # placing is not gated
    proposal = as_user(
        maker,
        "widget.thresholds.set",
        widget_key="cti",
        thresholds={
            "source": "override",
            "basis": "achievement",
            "bands": [{"label": "Off", "threshold": "0"}, {"label": "On", "threshold": "1"}],
        },
    )
    assert isinstance(proposal, Proposal)
    assert WidgetDefinition.objects.get(widget_key="cti", is_current=True).version == 1
    as_user(
        checker, "platform.approval.approve", approval_request_id=str(proposal.approval_request_id)
    )
    current = WidgetDefinition.objects.get(widget_key="cti", is_current=True)
    assert current.version == 2 and current.approval_request_id is not None


def test_removing_keeps_history_and_the_widget_can_be_placed_again() -> None:
    place()
    removed = run("widget.remove", widget_key="revenue")
    assert (removed.state, removed.version) == ("removed", 2)
    assert run("widget.dashboard", role_ctx("executive")).widgets == []
    assert run("widget.list").widgets == []
    assert [w.state for w in run("widget.list", include_removed=True).widgets] == ["removed"]
    with pytest.raises(Conflict):
        run("widget.remove", widget_key="revenue")

    again = place(widget_type="gauge", metrics=[{"metric_code": "ex_cti"}])
    assert (again.version, again.state, again.change) == (3, "placed", "placed")
    with pytest.raises(Conflict, match="already on the dashboard"):
        place()
    with pytest.raises(NotFound):
        run("widget.history", widget_key="nothing_here")


def test_only_an_admin_configures_widgets() -> None:
    from kpigo.action import PermissionDenied

    place()
    for role in ("executive", "campaign_manager", "data_steward"):
        with pytest.raises(PermissionDenied):
            run("widget.update", role_ctx(role), widget_key="revenue", widget_type="line")
    with pytest.raises(PermissionDenied):
        run("widget.dashboard", role_ctx("staff"))
