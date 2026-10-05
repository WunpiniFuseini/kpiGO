"""Directory import with a reviewed diff (PRD AD-9, App Flow §2 step 3)."""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import date
from typing import Any, Literal

import httpx
from django.utils import timezone
from pydantic import BaseModel, Field, field_serializer

from kpigo.access import directory
from kpigo.access.accounts import check_role_codes
from kpigo.access.auth import entra, ldap
from kpigo.access.models import DirectoryImport
from kpigo.action import ActionContext, Conflict, InvalidInput, NotFound, action
from kpigo.hierarchy import closure
from kpigo.hierarchy.scope import current_period_key
from kpigo.platform.vocab import period_key_for

Source = Literal["ldap", "entra", "file"]
MAX_FILE_BYTES = 10 * 1024 * 1024
EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"


class TestIn(BaseModel):
    source: Literal["ldap", "entra"]


class TestOut(BaseModel):
    ok: bool
    message: str


@action(
    name="directory.test",
    summary="Test the directory connection configured for this install.",
    schema=TestIn,
    output=TestOut,
    permission="directory.view",
    read_only=True,
    example={"source": "ldap"},
)
def test(params: TestIn, ctx: ActionContext) -> TestOut:
    ok, message = ldap.test_connection() if params.source == "ldap" else entra.test_connection()
    return TestOut(ok=ok, message=message)


class RosterFile(BaseModel):
    filename: str = Field(max_length=255)
    content_base64: str = Field(max_length=MAX_FILE_BYTES * 2)

    @field_serializer("content_base64", when_used="json")
    def _hide(self, value: str) -> str:
        return "<omitted>"


class PreviewIn(BaseModel):
    source: Source
    file: RosterFile | None = Field(
        default=None,
        description="For source 'file': CSV with staff_no, full_name, email, manager_staff_no.",
    )
    full_roster: bool = Field(
        default=True, description="True: people missing from the directory are leavers."
    )


class ImportOut(BaseModel):
    import_id: uuid.UUID
    source: str
    status: str
    full_roster: bool
    summary: dict[str, int]
    diff: dict[str, Any]
    rejected: list[dict[str, Any]]
    created_at: Any = None
    applied_at: Any = None
    applied: dict[str, int] | None = None


def import_out(row: DirectoryImport, applied: dict[str, int] | None = None) -> ImportOut:
    return ImportOut(
        import_id=row.import_id,
        source=row.source,
        status=row.status,
        full_roster=row.full_roster,
        summary=row.summary,
        diff=row.diff,
        rejected=list(row.diff.get("rejected", [])),
        created_at=row.created_at,
        applied_at=row.applied_at,
        applied=applied,
    )


def _read(params: PreviewIn) -> list[dict[str, str]]:
    try:
        if params.source == "file":
            if params.file is None:
                raise InvalidInput("Attach the roster file.")
            raw = base64.b64decode(params.file.content_base64, validate=True)
            if len(raw) > MAX_FILE_BYTES:
                raise InvalidInput("The roster file is larger than 10 MB.")
            return directory.read_csv(raw.decode("utf-8-sig"))
        if params.source == "ldap":
            return ldap.read_roster()
        return entra.read_roster()
    except (binascii.Error, UnicodeDecodeError):
        raise InvalidInput("The roster file is not base64-encoded UTF-8 CSV.") from None
    except (ValueError, LookupError, httpx.HTTPError) as exc:
        raise InvalidInput(f"The directory could not be read: {exc}") from None


@action(
    name="directory.import.preview",
    summary="Read the directory and show what would change: new, changed, moved, leavers.",
    schema=PreviewIn,
    output=ImportOut,
    permission="directory.manage",
    read_only=False,
    audit="directory.previewed",
    example={"source": "ldap", "full_roster": True},
)
def preview(params: PreviewIn, ctx: ActionContext) -> ImportOut:
    records, rejected = directory.normalise(_read(params))
    if not records:
        raise InvalidInput("The directory returned nobody kpiGo can import.", detail=rejected)
    diff = directory.compute(
        ctx.org_id, records, full_roster=params.full_roster, as_of=timezone.localdate()
    )
    diff["rejected"] = rejected
    row = DirectoryImport.objects.create(
        org_id=ctx.org_id,
        source=params.source,
        full_roster=params.full_roster,
        records=records,
        diff=diff,
        summary=directory.summary_of(diff, len(rejected)),
        created_by=ctx.user_id,
    )
    return import_out(row)


class ApplyIn(BaseModel):
    import_id: uuid.UUID
    effective_from: date | None = Field(default=None, description="Defaults to today.")
    apply_leavers: bool = True
    provision_role: str | None = Field(
        default=None, description="Also invite each new person as a user with this role."
    )
    provision_provider: Literal["local", "ldap", "oidc", "saml"] = "oidc"


@action(
    name="directory.import.apply",
    summary="Apply a reviewed directory import to subjects and reporting lines.",
    schema=ApplyIn,
    output=ImportOut,
    permission="directory.manage",
    read_only=False,
    requires_approval="hierarchy_change",
    audit="directory.applied",
    config_change=True,
    example={"import_id": EXAMPLE_ID},
)
def apply(params: ApplyIn, ctx: ActionContext) -> ImportOut:
    row = (
        DirectoryImport.objects.select_for_update()
        .filter(org_id=ctx.org_id, import_id=params.import_id)
        .first()
    )
    if row is None:
        raise NotFound("No such directory import.")
    if row.status != "previewed":
        raise Conflict(f"This import is already {row.status}.")
    if params.provision_role:
        check_role_codes(ctx.org_id, [params.provision_role])
    when = params.effective_from or timezone.localdate()
    fresh = directory.compute(ctx.org_id, row.records, full_roster=row.full_roster, as_of=when)
    reviewed = {k: v for k, v in row.diff.items() if k != "rejected"}
    if fresh != reviewed:
        raise Conflict(
            "The hierarchy changed since this preview, or the date moves who reports to whom. "
            "Preview again and review the new diff."
        )
    counts = directory.apply(
        ctx.org_id,
        fresh,
        effective_from=when,
        apply_leavers=params.apply_leavers,
        provision_role=params.provision_role,
        provision_provider=params.provision_provider,
        user_id=ctx.user_id,
    )
    # Rebuild visibility so people see their new teams this period.
    for period in sorted({period_key_for(when), current_period_key(ctx.org_id)}):
        closure.build(ctx.org_id, period, ctx.user_id)
    row.status = "applied"
    row.applied_at = timezone.now()
    row.applied_by = ctx.user_id
    row.save(update_fields=["status", "applied_at", "applied_by"])
    return import_out(row, counts)


class DiscardIn(BaseModel):
    import_id: uuid.UUID


@action(
    name="directory.import.discard",
    summary="Discard a previewed directory import without applying it.",
    schema=DiscardIn,
    output=ImportOut,
    permission="directory.manage",
    read_only=False,
    audit="directory.discarded",
    example={"import_id": EXAMPLE_ID},
)
def discard(params: DiscardIn, ctx: ActionContext) -> ImportOut:
    row = DirectoryImport.objects.filter(org_id=ctx.org_id, import_id=params.import_id).first()
    if row is None:
        raise NotFound("No such directory import.")
    if row.status != "previewed":
        raise Conflict(f"This import is already {row.status}.")
    row.status = "discarded"
    row.save(update_fields=["status"])
    return import_out(row)


class ListIn(BaseModel):
    status: Literal["previewed", "applied", "discarded"] | None = None


class ListOut(BaseModel):
    imports: list[ImportOut]


@action(
    name="directory.import.list",
    summary="Directory imports, newest first.",
    schema=ListIn,
    output=ListOut,
    permission="directory.view",
    read_only=True,
    example={},
)
def list_imports(params: ListIn, ctx: ActionContext) -> ListOut:
    query = DirectoryImport.objects.filter(org_id=ctx.org_id)
    if params.status:
        query = query.filter(status=params.status)
    return ListOut(imports=[import_out(r) for r in query.order_by("-created_at")[:50]])
