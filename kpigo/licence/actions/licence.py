"""Licence actions: status, offline activation, the version entitlement check, heartbeat."""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

import httpx
from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, Conflict, InvalidInput, action
from kpigo.hierarchy.models import Subject
from kpigo.licence import startup
from kpigo.licence.document import InvalidLicence, Terms, verify
from kpigo.licence.models import Licence
from kpigo.licence.state import (
    active_licence,
    current,
    effective_ceiling,
    install_fingerprint,
    major_of,
    product_version,
)

logger = logging.getLogger("kpigo.licence")


class LicenceOut(BaseModel):
    state: str
    mode: str
    message: str
    install_fingerprint: str
    product_version: str
    licence_key: str | None = None
    customer: str | None = None
    tier: str | None = None
    instance_kind: str | None = None
    modules: list[str] = []
    seats: int | None = None
    subjects_in_use: int | None = None
    seats_exceeded: bool = False
    max_major_version: int | None = None
    effective_major_ceiling: int | None = None
    trial_major_until: datetime | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    days_left: int | None = None
    last_heartbeat_at: datetime | None = None
    registered_modules: list[str] = []
    restart_required: bool = False


def describe(org_id: str) -> LicenceOut:
    state = current(org_id)
    lic = state.licence
    out = LicenceOut(
        state=state.state,
        mode=state.mode,
        message=state.message,
        install_fingerprint=install_fingerprint(),
        product_version=product_version(),
        modules=list(state.modules),
        days_left=state.days_left,
        registered_modules=list(startup.registered_modules),
    )
    if lic is not None:
        in_use = Subject.objects.filter(org_id=org_id, status="active").count()
        out = out.model_copy(
            update={
                "licence_key": lic.licence_key,
                "customer": lic.customer,
                "tier": lic.tier,
                "instance_kind": lic.instance_kind,
                "seats": lic.seats,
                "subjects_in_use": in_use,
                # Seats are trued up annually, never enforced mid-term (Brief §3.2).
                "seats_exceeded": in_use > lic.seats,
                "max_major_version": lic.max_major_version,
                "effective_major_ceiling": effective_ceiling(lic),
                "trial_major_until": lic.trial_major_until,
                "issued_at": lic.issued_at,
                "expires_at": lic.expires_at,
                "last_heartbeat_at": lic.last_heartbeat_at,
            }
        )
    out.restart_required = sorted(out.modules) != sorted(startup.registered_modules) and (
        lic is not None
    )
    return out


class LicenceStatusIn(BaseModel):
    pass


@action(
    name="licence.status",
    summary="The installed licence, its grace state, the install fingerprint and version.",
    schema=LicenceStatusIn,
    output=LicenceOut,
    permission="licence.view",
    read_only=True,
    http={"method": "GET", "path": "/licence"},
    example={},
)
def status(params: LicenceStatusIn, ctx: ActionContext) -> LicenceOut:
    return describe(ctx.org_id)


class ActivateIn(BaseModel):
    document: str = Field(min_length=10, max_length=20_000, description="The signed licence file.")


class ActivateOut(BaseModel):
    licence: LicenceOut
    file_written: bool
    restart_required: bool
    message: str


def install(org_id: str, terms: Terms, document: str, user_id: int | None) -> Licence:
    """Record a verified licence as the active one, superseding the previous."""
    if terms.install_fingerprint != install_fingerprint():
        raise InvalidInput(
            "This licence was issued for another installation.",
            detail={"install_fingerprint": install_fingerprint()},
        )
    if terms.expires_at <= timezone.now():
        raise InvalidInput(f"This licence expired on {terms.expires_at:%Y-%m-%d}.")
    previous = Licence.objects.select_for_update().filter(org_id=org_id, state="active").first()
    if previous is not None and terms.issued_at < previous.issued_at:
        raise Conflict("A newer licence is already active; an older one cannot replace it.")
    if previous is not None:
        previous.state = "superseded"
        previous.save(update_fields=["state"])
    return Licence.objects.create(
        org_id=org_id,
        install_fingerprint=terms.install_fingerprint,
        licence_key=terms.licence_key,
        customer=terms.customer,
        tier=terms.tier,
        seats=terms.seats,
        modules=list(terms.modules),
        max_major_version=terms.max_major_version,
        instance_kind=terms.instance_kind,
        trial_major_until=terms.trial_major_until,
        issued_at=terms.issued_at,
        expires_at=terms.expires_at,
        last_heartbeat_at=previous.last_heartbeat_at if previous else None,
        key_id=terms.key_id,
        document=document,
        created_by=user_id,
    )


def write_file(document: str) -> bool:
    """Write the licence file the next start reads its modules from. Atomic replace."""
    path = getattr(settings, "KPIGO_LICENCE_FILE", None)
    if not path:
        return False
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".licence-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(document)
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("licence file not written: %s", exc)
        return False
    return True


@action(
    name="licence.activate",
    summary="Activate a signed licence file, offline. Works in every grace and lock state.",
    schema=ActivateIn,
    output=ActivateOut,
    permission="licence.manage",
    read_only=False,
    audit="licence.activated",
    http={"method": "POST", "path": "/licence/activate"},
    example={
        "document": '{"format": "kpigo-licence/1", "key_id": "k", "payload": "", "signature": ""}'
    },
)
def activate(params: ActivateIn, ctx: ActionContext) -> ActivateOut:
    document = params.document.strip()
    try:
        terms = verify(document)
    except InvalidLicence as exc:
        raise InvalidInput(str(exc)) from None
    lic = install(ctx.org_id, terms, document, ctx.user_id)
    ctx.audit(
        "licence.terms",
        licence_key=lic.licence_key,
        modules=lic.modules,
        max_major_version=lic.max_major_version,
        instance_kind=lic.instance_kind,
        expires_at=lic.expires_at.isoformat(),
    )
    written = write_file(document)
    out = describe(ctx.org_id)
    message = "Licence activated."
    if out.restart_required:
        message += " Restart kpiGo so the licensed modules load."
    if not written:
        message += " The licence file could not be written; modules load from it at start."
    return ActivateOut(
        licence=out, file_written=written, restart_required=out.restart_required, message=message
    )


class EntitlementIn(BaseModel):
    target_version: str = Field(min_length=1, max_length=40)


class EntitlementOut(BaseModel):
    target_version: str
    target_major: int
    installed_version: str
    allowed: bool
    ceiling: int | None
    instance_kind: str | None
    trial_active: bool
    reason: str


@action(
    name="licence.entitlement.check",
    summary="Whether this install may apply a version: the max_major_version gate (UP-2/3).",
    schema=EntitlementIn,
    output=EntitlementOut,
    permission="licence.view",
    read_only=True,
    example={"target_version": "2.0.0"},
)
def entitlement(params: EntitlementIn, ctx: ActionContext) -> EntitlementOut:
    try:
        target = major_of(params.target_version)
    except ValueError as exc:
        raise InvalidInput(str(exc)) from None
    installed = product_version()
    lic = active_licence(ctx.org_id)
    base = {
        "target_version": params.target_version,
        "target_major": target,
        "installed_version": installed,
    }
    if lic is None:
        return EntitlementOut(
            **base,
            allowed=False,
            ceiling=None,
            instance_kind=None,
            trial_active=False,
            reason="No licence is installed.",
        )
    ceiling = effective_ceiling(lic)
    trial = ceiling > lic.max_major_version
    if target <= ceiling:
        reason = (
            f"Trial of major {target} allowed on this non-production instance until "
            f"{lic.trial_major_until:%Y-%m-%d}."
            if target > lic.max_major_version
            else f"Major {target} is within the licence (up to {lic.max_major_version})."
        )
        allowed = True
    else:
        reason = (
            f"Entitlement required: major {target} is above this licence's ceiling of {ceiling}."
        )
        allowed = False
    return EntitlementOut(
        **base,
        allowed=allowed,
        ceiling=ceiling,
        instance_kind=lic.instance_kind,
        trial_active=trial,
        reason=reason,
    )


class HeartbeatIn(BaseModel):
    pass


class HeartbeatOut(BaseModel):
    sent: bool
    ok: bool
    renewed: bool
    message: str


# Tests swap in an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None


@action(
    name="licence.heartbeat",
    summary="Send the licence heartbeat, the only outbound call kpiGo makes.",
    schema=HeartbeatIn,
    output=HeartbeatOut,
    permission="licence.manage",
    read_only=False,
    audit="licence.heartbeat",
    example={},
)
def heartbeat(params: HeartbeatIn, ctx: ActionContext) -> HeartbeatOut:
    """Sends the licence key, install fingerprint and product version. Nothing else.

    A failed heartbeat changes nothing: air-gapped installs never send one and
    run on their offline activation.
    """
    url = getattr(settings, "KPIGO_LICENCE_HEARTBEAT_URL", None)
    lic = active_licence(ctx.org_id)
    if not url:
        return HeartbeatOut(sent=False, ok=False, renewed=False, message="No heartbeat URL set.")
    if lic is None:
        return HeartbeatOut(sent=False, ok=False, renewed=False, message="No licence installed.")
    body = {
        "licence_key": lic.licence_key,
        "install_fingerprint": lic.install_fingerprint,
        "product_version": product_version(),
    }
    try:
        with httpx.Client(transport=TRANSPORT, timeout=10.0) as client:
            response = client.post(url, json=body)
            response.raise_for_status()
            reply = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return HeartbeatOut(sent=True, ok=False, renewed=False, message=f"Heartbeat failed: {exc}")
    lic.last_heartbeat_at = timezone.now()
    lic.save(update_fields=["last_heartbeat_at"])
    renewed = False
    document = reply.get("licence") if isinstance(reply, dict) else None
    if isinstance(document, str) and document.strip() != lic.document:
        try:
            terms = verify(document.strip())
            if terms.issued_at > lic.issued_at:
                install(ctx.org_id, terms, document.strip(), ctx.user_id)
                write_file(document.strip())
                renewed = True
        except (InvalidLicence, InvalidInput, Conflict) as exc:
            logger.warning("heartbeat licence ignored: %s", exc)
    return HeartbeatOut(
        sent=True,
        ok=True,
        renewed=renewed,
        message="Licence renewed." if renewed else "Heartbeat acknowledged.",
    )
