"""Sales and Service presets and their sections (PRD AP-4; Starter Packs §4.2)."""

from __future__ import annotations

import pytest

from kpigo.action import InvalidInput, NotFound
from tests.agent_support import PRODUCT, SINCE, day, load, targets, world
from tests.conftest import run

pytestmark = pytest.mark.django_db
SERVICE = "agent_service"

# The Service Officer starter pack's daily metrics (Starter Packs §4.1).
SO: dict[str, tuple[str, str, str, str, str]] = {
    "so_tat": ("Average handling time", "hours", "average", "lower_is_better", "profile"),
    "so_sla_breach": ("SLA breach rate", "percent", "average", "lower_is_better", "profile"),
    "so_resolution": (
        "First-contact resolution",
        "percent",
        "average",
        "higher_is_better",
        "profile",
    ),
    "so_complaints": ("Complaints handled", "count", "sum", "higher_is_better", "profile"),
}


def service() -> None:
    for code, (name, unit, agg, direction, scope) in SO.items():
        run(
            "metric.register",
            display_name=name,
            metric_code=code,
            direction=direction,
            aggregation=agg,
            unit=unit,
            target_scope=scope,
            products=[SERVICE],
            effective_from=SINCE,
            acknowledge_similar=True,
        )
        run(
            "metric.profile.assign",
            metric_code=code,
            profile_code="sme_rm",
            product=SERVICE,
            effective_from=SINCE,
        )
    rows = [
        {
            "metric_code": c,
            "scope_type": "profile",
            "scope_code": "sme_rm",
            "period_key": "202506",
            "target_value": v,
        }
        for c, v in (
            ("so_tat", "4"),
            ("so_sla_breach", "5"),
            ("so_resolution", "80"),
            ("so_complaints", "42"),
        )
    ]
    assert run("target.upload", rows=rows).accepted
    run("target.publish", period_keys=["202506"])


@pytest.fixture
def org() -> dict[str, str]:
    ids = world()
    targets()
    service()
    load(
        [
            ["value_booked", "A1", day(2), None, "150", "GHS"],
            ["value_booked", "A1", day(3), None, "50", "GHS"],
            ["value_booked", "A2", day(3), None, "120", "GHS"],
            ["so_tat", "A1", day(2), None, "3", None],
            ["so_tat", "A2", day(2), None, "5", None],
            ["so_tat", "A3", day(2), None, "8", None],
            ["so_tat", "A1", day(3), None, "4", None],
            ["so_complaints", "A1", day(2), None, "2", None],
            ["so_complaints", "A3", day(2), None, "1", None],
            ["so_resolution", "A1", day(2), None, "70", None],
            ["so_resolution", "A2", day(2), None, "90", None],
        ]
    )
    return ids


def sections(product: str) -> list[tuple[str, str, str | None]]:
    out = run("agent.preset", product=product, as_of=day(3))
    return [(s.key, s.kind, s.metric.key if s.metric else None) for s in out.sections]


def test_sales_opens_on_the_leaderboard_product_mix_and_month_to_date(org: dict[str, str]) -> None:
    assert sections(PRODUCT) == [
        ("leaderboard", "leaderboard", None),
        ("product_mix", "matrix", None),
        # No starter-pack code here: the first sum or count by name.
        ("to_date", "trend", "accounts_opened"),
    ]


def test_service_opens_on_sla_tat_queue_then_the_leaderboard(org: dict[str, str]) -> None:
    assert sections(SERVICE) == [
        ("sla", "heatmap", "so_sla_breach"),
        ("tat", "distribution", "so_tat"),
        ("queue", "trend", "so_complaints"),
        ("leaderboard", "leaderboard", None),
    ]
    out = run("agent.preset", product=SERVICE, as_of=day(3))
    assert [m.key for m in out.sections[0].metric_options] == [
        "so_tat",
        "so_complaints",
        "so_resolution",
        "so_sla_breach",
    ]


def test_service_ranks_by_resolution_with_tat_as_the_tiebreak_until_settings_say_otherwise(
    org: dict[str, str],
) -> None:
    board = run("agent.leaderboard", product=SERVICE, as_of=day(3))
    assert (board.rank_by.key, board.tiebreak.key) == ("so_resolution", "so_tat")
    assert [(r.agent.staff_no, r.rank) for r in board.rows] == [("A2", 1), ("A1", 2), ("A3", None)]
    # Once an Admin saves the module's settings, they rank: here, the composite.
    run("agent.settings.set", product=SERVICE)
    board = run("agent.leaderboard", product=SERVICE, as_of=day(3))
    assert (board.rank_by.key, board.tiebreak) == ("composite", None)
    # Sales has no preset ranking: the composite, as before.
    assert run("agent.leaderboard", product=PRODUCT, as_of=day(3)).rank_by.key == "composite"


def test_the_trend_runs_against_where_the_targets_expect_it(org: dict[str, str]) -> None:
    t = run("agent.trend", product=PRODUCT, as_of=day(4), metric_code="value_booked")
    assert (t.additive, t.agents, t.reported, t.currency_code) == (True, 3, 2, "GHS")
    got = [(p.day.day, p.working, p.value, p.reported, p.cumulative, p.expected) for p in t.points]
    # 100 a working day for each of the two agents who reported; A3 adds nothing.
    assert [(d, w, str(v) if v else v, r, str(c), str(e)) for d, w, v, r, c, e in got] == [
        (1, False, None, 0, "0.0000", "0.0000"),
        (2, True, "150.0000", 1, "150.0000", "200.0000"),
        (3, True, "170.0000", 2, "320.0000", "400.0000"),
        (4, True, None, 0, "320.0000", "600.0000"),
    ]


def test_an_average_trends_as_the_mean_of_who_reported(org: dict[str, str]) -> None:
    t = run("agent.trend", product=SERVICE, as_of=day(3), metric_code="so_tat")
    assert t.additive is False
    assert [
        (p.day.day, p.value and str(p.value), p.reported, p.target and str(p.target))
        for p in t.points
    ] == [
        (1, None, 0, None),
        (2, "5.3333", 3, "4.0000"),
        (3, "4.0000", 1, "4.0000"),
    ]
    assert all(p.cumulative is None and p.expected is None for p in t.points)


def test_the_sla_heatmap_shows_each_branch_day_against_target(org: dict[str, str]) -> None:
    h = run("agent.heatmap", product=SERVICE, as_of=day(3), metric_code="so_tat")
    # Sunday the 1st is a day off nobody worked: no column.
    assert [d.day for d in h.days] == [2, 3]
    rows = {
        r.key: [
            (c.value and str(c.value), c.achieved and str(c.achieved), c.rag, c.reported)
            for c in r.cells
        ]
        for r in h.rows
    }
    # Lower is better: 4 against 4 is on target; 8 against 4 is half.
    assert rows == {
        "GA-01": [("4.0000", "1.0000", "green", 2), ("4.0000", "1.0000", "green", 1)],
        "AS-01": [("8.0000", "0.5000", "red", 1), (None, None, None, 0)],
    }
    rms = run(
        "agent.heatmap",
        product=SERVICE,
        as_of=day(3),
        metric_code="so_tat",
        level="rm",
        branch_code="GA-01",
    )
    assert [r.name for r in rms.rows] == ["Agent A1", "Agent A2"]
    with pytest.raises(InvalidInput):
        run("agent.heatmap", product=SERVICE, level="rm")
    with pytest.raises(NotFound):
        run("agent.heatmap", product=SERVICE, as_of=day(3), region_code="NOWHERE")


def test_the_tat_distribution_bins_agents_to_date(org: dict[str, str]) -> None:
    d = run("agent.distribution", product=SERVICE, as_of=day(3))
    assert d.metric.key == "so_tat" and (d.agents, d.reported) == (3, 3)
    # A1 averages 3.5 over two days; A2 5; A3 8. One shared target of 4.
    assert [(str(b.low), str(b.high), b.agents, b.on_target) for b in d.bins] == [
        ("3.5000", "5.0000", 1, 1),
        ("5.0000", "6.5000", 1, 0),
        ("6.5000", "8.0000", 1, 0),
    ]
    assert (str(d.target), str(d.median)) == ("4.0000", "5.0000")


def test_sections_only_take_the_modules_metrics(org: dict[str, str]) -> None:
    with pytest.raises(InvalidInput):
        run("agent.trend", product=SERVICE, metric_code="value_booked")
    # Sales has no heatmap in its preset, but the read still works on any of its metrics.
    h = run("agent.heatmap", product=PRODUCT, as_of=day(3), metric_code="value_booked")
    by_key = {r.key: r for r in h.rows}
    assert str(by_key["GA-01"].cells[0].value) == "150.0000"
    # Ashanti is measured but sold nothing: a row of absent days, not zeros.
    assert all(c.value is None and c.reported == 0 for c in by_key["AS-01"].cells)
