#!/usr/bin/env python3
"""Sign a kpiGo update bundle. Vendor side only: never shipped to or run by a client.

    python tools/bundle_vendor.py keygen --key-id kpigo-bundle-2026 --out bundle.key
    python tools/bundle.py manifest --to 1.2.0 --min-from 1.0.0 \\
        --artefact dist/images.tar --root dist > dist/manifest.json
    python tools/bundle_vendor.py sign --key bundle.key --key-id kpigo-bundle-2026 \\
        --manifest dist/manifest.json > dist/manifest.signed.json

``keygen`` writes the private key (keep it offline, in the vendor's vault) and
prints the line to add to ``BUNDLE_TRUSTED_KEYS`` in ``kpigo/platform/updates.py``.
``sign`` adds ``key_id`` and ``signature`` to a manifest built by ``tools/bundle.py``;
the signed bytes are the manifest without those two fields (``bundle.canonical_payload``),
so the client's update service verifies byte-for-byte. Standard library plus
``cryptography``, so it runs on a laptop without Django.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# The canonical signed-payload layout the client's verifier also uses, so the two
# can never disagree about which bytes a signature covers. Run from the repo root
# (``python tools/bundle_vendor.py …``) so this sibling module is importable.
from tools.bundle import canonical_payload, read_manifest


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def public_line(key: Ed25519PrivateKey, key_id: str) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return f'    "{key_id}": "{base64.b64encode(raw).decode()}",'


def keygen(key_id: str, out: Path) -> str:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    out.write_bytes(pem)
    out.chmod(0o600)
    return public_line(key, key_id)


def load_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("The key is not an Ed25519 private key.")
    return key


def sign_manifest(key: Ed25519PrivateKey, key_id: str, manifest: dict[str, object]) -> str:
    body = {k: v for k, v in manifest.items() if k not in ("key_id", "signature")}
    signed = {**body, "key_id": key_id, "signature": b64url(key.sign(canonical_payload(body)))}
    return json.dumps(signed, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("keygen", help="Generate a bundle-signing key pair.")
    gen.add_argument("--key-id", required=True)
    gen.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("sign", help="Sign a manifest built by tools/bundle.py.")
    s.add_argument("--key", type=Path, required=True)
    s.add_argument("--key-id", required=True)
    s.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "keygen":
        print(keygen(args.key_id, args.out))
        print(f"Private key written to {args.out}. Keep it offline.", file=sys.stderr)
        return 0
    manifest = read_manifest(args.manifest)
    print(sign_manifest(load_key(args.key), args.key_id, manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
