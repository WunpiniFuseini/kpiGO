"""Acknowledgement, queries, commentary, history and PDF (PRD SC-8, SC-14–SC-17)."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from django.contrib.auth.models import User

from kpigo.action import ActionContext, Conflict, InvalidInput, OutOfScope, PermissionDenied
from kpigo.action.context import AllSubjects, ExplicitSubjects
from kpigo.scorecards.models import ScorecardInteraction
from tests.conftest import role_ctx, run
from tests.scorecard_support import CURRENT, NEXT, PREVIOUS, SINCE
from tests.test_period_close import fact, ids, resolve  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db

P = PREVIOUS


@pytest.fixture
def people(ids: dict[str, Any], make_user: Callable[..., User]) -> dict[str, ActionContext]:  # noqa: F811
    """E2 reports to E1 on a solid line; E3 has no manager. An Admin sees everyone."""
    run(
        "reporting_edge.create",
        subject_id=ids["E2"],
        manager_id=ids["E1"],
        relationship_type="solid",
        effective_from=SINCE,
    )

    def as_(role: str, staff_no: str, sees: list[str]) -> ActionContext:
        user = make_user(role, email=f"{staff_no.lower()}@bank.example")
        return replace(
            role_ctx(role), user=user, visible_subjects=ExplicitSubjects.of(ids[s] for s in sees)
        )

    return {
        "E1": as_("line_manager", "E1", ["E1", "E2"]),
        "E2": as_("staff", "E2", ["E2"]),
        "E3": as_("staff", "E3", ["E3"]),
        "admin": replace(
            role_ctx("admin"), user=make_user("admin"), visible_subjects=AllSubjects()
        ),
    }


def close(ids: dict[str, Any]) -> None:  # noqa: F811
    resolve(ids)
    run("scorecard.period.close", period_key=P)


def test_acknowledge_once_per_published_version(
    ids: dict[str, Any],  # noqa: F811
    people: dict[str, ActionContext],
) -> None:
    e2 = people["E2"]
    with pytest.raises(Conflict, match="not closed yet"):
        run("scorecard.acknowledge", e2, period_key=P)
    thread = run("scorecard.interaction.list", e2, subject_id=ids["E2"], period_key=P)
    assert (
        thread.is_self
        and not thread.can_acknowledge
        and "once the period is closed" in (thread.note or "")
    )

    close(ids)
    thread = run("scorecard.interaction.list", e2, subject_id=ids["E2"], period_key=P)
    assert thread.can_acknowledge and thread.snapshot_version == 1
    first = run("scorecard.acknowledge", e2, period_key=P)
    again = run("scorecard.acknowledge", e2, period_key=P)
    assert first.interaction_id == again.interaction_id and first.snapshot_version == 1
    thread = run("scorecard.interaction.list", e2, subject_id=ids["E2"], period_key=P)
    assert thread.acknowledged and not thread.can_acknowledge

    # A manager sees it but cannot acknowledge on someone's behalf.
    seen = run("scorecard.interaction.list", people["E1"], subject_id=ids["E2"], period_key=P)
    assert seen.acknowledged and not seen.is_self and not seen.can_acknowledge

    # A restatement asks again; the old acknowledgement stays on record.
    run("scorecard.period.restate", period_key=P, reason="Corrected CASA balances.")
    run("scorecard.period.close", period_key=P)
    thread = run("scorecard.interaction.list", e2, subject_id=ids["E2"], period_key=P)
    assert not thread.acknowledged and thread.acknowledged_version == 1 and thread.can_acknowledge
    assert run("scorecard.acknowledge", e2, period_key=P).snapshot_version == 2
    assert ScorecardInteraction.objects.filter(interaction_type="acknowledgement").count() == 2


def test_an_unlinked_account_has_no_scorecard_to_acknowledge(
    ids: dict[str, Any],  # noqa: F811
    make_user: Callable[..., User],
) -> None:
    close(ids)
    ctx = replace(role_ctx("staff"), user=make_user("staff"))
    with pytest.raises(Conflict, match="not linked to a person"):
        run("scorecard.acknowledge", ctx, period_key=P)


def test_a_query_goes_to_the_solid_line_manager(
    ids: dict[str, Any],  # noqa: F811
    people: dict[str, ActionContext],
) -> None:
    e1, e2, e3, admin = people["E1"], people["E2"], people["E3"], people["admin"]
    q = run(
        "scorecard.query.raise",
        e2,
        period_key=P,
        metric_code="casa_growth",
        body="My CASA growth looks low for September.",
    )
    assert q.routed_to_id is not None and str(q.routed_to_id) == ids["E1"] and q.mine
    with pytest.raises(InvalidInput, match="not on your scorecard"):
        run("scorecard.query.raise", e2, period_key=P, metric_code="nope", body="Not mine.")
    with pytest.raises(Conflict, match="has not started"):
        run("scorecard.query.raise", e2, period_key=NEXT, metric_code="casa_growth", body="Early.")

    # No manager: it waits for an Admin.
    orphan = run(
        "scorecard.query.raise", e3, period_key=P, metric_code="ntb_accounts", body="Missing one."
    )
    assert orphan.routed_to_id is None

    queue = run("scorecard.query.list", e1)
    assert [x.interaction_id for x in queue.queue] == [q.interaction_id]
    assert queue.routed_to_me == 1
    assert run("scorecard.query.list", e2).mine[0].interaction_id == q.interaction_id
    assert run("scorecard.query.list", e2).queue == []  # staff resolve nothing
    assert {x.interaction_id for x in run("scorecard.query.list", admin).queue} == {
        q.interaction_id,
        orphan.interaction_id,
    }

    with pytest.raises(PermissionDenied):
        run(
            "scorecard.query.resolve",
            e2,
            interaction_id=q.interaction_id,
            outcome="explained",
            resolution="Answering my own.",
        )
    with pytest.raises(OutOfScope):
        run(
            "scorecard.query.resolve",
            e1,
            interaction_id=orphan.interaction_id,
            outcome="explained",
            resolution="Not my person.",
        )
    with pytest.raises(InvalidInput):
        run(
            "scorecard.query.resolve",
            e1,
            interaction_id=q.interaction_id,
            outcome="adjusted",
            resolution="Fixing it.",
        )
    done = run(
        "scorecard.query.resolve",
        e1,
        interaction_id=q.interaction_id,
        outcome="explained",
        resolution="The reversal on the 30th is in the figure; it is correct.",
    )
    assert done.outcome == "explained" and done.resolved_at is not None
    with pytest.raises(Conflict, match="already resolved"):
        run(
            "scorecard.query.resolve",
            e1,
            interaction_id=q.interaction_id,
            outcome="explained",
            resolution="Twice.",
        )
    assert run("scorecard.query.list", e1).queue == []
    assert len(run("scorecard.query.list", e1, status="resolved").queue) == 1


def test_an_adjusted_query_names_the_override(
    ids: dict[str, Any],  # noqa: F811
    people: dict[str, ActionContext],
) -> None:
    e1, e2 = people["E1"], people["E2"]
    q = run(
        "scorecard.query.raise", e2, period_key=P, metric_code="ntb_accounts", body="Target high."
    )
    o = run(
        "override.request",
        e1,
        scope_type="subject",
        scope_code="E2",
        metric_code="ntb_accounts",
        period_from=P,
        change_type="target",
        override_value="80",
        reason="Target set before the branch merger.",
    )
    wrong = run(
        "override.request",
        e1,
        scope_type="subject",
        scope_code="E2",
        metric_code="casa_growth",
        period_from=P,
        change_type="target",
        override_value="80",
        reason="Different metric altogether.",
    )
    with pytest.raises(InvalidInput, match="queried metric"):
        run(
            "scorecard.query.resolve",
            e1,
            interaction_id=q.interaction_id,
            outcome="adjusted",
            resolution="Lowering it.",
            override_id=wrong.override_id,
        )
    done = run(
        "scorecard.query.resolve",
        e1,
        interaction_id=q.interaction_id,
        outcome="adjusted",
        resolution="Requested a lower target; it applies once approved.",
        override_id=o.override_id,
    )
    assert done.resulting_override_id == o.override_id


def test_manager_commentary_and_its_visibility(
    ids: dict[str, Any],  # noqa: F811
    people: dict[str, ActionContext],
) -> None:
    e1, e2 = people["E1"], people["E2"]
    run("scorecard.comment.add", e1, subject_id=ids["E2"], period_key=P, body="Strong month.")
    run(
        "scorecard.comment.add",
        e1,
        subject_id=ids["E2"],
        period_key=P,
        body="Watch the TAT trend.",
        visibility="managers",
    )
    with pytest.raises(Conflict, match="not your own"):
        run("scorecard.comment.add", e1, subject_id=ids["E1"], period_key=P, body="Self praise.")
    with pytest.raises(OutOfScope):
        run("scorecard.comment.add", e1, subject_id=ids["E3"], period_key=P, body="Not mine.")
    with pytest.raises(PermissionDenied):
        run("scorecard.comment.add", e2, subject_id=ids["E2"], period_key=P, body="Nice.")

    mine = run("scorecard.interaction.list", e2, subject_id=ids["E2"], period_key=P)
    assert [c.body for c in mine.comments] == ["Strong month."]
    assert not mine.can_comment
    managers = run("scorecard.interaction.list", e1, subject_id=ids["E2"], period_key=P)
    assert [c.body for c in managers.comments] == ["Strong month.", "Watch the TAT trend."]
    assert managers.can_comment and not managers.can_query


def test_history_reads_frozen_then_live(
    ids: dict[str, Any],  # noqa: F811
    people: dict[str, ActionContext],
) -> None:
    close(ids)
    out = run("scorecard.history", people["E2"], subject_id=ids["E2"], to_period=CURRENT, periods=3)
    by_period = {p.period_key: p for p in out.points}
    assert by_period[P].source == "snapshot" and by_period[P].snapshot_version == 1
    # No targets for the current month: a scorecard, but nothing scored yet.
    assert by_period[CURRENT].source == "live" and by_period[CURRENT].metrics_scored == 0
    with pytest.raises(OutOfScope):
        run("scorecard.history", people["E2"], subject_id=ids["E1"])


def test_pdf_export_is_the_screen_on_paper(
    ids: dict[str, Any],  # noqa: F811
    people: dict[str, ActionContext],
) -> None:
    close(ids)
    out = run("scorecard.export.pdf", people["E2"], subject_id=ids["E2"], period_key=P)
    assert out.filename == f"scorecard-E2-{P}.pdf" and out.media_type == "application/pdf"
    pdf = base64.b64decode(out.content_base64)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000
    with pytest.raises(OutOfScope):
        run("scorecard.export.pdf", people["E2"], subject_id=ids["E1"], period_key=P)
