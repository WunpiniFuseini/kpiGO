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
from kpigo.platform import import_assistant as engine
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
    created_at: Any = None
    applied_at: Any = None


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


def _decode(file: SpreadsheetFile) -> tuple[v.RawTable, str]:
    limit = int(getattr(settings, "KPIGO_UPLOAD_MAX_BYTES", 50 * 1024 * 1024))
    if len(file.content_base64) > limit * 4 // 3 + 4:
        raise InvalidInput(f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
    try:
        data = base64.b64decode(file.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidInput("The file is not valid base64.") from None
    try:
        table = v.read_file(file.filename, data, row_cap=ROW_CAP)
    except v.RowCapExceeded as exc:
        raise InvalidInput(str(exc)) from None
    except v.SourceError as exc:
        raise InvalidInput(str(exc)) from None
    return table, hashlib.sha256(data).hexdigest()


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
    table, content_hash = _decode(params.file)
    proposals = engine.propose(table.header, table.rows)
    row = ImportDraft.objects.create(
        org_id=ctx.org_id,
        filename=params.file.filename,
        content_hash=content_hash,
        kind=proposals.kind,
        proposals=proposals.model_dump(mode="json"),
        summary=proposals.summary(),
        created_by=ctx.user_id,
    )
    return _out(row)


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
    kind: Literal["scorecard", "roster", "unknown"] | None = None


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


class DraftApplyIn(BaseModel):
    import_id: uuid.UUID


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

    outcomes: list[ImportApplyOutcome] = []
    counts: dict[str, dict[str, int]] = {}

    def tally(kind: str, outcome: str) -> None:
        counts.setdefault(kind, {})[outcome] = counts.setdefault(kind, {}).get(outcome, 0) + 1

    for m in proposals.metrics:
        outcome, detail = _apply_one(
            "metric.register",
            {
                "display_name": m.display_name,
                "metric_code": m.metric_code,
                "description": m.description,
                "direction": m.direction,
                "aggregation": m.aggregation,
                "unit": m.unit,
                "decimal_places": m.decimal_places,
                "is_percentage": m.is_percentage,
                "products": ["scorecards"],
                "status": "draft",
                # The Admin reviewed the proposal list, so near-duplicate names are expected.
                "acknowledge_similar": True,
            },
            ctx,
        )
        outcomes.append(
            ImportApplyOutcome(kind="metric", ref=m.metric_code, outcome=outcome, detail=detail)
        )
        tally("metrics", outcome)

    for s in proposals.subjects:
        if not s.email:
            # A subject needs a real email to be registered; the roster had none for them.
            outcome, detail = "failed", "A valid email address is needed to register this person."
        else:
            outcome, detail = _apply_one(
                "subject.register",
                {"staff_no": s.staff_no, "full_name": s.full_name, "email": s.email},
                ctx,
            )
        outcomes.append(
            ImportApplyOutcome(kind="subject", ref=s.staff_no, outcome=outcome, detail=detail)
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
