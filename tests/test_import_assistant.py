"""The spreadsheet import assistant (R6): a client's own KPI sheet or roster becomes
a reviewable draft, then real metrics and subjects through the existing actions.

The pure engine is tested directly on headers and rows; the actions are driven through
the pipeline with ``run``, so the permission, approval and audit path is the UI's own.
"""

import base64
import io
import json
import zipfile
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import User

from kpigo.action.identity import build_context
from kpigo.hierarchy.models import Subject
from kpigo.metrics.models import Metric
from kpigo.platform import import_assistant as engine
from kpigo.platform import powerbi
from kpigo.platform.models import AuditLog, ImportDraft
from tests.conftest import ORG_ID, run

pytestmark = pytest.mark.django_db


def sheet(rows: list[list[str]]) -> dict[str, str]:
    """A CSV file argument from a list of rows (the first is the header)."""
    body = "\n".join(",".join(cell for cell in row) for row in rows)
    return {"filename": "scorecard.csv", "content_base64": base64.b64encode(body.encode()).decode()}


SCORECARD = [
    ["KPI", "Weight", "Target", "Unit", "Direction"],
    ["Total Deposits", "40", "GHS 5,000,000", "currency", "higher is better"],
    ["Cost to Income Ratio", "30", "45%", "%", ""],
    ["Customer Complaints", "30", "12", "count", ""],
]
ROSTER = [
    ["Staff No", "Full Name", "Email"],
    ["E1001", "Ama Mensah", "ama.mensah@bank.example"],
    ["E1002", "Kofi Owusu", "kofi.owusu@bank.example"],
]


# ── the pure engine ───────────────────────────────────────────────────────────


def test_engine_detects_a_scorecard_and_infers_metrics() -> None:
    p = engine.propose(SCORECARD[0], [tuple(r) for r in SCORECARD[1:]])
    assert p.kind == "scorecard"
    assert p.mapping["metric_name"] == "KPI"
    by_name = {m.display_name: m for m in p.metrics}
    deposits = by_name["Total Deposits"]
    assert deposits.unit == "currency" and deposits.direction == "higher_is_better"
    assert deposits.metric_code == "total_deposits"
    # The unit column was read, so unit is not in the inferred list for this row.
    assert "unit" not in deposits.inferred
    # A cost/complaints name with no direction column is inferred lower_is_better.
    assert by_name["Cost to Income Ratio"].direction == "lower_is_better"
    assert "direction" in by_name["Cost to Income Ratio"].inferred
    assert by_name["Cost to Income Ratio"].unit == "percent"
    assert by_name["Customer Complaints"].direction == "lower_is_better"
    # Targets and weights are carried as proposals.
    deposit_target = next(t for t in p.targets if t.metric_code == "total_deposits")
    assert deposit_target.target_value == "5000000" and deposit_target.weight == "40"


def test_engine_warns_when_weights_do_not_total_100() -> None:
    rows = [["KPI", "Weight"], ["A", "50"], ["B", "30"]]
    p = engine.propose(rows[0], [tuple(r) for r in rows[1:]])
    assert any("add up to 80" in w for w in p.warnings)


def test_engine_makes_unique_codes_for_repeated_names() -> None:
    rows = [["Metric", "Target"], ["Sales", "1"], ["Sales", "2"]]
    p = engine.propose(rows[0], [tuple(r) for r in rows[1:]])
    assert [m.metric_code for m in p.metrics] == ["sales", "sales_2"]


def test_engine_detects_a_roster() -> None:
    p = engine.propose(ROSTER[0], [tuple(r) for r in ROSTER[1:]])
    assert p.kind == "roster"
    assert [s.staff_no for s in p.subjects] == ["E1001", "E1002"]
    assert p.subjects[0].email == "ama.mensah@bank.example"


def test_engine_explains_an_unreadable_sheet() -> None:
    p = engine.propose(["colour", "shape"], [("red", "round")])
    assert p.kind == "unknown"
    assert not p.metrics and not p.subjects
    assert "could not tell" in p.message and "colour" in p.message


# ── the actions ────────────────────────────────────────────────────────────────


def test_preview_stores_a_draft_and_hides_the_file(make_user: Callable[..., User]) -> None:
    make_user("admin")
    out = run("import.spreadsheet.preview", file=sheet(SCORECARD))
    assert out.kind == "scorecard"
    assert out.status == "drafted"
    assert out.summary["metrics"] == 3
    assert ImportDraft.objects.filter(import_id=out.import_id).exists()
    # The uploaded bytes are never in the audit row: only a hash of what was sent.
    row = AuditLog.objects.filter(action_name="import.spreadsheet.preview").latest("occurred_at")
    assert "base64 characters" in str(row.payload["params"]["file"]["content_base64"])


def test_preview_rejects_a_non_spreadsheet(make_user: Callable[..., User]) -> None:
    from kpigo.action import InvalidInput

    make_user("admin")
    bad = {"filename": "notes.txt", "content_base64": base64.b64encode(b"hello").decode()}
    with pytest.raises(InvalidInput):
        run("import.spreadsheet.preview", file=bad)


def test_apply_registers_draft_metrics_through_the_metric_action(
    make_user: Callable[..., User],
) -> None:
    make_user("admin")
    draft = run("import.spreadsheet.preview", file=sheet(SCORECARD))
    out = run("import.draft.apply", import_id=draft.import_id)
    assert out.draft.status == "applied"
    assert out.draft.applied["metrics"]["registered"] == 3
    metrics = {m.metric_code: m for m in Metric.objects.filter(org_id=ORG_ID)}
    assert metrics["total_deposits"].status == "draft"
    assert metrics["total_deposits"].direction == "higher_is_better"
    # Every registration went through metric.register's own audited action.
    assert AuditLog.objects.filter(event="metric.registered").count() == 3


def test_apply_registers_roster_subjects(make_user: Callable[..., User]) -> None:
    make_user("admin")
    draft = run("import.spreadsheet.preview", file=sheet(ROSTER))
    out = run("import.draft.apply", import_id=draft.import_id)
    assert out.draft.applied["subjects"]["registered"] == 2
    assert Subject.objects.filter(org_id=ORG_ID).count() == 2


def test_apply_reports_a_duplicate_as_exists_not_failed(make_user: Callable[..., User]) -> None:
    make_user("admin")
    # Register one metric up front; the import proposes the same code.
    run(
        "metric.register",
        display_name="Total Deposits",
        direction="higher_is_better",
        aggregation="sum",
        unit="currency",
        products=["scorecards"],
    )
    draft = run("import.spreadsheet.preview", file=sheet(SCORECARD))
    out = run("import.draft.apply", import_id=draft.import_id)
    deposits = next(o for o in out.outcomes if o.ref == "total_deposits")
    assert deposits.outcome == "exists"
    assert out.draft.applied["metrics"]["exists"] == 1
    assert out.draft.applied["metrics"]["registered"] == 2


def test_apply_is_refused_twice(make_user: Callable[..., User]) -> None:
    from kpigo.action import Conflict

    make_user("admin")
    draft = run("import.spreadsheet.preview", file=sheet(ROSTER))
    run("import.draft.apply", import_id=draft.import_id)
    with pytest.raises(Conflict):
        run("import.draft.apply", import_id=draft.import_id)


def test_discard_stops_an_apply(make_user: Callable[..., User]) -> None:
    from kpigo.action import Conflict

    make_user("admin")
    draft = run("import.spreadsheet.preview", file=sheet(SCORECARD))
    discarded = run("import.draft.discard", import_id=draft.import_id)
    assert discarded.status == "discarded"
    with pytest.raises(Conflict):
        run("import.draft.apply", import_id=draft.import_id)
    assert not Metric.objects.filter(org_id=ORG_ID).exists()


def test_apply_honours_reviewer_edits(make_user: Callable[..., User]) -> None:
    make_user("admin")
    draft = run("import.spreadsheet.preview", file=sheet(SCORECARD))
    out = run(
        "import.draft.apply",
        import_id=draft.import_id,
        metrics=[
            # Row 1 (Total Deposits): rename its code and keep the rest as proposed.
            {"source_row": 1, "metric_code": "deposits_total"},
            # Row 2 (Cost to Income Ratio): the reviewer corrects the inferred unit.
            {"source_row": 2, "unit": "count"},
            # Row 3 (Customer Complaints): leave it out of the import entirely.
            {"source_row": 3, "include": False},
        ],
    )
    assert out.draft.applied["metrics"]["registered"] == 2
    metrics = {m.metric_code: m for m in Metric.objects.filter(org_id=ORG_ID)}
    assert set(metrics) == {"deposits_total", "cost_to_income_ratio"}
    assert metrics["cost_to_income_ratio"].unit == "count"


def test_apply_edit_supplies_a_missing_subject_email(make_user: Callable[..., User]) -> None:
    make_user("admin")
    roster = [["Staff No", "Full Name", "Email"], ["E1001", "Ama Mensah", ""]]
    draft = run("import.spreadsheet.preview", file=sheet(roster))
    # The roster had no email for this person; without one they cannot be registered.
    assert draft.proposals.subjects[0].email == ""
    out = run(
        "import.draft.apply",
        import_id=draft.import_id,
        subjects=[{"source_row": 1, "email": "ama.mensah@bank.example"}],
    )
    assert out.draft.applied["subjects"]["registered"] == 1
    assert Subject.objects.filter(org_id=ORG_ID, email="ama.mensah@bank.example").exists()


# A realistic retail-bank RM scorecard with only names, weights and targets — no unit or
# direction column — so every unit and direction is inferred. Scope §17.3 expects the
# assistant to auto-convert about 70% of such a sheet without correction; this pins that.
BANK_SCORECARD = [
    ["Metric", "Weight", "Target"],
    ["Total Deposits", "15", "5,000,000"],
    ["Loan Disbursement", "10", "2,000,000"],
    ["CASA Ratio", "10", "35%"],
    ["Cost to Income Ratio", "10", "48%"],
    ["NPL Ratio", "10", "5%"],
    ["Customer Complaints", "5", "10"],
    ["Account Opening TAT", "5", "2"],
    ["Customer Satisfaction Score", "10", "4.5"],
    ["Cross-Sell Ratio", "5", "2.5"],
    ["New Accounts Opened", "5", "150"],
    ["Portfolio at Risk", "5", "4%"],
    ["Digital Adoption Rate", "10", "60%"],
]
# What a bank analyst would confirm for each (direction, unit).
BANK_EXPECTED = {
    "Total Deposits": ("higher_is_better", "currency"),
    "Loan Disbursement": ("higher_is_better", "currency"),
    "CASA Ratio": ("higher_is_better", "percent"),
    "Cost to Income Ratio": ("lower_is_better", "percent"),
    "NPL Ratio": ("lower_is_better", "percent"),
    "Customer Complaints": ("lower_is_better", "count"),
    "Account Opening TAT": ("lower_is_better", "days"),
    "Customer Satisfaction Score": ("higher_is_better", "score"),
    "Cross-Sell Ratio": ("higher_is_better", "count"),
    "New Accounts Opened": ("higher_is_better", "count"),
    "Portfolio at Risk": ("lower_is_better", "percent"),
    "Digital Adoption Rate": ("higher_is_better", "percent"),
}


def test_engine_auto_converts_about_seventy_percent_of_a_real_sheet() -> None:
    p = engine.propose(BANK_SCORECARD[0], [tuple(r) for r in BANK_SCORECARD[1:]])
    assert p.kind == "scorecard"
    assert len(p.metrics) == len(BANK_EXPECTED)
    fully_correct = sum(
        1 for m in p.metrics if (m.direction, m.unit) == BANK_EXPECTED[m.display_name]
    )
    ratio = fully_correct / len(p.metrics)
    # Scope §17.3's ~70%: at least that share needs no correction at all.
    assert ratio >= 0.70, f"only {fully_correct}/{len(p.metrics)} inferred correctly"


def test_apply_surfaces_pending_approval_when_maker_checker_is_on(
    make_user: Callable[..., User], settings: Any
) -> None:
    settings.KPIGO_APPROVAL_CLASSES_ENABLED = ["metric_change"]
    make_user("admin")
    draft = run("import.spreadsheet.preview", file=sheet(SCORECARD))
    out = run("import.draft.apply", import_id=draft.import_id)
    assert out.draft.applied["metrics"]["pending_approval"] == 3
    # Nothing is registered yet; the changes wait in the approval queue.
    assert not Metric.objects.filter(org_id=ORG_ID).exists()


def test_a_non_admin_cannot_use_the_assistant(make_user: Callable[..., User]) -> None:
    from kpigo.action import PermissionDenied

    staff = make_user("staff")
    ctx = build_context(staff, caller="http")
    with pytest.raises(PermissionDenied):
        run("import.spreadsheet.preview", ctx, file=sheet(SCORECARD))


def test_list_filters_by_status_and_kind(make_user: Callable[..., User]) -> None:
    make_user("admin")
    run("import.spreadsheet.preview", file=sheet(SCORECARD))
    run("import.spreadsheet.preview", file=sheet(ROSTER))
    everything = run("import.draft.list")
    assert len(everything.drafts) == 2
    rosters = run("import.draft.list", kind="roster")
    assert [d.kind for d in rosters.drafts] == ["roster"]


# ── Power BI import (increment 4) ───────────────────────────────────────────

TMSL = {
    "model": {
        "tables": [
            {
                "name": "Sales",
                "measures": [
                    {
                        "name": "Total Revenue",
                        "expression": "SUM(Sales[Amt])",
                        "formatString": "\\$#,##0",
                    },
                    {
                        "name": "Cost to Income",
                        "expression": "DIVIDE([Cost],[Income])",
                        "formatString": "0.0%",
                    },
                    {"name": "Helper", "expression": "1", "isHidden": True},
                ],
            },
            {"name": "Calendar", "measures": []},
        ]
    }
}

TMDL_TEXT = (
    "table 'Sales'\n"
    "\tmeasure 'Total Revenue' = SUM('Sales'[Amt])\n"
    "\t\tformatString: \\$#,##0\n"
    "\tmeasure 'Cost to Income' =\n"
    "\t\t\tDIVIDE([Cost], [Income])\n"
    "\t\tformatString: 0.0%\n"
)


def bim_file() -> dict[str, str]:
    raw = json.dumps(TMSL).encode()
    return {"filename": "model.bim", "content_base64": base64.b64encode(raw).decode()}


def pbit_file() -> dict[str, str]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("DataModelSchema", json.dumps(TMSL).encode("utf-16"))
        archive.writestr("Report/Layout", b"{}")
    return {
        "filename": "report.pbit",
        "content_base64": base64.b64encode(buffer.getvalue()).decode(),
    }


def tmdl_file() -> dict[str, str]:
    return {
        "filename": "sales.tmdl",
        "content_base64": base64.b64encode(TMDL_TEXT.encode()).decode(),
    }


def test_powerbi_parses_a_bim_model() -> None:
    model = powerbi.parse_model("model.bim", json.dumps(TMSL).encode())
    names = {m.name for m in model.measures}
    assert names == {"Total Revenue", "Cost to Income", "Helper"}
    assert "Sales" in model.tables


def test_powerbi_parses_a_pbit_zip() -> None:
    raw = base64.b64decode(pbit_file()["content_base64"])
    model = powerbi.parse_model("report.pbit", raw)
    assert model.source == "pbit"
    assert {m.name for m in model.measures} >= {"Total Revenue", "Cost to Income"}


def test_powerbi_parses_tmdl_text() -> None:
    measures = {m.name: m for m in powerbi.parse_model("sales.tmdl", TMDL_TEXT.encode()).measures}
    assert set(measures) == {"Total Revenue", "Cost to Income"}
    assert measures["Cost to Income"].format_string == "0.0%"


def test_powerbi_rejects_a_pbix() -> None:
    with pytest.raises(powerbi.PowerBiError, match="template"):
        powerbi.parse_model("report.pbix", b"PK\x03\x04nope")


def test_engine_proposes_metrics_from_measures() -> None:
    model = powerbi.parse_model("model.bim", json.dumps(TMSL).encode())
    p = engine.propose_powerbi(model.measures, model.tables)
    assert p.kind == "powerbi"
    by_name = {m.display_name: m for m in p.metrics}
    # The hidden helper is not proposed.
    assert set(by_name) == {"Total Revenue", "Cost to Income"}
    assert by_name["Total Revenue"].unit == "currency"  # from the $ formatString
    assert by_name["Cost to Income"].unit == "percent"  # from the % formatString
    assert by_name["Cost to Income"].direction == "lower_is_better"  # "cost" in the name
    # The DAX is carried into the metric's note for the reviewer.
    assert "DAX" in by_name["Total Revenue"].description


def test_powerbi_preview_and_apply(make_user: Callable[..., User]) -> None:
    make_user("admin")
    draft = run("import.powerbi.preview", file=bim_file())
    assert draft.kind == "powerbi"
    assert draft.summary["metrics"] == 2
    out = run("import.draft.apply", import_id=draft.import_id)
    assert out.draft.applied["metrics"]["registered"] == 2
    metrics = {m.metric_code: m for m in Metric.objects.filter(org_id=ORG_ID)}
    assert metrics["total_revenue"].status == "draft"
    assert metrics["cost_to_income"].unit == "percent"


def test_powerbi_preview_accepts_a_pbit(make_user: Callable[..., User]) -> None:
    make_user("admin")
    draft = run("import.powerbi.preview", file=pbit_file())
    assert draft.summary["metrics"] == 2


def test_powerbi_preview_rejects_a_pbix(make_user: Callable[..., User]) -> None:
    from kpigo.action import InvalidInput

    make_user("admin")
    pbix = {"filename": "r.pbix", "content_base64": base64.b64encode(b"nope").decode()}
    with pytest.raises(InvalidInput, match="template"):
        run("import.powerbi.preview", file=pbix)
