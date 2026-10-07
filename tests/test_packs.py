"""Starter packs (PRD OP-9, Starter Packs doc): list and additive, non-destructive load."""

from __future__ import annotations

import pytest

from kpigo.platform.packs import ALL_PACKS
from tests.conftest import role_ctx, run

pytestmark = pytest.mark.django_db


def test_list_shows_every_shipped_pack_in_full() -> None:
    out = run("pack.list", role_ctx("admin"))
    keys = {p.key for p in out.packs}
    assert keys == {"retail_rm", "retail_service_officer", "executive_banking", "campaign_banking"}
    rm = next(p for p in out.packs if p.key == "retail_rm")
    assert rm.metric_count == 7 and rm.version
    assert "CASA balance growth" in {m.name for m in rm.metrics}
    assert len(rm.bands) == 4 and rm.bands[0].threshold == "0"


def test_load_creates_the_metrics_and_bands() -> None:
    from kpigo.metrics.models import Metric
    from kpigo.scorecards.models import RatingBand

    out = run("pack.load", role_ctx("admin"), pack_key="retail_rm")
    assert set(out.metrics_created) == {m.code for m in ALL_PACKS["retail_rm"].metrics}
    assert out.metrics_skipped == [] and out.bands_applied is True
    codes = set(
        Metric.objects.filter(org_id=role_ctx().org_id).values_list("metric_code", flat=True)
    )
    assert {"rm_casa_growth", "rm_fee_income"} <= codes
    casa = Metric.objects.get(org_id=role_ctx().org_id, metric_code="rm_casa_growth")
    assert casa.target_scope == "subject" and casa.direction == "higher_is_better"
    assert RatingBand.objects.filter(org_id=role_ctx().org_id, product="scorecards").count() == 4


def test_load_is_idempotent_and_non_destructive() -> None:
    from kpigo.metrics.models import Metric

    run("pack.load", role_ctx("admin"), pack_key="retail_rm")
    before = Metric.objects.filter(org_id=role_ctx().org_id).count()
    second = run("pack.load", role_ctx("admin"), pack_key="retail_rm")
    # Nothing new created; every code reported as already present.
    assert second.metrics_created == []
    assert set(second.metrics_skipped) == {m.code for m in ALL_PACKS["retail_rm"].metrics}
    assert Metric.objects.filter(org_id=role_ctx().org_id).count() == before


def test_load_does_not_overwrite_custom_bands() -> None:
    # A client who has set their own bands keeps them when a pack is loaded.
    run(
        "band.set",
        role_ctx("admin"),
        bands=[
            {"label": "Low", "threshold": "0", "ramp_position": 1},
            {"label": "High", "threshold": "1.0", "ramp_position": 3},
        ],
    )
    out = run("pack.load", role_ctx("admin"), pack_key="retail_rm")
    assert out.bands_applied is False and "left as they are" in out.bands_note
    from kpigo.scorecards.models import RatingBand

    labels = set(
        RatingBand.objects.filter(org_id=role_ctx().org_id, product="scorecards").values_list(
            "label", flat=True
        )
    )
    assert labels == {"Low", "High"}


def test_a_pack_with_a_manual_metric_loads_it_as_manual_input() -> None:
    from kpigo.metrics.models import Metric

    run("pack.load", role_ctx("admin"), pack_key="retail_service_officer")
    csat = Metric.objects.get(org_id=role_ctx().org_id, metric_code="so_csat")
    assert csat.collection_method == "manual_input"


def test_executive_pack_loads_manual_executive_metrics() -> None:
    from kpigo.metrics.models import Metric

    out = run("pack.load", role_ctx("admin"), pack_key="executive_banking")
    assert "ex_nps" in out.metrics_created
    nps = Metric.objects.get(org_id=role_ctx().org_id, metric_code="ex_nps")
    assert nps.collection_method == "manual_input"


def test_unknown_pack_is_refused() -> None:
    from kpigo.action import InvalidInput

    with pytest.raises(InvalidInput, match="No starter pack"):
        run("pack.load", role_ctx("admin"), pack_key="nope")


def test_name_conflict_leaves_the_clients_metric_alone() -> None:
    from kpigo.metrics.models import Metric

    # Register a metric that collides by name with a pack metric, under a different code.
    run(
        "metric.register",
        role_ctx("admin"),
        display_name="CASA balance growth",
        metric_code="my_casa",
        direction="higher_is_better",
        aggregation="sum",
        unit="currency",
        products=["scorecards"],
    )
    out = run("pack.load", role_ctx("admin"), pack_key="retail_rm")
    assert "rm_casa_growth" in out.name_conflicts
    assert "rm_casa_growth" not in out.metrics_created
    # The client's own metric is untouched; the pack's duplicate was not created.
    assert not Metric.objects.filter(
        org_id=role_ctx().org_id, metric_code="rm_casa_growth"
    ).exists()
    assert Metric.objects.filter(org_id=role_ctx().org_id, metric_code="my_casa").exists()
