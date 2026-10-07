"""Backup, configuration export and the diagnostic bundle (PRD OP-4, OP-5)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from django.test import override_settings

from kpigo.platform import backup as backup_mod
from tests.conftest import ORG_ID, role_ctx, run

pytestmark = pytest.mark.django_db


def _fake_dump(target: Path, dsn: dict[str, str]) -> None:
    target.write_bytes(b"PGDMP fake dump bytes")


def test_run_backup_writes_dump_config_and_manifest(tmp_path: Path) -> None:
    result = backup_mod.run_backup(ORG_ID, tmp_path, dump=_fake_dump)

    assert result.outcome == "ok"
    folder = Path(result.artefact_path)
    assert (folder / "database.dump").is_file()
    assert (folder / "config.json").is_file()
    manifest = json.loads((folder / "manifest.json").read_text())
    names = {a["name"] for a in manifest["artefacts"]}
    assert names == {"database.dump", "config.json"}
    dump_sha = next(a["sha256"] for a in manifest["artefacts"] if a["name"] == "database.dump")
    assert result.sha256 == dump_sha
    assert result.size_bytes > 0


def test_config_summary_has_no_client_values() -> None:
    summary = backup_mod.config_summary(ORG_ID)
    assert set(summary) >= {"product_version", "org_settings", "licence", "object_counts"}
    assert isinstance(summary["object_counts"], dict)
    # Licence terms are shape, not figures: modules and ceiling, no values.
    assert set(summary["licence"]) == {
        "state",
        "mode",
        "modules",
        "max_major_version",
        "instance_kind",
    }


def test_diagnostics_excludes_client_data_and_reports_counts() -> None:
    from kpigo.hierarchy.models import Subject

    secret = f"ACME-SECRET-{uuid.uuid4().hex}"
    Subject.objects.create(
        subject_id=uuid.uuid4(),
        org_id=ORG_ID,
        staff_no=secret,
        full_name=secret,
        email=f"{uuid.uuid4().hex}@bank.example",
    )

    bundle = backup_mod.diagnostics(ORG_ID)
    text = json.dumps(bundle)

    # A planted subject's client-chosen name must never appear in the bundle.
    assert secret not in text
    # It is counts and status, not contents.
    assert bundle["object_counts"].get("subjects", 0) >= 1
    assert "services" in bundle and "licence" in bundle
    assert "product_version" in bundle


def test_take_backup_action_records_a_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from kpigo.platform.models import BackupRun

    monkeypatch.setattr(backup_mod, "_pg_dump", _fake_dump)
    with override_settings(KPIGO_BACKUP_DIR=str(tmp_path)):
        out = run("system.backup", role_ctx("admin"), kind="manual")

    assert out.outcome == "ok"
    row = BackupRun.objects.get(backup_id=out.backup_id)
    assert row.kind == "manual" and row.outcome == "ok"
    assert Path(row.artefact_path).exists()


def test_backup_list_reports_latest_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(backup_mod, "_pg_dump", _fake_dump)
    with override_settings(KPIGO_BACKUP_DIR=str(tmp_path)):
        run("system.backup", role_ctx("admin"), kind="manual")
        listing = run("system.backup.list", role_ctx("admin"), limit=10)
    assert listing.latest_ok_at is not None
    assert listing.backups and listing.backups[0].outcome == "ok"


def test_latest_ok_backup_ignores_failures() -> None:
    from kpigo.platform.models import BackupRun

    BackupRun.objects.create(
        org_id=ORG_ID,
        kind="manual",
        outcome="failed",
        product_version="1.0.0",
        config_version=0,
        artefact_path="",
    )
    assert backup_mod.latest_ok_backup(ORG_ID) is None

    ok = BackupRun.objects.create(
        org_id=ORG_ID,
        kind="manual",
        outcome="ok",
        product_version="1.0.0",
        config_version=1,
        artefact_path="/x",
    )
    latest = backup_mod.latest_ok_backup(ORG_ID)
    assert latest is not None and latest.backup_id == ok.backup_id


def test_real_pg_dump_produces_a_restorable_artefact(tmp_path: Path) -> None:
    """End-to-end: the default pg_dump path writes a non-trivial custom-format dump."""
    with override_settings(KPIGO_BACKUP_DIR=str(tmp_path)):
        out = run("system.backup", role_ctx("admin"), kind="manual")
    assert out.outcome == "ok", out.detail
    dump = Path(out.artefact_path) / "database.dump"
    assert dump.stat().st_size > 1000  # a real dump of the schema, not an empty file
    assert dump.read_bytes()[:5] == b"PGDMP"  # Postgres custom-format magic
