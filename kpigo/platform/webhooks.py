"""Outbound webhooks to the install's own systems (PRD OP-8).

When a notice is raised, kpiGo POSTs a small signed payload to every active endpoint
subscribed to that category. Like email (``kpigo.platform.mail``) it is best-effort and
self-gating: with no endpoint configured nothing is sent, and a failed delivery is
recorded, never raised, so a broken receiver cannot stop the action that raised the
notice. The payload carries the category, level, title and link only — never a score or
a value — so it is safe to send off the install (Deployment constraints). Each delivery
is signed with the endpoint's secret (HMAC-SHA256) so the receiver can trust its origin.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx
from django.conf import settings
from django.utils import timezone

from kpigo.ingestion import crypto
from kpigo.ingestion.models import CredentialSecret
from kpigo.platform.models import Notification, WebhookEndpoint

log = logging.getLogger(__name__)

# Tests swap in an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None

TIMEOUT_SECONDS = 5.0
SIGNATURE_HEADER = "X-KpiGo-Signature"
EVENT_HEADER = "X-KpiGo-Event"

_LEVEL_RANK = {"info": 0, "warning": 1, "critical": 2}


def enabled() -> bool:
    """Webhooks can be switched off install-wide regardless of configured endpoints
    (an air-gapped install wants no outbound call); on by default."""
    return bool(getattr(settings, "KPIGO_WEBHOOKS_ENABLED", True))


def _wants(endpoint: WebhookEndpoint, category: str, level: str) -> bool:
    if endpoint.categories and category not in endpoint.categories:
        return False
    return _LEVEL_RANK.get(level, 0) >= _LEVEL_RANK.get(endpoint.min_level, 0)


def _secret(endpoint: WebhookEndpoint) -> str | None:
    if endpoint.secret_ref is None:
        return None
    row = CredentialSecret.objects.filter(
        org_id=endpoint.org_id, secret_id=endpoint.secret_ref
    ).first()
    if row is None:
        return None
    return crypto.decrypt(row.ciphertext)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload(
    org_id: object,
    category: str,
    level: str,
    title: str,
    link: str,
    subject_ref: str,
    occurred_at: str,
) -> dict[str, str]:
    # Titles and counts only — never a score or a value (Deployment constraints).
    return {
        "event": category,
        "level": level,
        "title": title,
        "link": link,
        "subject_ref": subject_ref,
        "occurred_at": occurred_at,
        "org_id": str(org_id),
    }


def _post(endpoint: WebhookEndpoint, payload: dict[str, str]) -> str:
    """Deliver one payload. Returns a short status; never raises."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    headers = {"Content-Type": "application/json", EVENT_HEADER: payload["event"]}
    try:
        secret = _secret(endpoint)
        if secret:
            headers[SIGNATURE_HEADER] = sign(secret, body)
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_SECONDS) as client:
            response = client.post(endpoint.url, content=body, headers=headers)
            response.raise_for_status()
        return str(response.status_code)
    except Exception as exc:  # a broken receiver cannot stop the producer
        log.warning("webhook delivery to %s failed: %s", endpoint.name, exc.__class__.__name__)
        return f"error: {exc.__class__.__name__}"


def _record(endpoint: WebhookEndpoint, status: str) -> None:
    WebhookEndpoint.objects.filter(pk=endpoint.pk).update(
        last_delivery_at=timezone.now(), last_status=status
    )


def deliver_for(notification: Notification) -> None:
    """POST a notice to every active, subscribed endpoint. Best-effort; swallows all
    errors. Called after the producer's transaction commits, so a dry run sends nothing."""
    if not enabled():
        return
    endpoints = [
        e
        for e in WebhookEndpoint.objects.filter(org_id=notification.org_id, status="active")
        if _wants(e, notification.category, notification.level)
    ]
    if not endpoints:
        return
    payload = _payload(
        notification.org_id,
        notification.category,
        notification.level,
        notification.title,
        notification.link,
        notification.subject_ref,
        (notification.created_at or timezone.now()).isoformat(),
    )
    for endpoint in endpoints:
        _record(endpoint, _post(endpoint, payload))


def deliver_test(endpoint: WebhookEndpoint) -> str:
    """Send a synthetic payload to one endpoint now, for the operator to confirm the
    receiver is reachable. Returns the delivery status."""
    payload = _payload(
        endpoint.org_id,
        "system",
        "info",
        "kpiGo webhook test",
        "",
        endpoint.name,
        timezone.now().isoformat(),
    )
    status = _post(endpoint, payload)
    _record(endpoint, status)
    return status
