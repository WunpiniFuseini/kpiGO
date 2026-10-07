"""Webhook-endpoint actions (PRD OP-8): register, list, test and remove the outbound
endpoints kpiGo delivers notices to.

An endpoint belongs to the client's own install and is configured by an Admin. On
``webhook.register`` kpiGo generates a signing secret and returns it **once** — it is
stored only as ciphertext and used to HMAC each delivery, so the receiver can trust the
origin and it can never be read back. Delivery itself is best-effort and self-gating and
carries titles and links only, never scores or values (see ``kpigo.platform.webhooks``).
"""

from __future__ import annotations

import secrets
import uuid

from pydantic import BaseModel, Field, field_validator

from kpigo.action import ActionContext, action
from kpigo.action.errors import NotFound
from kpigo.ingestion import crypto
from kpigo.ingestion.models import CredentialSecret
from kpigo.platform import webhooks
from kpigo.platform.models import NOTIFICATION_CATEGORIES, WebhookEndpoint


class WebhookOut(BaseModel):
    name: str
    url: str
    categories: list[str]
    min_level: str
    status: str
    last_delivery_at: str | None
    last_status: str

    @classmethod
    def of(cls, e: WebhookEndpoint) -> WebhookOut:
        return cls(
            name=e.name,
            url=e.url,
            categories=list(e.categories),
            min_level=e.min_level,
            status=e.status,
            last_delivery_at=e.last_delivery_at.isoformat() if e.last_delivery_at else None,
            last_status=e.last_status,
        )


def _get(org_id: str, name: str) -> WebhookEndpoint:
    found = WebhookEndpoint.objects.filter(org_id=org_id, name=name).first()
    if found is None:
        raise NotFound(f"No webhook endpoint named '{name}'.")
    return found


class WebhookRegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=2048)
    categories: list[str] = Field(default_factory=list)
    min_level: str = "info"

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return v

    @field_validator("categories")
    @classmethod
    def _known_categories(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in NOTIFICATION_CATEGORIES]
        if unknown:
            raise ValueError(f"unknown notification categories: {', '.join(unknown)}")
        return v

    @field_validator("min_level")
    @classmethod
    def _known_level(cls, v: str) -> str:
        if v not in webhooks._LEVEL_RANK:
            raise ValueError("min_level must be info, warning or critical")
        return v


class WebhookRegisterOut(BaseModel):
    endpoint: WebhookOut
    # The HMAC signing secret, shown this once and never again.
    signing_secret: str
    message: str


@action(
    name="webhook.register",
    summary="Register an outbound webhook endpoint; returns its signing secret once.",
    schema=WebhookRegisterIn,
    output=WebhookRegisterOut,
    permission="webhook.manage",
    read_only=False,
    audit="webhook.registered",
    http={"method": "POST", "path": "/webhooks"},
    example={
        "name": "ops-bus",
        "url": "https://events.bank.local/kpigo",
        "categories": ["load_quarantined", "escalation"],
        "min_level": "warning",
    },
)
def register(params: WebhookRegisterIn, ctx: ActionContext) -> WebhookRegisterOut:
    from kpigo.platform.db import conflicts

    signing_secret = secrets.token_urlsafe(32)
    secret = CredentialSecret.objects.create(
        org_id=ctx.org_id,
        ciphertext=crypto.encrypt(signing_secret),
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    with conflicts({"webhook_endpoint_name_unique": f"A webhook named '{params.name}' exists."}):
        endpoint = WebhookEndpoint.objects.create(
            org_id=ctx.org_id,
            name=params.name,
            url=params.url,
            categories=params.categories,
            min_level=params.min_level,
            secret_ref=secret.secret_id,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    ctx.audit("webhook.registered", name=endpoint.name, url=endpoint.url)
    return WebhookRegisterOut(
        endpoint=WebhookOut.of(endpoint),
        signing_secret=signing_secret,
        message=(
            "Store this signing secret now; it is not shown again. kpiGo signs every "
            f"delivery with it in the {webhooks.SIGNATURE_HEADER} header (HMAC-SHA256)."
        ),
    )


class WebhookListIn(BaseModel):
    pass


class WebhookListOut(BaseModel):
    endpoints: list[WebhookOut]
    delivery_enabled: bool


@action(
    name="webhook.list",
    summary="The outbound webhook endpoints configured for this org.",
    schema=WebhookListIn,
    output=WebhookListOut,
    permission="webhook.view",
    read_only=True,
    http={"method": "GET", "path": "/webhooks"},
    example={},
)
def list_endpoints(params: WebhookListIn, ctx: ActionContext) -> WebhookListOut:
    rows = WebhookEndpoint.objects.filter(org_id=ctx.org_id).order_by("name")
    return WebhookListOut(
        endpoints=[WebhookOut.of(e) for e in rows],
        delivery_enabled=webhooks.enabled(),
    )


class WebhookTestIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class WebhookTestOut(BaseModel):
    name: str
    status: str
    delivered: bool


@action(
    name="webhook.test",
    summary="Send a synthetic delivery to one endpoint to confirm the receiver is reachable.",
    schema=WebhookTestIn,
    output=WebhookTestOut,
    permission="webhook.manage",
    read_only=False,
    audit="webhook.tested",
    http={"method": "POST", "path": "/webhooks/test"},
    example={"name": "ops-bus"},
)
def test_endpoint(params: WebhookTestIn, ctx: ActionContext) -> WebhookTestOut:
    endpoint = _get(ctx.org_id, params.name)
    status = webhooks.deliver_test(endpoint)
    ctx.audit("webhook.tested", name=endpoint.name, status=status)
    return WebhookTestOut(name=endpoint.name, status=status, delivered=status.isdigit())


class WebhookRemoveIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class WebhookRemoveOut(BaseModel):
    name: str
    message: str


@action(
    name="webhook.remove",
    summary="Remove a webhook endpoint and its signing secret.",
    schema=WebhookRemoveIn,
    output=WebhookRemoveOut,
    permission="webhook.manage",
    read_only=False,
    audit="webhook.removed",
    http={"method": "POST", "path": "/webhooks/remove"},
    example={"name": "ops-bus"},
)
def remove(params: WebhookRemoveIn, ctx: ActionContext) -> WebhookRemoveOut:
    endpoint = _get(ctx.org_id, params.name)
    secret_ref: uuid.UUID | None = endpoint.secret_ref
    endpoint.delete()
    if secret_ref is not None:
        CredentialSecret.objects.filter(org_id=ctx.org_id, secret_id=secret_ref).delete()
    ctx.audit("webhook.removed", name=params.name)
    return WebhookRemoveOut(name=params.name, message=f"Removed webhook '{params.name}'.")
