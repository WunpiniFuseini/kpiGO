"""Fernet encryption for source credentials (TDD §5.2).

Keys come from the environment (``KPIGO_CREDENTIAL_KEYS``, comma-separated) or
the host's secret mount (``KPIGO_CREDENTIAL_KEY_FILE``, one key per line). The
first key encrypts; every key decrypts, so a key is rotated by putting the new
one first and re-saving each connection. A development or test install with no
key configured derives one from ``SECRET_KEY``; production never does.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

from kpigo.action.errors import ActionError


class CredentialKeyMissing(ActionError):
    code = "credential_key_missing"
    http_status = 503


def _configured_keys() -> list[str]:
    keys = list(getattr(settings, "KPIGO_CREDENTIAL_KEYS", []) or [])
    key_file = getattr(settings, "KPIGO_CREDENTIAL_KEY_FILE", None)
    if key_file:
        path = Path(key_file)
        if path.exists():
            keys += [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return keys


def _fernet() -> MultiFernet:
    keys = _configured_keys()
    if not keys and getattr(settings, "KPIGO_CREDENTIAL_DEV_KEY", False):
        digest = hashlib.sha256(f"kpigo-credentials:{settings.SECRET_KEY}".encode()).digest()
        keys = [base64.urlsafe_b64encode(digest).decode()]
    if not keys:
        raise CredentialKeyMissing(
            "No credential key is configured. Set KPIGO_CREDENTIAL_KEYS or "
            "KPIGO_CREDENTIAL_KEY_FILE before saving a connection."
        )
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except ValueError:
        raise CredentialKeyMissing(
            "A configured credential key is not a valid Fernet key."
        ) from None


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise CredentialKeyMissing(
            "The stored credential cannot be decrypted with the configured keys."
        ) from None


def generate_key() -> str:
    return Fernet.generate_key().decode()
