"""The spreadsheet import assistant as actions (R6, migration assistant).

A client migrating off Excel uploads a KPI scorecard sheet or a staff roster;
``import.spreadsheet.preview`` parses it, infers the metrics, targets/weights and
subjects it describes (:mod:`kpigo.platform.import_assistant`) and stores them as a
reviewable draft. ``import.draft.apply`` then registers the reviewed metrics and
subjects by invoking the existing ``metric.register`` and ``subject.register``
actions, so every write still passes the permission, approval and audit pipeline —
the assistant proposes, it never bypasses. Targets and weights are proposed and shown
for review; they are loaded through the target workbench once their metrics are active.

The uploaded file is never stored: only its name and a hash, for provenance.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints, field_serializer

from kpigo.action import (
    ActionContext,
    ActionError,
    Conflict,
    InvalidInput,
    NotFound,
    Proposal,
    action,
    invoke,
    registry,
)
from kpigo.ingestion import validator as v
from kpigo.metrics.models import Aggregation, Direction, Unit
from kpigo.platform import import_assistant as engine
from kpigo.platform import powerbi
from kpigo.platform.models import ImportDraft

Filename = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"
ROW_CAP = 50_000


# ── shared output ─────────────────────────────────────────────────────────────


class ImportDraftOut(BaseModel):
    import_id: uuid.UUID
    kind: str
    status: str
    filename: str
    summary: dict[str, int]
    proposals: engine.Proposals
    # Per-kind outcome counts once applied; null until then.
    applied: dict[str, dict[str, int]] | None = None
    created_at: datetime | None = None
    applied_at: datetime | None = None


def _out(row: ImportDraft) -> ImportDraftOut:
    return ImportDraftOut(
        import_id=row.import_id,
        kind=row.kind,
        status=row.status,
        filename=row.filename,
        summary=row.summary,
        proposals=engine.Proposals.model_validate(row.proposals),
        applied=row.applied,
        created_at=row.created_at,
        applied_at=row.applied_at,
    )


# ── import.spreadsheet.preview ──────────────────────────────────────────────


class SpreadsheetFile(BaseModel):
    """A CSV or XLSX workbook, base64-encoded."""

    filename: Filename
    content_base64: str = Field(min_length=1)

    @field_serializer("content_base64", when_used="json")
    def _summarise(self, value: str) -> str:
        """The audit log records what was sent, never the sheet itself."""
        digest = hashlib.sha256(value.encode()).hexdigest()[:16]
        return f"<{len(value)} base64 characters, sha256 {digest}>"


class SpreadsheetPreviewIn(BaseModel):
    file: SpreadsheetFile


def _raw_bytes(content_base64: str) -> bytes:
    limit = int(getattr(settings, "KPIGO_UPLOAD_MAX_BYTES", 50 * 1024 * 1024))
    if len(content_base64) > limit * 4 // 3 + 4:
        raise InvalidInput(f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidInput("The file is not valid base64.") from None


def _store(
    filename: str, data: bytes, proposals: engine.Proposals, ctx: ActionContext
) -> ImportDraft:
    return ImportDraft.objects.create(
        org_id=ctx.org_id,
        filename=filename,
        content_hash=hashlib.sha256(data).hexdigest(),
        kind=proposals.kind,
        proposals=proposals.model_dump(mode="json"),
        summary=proposals.summary(),
        created_by=ctx.user_id,
    )


@action(
    name="import.spreadsheet.preview",
    summary="Read an uploaded KPI sheet or staff roster and propose the config it describes.",
    schema=SpreadsheetPreviewIn,
    output=ImportDraftOut,
    permission="import.manage",
    read_only=False,
    audit="import.previewed",
    example={"file": {"filename": "scorecard.csv", "content_base64": "bWV0cmljLHdlaWdodA=="}},
)
def preview(params: SpreadsheetPreviewIn, ctx: ActionContext) -> ImportDraftOut:
    data = _raw_bytes(params.file.content_base64)
    try:
        table = v.read_file(params.file.filename, data, row_cap=ROW_CAP)
    except (v.RowCapExceeded, v.SourceError) as exc:
        raise InvalidInput(str(exc)) from None
    proposals = engine.propose(table.header, table.rows)
    return _out(_store(params.file.filename, data, proposals, ctx))


# ── import.powerbi.preview ──────────────────────────────────────────────────


class PowerBiFile(BaseModel):
    """A Power BI model: a .pbit template, a model.bim/.json, or a .tmdl (or zipped PBIP)."""

    filename: Filename
    content_base64: str = Field(min_length=1)

    @field_serializer("content_base64", when_used="json")
    def _summarise(self, value: str) -> str:
        digest = hashlib.sha256(value.encode()).hexdigest()[:16]
        return f"<{len(value)} base64 characters, sha256 {digest}>"


class PowerBiPreviewIn(BaseModel):
    file: PowerBiFile


@action(
    name="import.powerbi.preview",
    summary="Read a Power BI model's measures and propose them as metrics to review.",
    schema=PowerBiPreviewIn,
    output=ImportDraftOut,
    permission="import.manage",
    read_only=False,
    audit="import.previewed",
    example={"file": {"filename": "model.bim", "content_base64": "e30="}},
)
def preview_powerbi(params: PowerBiPreviewIn, ctx: ActionContext) -> ImportDraftOut:
    data = _raw_bytes(params.file.content_base64)
    try:
        model = powerbi.parse_model(params.file.filename, data)
    except powerbi.PowerBiError as exc:
        raise InvalidInput(str(exc)) from None
    proposals = engine.propose_powerbi(model.measures, model.tables)
    return _out(_store(params.file.filename, data, proposals, ctx))


# ── import.draft.get / import.draft.list ────────────────────────────────────


class DraftGetIn(BaseModel):
    import_id: uuid.UUID


@action(
    name="import.draft.get",
    summary="One import draft and everything it proposes.",
    schema=DraftGetIn,
    output=ImportDraftOut,
    permission="import.view",
    read_only=True,
    example={"import_id": EXAMPLE_ID},
)
def get(params: DraftGetIn, ctx: ActionContext) -> ImportDraftOut:
    row = ImportDraft.objects.filter(org_id=ctx.org_id, import_id=params.import_id).first()
    if row is None:
        raise NotFound("No such import draft.")
    return _out(row)


class DraftListIn(BaseModel):
    status: Literal["drafted", "applied", "discarded"] | None = None
    kind: Literal["scorecard", "roster", "powerbi", "unknown"] | None = None


class ImportDraftListOut(BaseModel):
    drafts: list[ImportDraftOut]


@action(
    name="import.draft.list",
    summary="Import drafts, newest first.",
    schema=DraftListIn,
    output=ImportDraftListOut,
    permission="import.view",
    read_only=True,
    example={},
)
def list_drafts(params: DraftListIn, ctx: ActionContext) -> ImportDraftListOut:
    query = ImportDraft.objects.filter(org_id=ctx.org_id)
    if params.status:
        query = query.filter(status=params.status)
    if params.kind:
        query = query.filter(kind=params.kind)
    return ImportDraftListOut(drafts=[_out(r) for r in query.order_by("-created_at")[:50]])


# ── import.draft.apply ──────────────────────────────────────────────────────


class ImportApplyOutcome(BaseModel):
    kind: str  # "metric" or "subject"
    ref: str  # the metric_code or staff number
    outcome: str  # registered | exists | pending_approval | failed
    detail: str = ""


class ImportApplyOut(BaseModel):
    draft: ImportDraftOut
    outcomes: list[ImportApplyOutcome]


class MetricEdit(BaseModel):
    """A reviewer's corrections to one proposed metric, keyed by its source row.

    Only the fields the reviewer changed are set; the rest fall back to what the
    assistant proposed. ``include=False`` leaves the metric out of the import.
    """

    source_row: int
    include: bool = True
    display_name: str | None = None
    metric_code: str | None = None
    direction: Direction | None = None
    aggregation: Aggregation | None = None
    unit: Unit | None = None
    is_percentage: bool | None = None
    decimal_places: int | None = Field(default=None, ge=0, le=6)
    description: str | None = Field(default=None, max_length=4000)


class SubjectEdit(BaseModel):
    """A reviewer's corrections to one proposed subject, keyed by its source row."""

    source_row: int
    include: bool = True
    staff_no: str | None = None
    full_name: str | None = None
    email: str | None = None


class DraftApplyIn(BaseModel):
    import_id: uuid.UUID
    # The reviewer's corrections, keyed by source row. Rows with no edit apply as
    # proposed; an empty list applies the whole draft verbatim.
    metrics: list[MetricEdit] = Field(default_factory=list)
    subjects: list[SubjectEdit] = Field(default_factory=list)


def _apply_one(name: str, payload: dict[str, Any], ctx: ActionContext) -> tuple[str, str]:
    """Invoke one registered action for the apply; (outcome, detail). Never raises."""
    try:
        with transaction.atomic():  # a savepoint, so one failure does not undo the rest
            result = invoke(registry.get(name), payload, ctx)
    except Conflict as exc:
        return "exists", exc.message
    except ActionError as exc:
        return "failed", exc.message
    if isinstance(result, Proposal):
        # Maker-checker is on for this change class: it is queued, not yet applied.
        return "pending_approval", "Submitted for approval."
    return "registered", ""


@action(
    name="import.draft.apply",
    summary="Register the reviewed metrics and subjects from an import draft.",
    schema=DraftApplyIn,
    output=ImportApplyOut,
    permission="import.manage",
    read_only=False,
    audit="import.applied",
    example={"import_id": EXAMPLE_ID},
)
def apply(params: DraftApplyIn, ctx: ActionContext) -> ImportApplyOut:
    row = (
        ImportDraft.objects.select_for_update()
        .filter(org_id=ctx.org_id, import_id=params.import_id)
        .first()
    )
    if row is None:
        raise NotFound("No such import draft.")
    if row.status != "drafted":
        raise Conflict(f"This import is already {row.status}.")
    proposals = engine.Proposals.model_validate(row.proposals)
    metric_edits = {e.source_row: e for e in params.metrics}
    subject_edits = {e.source_row: e for e in params.subjects}

    outcomes: list[ImportApplyOutcome] = []
    counts: dict[str, dict[str, int]] = {}

    def tally(kind: str, outcome: str) -> None:
        counts.setdefault(kind, {})[outcome] = counts.setdefault(kind, {}).get(outcome, 0) + 1

    for m in proposals.metrics:
        edit = metric_edits.get(m.source_row)
        if edit is not None and not edit.include:
            continue  # the reviewer unticked this metric
        code = edit.metric_code if edit and edit.metric_code else m.metric_code
        outcome, detail = _apply_one(
            "metric.register",
            {
                "display_name": edit.display_name if edit and edit.display_name else m.display_name,
                "metric_code": code,
                "description": edit.description
                if edit and edit.description is not None
                else m.description,
                "direction": edit.direction if edit and edit.direction else m.direction,
                "aggregation": edit.aggregation if edit and edit.aggregation else m.aggregation,
                "unit": edit.unit if edit and edit.unit else m.unit,
                "decimal_places": edit.decimal_places
                if edit and edit.decimal_places is not None
                else m.decimal_places,
                "is_percentage": edit.is_percentage
                if edit and edit.is_percentage is not None
                else m.is_percentage,
                "products": ["scorecards"],
                "status": "draft",
                # The Admin reviewed the proposal list, so near-duplicate names are expected.
                "acknowledge_similar": True,
            },
            ctx,
        )
        outcomes.append(ImportApplyOutcome(kind="metric", ref=code, outcome=outcome, detail=detail))
        tally("metrics", outcome)

    for s in proposals.subjects:
        sedit = subject_edits.get(s.source_row)
        if sedit is not None and not sedit.include:
            continue  # the reviewer unticked this person
        staff_no = sedit.staff_no if sedit and sedit.staff_no else s.staff_no
        full_name = sedit.full_name if sedit and sedit.full_name else s.full_name
        email = sedit.email if sedit and sedit.email is not None else s.email
        if not email:
            # A subject needs a real email to be registered; the roster had none for them.
            outcome, detail = "failed", "A valid email address is needed to register this person."
        else:
            outcome, detail = _apply_one(
                "subject.register",
                {"staff_no": staff_no, "full_name": full_name, "email": email},
                ctx,
            )
        outcomes.append(
            ImportApplyOutcome(kind="subject", ref=staff_no, outcome=outcome, detail=detail)
        )
        tally("subjects", outcome)

    row.status = "applied"
    row.applied = counts
    row.applied_at = timezone.now()
    row.applied_by = ctx.user_id
    row.save(update_fields=["status", "applied", "applied_at", "applied_by"])
    return ImportApplyOut(draft=_out(row), outcomes=outcomes)


# ── import.draft.discard ────────────────────────────────────────────────────


class DraftDiscardIn(BaseModel):
    import_id: uuid.UUID


@action(
    name="import.draft.discard",
    summary="Discard an import draft without applying it.",
    schema=DraftDiscardIn,
    output=ImportDraftOut,
    permission="import.manage",
    read_only=False,
    audit="import.discarded",
    example={"import_id": EXAMPLE_ID},
)
def discard(params: DraftDiscardIn, ctx: ActionContext) -> ImportDraftOut:
    row = ImportDraft.objects.filter(org_id=ctx.org_id, import_id=params.import_id).first()
    if row is None:
        raise NotFound("No such import draft.")
    if row.status != "drafted":
        raise Conflict(f"This import is already {row.status}.")
    row.status = "discarded"
    row.save(update_fields=["status"])
    return _out(row)
