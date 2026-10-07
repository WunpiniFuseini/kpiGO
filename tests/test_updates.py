"""The offline update service (PRD UP-*, TDD §12.1): verify, entitle, path, gate, ledger."""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import override_settings
from django.utils import timezone

from kpigo.licence.models import Licence
from kpigo.licence.state import install_fingerprint
from kpigo.platform import backup as backup_mod
from kpigo.platform import updates as updates_mod
from tests.conftest import ORG_ID, role_ctx, run
from tools import bundle as bundle_tool
from tools import bundle_vendor

pytestmark = pytest.mark.django_db

KEY = Ed25519PrivateKey.generate()
KEY_ID = "test-bundle-2026"


def _public_b64() -> str:
    raw = KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


@pytest.fixture(autouse=True)
def trusted_bundle_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates_mod, "BUNDLE_TRUSTED_KEYS", {KEY_ID: _public_b64()})
    # Pin the installed version so path and upgrade checks are deterministic.
    monkeypatch.setattr("kpigo.licence.state.product_version", lambda: "1.0.0")


@pytest.fixture
def licensed() -> Licence:
    now = timezone.now()
    return Licence.objects.create(
        org_id=ORG_ID,
        install_fingerprint=install_fingerprint(),
        licence_key="KPG-TEST-UPD",
        seats=100,
        modules=["scorecards"],
        max_major_version=1,
        instance_kind="production",
        issued_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=365),
        state="active",
        key_id="k",
        document="",
    )


def _fake_dump(target: Path, dsn: dict[str, str]) -> None:
    target.write_bytes(b"PGDMP fake dump bytes")


def stage_bundle(
    directory: Path,
    *,
    to_version: str = "1.2.0",
    min_from: str = "1.0.0",
    sign: bool = True,
) -> Path:
    """Write a signed manifest.json beside a fake images.tar, as a carried-in bundle would."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "images.tar").write_bytes(b"fake docker save image set")
    manifest = bundle_tool.build_manifest(
        to_version,
        [directory / "images.tar"],
        min_from_version=min_from,
        root=directory,
    )
    text = bundle_vendor.sign_manifest(KEY, KEY_ID, manifest) if sign else json.dumps(manifest)
    (directory / "manifest.json").write_text(text)
    return directory


def test_check_reports_ready_for_a_good_bundle(licensed: Licence, tmp_path: Path) -> None:
    stage_bundle(tmp_path)
    with override_settings(KPIGO_UPDATE_DIR=str(tmp_path)):
        out = run("system.update.check", role_ctx("admin"))
    assert out.found and out.signature_ok and out.artefacts_ok
    assert out.entitled and out.path_ok and out.is_upgrade
    assert out.ready is True
    assert out.to_version == "1.2.0"


def test_check_without_a_bundle_is_not_found(tmp_path: Path) -> None:
    with override_settings(KPIGO_UPDATE_DIR=str(tmp_path)):
        out = run("system.update.check", role_ctx("admin"))
    assert out.found is False and out.ready is False


def test_untrusted_signature_is_refused(licensed: Licence, tmp_path: Path) -> None:
    stage_bundle(tmp_path, sign=False)
    with override_settings(KPIGO_UPDATE_DIR=str(tmp_path)):
        out = run("system.update.check", role_ctx("admin"))
    assert out.signature_ok is False and out.ready is False
    assert "trust" in out.reason.lower()


def test_tampered_artefact_is_detected(licensed: Licence, tmp_path: Path) -> None:
    stage_bundle(tmp_path)
    (tmp_path / "images.tar").write_bytes(b"swapped after signing")
    with override_settings(KPIGO_UPDATE_DIR=str(tmp_path)):
        out = run("system.update.check", role_ctx("admin"))
    # The signature still verifies (manifest untouched), but the artefact does not.
    assert out.signature_ok is True and out.artefacts_ok is False
    assert out.ready is False


def test_entitlement_blocks_a_major_above_the_ceiling(licensed: Licence, tmp_path: Path) -> None:
    stage_bundle(tmp_path, to_version="2.0.0")
    with override_settings(KPIGO_UPDATE_DIR=str(tmp_path)):
        out = run("system.update.check", role_ctx("admin"))
    assert out.entitled is False and out.ready is False
    assert out.ceiling == 1


def test_path_floor_blocks_a_skipped_step(licensed: Licence, tmp_path: Path) -> None:
    stage_bundle(tmp_path, to_version="1.9.0", min_from="1.5.0")
    with override_settings(KPIGO_UPDATE_DIR=str(tmp_path)):
        out = run("system.update.check", role_ctx("admin"))
    assert out.path_ok is False and out.ready is False


def test_apply_takes_a_pre_upgrade_backup_and_records_the_ledger(
    monkeypatch: pytest.MonkeyPatch, licensed: Licence, tmp_path: Path
) -> None:
    from kpigo.platform.models import BackupRun, VersionHistory

    monkeypatch.setattr(backup_mod, "_pg_dump", _fake_dump)
    bundle_dir = stage_bundle(tmp_path / "update")
    with override_settings(
        KPIGO_UPDATE_DIR=str(bundle_dir), KPIGO_BACKUP_DIR=str(tmp_path / "backups")
    ):
        out = run("system.update.apply", role_ctx("admin"), confirm_version="1.2.0")

    assert out.authorised is True and out.backup_id is not None
    assert out.next_steps and any("migrate" in step for step in out.next_steps)
    row = VersionHistory.objects.get(version_id=out.record.version_id)
    assert row.outcome == "authorised" and row.to_version == "1.2.0"
    assert row.from_version == "1.0.0"
    assert row.backup_id is not None
    backup = BackupRun.objects.get(backup_id=row.backup_id)
    assert backup.kind == "pre_upgrade" and backup.outcome == "ok"


def test_apply_refuses_a_bundle_that_is_not_ready(licensed: Licence, tmp_path: Path) -> None:
    from kpigo.action.errors import InvalidInput

    stage_bundle(tmp_path, to_version="2.0.0")  # above the ceiling
    with override_settings(KPIGO_UPDATE_DIR=str(tmp_path)), pytest.raises(InvalidInput):
        run("system.update.apply", role_ctx("admin"), confirm_version="2.0.0")


def test_apply_rejects_a_version_mismatch(licensed: Licence, tmp_path: Path) -> None:
    from kpigo.action.errors import InvalidInput

    stage_bundle(tmp_path)
    with override_settings(KPIGO_UPDATE_DIR=str(tmp_path)), pytest.raises(InvalidInput):
        run("system.update.apply", role_ctx("admin"), confirm_version="9.9.9")


def test_apply_records_failed_when_the_mandatory_backup_fails(
    monkeypatch: pytest.MonkeyPatch, licensed: Licence, tmp_path: Path
) -> None:
    from kpigo.platform.models import VersionHistory

    def _boom(target: Path, dsn: dict[str, str]) -> None:
        raise RuntimeError("pg_dump exploded")

    monkeypatch.setattr(backup_mod, "_pg_dump", _boom)
    bundle_dir = stage_bundle(tmp_path / "update")
    with override_settings(
        KPIGO_UPDATE_DIR=str(bundle_dir), KPIGO_BACKUP_DIR=str(tmp_path / "backups")
    ):
        out = run("system.update.apply", role_ctx("admin"), confirm_version="1.2.0")

    assert out.authorised is False and out.backup_id is None
    assert VersionHistory.objects.get(version_id=out.record.version_id).outcome == "failed"


def test_history_lists_authorisations(
    monkeypatch: pytest.MonkeyPatch, licensed: Licence, tmp_path: Path
) -> None:
    monkeypatch.setattr(backup_mod, "_pg_dump", _fake_dump)
    bundle_dir = stage_bundle(tmp_path / "update")
    with override_settings(
        KPIGO_UPDATE_DIR=str(bundle_dir), KPIGO_BACKUP_DIR=str(tmp_path / "backups")
    ):
        run("system.update.apply", role_ctx("admin"), confirm_version="1.2.0")
        listing = run("system.update.history", role_ctx("admin"), limit=10)
    assert listing.updates and listing.updates[0].outcome == "authorised"


def test_health_surfaces_a_staged_update(licensed: Licence, tmp_path: Path) -> None:
    stage_bundle(tmp_path)
    with override_settings(KPIGO_UPDATE_DIR=str(tmp_path)):
        out = run("system.health", role_ctx("admin"), check_workers=False)
    assert out.update is not None
    assert out.update.available is True and out.update.to_version == "1.2.0"
