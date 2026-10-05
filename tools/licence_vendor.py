#!/usr/bin/env python3
"""kpiGo's licence signing tool. Vendor side only: never shipped to or run by a client.

    python tools/licence_vendor.py keygen --key-id kpigo-2026 --out kpigo-2026.key
    python tools/licence_vendor.py sign --key kpigo-2026.key --key-id kpigo-2026 \\
        --licence-key KPG-ACME-0001 --customer "Acme Bank" --fingerprint ABCD-... \\
        --modules scorecards,agent_performance --seats 500 --max-major 1 \\
        --instance-kind production --expires 2027-10-31 > acme.lic

``keygen`` writes the private key (keep it offline, in the vendor's vault) and
prints the line to add to ``TRUSTED_KEYS`` in ``kpigo/licence/document.py``.
Standard library plus ``cryptography``, so it runs on a laptop without Django.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import UTC, datetime, time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

FORMAT = "kpigo-licence/1"


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


def sign(key: Ed25519PrivateKey, key_id: str, terms: dict[str, object]) -> str:
    payload = json.dumps(terms, sort_keys=True, separators=(",", ":")).encode()
    return json.dumps(
        {
            "format": FORMAT,
            "key_id": key_id,
            "payload": b64url(payload),
            "signature": b64url(key.sign(payload)),
        },
        indent=2,
    )


def _day_end(value: str) -> str:
    return datetime.combine(datetime.fromisoformat(value).date(), time(23, 59, 59), UTC).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("keygen", help="Generate a signing key pair.")
    gen.add_argument("--key-id", required=True)
    gen.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("sign", help="Sign a licence for one install.")
    s.add_argument("--key", type=Path, required=True)
    s.add_argument("--key-id", required=True)
    s.add_argument("--licence-key", required=True)
    s.add_argument("--customer", default="")
    s.add_argument("--tier", default="")
    s.add_argument("--fingerprint", required=True, help="From the client's licence screen.")
    s.add_argument("--modules", required=True, help="Comma-separated module names.")
    s.add_argument("--seats", type=int, required=True)
    s.add_argument("--max-major", type=int, required=True)
    s.add_argument("--instance-kind", choices=["production", "non_production"], required=True)
    s.add_argument("--trial-major-until", help="UAT only: date the +1 major trial ends.")
    s.add_argument("--expires", required=True, help="Last day of the term, YYYY-MM-DD.")
    args = parser.parse_args(argv)

    if args.command == "keygen":
        print(keygen(args.key_id, args.out))
        print(f"Private key written to {args.out}. Keep it offline.", file=sys.stderr)
        return 0
    terms: dict[str, object] = {
        "licence_key": args.licence_key,
        "customer": args.customer,
        "tier": args.tier,
        "install_fingerprint": args.fingerprint,
        "modules": sorted({m.strip() for m in args.modules.split(",") if m.strip()}),
        "seats": args.seats,
        "max_major_version": args.max_major,
        "instance_kind": args.instance_kind,
        "trial_major_until": _day_end(args.trial_major_until) if args.trial_major_until else None,
        "issued_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "expires_at": _day_end(args.expires),
    }
    print(sign(load_key(args.key), args.key_id, terms))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
