#!/usr/bin/env python3
"""The offline update/install bundle manifest: the contract the update service reads.

A bundle is a single tar carrying the image set (``docker save``), the manifest,
and nothing that reaches the internet. The manifest names the versions, the path
constraint, and a SHA-256 over each artefact so a carried-in bundle can be proven
intact before anything is loaded:

    {"format": "kpigo-bundle/1",
     "to_version": "1.2.0", "from_version": "", "min_from_version": "1.0.0",
     "artefacts": [{"name": "images.tar", "sha256": "..."}, ...],
     "key_id": "<set by the signer>", "signature": "<base64url, set by the signer>"}

This module is standard-library only (``build_manifest``/``read_manifest`` run on
the vendor's build box and on the client's air-gapped install host alike). Signing
and verifying against a trusted key live in ``tools/bundle_vendor.py`` and in the
app's update service, which import the canonical field layout from here.

    python tools/bundle.py manifest --to 1.2.0 --min-from 1.0.0 \\
        --artefact dist/images.tar > dist/manifest.json
    python tools/bundle.py verify --manifest dist/manifest.json --root dist
    python tools/bundle.py show --manifest dist/manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

MANIFEST_FORMAT = "kpigo-bundle/1"
_CHUNK = 1 << 20

# Fields the update service requires to be present and well-formed. Signature and
# key_id are optional in the raw manifest (the signer adds them), everything else
# must be there before a bundle is considered loadable.
REQUIRED = ("format", "to_version", "min_from_version", "artefacts")


class ManifestError(ValueError):
    """The manifest is malformed, or an artefact does not match its recorded digest."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    to_version: str,
    artefacts: list[Path],
    *,
    from_version: str = "",
    min_from_version: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    """Describe a bundle: versions, path floor, and a digest per artefact.

    ``name`` is recorded relative to ``root`` (the directory the bundle unpacks
    into) so the client verifies by the same relative path it carries in.
    """
    if not to_version:
        raise ManifestError("A bundle needs a to_version.")
    base = root or Path.cwd()
    recorded = []
    for artefact in artefacts:
        if not artefact.is_file():
            raise ManifestError(f"Artefact is missing: {artefact}")
        name = artefact.relative_to(base).as_posix() if root else artefact.name
        recorded.append({"name": name, "sha256": sha256_file(artefact)})
    return {
        "format": MANIFEST_FORMAT,
        "to_version": to_version,
        "from_version": from_version,
        "min_from_version": min_from_version or to_version,
        "artefacts": recorded,
    }


def read_manifest(path: Path) -> dict[str, Any]:
    """Load and shape-check a manifest. Does not verify checksums or signature."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"The manifest could not be read: {exc}") from None
    if not isinstance(data, dict):
        raise ManifestError("The manifest is not a JSON object.")
    missing = [key for key in REQUIRED if key not in data]
    if missing:
        raise ManifestError(f"The manifest is missing: {', '.join(missing)}.")
    if data["format"] != MANIFEST_FORMAT:
        raise ManifestError(f"Unknown bundle format: {data['format']!r}.")
    artefacts = data["artefacts"]
    if not isinstance(artefacts, list) or not artefacts:
        raise ManifestError("The manifest lists no artefacts.")
    for entry in artefacts:
        if not isinstance(entry, dict) or "name" not in entry or "sha256" not in entry:
            raise ManifestError("Each artefact needs a name and a sha256.")
    return data


def verify_artefacts(manifest: dict[str, Any], root: Path) -> None:
    """Prove every artefact beside the manifest matches its recorded digest.

    Raises ``ManifestError`` on the first mismatch or missing file, so a tampered
    or truncated carried-in bundle is refused before any image is loaded.
    """
    for entry in manifest["artefacts"]:
        artefact = root / entry["name"]
        if not artefact.is_file():
            raise ManifestError(f"Artefact named in the manifest is missing: {entry['name']}")
        actual = sha256_file(artefact)
        if actual != entry["sha256"]:
            raise ManifestError(
                f"Artefact {entry['name']} does not match the manifest "
                f"(expected {entry['sha256'][:12]}…, got {actual[:12]}…)."
            )


def _cmd_manifest(args: argparse.Namespace) -> int:
    manifest = build_manifest(
        args.to,
        [Path(a) for a in args.artefact],
        from_version=args.from_version,
        min_from_version=args.min_from,
        root=Path(args.root) if args.root else None,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    manifest = read_manifest(Path(args.manifest))
    verify_artefacts(manifest, Path(args.root))
    print(
        f"OK: {len(manifest['artefacts'])} artefact(s) match the manifest for "
        f"{manifest['to_version']}."
    )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    manifest = read_manifest(Path(args.manifest))
    signed = "signed" if manifest.get("signature") else "unsigned"
    print(
        f"{manifest['to_version']} ({signed}), "
        f"from ≥{manifest['min_from_version']}, {len(manifest['artefacts'])} artefact(s)"
    )
    for entry in manifest["artefacts"]:
        print(f"  {entry['name']}  {entry['sha256']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("manifest", help="Build a manifest over a bundle's artefacts.")
    m.add_argument("--to", required=True, help="to_version, e.g. 1.2.0.")
    m.add_argument("--from", dest="from_version", default="", help="from_version, if known.")
    m.add_argument("--min-from", default="", help="Lowest installed version that may apply this.")
    m.add_argument("--root", default="", help="Directory artefact names are recorded relative to.")
    m.add_argument("--artefact", action="append", required=True, help="An artefact file (repeat).")
    m.set_defaults(func=_cmd_manifest)

    v = sub.add_parser("verify", help="Check artefacts beside a manifest against their digests.")
    v.add_argument("--manifest", required=True)
    v.add_argument("--root", required=True, help="Directory the artefacts sit in.")
    v.set_defaults(func=_cmd_verify)

    s = sub.add_parser("show", help="Print a manifest's versions and artefacts.")
    s.add_argument("--manifest", required=True)
    s.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
