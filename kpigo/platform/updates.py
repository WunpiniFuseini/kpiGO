"""The offline update service: prove a carried-in bundle before anything is applied.

An upgrade arrives as a single bundle on the install host (``docker save`` images
plus a signed manifest, no network). Before a byte is loaded, kpiGo checks, in the
order the TDD lays down (§12.1):

    verify signature → verify checksums → entitlement (max_major_version) →
    upgrade path (min_from_version) → mandatory backup → (operator applies)

Only the first four are decided here; ``system.update.apply`` adds the mandatory
pre-upgrade backup and the ledger entry. The mechanical steps an action cannot do
— loading images, flipping the env-driven maintenance flag, running migrations,
restarting — stay with the operator and ``scripts/apply_update.sh``, so the audited
authorisation and the running of the upgrade never drift apart.

Signatures reuse the licence service's Ed25519 scheme (``tools/bundle_vendor.py``
signs; this module verifies against the keys the release trusts). The canonical
signed-payload layout lives in ``tools/bundle.py`` so the signer and the verifier
cannot disagree.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings

from tools.bundle import ManifestError, canonical_payload, read_manifest, verify_artefacts

MANIFEST_NAME = "manifest.json"

# key_id -> raw Ed25519 public key, base64. kpiGo's release process adds its
# bundle-signing public key here (``tools/bundle_vendor.py keygen`` prints the
# line); the private half never leaves the vendor's vault. Empty by default, so
# an unsigned or wrongly-signed bundle is refused until a key is shipped.
BUNDLE_TRUSTED_KEYS: dict[str, str] = {}


def _b64decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def signature_verifies(manifest: dict[str, Any]) -> bool:
    """True when the manifest is signed by a key this release trusts.

    Never raises on an untrusted or malformed signature: a bundle that does not
    verify is simply not trusted, which the caller reports rather than crashing on.
    """
    key_id = manifest.get("key_id")
    public = BUNDLE_TRUSTED_KEYS.get(key_id) if isinstance(key_id, str) else None
    if public is None:
        return False
    signature = manifest.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(
            _b64decode(signature), canonical_payload(manifest)
        )
    except (InvalidSignature, binascii.Error, ValueError):
        return False
    return True


def parse_version(value: str) -> tuple[int, ...]:
    """A dotted version as a comparable tuple. Raises ``ValueError`` on anything else."""
    head = value.strip().lstrip("vV")
    if not head:
        raise ValueError(f"'{value}' is not a version number.")
    parts = head.split(".")
    if not all(p.isdigit() for p in parts):
        raise ValueError(f"'{value}' is not a version number.")
    return tuple(int(p) for p in parts)


@dataclass(frozen=True)
class BundleCheck:
    """The result of inspecting the update directory, every gate decided or skipped.

    ``ready`` is the conjunction a caller acts on: a trusted, intact, entitled
    bundle whose floor this install clears and whose version is actually ahead.
    ``reason`` is the one sentence an operator reads when it is not ready.
    """

    found: bool
    installed_version: str
    to_version: str = ""
    from_version: str = ""
    min_from_version: str = ""
    key_id: str = ""
    signature_ok: bool = False
    artefacts_ok: bool = False
    entitled: bool = False
    ceiling: int | None = None
    path_ok: bool = False
    is_upgrade: bool = False
    ready: bool = False
    reason: str = ""


def update_dir() -> Path | None:
    path = getattr(settings, "KPIGO_UPDATE_DIR", None)
    return Path(str(path)) if path else None


def check_bundle(org_id: str, directory: Path | None = None) -> BundleCheck:
    """Inspect the bundle in the update directory and decide every gate.

    Fails soft throughout: a missing directory, an unreadable manifest, an
    untrusted signature or a tampered artefact each produce a ``BundleCheck`` with
    the right flag false and a reason, never an exception.
    """
    from kpigo.licence.state import active_licence, effective_ceiling, major_of, product_version

    installed = product_version()
    folder = directory or update_dir()
    if folder is None or not (folder / MANIFEST_NAME).is_file():
        return BundleCheck(
            found=False,
            installed_version=installed,
            reason="No update bundle is staged. Unpack a signed bundle into the update directory.",
        )

    try:
        manifest = read_manifest(folder / MANIFEST_NAME)
    except ManifestError as exc:
        return BundleCheck(found=True, installed_version=installed, reason=str(exc))

    to_version = str(manifest["to_version"])
    min_from = str(manifest["min_from_version"])
    key_id = str(manifest.get("key_id", ""))

    signature_ok = signature_verifies(manifest)
    try:
        verify_artefacts(manifest, folder)
        artefacts_ok = True
    except ManifestError:
        artefacts_ok = False

    entitled = False
    ceiling: int | None = None
    licence = active_licence(org_id)
    if licence is not None:
        ceiling = effective_ceiling(licence)
        try:
            entitled = major_of(to_version) <= ceiling
        except ValueError:
            entitled = False

    path_ok = is_upgrade = False
    try:
        installed_v = parse_version(installed)
        path_ok = installed_v >= parse_version(min_from)
        is_upgrade = parse_version(to_version) > installed_v
    except ValueError:
        pass

    ready = signature_ok and artefacts_ok and entitled and path_ok and is_upgrade
    return BundleCheck(
        found=True,
        installed_version=installed,
        to_version=to_version,
        from_version=str(manifest.get("from_version", "")),
        min_from_version=min_from,
        key_id=key_id,
        signature_ok=signature_ok,
        artefacts_ok=artefacts_ok,
        entitled=entitled,
        ceiling=ceiling,
        path_ok=path_ok,
        is_upgrade=is_upgrade,
        ready=ready,
        reason=_reason(
            signature_ok, artefacts_ok, entitled, path_ok, is_upgrade, to_version, min_from, ceiling
        ),
    )


def _reason(
    signature_ok: bool,
    artefacts_ok: bool,
    entitled: bool,
    path_ok: bool,
    is_upgrade: bool,
    to_version: str,
    min_from: str,
    ceiling: int | None,
) -> str:
    if not signature_ok:
        return "The bundle is not signed by a key this version trusts; refusing it."
    if not artefacts_ok:
        return "A bundle artefact does not match the manifest; the bundle may be corrupt."
    if not entitled:
        limit = (
            "no licence is installed" if ceiling is None else f"the licence ceiling is {ceiling}"
        )
        return f"Not entitled to {to_version}: {limit}."
    if not path_ok:
        return f"This install is below the bundle's floor of {min_from}; upgrade in steps."
    if not is_upgrade:
        return f"{to_version} is not ahead of the installed version; nothing to apply."
    return f"Ready to apply {to_version}."
