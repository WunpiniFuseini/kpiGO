"""Microsoft Entra ID roster reads through Microsoft Graph (client credentials).

This calls the client's own tenant, never kpiGo: it is the directory import the
client configured, not telemetry. Sign-in through Entra uses OIDC or SAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from django.conf import settings

from kpigo.access.auth.http import client

PAGE_LIMIT = 500  # pages of 999 users: a ceiling far above any client's headcount


@dataclass(frozen=True)
class EntraConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    authority: str
    graph_url: str
    staff_no_attribute: str


def config() -> EntraConfig | None:
    tenant = getattr(settings, "KPIGO_ENTRA_TENANT_ID", None)
    if not tenant or not settings.KPIGO_ENTRA_CLIENT_ID:
        return None
    return EntraConfig(
        tenant_id=tenant,
        client_id=settings.KPIGO_ENTRA_CLIENT_ID,
        client_secret=settings.KPIGO_ENTRA_CLIENT_SECRET or "",
        authority=settings.KPIGO_ENTRA_AUTHORITY.rstrip("/"),
        graph_url=settings.KPIGO_ENTRA_GRAPH_URL.rstrip("/"),
        staff_no_attribute=settings.KPIGO_ENTRA_STAFF_NO_ATTRIBUTE,
    )


def _token(cfg: EntraConfig, http: httpx.Client) -> str:
    response = http.post(
        f"{cfg.authority}/{quote(cfg.tenant_id)}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "scope": f"{cfg.graph_url}/.default",
        },
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not isinstance(token, str):
        raise LookupError("Entra returned no access token.")
    return token


def test_connection() -> tuple[bool, str]:
    cfg = config()
    if cfg is None:
        return False, "Entra ID is not configured (KPIGO_ENTRA_TENANT_ID, KPIGO_ENTRA_CLIENT_ID)."
    try:
        with client() as http:
            _token(cfg, http)
    except (httpx.HTTPError, LookupError, ValueError) as exc:
        return False, f"Could not get a Graph token: {exc}"
    return True, "Got a Microsoft Graph token for the tenant."


def read_roster() -> list[dict[str, str]]:
    cfg = config()
    if cfg is None:
        raise LookupError("Entra ID is not configured.")
    fields = ",".join(
        sorted(
            {
                "id",
                "displayName",
                "mail",
                "userPrincipalName",
                "accountEnabled",
                cfg.staff_no_attribute,
            }
        )
    )
    url: str | None = (
        f"{cfg.graph_url}/v1.0/users?$select={fields}&$expand=manager($select=id)&$top=999"
    )
    users: list[dict[str, Any]] = []
    with client() as http:
        headers = {"Authorization": f"Bearer {_token(cfg, http)}"}
        for _ in range(PAGE_LIMIT):
            if url is None:
                break
            response = http.get(url, headers=headers)
            response.raise_for_status()
            page = response.json()
            users.extend(page.get("value", []))
            url = page.get("@odata.nextLink")
    staff_by_id = {
        str(u.get("id")): str(u.get(cfg.staff_no_attribute) or "").strip() for u in users
    }
    records = []
    for u in users:
        if u.get("accountEnabled") is False:
            continue  # a disabled directory account counts as gone
        manager = u.get("manager") or {}
        records.append(
            {
                "staff_no": staff_by_id.get(str(u.get("id")), ""),
                "full_name": str(u.get("displayName") or "").strip(),
                "email": str(u.get("mail") or u.get("userPrincipalName") or "").strip(),
                "manager_staff_no": staff_by_id.get(str(manager.get("id")), ""),
                "external_id": str(u.get("id")),
            }
        )
    return records
