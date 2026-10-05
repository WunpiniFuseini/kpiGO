"""The leaderboard: cohorts, ranking, the tie-break and the summary cards (PRD AP-3; Scope §8.1)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from kpigo.action import Conflict, InvalidInput, NotFound, PermissionDenied
from kpigo.action.identity import build_context
from tests.agent_support import PRODUCT, day, load, targets, world
from tests.conftest import role_ctx, run

pytestmark = pytest.mark.django_db

# As of 17 June (working day 12 of 21), value booked expects 1200 of 2100 and
# accounts opened 12 of 21. A1 and A2 level on value booked; A1 opened more.
ROWS: list[list[Any]] = [
    ["value_booked", "A1", day(2), None, "1000", "GHS"],
    ["value_booked", "A1", day(17), None, "320", "GHS"],
    ["value_booked", "A2", day(3), "CARDS", "1000", "GHS"],
    ["value_booked", "A2", day(3), "LOANS", "320", "GHS"],
    ["value_booked", "A3", day(17), None, "900", "GHS"],
    ["accounts_opened", "A1", day(2), None, "15", None],
    ["accounts_opened", "A2", day(2), None, "12", None],
    ["service_tat", "A1", day(2), None, "3", None],
    ["service_tat", "A2", day(2), None, "5", None],
]


@pytest.fixture
def org() -> dict[str, str]:
    ids = world()
    targets()
    load(ROWS)
    return ids


def board(ctx: Any = None, **payload: Any) -> Any:
    payload.setdefault("product", PRODUCT)
    return run("agent.leaderboard", ctx or role_ctx(), **payload)


def ranks(out: Any) -> list[tuple[str, int | None]]:
    return [(r.agent.staff_no, r.rank) for r in out.rows]


def sign_in(make_user: Any, staff_no: str) -> Any:
    return build_context(make_user("staff", email=f"{staff_no.lower()}@bank.example"), caller="cli")


def test_ranked_by_value_to_date_with_shared_ranks(org: dict[str, str]) -> None:
    out = board(cohort_type="all", rank_by="value_booked")
    assert out.window.as_of.isoformat() == day(17)
    assert out.cohort.name == "Everyone" and out.cohort.agents == 3
    # No tie-break declared: the level pair share first place, and third follows.
    assert ranks(out) == [("A1", 1), ("A2", 1), ("A3", 3)]
    first = out.rows[0]
    assert first.value == Decimal("1320.0000") and first.pace == Decimal("1.1000")
    assert first.state == "paced" and first.band is not None
    assert out.rank_by.key == "value_booked" and out.tiebreak is None
    assert out.total == 3


def test_a_declared_second_metric_breaks_the_tie(org: dict[str, str]) -> None:
    run(
        "agent.settings.set",
        product=PRODUCT,
        rank_metric_code="value_booked",
        tiebreak_metric_code="accounts_opened",
        cohort_type="all",
    )
    out = board()
    assert out.cohort_type == "all"
    assert out.rank_by.key == "value_booked" and out.tiebreak.key == "accounts_opened"
    assert ranks(out) == [("A1", 1), ("A2", 2), ("A3", 3)]
    assert [r.tiebreak_value for r in out.rows] == [Decimal("15.0000"), Decimal("12.0000"), None]


def test_absent_is_listed_unranked_not_ranked_last_on_zero(org: dict[str, str]) -> None:
    out = board(cohort_type="all", rank_by="accounts_opened")
    assert ranks(out) == [("A1", 1), ("A2", 2), ("A3", None)]
    assert out.rows[-1].state == "not_reported" and out.rows[-1].value is None


def test_lower_is_better_ranks_the_smaller_figure_first(org: dict[str, str]) -> None:
    out = board(cohort_type="all", rank_by="service_tat")
    assert ranks(out) == [("A1", 1), ("A2", 2), ("A3", None)]


def test_the_composite_is_the_mean_of_capped_paces(org: dict[str, str]) -> None:
    out = board(cohort_type="all")
    assert out.rank_by.key == "composite"
    # A1: 1.1, 1.25 and 4/3 on TAT; A2: 1.1, 1.0 and 0.8; A3 paced on value only.
    assert ranks(out) == [("A1", 1), ("A2", 2), ("A3", 3)]
    assert [r.pace for r in out.rows] == [Decimal("1.2278"), Decimal("0.9667"), Decimal("0.7500")]
    assert {o.key for o in out.rank_options} == {
        "value_booked",
        "accounts_opened",
        "service_tat",
        "composite",
    }
    # The summary only adds up an additive metric.
    assert out.summary.value_to_date is None and out.summary.on_pace == 1


def test_a_runaway_metric_is_capped_in_the_composite(org: dict[str, str]) -> None:
    load([["accounts_opened", "A3", day(17), None, "120", None]], feed="runaway")
    out = board(cohort_type="all")
    a3 = next(r for r in out.rows if r.agent.staff_no == "A3")
    # Accounts pace 10 is read at the cap of 2: (0.75 + 2) / 2.
    assert a3.pace == Decimal("1.3750")


def test_the_summary_cards_add_up_the_cohort(org: dict[str, str]) -> None:
    s = board(cohort_type="all", rank_by="value_booked").summary
    assert s.agents == 3 and s.on_pace == 2
    assert s.value_to_date == Decimal("3540.0000")
    assert s.target_to_date == Decimal("3600.0000")
    assert s.pace == Decimal("0.9833")
    assert s.short_of_pace == Decimal("60.0000")
    # The as-of day's own figure: A1's 320 and A3's 900.
    assert s.day_value == Decimal("1220.0000")
    assert s.currency_code == "GHS"


def test_region_and_branch_cohorts_default_to_your_own(org: dict[str, str], make_user: Any) -> None:
    out = board(cohort_type="region", rank_by="value_booked")
    assert [(c.code, c.agents) for c in out.cohorts] == [("AS", 1), ("GA", 2)]
    # Nobody in the hierarchy: the first cohort by name.
    assert out.cohort.code == "AS"
    ga = board(cohort_type="region", cohort_code="GA", rank_by="value_booked")
    assert ranks(ga) == [("A1", 1), ("A2", 1)]
    mine = board(sign_in(make_user, "A2"), cohort_type="branch", rank_by="value_booked")
    assert mine.cohort.code == "GA-01"
    assert [r.is_you for r in mine.rows] == [False, True]
    with pytest.raises(NotFound):
        board(cohort_type="region", cohort_code="NR")


def test_the_default_cohort_is_the_profile(org: dict[str, str]) -> None:
    out = board()
    assert out.cohort_type == "profile"
    assert [(c.code, c.agents) for c in out.cohorts] == [("sme_rm", 3)]


def test_a_limit_still_shows_your_own_row(org: dict[str, str], make_user: Any) -> None:
    out = board(sign_in(make_user, "A3"), cohort_type="all", rank_by="value_booked", limit=1)
    assert ranks(out) == [("A1", 1), ("A3", 3)]
    assert out.rows[-1].is_you and out.total == 3


def test_custom_cohorts_are_effective_dated(org: dict[str, str]) -> None:
    run(
        "agent.cohort.set",
        product=PRODUCT,
        code="stars",
        name="Stars",
        subject_ids=[org["A1"], org["A3"]],
        effective_from=day(1),
    )
    # From the 20th A1 leaves and A2 joins; the 17th still has the first line-up.
    later = run(
        "agent.cohort.set",
        product=PRODUCT,
        code="stars",
        name="Stars",
        subject_ids=[org["A2"], org["A3"]],
        effective_from=day(20),
    )
    assert {m.staff_no for m in later.members} == {"A2", "A3"}
    out = board(cohort_type="cohort", rank_by="value_booked")
    assert out.cohort.name == "Stars"
    assert ranks(out) == [("A1", 1), ("A3", 2)]
    listed = run("agent.cohort.list", product=PRODUCT, as_of=day(17)).cohorts
    assert [{m.staff_no for m in c.members} for c in listed] == [{"A1", "A3"}]
    with pytest.raises(Conflict):
        run(
            "agent.cohort.set",
            product=PRODUCT,
            code="stars",
            name="Stars",
            subject_ids=[org["A3"]],
            effective_from=day(10),
        )
    with pytest.raises(InvalidInput):
        run(
            "agent.cohort.set",
            product=PRODUCT,
            code="ghosts",
            name="Ghosts",
            subject_ids=["00000000-0000-0000-0000-00000000dead"],
            effective_from=day(1),
        )


def test_settings_only_name_the_modules_own_metrics(org: dict[str, str]) -> None:
    with pytest.raises(InvalidInput):
        run("agent.settings.set", product="agent_service", rank_metric_code="value_booked")
    with pytest.raises(InvalidInput):
        run(
            "agent.settings.set",
            product=PRODUCT,
            rank_metric_code="value_booked",
            tiebreak_metric_code="value_booked",
        )
    with pytest.raises(InvalidInput):
        board(rank_by="deposits")


def test_the_board_follows_each_daily_load(org: dict[str, str]) -> None:
    load([["value_booked", "A3", day(16), None, "600", "GHS"]], feed="late")
    out = board(cohort_type="all", rank_by="value_booked")
    assert ranks(out) == [("A3", 1), ("A1", 2), ("A2", 2)]
    assert out.rows[0].value == Decimal("1500.0000")
    assert run("agent.leaderboard.refresh").refreshed_at


def test_open_to_every_role_with_the_page(org: dict[str, str]) -> None:
    assert board(role_ctx("staff"), cohort_type="all").total == 3
    with pytest.raises(PermissionDenied):
        board(role_ctx("contributor"))
    with pytest.raises(PermissionDenied):
        run("agent.leaderboard.refresh", role_ctx("staff"))
    with pytest.raises(PermissionDenied):
        run(
            "agent.cohort.set",
            role_ctx("staff"),
            product=PRODUCT,
            code="x",
            name="X",
            subject_ids=[],
            effective_from=day(1),
        )


def test_an_empty_module_has_no_board(db: None) -> None:
    out = run("agent.leaderboard", product="agent_service")
    assert out.rows == [] and out.cohort is None and out.rank_options == []
