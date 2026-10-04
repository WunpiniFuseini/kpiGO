"""Metric registry (PRD MR-1 … MR-9): uniqueness, similarity, versioning, bindings."""

import io
import json
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client

from kpigo.action import Conflict, InvalidInput, NotFound, PermissionDenied
from kpigo.metrics.models import Metric, MetricBinding, MetricFamily, MetricProfileAssignment
from kpigo.metrics.naming import normalise_name
from kpigo.platform.config import config_version
from kpigo.platform.models import AuditLog
from tests.conftest import ORG_ID, role_ctx, run

pytestmark = pytest.mark.django_db

DEPOSITS: dict[str, Any] = {
    "display_name": "Total Deposits",
    "direction": "higher_is_better",
    "aggregation": "sum",
    "unit": "currency",
    "decimal_places": 2,
    "products": ["scorecards", "executive"],
    "effective_from": "2026-01-01",
}


def register(**overrides: Any) -> Any:
    return run("metric.register", **{**DEPOSITS, **overrides})


def test_normalised_name_folds_case_punctuation_and_accents() -> None:
    assert normalise_name("  Total Deposits (GHS)! ") == "total deposits ghs"
    assert normalise_name("Café  Sales & Fees") == "cafe sales and fees"


def test_register_creates_family_metric_and_bindings() -> None:
    before = config_version(ORG_ID)
    out = register(computation_note="Month-end balance.")
    assert out.family.normalised_name == "total deposits"
    (metric,) = out.metrics
    assert metric.metric_code == "total_deposits"
    assert metric.status == "active"
    assert metric.target_scope == "profile"
    assert metric.collection_method == "feed"
    assert metric.effective_from == date(2026, 1, 1)
    assert metric.effective_to is None
    assert [b.product for b in metric.bindings] == ["executive", "scorecards"]
    assert config_version(ORG_ID) == before + 1
    audit = AuditLog.objects.get(event="metric.registered")
    assert audit.payload["config_version"] == before + 1


def test_duplicate_normalised_name_is_blocked() -> None:
    register()
    with pytest.raises(Conflict, match="already exists"):
        register(display_name="total-deposits", metric_code="other_code")
    assert MetricFamily.objects.count() == 1


def test_database_refuses_duplicate_family_name() -> None:
    register()
    with pytest.raises(IntegrityError), transaction.atomic():
        MetricFamily.objects.create(
            org_id=ORG_ID, display_name="x", normalised_name="total deposits"
        )


def test_similar_name_returns_comparison_panel_until_acknowledged() -> None:
    register()
    with pytest.raises(Conflict) as exc:
        register(display_name="Total Deposit", metric_code="total_deposit")
    (similar,) = exc.value.detail["similar"]
    assert similar["display_name"] == "Total Deposits"
    assert similar["similarity"] >= 0.45
    assert similar["metrics"][0]["metric_code"] == "total_deposits"
    assert similar["metrics"][0]["products"] == ["executive", "scorecards"]

    out = register(
        display_name="Total Deposit", metric_code="total_deposit", acknowledge_similar=True
    )
    assert [s.display_name for s in out.acknowledged_similar] == ["Total Deposits"]
    assert AuditLog.objects.filter(event="metric.similar_acknowledged").count() == 1


def test_unrelated_name_is_not_flagged() -> None:
    register()
    out = register(display_name="Loan disbursement count", unit="count", metric_code=None)
    assert out.acknowledged_similar == []


def test_check_name_reports_exact_and_similar() -> None:
    register()
    exact = run("metric.check_name", display_name="TOTAL deposits")
    assert exact.exact is not None and exact.exact.display_name == "Total Deposits"
    near = run("metric.check_name", display_name="Total deposit balance")
    assert near.exact is None
    assert [s.normalised_name for s in near.similar] == ["total deposits"]


def test_manual_input_only_for_scorecards_and_executive() -> None:
    with pytest.raises(InvalidInput, match="Scorecards and Executive"):
        register(collection_method="manual_input", products=["scorecards", "agent_sales"])
    out = register(collection_method="manual_input", products=["scorecards"])
    with pytest.raises(InvalidInput):
        run("metric.binding.set", metric_code=out.metrics[0].metric_code, product="campaign")


def test_fork_per_product_creates_one_metric_per_product() -> None:
    out = register(fork_per_product=True)
    assert sorted(m.metric_code for m in out.metrics) == [
        "total_deposits_executive",
        "total_deposits_scorecards",
    ]
    assert {m.family_id for m in out.metrics} == {out.family.family_id}
    assert all(len(m.bindings) == 1 for m in out.metrics)


def test_metric_code_cannot_be_reused_by_another_family() -> None:
    register()
    with pytest.raises(Conflict, match="code already in use"):
        register(display_name="Something else", metric_code="total_deposits")


# ── MR-7: definitional changes open a new effective period ─────────────────


def test_definitional_change_opens_a_new_effective_period() -> None:
    first = register().metrics[0]
    run(
        "metric.profile.assign",
        metric_code="total_deposits",
        profile_code="retail_rm",
        product="scorecards",
        effective_from="2026-01-01",
    )
    out = run(
        "metric.update",
        metric_code="total_deposits",
        aggregation="latest",
        effective_from="2026-07-01",
    )
    assert out.new_period is True
    assert out.superseded.metric_id == first.metric_id
    assert out.superseded.effective_to == date(2026, 7, 1)
    assert out.superseded.aggregation == "sum"
    new = out.metric
    assert new.metric_id != first.metric_id
    assert new.metric_code == "total_deposits"
    assert new.aggregation == "latest"
    assert new.effective_from == date(2026, 7, 1)
    assert new.supersedes_id == first.metric_id
    assert [b.product for b in new.bindings] == ["executive", "scorecards"]
    # The profile assignment carries over; the old one ends with the old period.
    assert [(p.profile_code, p.effective_from) for p in new.profiles] == [
        ("retail_rm", date(2026, 7, 1))
    ]
    old_profile = MetricProfileAssignment.objects.get(metric_id=first.metric_id)
    assert old_profile.effective_to == date(2026, 7, 1)

    history = run("metric.get", metric_code="total_deposits")
    assert [(p.aggregation, p.effective_from, p.effective_to) for p in history.periods] == [
        ("sum", date(2026, 1, 1), date(2026, 7, 1)),
        ("latest", date(2026, 7, 1), None),
    ]
    # Historical reads resolve to the definition in force at the time.
    june = run("metric.list", as_of="2026-06-30").metrics
    july = run("metric.list", as_of="2026-07-01").metrics
    assert [m.aggregation for m in june] == ["sum"]
    assert [m.aggregation for m in july] == ["latest"]
    assert AuditLog.objects.filter(event="metric.period_opened").count() == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("direction", "lower_is_better"), ("unit", "count"), ("target_scope", "subject")],
)
def test_each_definitional_field_versions(field: str, value: str) -> None:
    register()
    out = run(
        "metric.update", metric_code="total_deposits", effective_from="2026-03-01", **{field: value}
    )
    assert out.new_period is True
    assert getattr(out.metric, field) == value
    assert Metric.objects.filter(metric_code="total_deposits").count() == 2


def test_definitional_change_cannot_rewrite_history() -> None:
    register()
    with pytest.raises(Conflict, match="history is never rewritten"):
        run(
            "metric.update", metric_code="total_deposits", unit="count", effective_from="2026-01-01"
        )
    with pytest.raises(Conflict):
        run(
            "metric.update", metric_code="total_deposits", unit="count", effective_from="2025-12-01"
        )
    assert Metric.objects.count() == 1


def test_non_definitional_change_edits_in_place() -> None:
    first = register().metrics[0]
    out = run(
        "metric.update", metric_code="total_deposits", display_name="Deposits", decimal_places=0
    )
    assert out.new_period is False
    assert out.metric.metric_id == first.metric_id
    assert (out.metric.display_name, out.metric.decimal_places) == ("Deposits", 0)
    with pytest.raises(InvalidInput, match="effective_from applies only"):
        run(
            "metric.update",
            metric_code="total_deposits",
            display_name="Deposits 2",
            effective_from="2026-05-01",
        )


def test_draft_metric_definitional_change_is_in_place() -> None:
    first = register(status="draft").metrics[0]
    out = run("metric.update", metric_code="total_deposits", unit="count")
    assert out.new_period is False
    assert out.metric.metric_id == first.metric_id
    assert out.metric.unit == "count"


def test_database_refuses_overlapping_periods_for_a_code() -> None:
    first = register().metrics[0]
    with pytest.raises(IntegrityError), transaction.atomic():
        Metric.objects.create(
            org_id=ORG_ID,
            family_id=first.family_id,
            metric_code="total_deposits",
            display_name="x",
            direction="higher_is_better",
            aggregation="sum",
            unit="count",
            effective_from=date(2026, 6, 1),
        )


def test_update_with_nothing_to_change_is_invalid() -> None:
    register()
    with pytest.raises(InvalidInput, match="Nothing to change"):
        run("metric.update", metric_code="total_deposits", unit="currency")


def test_update_unknown_metric_is_not_found() -> None:
    with pytest.raises(NotFound):
        run("metric.update", metric_code="nope", unit="count")


# ── MR-6: status, never deletion ────────────────────────────────────────────


def test_status_lifecycle() -> None:
    register()
    assert run("metric.set_status", metric_code="total_deposits", status="inactive").status == (
        "inactive"
    )
    assert run("metric.set_status", metric_code="total_deposits", status="active").status == (
        "active"
    )
    with pytest.raises(Conflict, match="cannot become deprecated"):
        run("metric.set_status", metric_code="total_deposits", status="deprecated")
    run("metric.set_status", metric_code="total_deposits", status="inactive")
    run("metric.set_status", metric_code="total_deposits", status="deprecated")
    with pytest.raises(Conflict):
        run("metric.set_status", metric_code="total_deposits", status="active")
    assert Metric.objects.count() == 1


def test_binding_set_toggles_and_adds() -> None:
    register()
    out = run(
        "metric.binding.set", metric_code="total_deposits", product="executive", is_active=False
    )
    assert {b.product: b.is_active for b in out.bindings} == {
        "executive": False,
        "scorecards": True,
    }
    out = run("metric.binding.set", metric_code="total_deposits", product="agent_sales")
    assert MetricBinding.objects.count() == 3
    listed = run("metric.list", product="executive", as_of="2026-02-01").metrics
    assert listed == []


def test_profile_assignment_requires_binding_and_refuses_overlap() -> None:
    register()
    with pytest.raises(Conflict, match="not bound"):
        run(
            "metric.profile.assign",
            metric_code="total_deposits",
            profile_code="teller",
            product="agent_service",
            effective_from="2026-02-01",
        )
    run(
        "metric.profile.assign",
        metric_code="total_deposits",
        profile_code="teller",
        product="scorecards",
        effective_from="2026-02-01",
    )
    with pytest.raises(Conflict, match="overlapping"):
        run(
            "metric.profile.assign",
            metric_code="total_deposits",
            profile_code="teller",
            product="scorecards",
            effective_from="2026-03-01",
        )


def test_dry_run_registers_nothing() -> None:
    ctx = role_ctx(dry_run=True)
    out = run("metric.register", ctx, **DEPOSITS)
    assert out.metrics[0].metric_code == "total_deposits"
    assert MetricFamily.objects.count() == 0
    assert config_version(ORG_ID) == 0


def test_metric_owner_can_register_but_staff_cannot() -> None:
    register()
    run("metric.list", role_ctx("staff"))
    with pytest.raises(PermissionDenied):
        run("metric.register", role_ctx("staff"), **{**DEPOSITS, "display_name": "Fees"})
    run("metric.register", role_ctx("metric_owner"), **{**DEPOSITS, "display_name": "Loan count"})


# ── R0 exit criterion: a registered metric appears over HTTP, CLI and the registry ─


def test_metric_registered_over_http_appears_via_cli_and_registry(
    make_user: Callable[..., User],
) -> None:
    admin = make_user("admin", username="ops")
    client = Client()
    client.force_login(admin)
    response = client.post(
        "/api/v1/actions/metric.register", DEPOSITS, content_type="application/json"
    )
    assert response.status_code == 200, response.content
    assert response.json()["metrics"][0]["metric_code"] == "total_deposits"

    over_http = client.get("/api/v1/actions/metric.get", {"metric_code": "total_deposits"})
    assert over_http.status_code == 200
    assert over_http.json()["family"]["display_name"] == "Total Deposits"

    out = io.StringIO()
    call_command("action", "metric.list", "--user", "ops", "--as-of", "2026-02-01", stdout=out)
    assert [m["metric_code"] for m in json.loads(out.getvalue())["metrics"]] == ["total_deposits"]

    registry = client.get("/api/v1/registry", {"module": "platform"}).json()
    register_info = next(a for a in registry["actions"] if a["name"] == "metric.register")
    assert register_info["permission"] == "metric.manage"
    assert register_info["config_change"] is True
    assert "products" in register_info["input_schema"]["properties"]
