"""Raising notices and resolving who should hear them (PRD NT-1..5).

``notify`` writes one in-app row for one person — the notification centre is always
on, whether or not a mail relay is configured (NT-3). The daily digest action emails
the un-emailed ones in a single batched message (NT-4); a producer that sends its own
mail passes ``digest=False`` so the digest does not email the same thing twice.

``holders`` resolves the accounts a role-scoped notice should reach (feed stewards,
input managers), reused from the escalation ladder's own resolution.
"""

from __future__ import annotations

from typing import Any

from kpigo.access.identity import role_codes, role_permissions
from kpigo.access.models import AppUser
from kpigo.platform.models import Notification


def holders(org_id: str, permission: str) -> list[AppUser]:
    """Active accounts whose roles grant ``permission``, by display name."""
    return [
        u
        for u in AppUser.objects.filter(org_id=org_id, status="active").order_by("display_name")
        if permission in role_permissions(org_id, role_codes(u))
    ]


def notify(
    org_id: str,
    recipient: Any,
    category: str,
    title: str,
    *,
    body: str = "",
    link: str = "",
    level: str = "info",
    subject_ref: str = "",
    digest: bool = True,
    created_by: int | None = None,
) -> Notification:
    """Write one in-app notice. ``recipient`` is an ``AppUser`` or its ``user_id``.

    Always persists the row (the centre is always on); it is the caller's transaction
    that decides whether it sticks, so a dry run rolls it back with everything else.
    """
    recipient_id = getattr(recipient, "user_id", recipient)
    return Notification.objects.create(
        org_id=org_id,
        recipient_id=recipient_id,
        category=category,
        level=level,
        title=title,
        body=body,
        link=link,
        subject_ref=subject_ref,
        digest=digest,
        created_by=created_by,
    )
