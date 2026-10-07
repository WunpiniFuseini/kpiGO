"""Outbound webhooks (PRD OP-8): register endpoints, deliver notices to the install's own
systems, signed and safe (titles and links only), best-effort and self-gating."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from kpigo.platform import notify, webhooks
from tests.conftest import ORG_ID, role_ctx, run

pytestmark = pytest.mark.django_db


class _Capture:
    """An httpx MockTransport that records every delivery and replies with a chosen code."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status)

        return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _reset_transport() -> Any:
    webhooks.TRANSPORT = None
    yield
    webhooks.TRANSPORT = None


def _register(name: str = "ops-bus", **kw: Any) -> Any:
    params = {"name": name, "url": "https://events.bank.local/kpigo"}
    params.update(kw)
    return run("webhook.register", role_ctx("admin"), **params)


def test_register_returns_the_secret_once_and_list_never_does() -> None:
    out = _register(categories=["load_quarantined"], min_level="warning")
    assert out.signing_secret and len(out.signing_secret) >= 32
    assert out.endpoint.name == "ops-bus" and out.endpoint.min_level == "warning"

    listed = run("webhook.list", role_ctx("admin"))
    assert [e.name for e in listed.endpoints] == ["ops-bus"]
    # The secret is not a field of the listed shape at all.
    assert not hasattr(listed.endpoints[0], "signing_secret")
    assert listed.delivery_enabled is True


def test_a_notice_is_delivered_signed_with_a_safe_payload(
    django_capture_on_commit_callbacks: Any,
) -> None:
    out = _register()
    cap = _Capture()
    webhooks.TRANSPORT = cap.transport()

    with django_capture_on_commit_callbacks(execute=True):
        notify.notify(
            ORG_ID,
            "11111111-1111-1111-1111-111111111111",
            "scorecard_ready",
            "Scorecard ready for 202610",
            link="/scorecards",
            level="info",
        )

    assert len(cap.requests) == 1
    req = cap.requests[0]
    assert str(req.url) == "https://events.bank.local/kpigo"
    payload = json.loads(req.content)
    assert payload["event"] == "scorecard_ready"
    assert payload["title"] == "Scorecard ready for 202610"
    assert payload["link"] == "/scorecards" and payload["level"] == "info"
    # Signed with the secret the register call returned.
    assert req.headers[webhooks.SIGNATURE_HEADER] == webhooks.sign(out.signing_secret, req.content)
    assert req.headers[webhooks.EVENT_HEADER] == "scorecard_ready"


def test_a_rolled_back_notice_delivers_nothing(
    django_capture_on_commit_callbacks: Any,
) -> None:
    from django.db import transaction

    _register()
    cap = _Capture()
    webhooks.TRANSPORT = cap.transport()

    # Delivery hangs off transaction.on_commit, so a rolled-back notice (as a dry run
    # rolls back the whole action) never leaves the building.
    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        notify.notify(ORG_ID, "2" * 32, "scorecard_ready", "x")
        transaction.set_rollback(True)
    assert cap.requests == []


def test_category_and_level_filters(django_capture_on_commit_callbacks: Any) -> None:
    _register(name="warn-only", categories=["load_quarantined"], min_level="warning")
    cap = _Capture()
    webhooks.TRANSPORT = cap.transport()

    with django_capture_on_commit_callbacks(execute=True):
        # Wrong category: skipped.
        notify.notify(ORG_ID, "3" * 32, "scorecard_ready", "a", level="critical")
        # Right category but below min_level: skipped.
        notify.notify(ORG_ID, "3" * 32, "load_quarantined", "b", level="info")
        # Right category, at min_level: delivered.
        notify.notify(ORG_ID, "3" * 32, "load_quarantined", "c", level="warning")

    assert len(cap.requests) == 1
    assert json.loads(cap.requests[0].content)["title"] == "c"


def test_delivery_is_off_when_disabled_install_wide(
    settings: Any, django_capture_on_commit_callbacks: Any
) -> None:
    _register()
    settings.KPIGO_WEBHOOKS_ENABLED = False
    cap = _Capture()
    webhooks.TRANSPORT = cap.transport()

    with django_capture_on_commit_callbacks(execute=True):
        notify.notify(ORG_ID, "4" * 32, "scorecard_ready", "a")
    assert cap.requests == []


def test_a_failing_receiver_does_not_raise(django_capture_on_commit_callbacks: Any) -> None:
    _register()
    cap = _Capture(status=500)
    webhooks.TRANSPORT = cap.transport()

    # The producer's notify must complete even though the receiver 500s.
    with django_capture_on_commit_callbacks(execute=True):
        notify.notify(ORG_ID, "5" * 32, "scorecard_ready", "a")
    assert len(cap.requests) == 1  # attempted
    from kpigo.platform.models import WebhookEndpoint

    e = WebhookEndpoint.objects.get(org_id=ORG_ID, name="ops-bus")
    assert e.last_status.startswith("error:")  # recorded, not raised


def test_test_action_reports_delivery() -> None:
    _register()
    cap = _Capture()
    webhooks.TRANSPORT = cap.transport()
    out = run("webhook.test", role_ctx("admin"), name="ops-bus")
    assert out.delivered is True and out.status == "200"
    assert len(cap.requests) == 1
    assert json.loads(cap.requests[0].content)["event"] == "system"


def test_remove_deletes_the_endpoint_and_its_secret() -> None:
    from kpigo.ingestion.models import CredentialSecret
    from kpigo.platform.models import WebhookEndpoint

    _register()
    endpoint = WebhookEndpoint.objects.get(org_id=ORG_ID, name="ops-bus")
    secret_ref = endpoint.secret_ref
    assert secret_ref is not None
    run("webhook.remove", role_ctx("admin"), name="ops-bus")
    assert not WebhookEndpoint.objects.filter(org_id=ORG_ID, name="ops-bus").exists()
    assert not CredentialSecret.objects.filter(org_id=ORG_ID, secret_id=secret_ref).exists()


def test_register_rejects_a_bad_url_and_unknown_category() -> None:
    from kpigo.action import InvalidInput

    with pytest.raises(InvalidInput):
        run("webhook.register", role_ctx("admin"), name="x", url="ftp://nope")
    with pytest.raises(InvalidInput):
        _register(name="y", categories=["not_a_category"])


def test_duplicate_name_is_refused() -> None:
    from kpigo.action import Conflict

    _register()
    with pytest.raises(Conflict):
        _register()
