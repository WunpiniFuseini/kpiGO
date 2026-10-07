"""The notification centre and the daily digest (PRD NT-1..5).

``notification.list`` and ``notification.mark_read`` are the in-app centre every
signed-in person has over their own notices — granted to everyone, scoped to the
caller's own account, never another's. ``notification.digest`` is the batched email
run (NT-4): one message per person listing their un-emailed notices, sent through the
install's own relay when one is set, carrying titles and counts and no scores.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from kpigo.access.identity import app_user_for
from kpigo.action import ActionContext, action
from kpigo.action.errors import InvalidInput
from kpigo.platform import mail


class NotificationOut(BaseModel):
    notification_id: str
    category: str
    level: str
    title: str
    body: str
    link: str
    created_at: datetime
    read_at: datetime | None


class NotificationListIn(BaseModel):
    unread_only: bool = False
    limit: int = Field(default=30, ge=1, le=100)


class NotificationListOut(BaseModel):
    notifications: list[NotificationOut]
    unread_count: int


def _recipient_id(ctx: ActionContext) -> str:
    account = app_user_for(ctx.user, ctx.org_id) if ctx.user is not None else None
    if account is None:
        raise InvalidInput("Notifications belong to a signed-in account.")
    return str(account.user_id)


@action(
    name="notification.list",
    summary="The signed-in person's own notifications, newest first, with the unread count.",
    schema=NotificationListIn,
    output=NotificationListOut,
    permission="notification.view",
    read_only=True,
    http={"method": "GET", "path": "/notifications"},
    example={"unread_only": False, "limit": 30},
)
def list_notifications(params: NotificationListIn, ctx: ActionContext) -> NotificationListOut:
    from kpigo.platform.models import Notification

    recipient_id = _recipient_id(ctx)
    mine = Notification.objects.filter(org_id=ctx.org_id, recipient_id=recipient_id)
    qs = mine.filter(read_at__isnull=True) if params.unread_only else mine
    rows = list(qs.order_by("-created_at")[: params.limit])
    unread = mine.filter(read_at__isnull=True).count()
    return NotificationListOut(
        notifications=[_to_out(r) for r in rows],
        unread_count=unread,
    )


class MarkReadIn(BaseModel):
    # One notification to mark read, or all of the caller's when omitted.
    notification_id: str | None = None


class MarkReadOut(BaseModel):
    marked: int
    unread_count: int


@action(
    name="notification.mark_read",
    summary="Mark one of your notifications read, or all of them.",
    schema=MarkReadIn,
    output=MarkReadOut,
    permission="notification.view",
    read_only=False,
    http={"method": "POST", "path": "/notifications/read"},
    example={"notification_id": None},
)
def mark_read(params: MarkReadIn, ctx: ActionContext) -> MarkReadOut:
    from django.utils import timezone

    from kpigo.platform.models import Notification

    recipient_id = _recipient_id(ctx)
    mine = Notification.objects.filter(
        org_id=ctx.org_id, recipient_id=recipient_id, read_at__isnull=True
    )
    if params.notification_id is not None:
        mine = mine.filter(notification_id=params.notification_id)
    marked = mine.update(read_at=timezone.now())
    unread = Notification.objects.filter(
        org_id=ctx.org_id, recipient_id=recipient_id, read_at__isnull=True
    ).count()
    return MarkReadOut(marked=marked, unread_count=unread)


class DigestIn(BaseModel):
    pass


class DigestOut(BaseModel):
    recipients: int
    emailed: int
    relay_configured: bool
    message: str


@action(
    name="notification.digest",
    summary="Email each person a single digest of their un-emailed notices (scheduled daily).",
    schema=DigestIn,
    output=DigestOut,
    permission="notification.manage",
    read_only=False,
    audit="notification.digest_run",
    example={},
)
def digest(params: DigestIn, ctx: ActionContext) -> DigestOut:
    """Batch the day's notices into one email per person (NT-4). In-app notices are
    unaffected; this only sends the ones marked for the digest that have not been
    emailed. Does nothing (cleanly) when no relay is set or on a dry run."""
    from django.utils import timezone

    from kpigo.access.models import AppUser
    from kpigo.platform.models import Notification

    pending = list(
        Notification.objects.filter(
            org_id=ctx.org_id, digest=True, emailed_at__isnull=True
        ).order_by("recipient_id", "-created_at")
    )
    recipients = {str(n.recipient_id) for n in pending}
    if not mail.enabled() or ctx.dry_run or not pending:
        why = (
            "No mail relay is configured; notices stay in the in-app centre."
            if not mail.enabled()
            else ("Nothing to send." if not pending else "Dry run; nothing sent.")
        )
        return DigestOut(
            recipients=len(recipients), emailed=0, relay_configured=mail.enabled(), message=why
        )

    by_email = {
        str(u.user_id): u
        for u in AppUser.objects.filter(org_id=ctx.org_id, user_id__in=list(recipients))
    }
    emailed = 0
    for recipient_id in recipients:
        who = by_email.get(recipient_id)
        items = [n for n in pending if str(n.recipient_id) == recipient_id]
        if who is None or not who.email:
            continue
        if mail.send(who.email, *_compose(who.display_name, items)):
            ids = [n.notification_id for n in items]
            Notification.objects.filter(notification_id__in=ids).update(emailed_at=timezone.now())
            emailed += 1
            ctx.audit("notification.digest_emailed", to_user_id=recipient_id, count=len(items))
    return DigestOut(
        recipients=len(recipients),
        emailed=emailed,
        relay_configured=True,
        message=f"Sent {emailed} digest(s).",
    )


def _compose(name: str, items: list[Any]) -> tuple[str, str]:
    """One plain-text digest: a line per notice, titles only — no scores or values."""
    where = mail.link("/notifications")
    lines = [f"Hello {name},", "", f"You have {len(items)} new notification(s) in kpiGo:", ""]
    lines += [f"  - {n.title}" for n in items]
    lines.append("")
    lines.append(f"Open the notification centre: {where}" if where else "Open kpiGo to see them.")
    subject = f"kpiGo: {len(items)} new notification(s)"
    return subject, "\n".join(lines)


def _to_out(row) -> NotificationOut:  # type: ignore[no-untyped-def]
    return NotificationOut(
        notification_id=str(row.notification_id),
        category=row.category,
        level=row.level,
        title=row.title,
        body=row.body,
        link=row.link,
        created_at=row.created_at,
        read_at=row.read_at,
    )
