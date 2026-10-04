"""Directory import: read a roster, diff it against the hierarchy, apply what was reviewed.

PRD AD-9: roster and manager attributes at onboarding, and a reviewed diff on
every re-sync. The diff is stored with the preview; applying recomputes it and
refuses if anything moved since, so what lands is exactly what was reviewed.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any

from django.db.models import Q
from django.utils import timezone

from kpigo.access.accounts import create_account
from kpigo.access.models import AppUser
from kpigo.action.errors import Conflict
from kpigo.hierarchy.models import ReportingEdge, Subject
from kpigo.platform.db import conflicts
from kpigo.platform.vocab import CODE_PATTERN

FIELDS = ("staff_no", "full_name", "email", "manager_staff_no")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STAFF_RE = re.compile(CODE_PATTERN)

_CONFLICTS = {
    "subject_email_unique": "Two people would share an email; fix the directory and preview again.",
    "subject_staff_no_unique": "Two people would share a staff number.",
    "reporting_edge_one_solid_manager": "A person would have two solid-line managers.",
    "reporting_edge_not_self": "A person would report to themself.",
}


def read_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [f for f in ("staff_no", "full_name", "email") if f not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"The file needs the columns {', '.join(missing)}.")
    return [{f: (row.get(f) or "").strip() for f in FIELDS} | {"external_id": ""} for row in reader]


def normalise(records: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Clean records and the reasons any were left out."""
    errors: list[dict[str, Any]] = []
    kept: dict[str, dict[str, str]] = {}
    emails: dict[str, str] = {}
    for i, raw in enumerate(records, start=1):
        rec = {f: str(raw.get(f) or "").strip() for f in FIELDS}
        rec["email"] = rec["email"].lower()
        rec["external_id"] = str(raw.get("external_id") or "")
        problem = None
        if not rec["staff_no"]:
            problem = "no staff number"
        elif not STAFF_RE.match(rec["staff_no"]) or len(rec["staff_no"]) > 64:
            problem = "staff number has characters kpiGo does not accept"
        elif not rec["full_name"]:
            problem = "no name"
        elif not EMAIL_RE.match(rec["email"]):
            problem = "no valid email"
        elif rec["staff_no"] in kept:
            problem = "staff number appears twice"
        elif rec["email"] in emails:
            problem = f"email also used by {emails[rec['email']]}"
        if problem:
            errors.append({"row": i, "staff_no": rec["staff_no"], "problem": problem})
            continue
        kept[rec["staff_no"]] = rec
        emails[rec["email"]] = rec["staff_no"]
    for rec in kept.values():
        manager = rec["manager_staff_no"]
        if manager == rec["staff_no"]:
            errors.append({"staff_no": manager, "problem": "reports to themself; manager ignored"})
            rec["manager_staff_no"] = ""
    return sorted(kept.values(), key=lambda r: r["staff_no"]), errors


def _in_force(as_of: date) -> Q:
    return Q(effective_from__lte=as_of) & (Q(effective_to__isnull=True) | Q(effective_to__gt=as_of))


def current_managers(org_id: str, as_of: date) -> dict[str, str]:
    """staff_no -> solid-line manager's staff_no, in force on the day."""
    edges = ReportingEdge.objects.filter(
        _in_force(as_of), org_id=org_id, relationship_type="solid"
    ).values_list("subject__staff_no", "manager__staff_no")
    return dict(edges)


def compute(
    org_id: str, records: list[dict[str, str]], *, full_roster: bool, as_of: date
) -> dict[str, Any]:
    subjects = {s.staff_no: s for s in Subject.objects.filter(org_id=org_id)}
    managers = current_managers(org_id, as_of)
    listed = {r["staff_no"] for r in records}
    new, changed, manager_changes, errors = [], [], [], []
    unchanged = 0
    for rec in records:
        subject = subjects.get(rec["staff_no"])
        manager = rec["manager_staff_no"]
        if manager and manager not in listed and manager not in subjects:
            errors.append(
                {"staff_no": rec["staff_no"], "problem": f"manager {manager} is unknown; ignored"}
            )
            manager = ""
        if subject is None:
            new.append(
                {
                    **{f: rec[f] for f in ("staff_no", "full_name", "email")},
                    "manager_staff_no": manager,
                }
            )
            continue
        changes: dict[str, list[str]] = {}
        if subject.full_name != rec["full_name"]:
            changes["full_name"] = [subject.full_name, rec["full_name"]]
        if str(subject.email).lower() != rec["email"]:
            changes["email"] = [str(subject.email), rec["email"]]
        if subject.status != "active":
            changes["status"] = [subject.status, "active"]
        if changes:
            changed.append({"staff_no": rec["staff_no"], "changes": changes})
        current = managers.get(rec["staff_no"], "")
        if current != manager:
            manager_changes.append({"staff_no": rec["staff_no"], "from": current, "to": manager})
        if not changes and current == manager:
            unchanged += 1
    leavers = []
    if full_roster:
        leavers = [
            {"staff_no": s.staff_no, "full_name": s.full_name}
            for code, s in sorted(subjects.items())
            if s.status == "active" and code not in listed
        ]
    return {
        "new": new,
        "changed": changed,
        "manager_changes": manager_changes,
        "leavers": leavers,
        "warnings": errors,
        "unchanged": unchanged,
    }


def summary_of(diff: dict[str, Any], rejected: int) -> dict[str, int]:
    return {
        "new": len(diff["new"]),
        "changed": len(diff["changed"]),
        "manager_changes": len(diff["manager_changes"]),
        "leavers": len(diff["leavers"]),
        "unchanged": int(diff["unchanged"]),
        "rejected": rejected,
    }


def apply(
    org_id: str,
    diff: dict[str, Any],
    *,
    effective_from: date,
    apply_leavers: bool,
    provision_role: str | None,
    provision_provider: str,
    user_id: int | None,
) -> dict[str, int]:
    now = timezone.now()
    counts = {
        "created": 0,
        "updated": 0,
        "lines_ended": 0,
        "lines_added": 0,
        "left": 0,
        "accounts_disabled": 0,
        "accounts_invited": 0,
    }
    with conflicts(_CONFLICTS):
        for rec in diff["new"]:
            Subject.objects.create(
                org_id=org_id,
                staff_no=rec["staff_no"],
                full_name=rec["full_name"],
                email=rec["email"],
                joined_at=effective_from,
                created_by=user_id,
                updated_by=user_id,
            )
            counts["created"] += 1
        for change in diff["changed"]:
            subject = Subject.objects.get(org_id=org_id, staff_no=change["staff_no"])
            for field, (_, new) in change["changes"].items():
                setattr(subject, field, new)
                if field == "status":
                    subject.left_at = None
            subject.updated_by = user_id
            subject.updated_at = now
            subject.save()
            counts["updated"] += 1
    subjects = {s.staff_no: s for s in Subject.objects.filter(org_id=org_id)}
    moves = list(diff["manager_changes"]) + [
        {"staff_no": r["staff_no"], "from": "", "to": r["manager_staff_no"]}
        for r in diff["new"]
        if r["manager_staff_no"]
    ]
    for move in moves:
        subject = subjects[move["staff_no"]]
        if move["from"]:
            counts["lines_ended"] += _end_solid_line(org_id, subject, effective_from, user_id)
        if move["to"]:
            with conflicts(_CONFLICTS):
                ReportingEdge.objects.create(
                    org_id=org_id,
                    subject=subject,
                    manager=subjects[move["to"]],
                    relationship_type="solid",
                    effective_from=effective_from,
                    created_by=user_id,
                    updated_by=user_id,
                )
            counts["lines_added"] += 1
    if apply_leavers:
        for leaver in diff["leavers"]:
            subject = subjects[leaver["staff_no"]]
            ended = ReportingEdge.objects.filter(
                Q(subject=subject) | Q(manager=subject),
                Q(effective_to__isnull=True) | Q(effective_to__gt=effective_from),
                org_id=org_id,
            )
            for edge in ended:
                counts["lines_ended"] += _end_edge(edge, effective_from, user_id)
            subject.status = "inactive"
            subject.left_at = effective_from
            subject.updated_by = user_id
            subject.updated_at = now
            subject.save()
            counts["left"] += 1
            for account in AppUser.objects.filter(
                org_id=org_id, subject=subject, status__in=("active", "invited")
            ):
                account.status = "disabled"
                account.disabled_at = now
                account.disabled_reason = "Left, per directory import"
                account.save(update_fields=["status", "disabled_at", "disabled_reason"])
                account.auth_user.is_active = False
                account.auth_user.save(update_fields=["is_active"])
                counts["accounts_disabled"] += 1
    if provision_role:
        for rec in diff["new"]:
            if AppUser.objects.filter(org_id=org_id, email=rec["email"]).exists():
                continue
            create_account(
                org_id,
                email=rec["email"],
                display_name=rec["full_name"],
                auth_provider=provision_provider,
                role_codes=[provision_role],
                subject=subjects[rec["staff_no"]],
                created_by=user_id,
            )
            counts["accounts_invited"] += 1
    return counts


def _end_solid_line(org_id: str, subject: Subject, when: date, user_id: int | None) -> int:
    edge = (
        ReportingEdge.objects.select_for_update()
        .filter(_in_force(when), org_id=org_id, subject=subject, relationship_type="solid")
        .first()
    )
    return _end_edge(edge, when, user_id) if edge is not None else 0


def _end_edge(edge: ReportingEdge, when: date, user_id: int | None) -> int:
    if edge.effective_from >= when:
        raise Conflict(
            f"{edge.subject.staff_no}'s reporting line starts on {edge.effective_from}, on or "
            f"after {when}. Apply the import from a later date."
        )
    edge.effective_to = when
    edge.updated_by = user_id
    edge.updated_at = timezone.now()
    edge.save(update_fields=["effective_to", "updated_by", "updated_at"])
    return 1
