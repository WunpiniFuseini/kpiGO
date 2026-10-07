"""Demo mode (PRD OP-9): seed a synthetic Scorecards world, explore it live, remove it
cleanly without touching anything a client authored."""

from __future__ import annotations

from dataclasses import replace

import pytest

from kpigo.action import ActionContext
from kpigo.action.context import AllSubjects
from tests.conftest import ORG_ID, role_ctx, run

pytestmark = pytest.mark.django_db


def everyone() -> ActionContext:
    return replace(role_ctx("admin"), visible_subjects=AllSubjects())


def test_status_reports_absent_then_present() -> None:
    before = run("demo.status", role_ctx("admin"))
    assert before.present is False and before.batches == 0

    run("demo.seed", role_ctx("admin"))
    after = run("demo.status", role_ctx("admin"))
    assert after.present is True and after.batches == 1


def test_seed_builds_the_world() -> None:
    from kpigo.hierarchy.models import Subject
    from kpigo.ingestion.models import FactActualMonthly
    from kpigo.metrics.models import Metric
    from kpigo.scorecards.models import RatingBand, Target

    out = run("demo.seed", role_ctx("admin"))
    assert out.subjects == 7 and out.metrics == 4 and len(out.periods) == 3
    assert out.bands_applied is True  # fresh org had no bands
    assert Subject.objects.filter(org_id=ORG_ID).count() == 7
    assert Metric.objects.filter(org_id=ORG_ID, status="active").count() == 4
    # Targets and actuals exist for every seeded period.
    assert Target.objects.filter(org_id=ORG_ID, state="published").count() == out.targets
    assert FactActualMonthly.objects.filter(org_id=ORG_ID).count() == out.actuals
    assert out.actuals == 7 * 4 * 3  # subjects × metrics × periods
    assert RatingBand.objects.filter(org_id=ORG_ID, product="scorecards").count() == 4


def test_seed_lights_up_a_live_scorecard() -> None:
    from kpigo.hierarchy.models import Subject

    out = run("demo.seed", role_ctx("admin"))
    current = out.periods[-1]
    rm = Subject.objects.get(org_id=ORG_ID, staff_no="DEMO-E1")
    card = run("scorecard.compute", everyone(), subject_id=str(rm.subject_id), period_key=current)
    assert card.assigned is True
    assert card.metrics_total == 4 and card.metrics_scored == 4
    assert card.band is not None  # a grade resolved against the seeded bands


def test_seed_builds_the_visibility_closure_for_the_manager() -> None:
    from kpigo.hierarchy.models import Subject, VisibilityClosure

    out = run("demo.seed", role_ctx("admin"))
    current = out.periods[-1]
    manager = Subject.objects.get(org_id=ORG_ID, staff_no="DEMO-M1")
    visible = set(
        VisibilityClosure.objects.filter(
            org_id=ORG_ID, period_key=current, viewer_subject_id=manager.subject_id
        ).values_list("visible_subject_id", flat=True)
    )
    team = set(
        Subject.objects.filter(org_id=ORG_ID, staff_no__startswith="DEMO-E").values_list(
            "subject_id", flat=True
        )
    )
    assert team <= visible  # the manager sees every RM on the team


def test_seed_refuses_when_already_present() -> None:
    from kpigo.action import InvalidInput

    run("demo.seed", role_ctx("admin"))
    with pytest.raises(InvalidInput, match="already loaded"):
        run("demo.seed", role_ctx("admin"))


def test_reset_removes_everything_it_created() -> None:
    from kpigo.hierarchy.models import Subject
    from kpigo.ingestion.models import FactActualMonthly
    from kpigo.metrics.models import Metric
    from kpigo.platform.models import DemoArtifact
    from kpigo.scorecards.models import Target

    seeded = run("demo.seed", role_ctx("admin"))
    out = run("demo.reset", role_ctx("admin"))
    assert out.batches == 1 and out.deleted > 0 and set(out.periods) == set(seeded.periods)

    assert Subject.objects.filter(org_id=ORG_ID).count() == 0
    assert Metric.objects.filter(org_id=ORG_ID).count() == 0
    assert Target.objects.filter(org_id=ORG_ID).count() == 0
    assert FactActualMonthly.objects.filter(org_id=ORG_ID).count() == 0
    assert DemoArtifact.objects.filter(org_id=ORG_ID).count() == 0
    assert run("demo.status", role_ctx("admin")).present is False


def test_reset_leaves_client_data_untouched() -> None:
    from kpigo.hierarchy.models import Subject
    from kpigo.metrics.models import Metric

    # A client authors their own subject and metric before loading the demo.
    client = run(
        "subject.register",
        role_ctx("admin"),
        staff_no="REAL-1",
        full_name="A real employee",
        email="real1@bank.example",
    )
    run(
        "metric.register",
        role_ctx("admin"),
        display_name="Real deposits",
        metric_code="real_deposits",
        direction="higher_is_better",
        aggregation="sum",
        unit="currency",
        products=["scorecards"],
    )

    run("demo.seed", role_ctx("admin"))
    run("demo.reset", role_ctx("admin"))

    # The client's own rows survive; only the demo's are gone.
    assert Subject.objects.filter(org_id=ORG_ID, subject_id=client.subject_id).exists()
    assert Metric.objects.filter(org_id=ORG_ID, metric_code="real_deposits").exists()
    assert not Metric.objects.filter(org_id=ORG_ID, metric_code__startswith="demo_").exists()
    assert not Subject.objects.filter(org_id=ORG_ID, staff_no__startswith="DEMO-").exists()


def test_reset_is_a_noop_when_there_is_nothing_to_remove() -> None:
    out = run("demo.reset", role_ctx("admin"))
    assert out.batches == 0 and out.deleted == 0 and "No demo data" in out.message


def test_reset_preserves_client_rating_bands() -> None:
    from kpigo.scorecards.models import RatingBand

    # A client with their own bands: the demo neither creates nor removes bands.
    run(
        "band.set",
        role_ctx("admin"),
        bands=[
            {"label": "Low", "threshold": "0", "ramp_position": 1},
            {"label": "High", "threshold": "1.0", "ramp_position": 3},
        ],
    )
    seeded = run("demo.seed", role_ctx("admin"))
    assert seeded.bands_applied is False

    run("demo.reset", role_ctx("admin"))
    labels = set(
        RatingBand.objects.filter(org_id=ORG_ID, product="scorecards").values_list(
            "label", flat=True
        )
    )
    assert labels == {"Low", "High"}
