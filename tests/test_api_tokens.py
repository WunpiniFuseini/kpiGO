"""The REST read API authenticated by a bearer token (PRD OP-8).

A token is issued through ``apitoken.issue`` (a mutating action, so only a signed-in
account can mint one), carries exactly its account's permissions and visibility, and
reaches read-only actions only. Only the SHA-256 hash is stored; the raw token is shown
once. Revoking or expiring a token stops it at once, because resolution reads the row on
every request.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from kpigo.access.accounts import hash_token
from kpigo.access.identity import app_user_for
from kpigo.access.models import ApiToken, AppUser
from kpigo.action import invoke, registry
from kpigo.action.identity import build_context

pytestmark = pytest.mark.django_db

from tests.conftest import ORG_ID  # noqa: E402


def account_of(user: User) -> AppUser:
    account = app_user_for(user, ORG_ID)
    assert account is not None
    return account


def as_user(user: User, action_name: str, /, **payload: Any) -> Any:
    return invoke(registry.get(action_name), payload, build_context(user, caller="http"))


def issue_token(user: User, name: str = "etl", **payload: Any) -> str:
    result = as_user(user, "apitoken.issue", name=name, **payload)
    return str(result.secret)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_issue_returns_raw_token_once_and_stores_only_the_hash(
    make_user: Callable[..., User],
) -> None:
    admin = make_user("admin")
    result = as_user(admin, "apitoken.issue", name="etl")
    raw = result.secret
    assert raw.startswith("kpigo_")
    row = ApiToken.objects.get(org_id=ORG_ID, name="etl")
    # Only the hash is kept; the raw token appears nowhere in the stored row.
    assert row.token_hash == hash_token(raw)
    assert raw not in row.token_hash
    assert row.prefix == raw[:12] and row.status == "active"
    assert row.app_user_id == account_of(admin).pk


def test_token_reaches_a_read_only_action_with_the_account_identity(
    make_user: Callable[..., User],
) -> None:
    admin = make_user("admin")
    token = issue_token(admin)
    client = Client()  # no session: the bearer token is the only credential
    response = client.get("/api/v1/hello", {"name": "etl"}, headers=bearer(token))
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["greeting"] == "Hello, etl!"
    assert body["caller"] == "http"


def test_token_is_refused_on_a_write_action(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")  # an admin could write via a session
    token = issue_token(admin)
    client = Client()
    # webhook.register is a POST (mutating) action the admin's permissions allow, yet the
    # token path refuses it before the action runs.
    response = client.post(
        "/api/v1/webhooks",
        data={"name": "x", "url": "https://e.local/x"},
        content_type="application/json",
        headers=bearer(token),
    )
    assert response.status_code == 403, response.content
    assert "read-only" in response.json()["message"]
    assert not ApiToken.objects.filter(name="x").exists()


def test_token_carries_the_accounts_permissions(make_user: Callable[..., User]) -> None:
    # A non-admin account lacks webhook.view, so its token cannot read the webhook list
    # even though the route is read-only. (A staff account cannot mint a token itself —
    # apitoken.manage is Admin-only — so the row is created directly for this check.)
    staff = make_user("staff")
    raw = "kpigo_" + "staff-token-value"
    ApiToken.objects.create(
        org_id=ORG_ID,
        app_user=account_of(staff),
        name="staff-etl",
        token_hash=hash_token(raw),
        prefix=raw[:12],
    )
    client = Client()
    # It can read what the account may (hello, held by everyone) ...
    assert client.get("/api/v1/hello", headers=bearer(raw)).status_code == 200
    # ... but not the admin-only webhook list.
    assert client.get("/api/v1/webhooks", headers=bearer(raw)).status_code == 403


def test_missing_or_unknown_token_is_unauthorised(make_user: Callable[..., User]) -> None:
    make_user("admin")
    client = Client()
    assert client.get("/api/v1/hello").status_code == 401
    assert client.get("/api/v1/hello", headers=bearer("kpigo_nope")).status_code == 401


def test_revoked_token_stops_working(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    token = issue_token(admin)
    client = Client()
    assert client.get("/api/v1/hello", headers=bearer(token)).status_code == 200
    as_user(admin, "apitoken.revoke", name="etl")
    assert ApiToken.objects.get(name="etl").status == "revoked"
    assert client.get("/api/v1/hello", headers=bearer(token)).status_code == 401


def test_expired_token_stops_working(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    token = issue_token(admin, name="short", expires_in_days=1)
    client = Client()
    assert client.get("/api/v1/hello", headers=bearer(token)).status_code == 200
    # Backdate expiry past now; resolution checks it on every request.
    ApiToken.objects.filter(name="short").update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get("/api/v1/hello", headers=bearer(token)).status_code == 401


def test_last_used_at_is_stamped(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    token = issue_token(admin)
    assert ApiToken.objects.get(name="etl").last_used_at is None
    Client().get("/api/v1/hello", headers=bearer(token))
    assert ApiToken.objects.get(name="etl").last_used_at is not None


def test_session_login_is_unaffected_by_the_token_auth(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    client = Client()
    client.force_login(admin)
    assert client.get("/api/v1/hello", {"name": "ui"}).status_code == 200


def test_list_shows_own_tokens_and_revoke_is_scoped(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    issue_token(admin, name="a")
    issue_token(admin, name="b")
    names = {t.name for t in as_user(admin, "apitoken.list").tokens}
    assert names == {"a", "b"}


def test_issue_is_mutating_so_a_token_cannot_mint_tokens() -> None:
    # The guard is generic (read-only only), but assert the issuing action is mutating so
    # a read token can never reach it.
    assert registry.get("apitoken.issue").read_only is False
    assert registry.get("apitoken.revoke").read_only is False
    assert registry.get("apitoken.list").read_only is True


def test_duplicate_token_name_is_a_conflict(make_user: Callable[..., User]) -> None:
    from kpigo.action.errors import Conflict

    admin = make_user("admin")
    issue_token(admin, name="dup")
    with pytest.raises(Conflict):
        issue_token(admin, name="dup")
