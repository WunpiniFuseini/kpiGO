"""Licence state at a moment: the grace timeline, the version ceiling, the fingerprint.

PRD OP-2: after expiry, 30 days of full function, then 45 days read-only, then
locked to the licence screen. Client data is never touched at any stage; the
states only decide which actions run.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from importlib import metadata
from typing import Literal

from django.conf import settings
from django.db import connection
from django.utils import timezone

from kpigo.licence.models import Licence

GRACE_FULL_DAYS = 30
GRACE_READ_ONLY_DAYS = 45
EXPIRY_WARNING_DAYS = 30

State = Literal["development", "unlicensed", "invalid", "active", "grace", "read_only", "locked"]
Mode = Literal["full", "read_only", "locked"]
MODE: dict[str, Mode] = {
    "development": "full",
    "active": "full",
    "grace": "full",
    "read_only": "read_only",
    "unlicensed": "locked",
    "invalid": "locked",
    "locked": "locked",
}

_fingerprint: str | None = None


def install_fingerprint() -> str:
    """What a licence is bound to: this Postgres cluster and this database.

    ``system_identifier`` is fixed when the cluster is initialised and the
    database oid when the database is created, so a copy restored elsewhere
    (prod into UAT, or a rebuilt server) has a new fingerprint and needs its own
    licence. Nothing is written to compute it.
    """
    global _fingerprint
    if _fingerprint is None:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT (SELECT system_identifier FROM pg_control_system()), "
                "(SELECT oid FROM pg_database WHERE datname = current_database())"
            )
            system_id, db_oid = cur.fetchone()
        digest = hashlib.sha256(f"kpigo-install:{system_id}:{db_oid}".encode()).hexdigest()
        _fingerprint = "-".join(digest[i : i + 4] for i in range(0, 32, 4)).upper()
    return _fingerprint


def product_version() -> str:
    try:
        return metadata.version("kpigo")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def major_of(version: str) -> int:
    head = version.strip().lstrip("vV").split(".", 1)[0]
    if not head.isdigit():
        raise ValueError(f"'{version}' is not a version number.")
    return int(head)


def effective_ceiling(licence: Licence, now: datetime | None = None) -> int:
    """The highest major this install may apply (TDD §12.1).

    A non-production instance may trial one major ahead while its trial window
    is open. A production instance never gains the +1, whatever the file says.
    """
    now = now or timezone.now()
    if (
        licence.instance_kind == "non_production"
        and licence.trial_major_until is not None
        and now < licence.trial_major_until
    ):
        return licence.max_major_version + 1
    return licence.max_major_version


def enforced() -> bool:
    return bool(getattr(settings, "KPIGO_LICENCE_ENFORCED", True))


@dataclass(frozen=True)
class LicenceState:
    state: State
    message: str
    licence: Licence | None = None
    days_left: int | None = None
    modules: tuple[str, ...] = field(default_factory=tuple)

    @property
    def mode(self) -> Mode:
        return MODE[self.state]


def active_licence(org_id: str) -> Licence | None:
    return Licence.objects.filter(org_id=org_id, state="active").first()


def current(org_id: str, now: datetime | None = None) -> LicenceState:
    now = now or timezone.now()
    licence = active_licence(org_id)
    if licence is None:
        if not enforced():
            return LicenceState(
                "development",
                "Development install: no licence is enforced.",
                modules=tuple(settings.KPIGO_ENTITLED_MODULES),
            )
        return LicenceState("unlicensed", "No licence is installed. An Admin must activate one.")
    modules = tuple(licence.modules)
    if licence.install_fingerprint != install_fingerprint():
        return LicenceState(
            "invalid",
            "This licence was issued for another installation. Activate a licence for "
            "this one; its fingerprint is on the licence screen.",
            licence=licence,
        )
    if now < licence.expires_at:
        days = (licence.expires_at - now).days
        message = (
            f"Licence expires in {days} days." if days < EXPIRY_WARNING_DAYS else "Licence active."
        )
        return LicenceState("active", message, licence, days, modules)
    read_only_from = licence.expires_at + timedelta(days=GRACE_FULL_DAYS)
    locked_from = read_only_from + timedelta(days=GRACE_READ_ONLY_DAYS)
    if now < read_only_from:
        days = (read_only_from - now).days
        return LicenceState(
            "grace",
            f"Licence expired on {licence.expires_at:%Y-%m-%d}. Everything works for "
            f"{days} more days, then changes are paused.",
            licence,
            days,
            modules,
        )
    if now < locked_from:
        days = (locked_from - now).days
        return LicenceState(
            "read_only",
            f"Licence expired on {licence.expires_at:%Y-%m-%d}. kpiGo is read-only; it "
            f"locks in {days} days. Your data is untouched.",
            licence,
            days,
            modules,
        )
    return LicenceState(
        "locked",
        f"Licence expired on {licence.expires_at:%Y-%m-%d}. kpiGo is locked until a "
        "licence is activated. Your data is untouched.",
        licence,
        0,
        modules,
    )
