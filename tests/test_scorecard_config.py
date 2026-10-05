"""Scorecard taxonomy, profiles' metric sets, rating bands and settings (PRD SC-1, SC-4)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kpigo.action import Conflict, InvalidInput, NotFound
from kpigo.metrics.models import MetricProfileAssignment
from kpigo.scorecards.bands import DEFAULT_BANDS, bands_for, lookup, next_band
from kpigo.scorecards.cycles import Cycle, period_range, shift
from kpigo.scorecards.taxonomy import Placer
from tests.conftest import ORG_ID, run
from tests.scorecard_support import NEXT, PROFILE, SINCE, world

pytestmark = pytest.mark.django_db


@pytest.fixture
def ids() -> dict[str, Any]:
    return world()


def build_template() -> Any:
    t = run("scorecard.template.save", name="Bank scorecard", levels=["Objective", "Driver"])
    tid = str(t.template_id)
    t = run("scorecard.node.save", template_id=tid, label="Grow the balance sheet")
    grow = t.nodes[0].node_id
    t = run("scorecard.node.save", template_id=tid, parent_id=str(grow), label="Deposits")
    t = run("scorecard.node.save", template_id=tid, label="Serve reliably", sort_order=2)
    serve = next(n for n in t.nodes if n.label == "Serve reliably").node_id
    t = run("scorecard.node.save", template_id=tid, parent_id=str(serve), label="Speed")
    return t


def node(t: Any, label: str) -> str:
    return str(next(n for n in t.nodes if n.label == label).node_id)


def test_a_template_has_one_to_three_named_levels(ids: dict[str, Any]) -> None:
    t = build_template()
    assert [lv.label for lv in t.levels] == ["Objective", "Driver"]
    assert t.level_count == 2 and t.status == "draft"
    with pytest.raises(InvalidInput):
        run("scorecard.template.save", name="Too deep", levels=["a", "b", "c", "d"])
    # Metrics sit under the deepest level, not more nodes.
    with pytest.raises(InvalidInput, match="deepest"):
        run(
            "scorecard.node.save",
            template_id=str(t.template_id),
            parent_id=node(t, "Deposits"),
            label="Too deep",
        )


def test_placements_and_activation(ids: dict[str, Any]) -> None:
    t = build_template()
    tid = str(t.template_id)
    with pytest.raises(Conflict, match="deepest level"):
        empty = run("scorecard.template.save", name="Empty", levels=["Goal"])
        run("scorecard.template.activate", template_id=str(empty.template_id))
    with pytest.raises(InvalidInput, match="deepest level"):
        run(
            "scorecard.placement.set",
            template_id=tid,
            node_id=node(t, "Grow the balance sheet"),
            metric_code="casa_growth",
        )
    with pytest.raises(NotFound):
        run(
            "scorecard.placement.set",
            template_id=tid,
            node_id=node(t, "Deposits"),
            metric_code="nope",
        )
    run(
        "scorecard.placement.set",
        template_id=tid,
        node_id=node(t, "Deposits"),
        metric_code="casa_growth",
    )
    run(
        "scorecard.placement.set",
        template_id=tid,
        node_id=node(t, "Speed"),
        metric_code="service_tat",
    )
    # A profile may place a metric differently from the default.
    run(
        "scorecard.placement.set",
        template_id=tid,
        node_id=node(t, "Speed"),
        metric_code="casa_growth",
        profile_code="teller",
    )
    run("scorecard.template.activate", template_id=tid)
    placer = Placer.active(ORG_ID)
    assert placer.path("casa_growth", PROFILE) == ["Grow the balance sheet", "Deposits"]
    assert placer.path("casa_growth", "teller") == ["Serve reliably", "Speed"]
    assert placer.path("ntb_accounts", PROFILE) == []

    got = run("scorecard.template.get")
    assert got.template.template_id == t.template_id
    assert {p.metric_name for p in got.template.placements} == {
        "CASA balance growth",
        "Service TAT",
    }

    other = run("scorecard.template.save", name="Other", levels=["Goal"])
    run("scorecard.node.save", template_id=str(other.template_id), label="Everything")
    run("scorecard.template.activate", template_id=str(other.template_id))
    listed = run("scorecard.template.list").templates
    assert [(x.name, x.status) for x in listed] == [
        ("Other", "active"),
        ("Bank scorecard", "inactive"),
        ("Empty", "draft"),
    ]
    assert listed[1].placement_count == 3


def test_structure_changes_are_guarded(ids: dict[str, Any]) -> None:
    t = build_template()
    tid = str(t.template_id)
    run(
        "scorecard.placement.set",
        template_id=tid,
        node_id=node(t, "Deposits"),
        metric_code="casa_growth",
    )
    with pytest.raises(Conflict, match="nodes at level 2"):
        run("scorecard.template.save", template_id=tid, name="Bank scorecard", levels=["Objective"])
    with pytest.raises(Conflict, match="Move or remove"):
        run("scorecard.node.delete", template_id=tid, node_id=node(t, "Deposits"))
    run("scorecard.placement.clear", template_id=tid, metric_code="casa_growth")
    after = run("scorecard.node.delete", template_id=tid, node_id=node(t, "Deposits"))
    assert "Deposits" not in {n.label for n in after.nodes}
    with pytest.raises(Conflict, match="already exists"):
        run("scorecard.node.save", template_id=tid, label="Serve reliably")
    renamed = run(
        "scorecard.template.save",
        template_id=tid,
        name="Renamed",
        levels=["Perspective", "Objective"],
    )
    assert [lv.label for lv in renamed.levels] == ["Perspective", "Objective"]


def test_profile_metric_sets_are_effective_dated(ids: dict[str, Any]) -> None:
    out = run("scorecard.profile.list", period_key=NEXT)
    (profile,) = [p for p in out.profiles if p.profile_code == PROFILE]
    assert profile.member_count == 3
    assert {m.metric_code for m in profile.metrics} == {
        "casa_growth",
        "ntb_accounts",
        "service_tat",
        "fee_income",
    }
    first_of_next = f"{NEXT[:4]}-{NEXT[4:]}-01"
    changed = run(
        "scorecard.profile.set_metrics",
        profile_code=PROFILE,
        metric_codes=["casa_growth", "ntb_accounts", "service_tat"],
        effective_from=first_of_next,
    )
    assert changed.removed == ["fee_income"] and changed.added == []
    ended = MetricProfileAssignment.objects.get(metric__metric_code="fee_income")
    assert str(ended.effective_from) == SINCE and str(ended.effective_to) == first_of_next
    assert len(run("scorecard.profile.list", period_key=NEXT).profiles[0].metrics) == 3
    # This month keeps its metric set.
    current = run("scorecard.profile.list", period_key=shift(NEXT, -1)).profiles[0]
    assert len(current.metrics) == 4
    with pytest.raises(Conflict, match="already has exactly"):
        run(
            "scorecard.profile.set_metrics",
            profile_code=PROFILE,
            metric_codes=["casa_growth", "ntb_accounts", "service_tat"],
            effective_from=first_of_next,
        )
    with pytest.raises(InvalidInput, match="Not Scorecards metrics"):
        run(
            "scorecard.profile.set_metrics",
            profile_code=PROFILE,
            metric_codes=["unknown_metric"],
            effective_from=first_of_next,
        )


def test_default_bands_are_the_standard_four(ids: dict[str, Any]) -> None:
    out = run("band.list")
    assert out.is_default
    assert [b.label for b in out.bands] == [
        "Needs Focus",
        "Gaining Momentum",
        "On Target",
        "Exemplary",
    ]
    bands = bands_for(ORG_ID)
    assert lookup(bands, Decimal("1.023")).label == "On Target"  # type: ignore[union-attr]
    assert lookup(bands, Decimal("0.2")).label == "Needs Focus"  # type: ignore[union-attr]
    assert lookup(bands, Decimal("1.2")).label == "Exemplary"  # type: ignore[union-attr]
    assert next_band(bands, lookup(bands, Decimal("1.0"))) == DEFAULT_BANDS[3]
    assert next_band(bands, DEFAULT_BANDS[3]) is None


def test_bands_are_client_configurable_and_stay_ordinal(ids: dict[str, Any]) -> None:
    out = run(
        "band.set",
        bands=[
            {"label": "Below", "threshold": "0", "ramp_position": 1},
            {"label": "Meets", "threshold": "0.9", "ramp_position": 3},
            {"label": "Exceeds", "threshold": "1.1", "ramp_position": 5, "colour_hex": "#123456"},
        ],
    )
    assert not out.is_default and [b.label for b in out.bands] == ["Below", "Meets", "Exceeds"]
    with pytest.raises(InvalidInput):
        run(
            "band.set",
            bands=[
                {"label": "Low", "threshold": "0", "ramp_position": 3},
                {"label": "High", "threshold": "1", "ramp_position": 2},
            ],
        )
    with pytest.raises(InvalidInput):
        run("band.set", bands=[{"label": "High", "threshold": "1", "ramp_position": 2}])
    assert run("band.set", bands=[]).is_default


def test_settings_have_defaults_and_change(ids: dict[str, Any]) -> None:
    s = run("scorecard.settings.get")
    assert (s.weight_total, s.weight_tolerance, s.denominator_policy) == (
        Decimal(100),
        Decimal("0.5"),
        "reduced",
    )
    s = run("scorecard.settings.set", weight_tolerance="0", denominator_policy="redistribute")
    assert s.weight_tolerance == 0 and s.denominator_policy == "redistribute"
    with pytest.raises(InvalidInput):
        run("scorecard.settings.set", cap_min_ratio="2", cap_max_ratio="1.5")


def test_cycle_arithmetic_counts_against_the_cycle() -> None:
    april = Cycle(start_month=4, end_month=3)
    assert april.months == 12
    assert april.month_no("202606") == 3 and april.quarter_no("202606") == 1
    assert april.month_no("202607") == 4 and april.quarter_no("202607") == 2
    assert april.month_no("202703") == 12 and april.quarter_no("202703") == 4
    assert april.first_period("202602") == "202504"
    assert april.periods("202605")[0] == "202604" and april.periods("202605")[-1] == "202703"
    assert shift("202612", 1) == "202701" and shift("202601", -1) == "202512"
    assert period_range("202611", "202702") == ["202611", "202612", "202701", "202702"]
