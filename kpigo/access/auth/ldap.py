"""On-prem LDAP / Active Directory: password sign-in by bind, and roster reads.

A service account searches for the user's entry by email, then kpiGo binds as
that entry with the password given. The password is never stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from ldap3 import SUBTREE, SYNC, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

# Tests replace these with an ldap3 mock (MOCK_SYNC) and its server.
CLIENT_STRATEGY: Any = SYNC
SERVER: Server | None = None


@dataclass(frozen=True)
class LdapConfig:
    url: str
    bind_dn: str
    bind_password: str
    user_base: str
    user_filter: str
    attr_staff_no: str
    attr_email: str
    attr_name: str
    attr_manager: str
    timeout: int


def config() -> LdapConfig | None:
    url = getattr(settings, "KPIGO_LDAP_URL", None)
    if not url:
        return None
    return LdapConfig(
        url=url,
        bind_dn=settings.KPIGO_LDAP_BIND_DN or "",
        bind_password=settings.KPIGO_LDAP_BIND_PASSWORD or "",
        user_base=settings.KPIGO_LDAP_USER_BASE or "",
        user_filter=settings.KPIGO_LDAP_USER_FILTER,
        attr_staff_no=settings.KPIGO_LDAP_ATTR_STAFF_NO,
        attr_email=settings.KPIGO_LDAP_ATTR_EMAIL,
        attr_name=settings.KPIGO_LDAP_ATTR_NAME,
        attr_manager=settings.KPIGO_LDAP_ATTR_MANAGER,
        timeout=int(settings.KPIGO_LDAP_TIMEOUT_SECONDS),
    )


def _server(cfg: LdapConfig) -> Server:
    return SERVER if SERVER is not None else Server(cfg.url, connect_timeout=cfg.timeout)


def connect(cfg: LdapConfig, user: str, password: str) -> Connection | None:
    """A bound connection, or None when the bind is refused."""
    if not password:
        return None  # an empty password is an anonymous bind: never a sign-in
    conn = Connection(
        _server(cfg),
        user=user,
        password=password,
        client_strategy=CLIENT_STRATEGY,
        receive_timeout=cfg.timeout,
        raise_exceptions=False,
    )
    try:
        if not conn.bind():
            return None
    except LDAPException:
        return None
    return conn


def find_dn(cfg: LdapConfig, email: str) -> str | None:
    service = connect(cfg, cfg.bind_dn, cfg.bind_password)
    if service is None:
        return None
    try:
        query = cfg.user_filter.format(email=escape_filter_chars(email))
        service.search(cfg.user_base, query, search_scope=SUBTREE, attributes=[], size_limit=2)
        entries = list(service.entries)
        # Zero or several matches: refuse rather than guess whose password this is.
        return str(entries[0].entry_dn) if len(entries) == 1 else None
    finally:
        service.unbind()


def bind_as(email: str, password: str) -> str | None:
    """The user's DN when their password binds, else None."""
    cfg = config()
    if cfg is None:
        return None
    dn = find_dn(cfg, email)
    if dn is None:
        return None
    conn = connect(cfg, dn, password)
    if conn is None:
        return None
    conn.unbind()
    return dn


def test_connection() -> tuple[bool, str]:
    cfg = config()
    if cfg is None:
        return False, "LDAP is not configured (KPIGO_LDAP_URL)."
    conn = connect(cfg, cfg.bind_dn, cfg.bind_password)
    if conn is None:
        return False, "The service account could not bind."
    conn.unbind()
    return True, "Bound as the service account."


def _first(entry: Any, attr: str) -> str:
    if not attr or attr not in entry:
        return ""
    value = entry[attr].value
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def read_roster() -> list[dict[str, str]]:
    """Every user entry under the base: staff number, name, email, manager."""
    cfg = config()
    if cfg is None:
        raise LookupError("LDAP is not configured (KPIGO_LDAP_URL).")
    conn = connect(cfg, cfg.bind_dn, cfg.bind_password)
    if conn is None:
        raise LookupError("The LDAP service account could not bind.")
    attrs = [a for a in (cfg.attr_staff_no, cfg.attr_email, cfg.attr_name, cfg.attr_manager) if a]
    try:
        roster_filter = cfg.user_filter.format(email="*")
        conn.search(cfg.user_base, roster_filter, search_scope=SUBTREE, attributes=attrs)
        by_dn: dict[str, dict[str, str]] = {}
        for entry in conn.entries:
            by_dn[str(entry.entry_dn).lower()] = {
                "staff_no": _first(entry, cfg.attr_staff_no),
                "full_name": _first(entry, cfg.attr_name),
                "email": _first(entry, cfg.attr_email),
                "manager_dn": _first(entry, cfg.attr_manager).lower(),
                "external_id": str(entry.entry_dn),
            }
    finally:
        conn.unbind()
    records = []
    for rec in by_dn.values():
        manager = by_dn.get(rec.pop("manager_dn"))
        rec["manager_staff_no"] = manager["staff_no"] if manager else ""
        records.append(rec)
    return records
