"""The licence service (PRD OP-2, UP-2/3, TDD §12.1): activation, grace, entitlement."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth.models import User
from django.utils import timezone

from kpigo.action import LicenceRestricted, invoke, registry
from kpigo.action.identity import build_context
from kpigo.licence import document, startup
from kpigo.licence.actions import licence as licence_actions
from kpigo.licence.models import Licence
from kpigo.licence.state import current, effective_ceiling, install_fingerprint
from tools import licence_vendor

pytestmark = pytest.mark.django_db
KEY = Ed25519PrivateKey.generate()
KEY_ID = "test-2026"


@pytest.fixture(autouse=True)
def trusted_key(monkeypatch: pytest.MonkeyPatch, settings: Any, tmp_path: Path) -> None:
    raw = KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(document, "TRUSTED_KEYS", {KEY_ID: base64.b64encode(raw).decode()})
    settings.KPIGO_LICENCE_FILE = str(tmp_path / "licence" / "kpigo.lic")


def issue(**overrides: Any) -> str:
    now = timezone.now()
    terms: dict[str, Any] = {
        "licence_key": "KPG-TEST-0001",
        "customer": "Test Bank",
        "tier": "growth",
        "install_fingerprint": install_fingerprint(),
        "modules": ["scorecards", "agent_performance"],
        "seats": 250,
        "max_major_version": 1,
        "instance_kind": "production",
        "trial_major_until": None,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=365)).isoformat(),
        **overrides,
    }
    return licence_vendor.sign(KEY, KEY_ID, terms)


def as_user(user: User, action_name: str, /, **payload: Any) -> Any:
    return invoke(registry.get(action_name), payload, build_context(user, caller="cli"))


@pytest.fixture
def admin(make_user: Callable[..., User]) -> User:
    return make_user("admin")


def activate(admin: User, text: str) -> Any:
    return as_user(admin, "licence.activate", document=text)


# ── activation ─────────────────────────────────────────────────────────────


def test_activation_records_the_licence_and_writes_the_file(admin: User, settings: Any) -> None:
    out = activate(admin, issue())
    assert out.licence.state == "active" and out.licence.licence_key == "KPG-TEST-0001"
    assert out.file_written is True
    assert Path(settings.KPIGO_LICENCE_FILE).read_text() == issue_text_of(Licence.objects.get())
    # The next start reads its modules from the file.
    assert sorted(startup.entitled_modules()) == ["agent_performance", "scorecards"]


def issue_text_of(row: Licence) -> str:
    return row.document


def test_a_new_licence_supersedes_the_old_and_an_older_one_is_refused(admin: User) -> None:
    first = issue(issued_at=(timezone.now() - timedelta(days=2)).isoformat())
    activate(admin, first)
    activate(admin, issue(licence_key="KPG-TEST-0002"))
    assert list(Licence.objects.order_by("created_at").values_list("state", flat=True)) == [
        "superseded",
        "active",
    ]
    from kpigo.action import Conflict

    with pytest.raises(Conflict):
        activate(admin, first)


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: issue(install_fingerprint="0000-0000"), "another installation"),
        (
            lambda: issue(
                expires_at=(timezone.now() - timedelta(days=1)).isoformat(),
                issued_at=(timezone.now() - timedelta(days=30)).isoformat(),
            ),
            "expired",
        ),
        (lambda: issue(modules=["scorecards", "crypto_mining"]), "unknown modules"),
        (lambda: "not json at all", "not a kpiGo licence"),
        (lambda: _tampered(), "signature"),
        (lambda: _foreign(), "does not trust"),
    ],
)
def test_bad_licences_are_refused(admin: User, make: Callable[[], str], message: str) -> None:
    from kpigo.action import InvalidInput

    with pytest.raises(InvalidInput, match=message):
        activate(admin, make())
    assert not Licence.objects.exists()


def _tampered() -> str:
    envelope = json.loads(issue())
    payload = json.loads(document.b64decode(envelope["payload"]))
    payload["seats"] = 100_000
    envelope["payload"] = document.b64encode(json.dumps(payload).encode())
    return json.dumps(envelope)


def _foreign() -> str:
    envelope = json.loads(issue())
    envelope["key_id"] = "someone-else"
    return json.dumps(envelope)


# ── grace timeline ───────────────────────────────────────────────────────────


def _installed(admin: User, expires_in: timedelta) -> Licence:
    activate(admin, issue())
    row = Licence.objects.get(state="active")
    row.expires_at = timezone.now() + expires_in
    row.issued_at = row.expires_at - timedelta(days=365)
    row.save()
    return row


@pytest.mark.parametrize(
    ("expires_in", "state", "mode"),
    [
        (timedelta(days=100), "active", "full"),
        (timedelta(days=-1), "grace", "full"),
        (timedelta(days=-29), "grace", "full"),
        (timedelta(days=-31), "read_only", "read_only"),
        (timedelta(days=-74), "read_only", "read_only"),
        (timedelta(days=-76), "locked", "locked"),
    ],
)
def test_grace_timeline(admin: User, expires_in: timedelta, state: str, mode: str) -> None:
    _installed(admin, expires_in)
    now = current("00000000-0000-0000-0000-000000000001")
    assert (now.state, now.mode) == (state, mode)


def test_read_only_refuses_changes_but_keeps_reads_and_the_licence_screen(admin: User) -> None:
    _installed(admin, timedelta(days=-40))
    assert as_user(admin, "metric.list").metrics == []  # reads still work
    with pytest.raises(LicenceRestricted, match="read-only"):
        as_user(admin, "dimension.define", dimension_type="region", display_name="Region")
    # The way out is always open: a new licence activates in any state.
    activate(admin, issue(licence_key="KPG-RENEWED"))
    as_user(admin, "dimension.define", dimension_type="region", display_name="Region")


def test_locked_refuses_everything_but_sign_in_licence_and_health(admin: User) -> None:
    _installed(admin, timedelta(days=-90))
    with pytest.raises(LicenceRestricted, match="locked"):
        as_user(admin, "metric.list")
    assert as_user(admin, "auth.me").licence.state == "locked"
    assert as_user(admin, "licence.status").state == "locked"
    assert as_user(admin, "system.health", check_workers=False).licence.mode == "locked"


def test_data_is_never_touched_at_any_stage(admin: User) -> None:
    from tests.conftest import run

    run("dimension.define", dimension_type="region", display_name="Region")
    _installed(admin, timedelta(days=-400))
    from kpigo.hierarchy.models import Dimension

    assert Dimension.objects.count() == 1


def test_an_enforced_install_without_a_licence_is_limited_to_setup(
    admin: User, settings: Any
) -> None:
    settings.KPIGO_LICENCE_ENFORCED = True
    with pytest.raises(LicenceRestricted, match="No licence"):
        as_user(admin, "metric.list")
    assert as_user(admin, "licence.status").state == "unlicensed"
    activate(admin, issue())
    assert as_user(admin, "metric.list").metrics == []


def test_a_licence_restored_onto_another_install_locks(admin: User) -> None:
    activate(admin, issue())
    Licence.objects.update(install_fingerprint="FFFF-FFFF")
    assert as_user(admin, "licence.status").state == "invalid"
    with pytest.raises(LicenceRestricted, match="another installation"):
        as_user(admin, "metric.list")


def test_actions_of_a_module_the_licence_lacks_are_refused(admin: User) -> None:
    activate(admin, issue(modules=["scorecards"]))
    fake = registry.get("metric.list")
    from dataclasses import replace

    executive_action = replace(fake, name="executive.sample", module="executive")
    with pytest.raises(LicenceRestricted, match="executive"):
        invoke(executive_action, {}, build_context(admin, caller="cli"))
    out = as_user(admin, "licence.status")
    assert out.restart_required is True  # registered with all four; licence carries one


def test_seats_warn_but_never_lock(admin: User) -> None:
    from tests.conftest import run

    activate(admin, issue(seats=1))
    for n in range(2):
        run("subject.register", staff_no=f"S{n}", full_name=f"S {n}", email=f"s{n}@bank.example")
    out = as_user(admin, "licence.status")
    assert out.seats_exceeded is True and out.subjects_in_use == 2 and out.state == "active"
    as_user(admin, "dimension.define", dimension_type="region", display_name="Region")


# ── version entitlement ─────────────────────────────────────────────────────


def test_production_never_gains_the_plus_one(admin: User) -> None:
    trial = (timezone.now() + timedelta(days=60)).isoformat()
    activate(admin, issue(instance_kind="production", trial_major_until=trial))
    row = Licence.objects.get(state="active")
    assert effective_ceiling(row) == 1
    assert effective_ceiling(row, now=timezone.now() - timedelta(days=1000)) == 1
    out = as_user(admin, "licence.entitlement.check", target_version="2.0.0")
    assert out.allowed is False and "Entitlement required" in out.reason
    assert as_user(admin, "licence.entitlement.check", target_version="1.9.3").allowed is True


def test_uat_may_trial_one_major_ahead_until_the_trial_ends(admin: User) -> None:
    trial_end = timezone.now() + timedelta(days=30)
    activate(admin, issue(instance_kind="non_production", trial_major_until=trial_end.isoformat()))
    row = Licence.objects.get(state="active")
    assert effective_ceiling(row) == 2
    assert effective_ceiling(row, now=trial_end + timedelta(seconds=1)) == 1
    out = as_user(admin, "licence.entitlement.check", target_version="2.1.0")
    assert out.allowed is True and out.trial_active is True
    assert as_user(admin, "licence.entitlement.check", target_version="3.0.0").allowed is False
    row.trial_major_until = timezone.now() - timedelta(days=1)
    row.save()
    assert as_user(admin, "licence.entitlement.check", target_version="2.0.0").allowed is False


# ── heartbeat ───────────────────────────────────────────────────────────────


@pytest.fixture
def portal(settings: Any) -> Iterator[list[dict[str, Any]]]:
    settings.KPIGO_LICENCE_HEARTBEAT_URL = "https://licence.kpigo.example/heartbeat"
    received: list[dict[str, Any]] = []
    renewal: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(200, json=renewal)

    licence_actions.TRANSPORT = httpx.MockTransport(handler)
    received.append({"_renewal": renewal})  # handle for the test to set a renewal
    yield received
    licence_actions.TRANSPORT = None


def test_heartbeat_sends_only_the_licence_key_fingerprint_and_version(
    admin: User, portal: list[dict[str, Any]]
) -> None:
    renewal = portal.pop(0)["_renewal"]
    activate(admin, issue(issued_at=(timezone.now() - timedelta(days=1)).isoformat()))
    out = as_user(admin, "licence.heartbeat")
    assert out.ok is True and out.renewed is False
    assert set(portal[0]) == {"licence_key", "install_fingerprint", "product_version"}
    assert Licence.objects.get(state="active").last_heartbeat_at is not None
    renewal["licence"] = issue(licence_key="KPG-RENEWED")
    assert as_user(admin, "licence.heartbeat").renewed is True
    assert Licence.objects.get(state="active").licence_key == "KPG-RENEWED"


def test_no_heartbeat_without_a_url(admin: User, settings: Any) -> None:
    settings.KPIGO_LICENCE_HEARTBEAT_URL = None
    activate(admin, issue())
    assert as_user(admin, "licence.heartbeat").sent is False


def test_vendor_keygen_prints_a_trusted_key_line(tmp_path: Path) -> None:
    line = licence_vendor.keygen("vendor-x", tmp_path / "k.pem")
    assert line.strip().startswith('"vendor-x": "')
    key = licence_vendor.load_key(tmp_path / "k.pem")
    assert isinstance(key, Ed25519PrivateKey)
    assert (tmp_path / "k.pem").stat().st_mode & 0o077 == 0


def test_the_shipped_build_trusts_no_development_key() -> None:
    # Production keys are added by kpiGo's release process, never a key whose
    # private half sits in this repository.
    import ast

    tree = ast.parse(Path(document.__file__).read_text())
    shipped = next(
        ast.literal_eval(node.value)  # type: ignore[arg-type]
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TRUSTED_KEYS"
    )
    assert all(not k.startswith(("test", "dev")) for k in shipped)
