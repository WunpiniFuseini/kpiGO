"""Outbound email to the install's own relay (KPIGO_EMAIL_*).

Mail is optional: with no relay configured nothing is sent and callers fall
back to in-app notice. A failed send is reported, never raised, so a broken
relay cannot stop the job that wanted to send.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(settings.KPIGO_EMAIL_HOST)


def link(path: str) -> str:
    """An absolute link into kpiGo when the public address is known, else ''."""
    base = settings.KPIGO_PUBLIC_URL
    return f"{base}{path}" if base else ""


def send(to: str, subject: str, body: str) -> bool:
    """Send one plain-text message; True when the relay accepted it."""
    if not enabled():
        return False
    try:
        return send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to]) == 1
    except Exception as exc:  # any relay failure is reported, not raised
        log.warning("mail send failed: %s", exc.__class__.__name__)
        return False
