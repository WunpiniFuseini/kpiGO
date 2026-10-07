"""Saved personal views of the Executive dashboard (PRD EX-9, Scope §10.5).

A reader keeps named snapshots of the dashboard's filter state — the period and
each breakdown widget's drill path — and recalls them. The views are personal:
what one reader saves is invisible to everyone else, and a name is unique only
within one reader's own set.
"""

from __future__ import annotations

from typing import Any

import pytest

from kpigo.action import Conflict, InvalidInput, NotFound
from kpigo.action.identity import build_context
from kpigo.executive.models import ExecutiveSavedView
from tests.conftest import role_ctx, run
from tests.executive_support import world

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def org() -> None:
    world()


def ctx_for(user: Any) -> Any:
    return build_context(user, caller="cli")


def names(user: Any) -> list[str]:
    return [v.name for v in run("executive.view.list", ctx_for(user)).views]


def test_a_saved_view_round_trips_its_period_and_drill(make_user: Any) -> None:
    ctx = ctx_for(make_user("executive"))
    state = {"period_key": "202610", "drill": {"nps_by_region": "south"}}
    out = run("executive.view.save", ctx, name="Q4 south", state=state)
    assert out.name == "Q4 south"
    assert out.state.period_key == "202610"
    assert out.state.drill == {"nps_by_region": "south"}

    listed = run("executive.view.list", ctx).views
    assert [v.name for v in listed] == ["Q4 south"]
    assert listed[0].state.drill == {"nps_by_region": "south"}


def test_saving_the_same_name_replaces_the_state(make_user: Any) -> None:
    ctx = ctx_for(make_user("executive"))
    run("executive.view.save", ctx, name="mine", state={"period_key": "202609", "drill": {}})
    again = run(
        "executive.view.save",
        ctx,
        name="mine",
        state={"period_key": "202610", "drill": {"rev": "GA"}},
    )
    assert again.state.period_key == "202610" and again.state.drill == {"rev": "GA"}
    # Still one row: a re-save is an update, not a second view.
    assert ExecutiveSavedView.objects.count() == 1
    assert again.updated_at >= again.created_at


def test_a_view_defaults_to_no_drill(make_user: Any) -> None:
    ctx = ctx_for(make_user("executive"))
    out = run("executive.view.save", ctx, name="just a period", state={"period_key": "202610"})
    assert out.state.drill == {}


def test_views_are_each_readers_own(make_user: Any) -> None:
    alice = make_user("executive", username="alice")
    bob = make_user("executive", username="bob")
    run("executive.view.save", ctx_for(alice), name="shared name", state={"period_key": "202610"})
    run("executive.view.save", ctx_for(bob), name="shared name", state={"period_key": "202608"})
    # The same name lives independently under each reader; neither sees the other's.
    assert names(alice) == ["shared name"] and names(bob) == ["shared name"]
    assert ExecutiveSavedView.objects.count() == 2
    a = run("executive.view.list", ctx_for(alice)).views[0]
    b = run("executive.view.list", ctx_for(bob)).views[0]
    assert a.state.period_key == "202610" and b.state.period_key == "202608"


def test_delete_removes_only_that_view(make_user: Any) -> None:
    ctx = ctx_for(make_user("executive"))
    run("executive.view.save", ctx, name="keep", state={"period_key": "202610"})
    run("executive.view.save", ctx, name="drop", state={"period_key": "202610"})
    out = run("executive.view.delete", ctx, name="drop")
    assert out.deleted is True
    assert [v.name for v in run("executive.view.list", ctx).views] == ["keep"]


def test_deleting_a_missing_view_is_a_clear_not_found(make_user: Any) -> None:
    ctx = ctx_for(make_user("executive"))
    with pytest.raises(NotFound, match="no saved view"):
        run("executive.view.delete", ctx, name="never saved")


def test_a_saved_view_belongs_to_a_signed_in_reader() -> None:
    # role_ctx carries the permission but no signed-in account.
    with pytest.raises(Conflict, match="signed-in"):
        run(
            "executive.view.save",
            role_ctx("executive"),
            name="x",
            state={"period_key": "202610"},
        )


def test_a_bad_period_is_refused(make_user: Any) -> None:
    ctx = ctx_for(make_user("executive"))
    with pytest.raises(InvalidInput):
        run("executive.view.save", ctx, name="bad", state={"period_key": "2026-10"})


def test_a_bad_widget_key_in_the_drill_is_refused(make_user: Any) -> None:
    ctx = ctx_for(make_user("executive"))
    with pytest.raises(InvalidInput):
        run(
            "executive.view.save",
            ctx,
            name="bad drill",
            state={"period_key": "202610", "drill": {"Not A Key": "south"}},
        )
