"""The signed licence file: Ed25519 over a JSON payload.

The file is plain JSON so an infra team can read what they were issued::

    {"format": "kpigo-licence/1", "key_id": "...", "payload": "<base64url JSON>",
     "signature": "<base64url Ed25519 signature over the payload bytes>"}

Only kpiGo holds the private key; the product ships the public keys it trusts.
``tools/licence_vendor.py`` generates keys and signs licences, offline.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from kpigo.action.definition import MODULES

FORMAT = "kpigo-licence/1"
LICENSABLE_MODULES = tuple(m for m in MODULES if m != "platform")

# key_id -> raw Ed25519 public key, base64. kpiGo's release process adds its
# production key here (``tools/licence_vendor.py keygen`` prints the line).
# Rotating keys means shipping the new one alongside the old before signing with it.
TRUSTED_KEYS: dict[str, str] = {}


class InvalidLicence(ValueError):
    """The file is malformed, unsigned by a trusted key, or its terms are invalid."""


@dataclass(frozen=True)
class Terms:
    key_id: str
    licence_key: str
    customer: str
    install_fingerprint: str
    tier: str
    seats: int
    modules: tuple[str, ...]
    max_major_version: int
    instance_kind: str
    trial_major_until: datetime | None
    issued_at: datetime
    expires_at: datetime


def b64decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _when(value: Any, name: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise InvalidLicence(f"'{name}' must be an ISO 8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InvalidLicence(f"'{name}' must be an ISO 8601 timestamp.") from None
    if parsed.tzinfo is None:
        raise InvalidLicence(f"'{name}' must carry a timezone.")
    return parsed


def verify(text: str) -> Terms:
    """Check the signature against a trusted key and parse the terms."""
    try:
        envelope = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise InvalidLicence("The licence is not a kpiGo licence file.") from None
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
        raise InvalidLicence("The licence is not a kpiGo licence file.")
    key_id = envelope.get("key_id")
    public = TRUSTED_KEYS.get(key_id) if isinstance(key_id, str) else None
    if public is None:
        raise InvalidLicence("The licence is signed with a key this version does not trust.")
    try:
        payload = b64decode(str(envelope.get("payload", "")))
        signature = b64decode(str(envelope.get("signature", "")))
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(signature, payload)
    except (InvalidSignature, binascii.Error, ValueError):
        raise InvalidLicence("The licence signature does not verify.") from None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        raise InvalidLicence("The licence payload is not JSON.") from None
    if not isinstance(data, dict):
        raise InvalidLicence("The licence payload is not an object.")
    return _terms(str(key_id), data)


def _terms(key_id: str, data: dict[str, Any]) -> Terms:
    modules = data.get("modules")
    if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
        raise InvalidLicence("'modules' must be a list of module names.")
    unknown = sorted(set(modules) - set(LICENSABLE_MODULES))
    if unknown:
        raise InvalidLicence(f"The licence names unknown modules: {', '.join(unknown)}.")
    kind = data.get("instance_kind")
    if kind not in ("production", "non_production"):
        raise InvalidLicence("'instance_kind' must be production or non_production.")
    for name in ("seats", "max_major_version"):
        value = data.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InvalidLicence(f"'{name}' must be a whole number.")
    for name in ("licence_key", "install_fingerprint"):
        if not isinstance(data.get(name), str) or not data[name]:
            raise InvalidLicence(f"'{name}' is required.")
    issued = _when(data.get("issued_at"), "issued_at")
    expires = _when(data.get("expires_at"), "expires_at")
    assert issued is not None and expires is not None
    if expires <= issued:
        raise InvalidLicence("The licence expires before it is issued.")
    return Terms(
        key_id=key_id,
        licence_key=data["licence_key"],
        customer=str(data.get("customer", "")),
        install_fingerprint=data["install_fingerprint"],
        tier=str(data.get("tier", "")),
        seats=int(data["seats"]),
        modules=tuple(sorted(set(modules))),
        max_major_version=int(data["max_major_version"]),
        instance_kind=str(kind),
        trial_major_until=_when(data.get("trial_major_until"), "trial_major_until", optional=True),
        issued_at=issued,
        expires_at=expires,
    )
