"""The offline bundle manifest (tools/bundle.py): build, read, and verify.

These run without Django — the manifest tooling is standard-library only so it
works on the vendor's build box and the client's air-gapped install host alike.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import bundle


def _artefact(root: Path, name: str, content: bytes) -> Path:
    path = root / name
    path.write_bytes(content)
    return path


def test_build_manifest_records_versions_and_digests(tmp_path: Path) -> None:
    images = _artefact(tmp_path, "images.tar", b"image-set-bytes")
    compose = _artefact(tmp_path, "compose.yaml", b"services: {}")

    manifest = bundle.build_manifest(
        "1.2.0", [images, compose], from_version="1.1.0", min_from_version="1.0.0", root=tmp_path
    )

    assert manifest["format"] == bundle.MANIFEST_FORMAT
    assert manifest["to_version"] == "1.2.0"
    assert manifest["from_version"] == "1.1.0"
    assert manifest["min_from_version"] == "1.0.0"
    names = {a["name"]: a["sha256"] for a in manifest["artefacts"]}
    assert set(names) == {"images.tar", "compose.yaml"}
    assert names["images.tar"] == bundle.sha256_file(images)


def test_min_from_defaults_to_to_version(tmp_path: Path) -> None:
    images = _artefact(tmp_path, "images.tar", b"x")
    manifest = bundle.build_manifest("2.0.0", [images], root=tmp_path)
    assert manifest["min_from_version"] == "2.0.0"


def test_build_manifest_rejects_missing_artefact(tmp_path: Path) -> None:
    with pytest.raises(bundle.ManifestError, match="missing"):
        bundle.build_manifest("1.0.0", [tmp_path / "absent.tar"], root=tmp_path)


def test_build_manifest_requires_to_version(tmp_path: Path) -> None:
    images = _artefact(tmp_path, "images.tar", b"x")
    with pytest.raises(bundle.ManifestError, match="to_version"):
        bundle.build_manifest("", [images], root=tmp_path)


def test_verify_passes_on_intact_bundle(tmp_path: Path) -> None:
    images = _artefact(tmp_path, "images.tar", b"image-set-bytes")
    manifest = bundle.build_manifest("1.0.0", [images], root=tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    loaded = bundle.read_manifest(tmp_path / "manifest.json")
    bundle.verify_artefacts(loaded, tmp_path)  # does not raise


def test_verify_refuses_a_tampered_artefact(tmp_path: Path) -> None:
    images = _artefact(tmp_path, "images.tar", b"image-set-bytes")
    manifest = bundle.build_manifest("1.0.0", [images], root=tmp_path)
    images.write_bytes(b"tampered")  # change after the digest was taken

    with pytest.raises(bundle.ManifestError, match="does not match"):
        bundle.verify_artefacts(manifest, tmp_path)


def test_verify_refuses_a_missing_artefact(tmp_path: Path) -> None:
    images = _artefact(tmp_path, "images.tar", b"bytes")
    manifest = bundle.build_manifest("1.0.0", [images], root=tmp_path)
    images.unlink()

    with pytest.raises(bundle.ManifestError, match="missing"):
        bundle.verify_artefacts(manifest, tmp_path)


def test_read_manifest_rejects_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "format": "other/9",
                "to_version": "1.0.0",
                "min_from_version": "1.0.0",
                "artefacts": [{"name": "x", "sha256": "y"}],
            }
        )
    )
    with pytest.raises(bundle.ManifestError, match="format"):
        bundle.read_manifest(path)


def test_read_manifest_reports_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"format": bundle.MANIFEST_FORMAT}))
    with pytest.raises(bundle.ManifestError, match="missing"):
        bundle.read_manifest(path)


def test_cli_manifest_then_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    images = _artefact(tmp_path, "images.tar", b"image-set-bytes")
    manifest_path = tmp_path / "manifest.json"

    rc = bundle.main(
        ["manifest", "--to", "1.0.0", "--root", str(tmp_path), "--artefact", str(images)]
    )
    assert rc == 0
    manifest_path.write_text(capsys.readouterr().out)

    rc = bundle.main(["verify", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    assert "match the manifest" in capsys.readouterr().out


def test_cli_verify_fails_on_tamper(tmp_path: Path) -> None:
    images = _artefact(tmp_path, "images.tar", b"bytes")
    manifest = bundle.build_manifest("1.0.0", [images], root=tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    images.write_bytes(b"changed")

    rc = bundle.main(
        ["verify", "--manifest", str(tmp_path / "manifest.json"), "--root", str(tmp_path)]
    )
    assert rc == 2
