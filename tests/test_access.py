"""Users, roles, page access, data scope, maker-checker and sessions (R0 Workstream D)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from kpigo.access.models import AppUser, UserRole
from kpigo.action import (
    AuthenticationFailed,
    Conflict,
    InvalidInput,
    NotAuthenticated,
    PermissionDenied,
    Proposal,
    invoke,
    registry,
)
from kpigo.action.adapters.http import AUTH_AT_KEY
from kpigo.action.identity import anonymous_context, build_context
from kpigo.ingestion.models import Feed
from kpigo.platform.models import AuditLog
from tests.access_support import PASSWORD, get, post
from tests.conftest import run

pytestmark = pytest.mark.django_db
SETUP_TOKEN = "one-time-setup-token"


@pytest.fixture(autouse=True)
def _setup_token(settings: Any) -> None:
    settings.KPIGO_SETUP_TOKEN = SETUP_TOKEN


def bootstrap(client: Client, email: str = "admin@bank.example") -> Any:
    return post(
        client,
        "/setup/bootstrap",
        {"setup_token": SETUP_TOKEN, "email": email, "display_name": "First", "password": PASSWORD},
    )


def anon(action_name: str, /, **payload: Any) -> Any:
    return invoke(registry.get(action_name), payload, anonymous_context(caller="cli"))


def as_user(user: User, action_name: str, /, **payload: Any) -> Any:
    return invoke(registry.get(action_name), payload, build_context(user, caller="http"))


# ── first run ───────────────────────────────────────────────────────────────


def test_bootstrap_creates_the_first_admin_once() -> None:
    client = Client()
    assert get(client, "/setup").json()["needs_admin"] is True
    wrong = post(
        client,
        "/setup/bootstrap",
        {
            "setup_token": "nope",
            "email": "a@bank.example",
            "display_name": "A",
            "password": PASSWORD,
        },
    )
    assert wrong.status_code == 403
    response = bootstrap(client)
    assert response.status_code == 200, response.content
    me = response.json()
    assert me["user"]["roles"] == ["admin"]
    assert {"admin.users", "admin.health"} <= {p["page_key"] for p in me["pages"]}
    # The bootstrap call signed the browser in.
    assert get(client, "/auth/me").json()["user"]["email"] == "admin@bank.example"
    assert get(client, "/setup").json()["needs_admin"] is False
    assert bootstrap(Client(), "second@bank.example").status_code == 409


def test_bootstrap_is_off_without_a_setup_token(settings: Any) -> None:
    settings.KPIGO_SETUP_TOKEN = None
    assert bootstrap(Client()).status_code == 403


def test_bootstrap_refuses_a_weak_password() -> None:
    response = post(
        Client(),
        "/setup/bootstrap",
        {
            "setup_token": SETUP_TOKEN,
            "email": "a@bank.example",
            "display_name": "A",
            "password": "short",
        },
    )
    assert response.status_code == 422
    assert not AppUser.objects.exists()


# ── invite, accept, sign in, sign out ────────────────────────────────────────


def test_invite_accept_login_logout_over_http(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    invited = as_user(
        admin,
        "user.invite",
        email="Kofi@Bank.example",
        display_name="Kofi",
        role_codes=["staff"],
        auth_provider="local",
    )
    assert invited.user.status == "invited" and invited.user.email == "kofi@bank.example"
    token = invited.invite_token
    assert token and AppUser.objects.get(email="kofi@bank.example").invite_token_hash != token

    browser = Client()
    # Not yet accepted: password sign-in is refused.
    assert (
        post(browser, "/auth/login", {"email": "kofi@bank.example", "password": "x"}).status_code
        == 401
    )
    assert (
        post(browser, "/auth/invite/accept", {"token": token, "password": "123"}).status_code == 422
    )
    accepted = post(browser, "/auth/invite/accept", {"token": token, "password": PASSWORD})
    assert accepted.status_code == 200, accepted.content
    assert accepted.json()["user"]["status"] == "active"
    # The token is single use.
    assert (
        post(Client(), "/auth/invite/accept", {"token": token, "password": PASSWORD}).status_code
        == 422
    )

    browser = Client()
    assert get(browser, "/auth/me").status_code == 401
    login = post(browser, "/auth/login", {"email": "KOFI@bank.example", "password": PASSWORD})
    assert login.status_code == 200, login.content
    me = get(browser, "/auth/me").json()
    assert [p["page_key"] for p in me["pages"]] == ["scorecards"]
    assert me["home"] == "scorecards"
    assert post(browser, "/auth/logout").status_code == 200
    assert get(browser, "/auth/me").status_code == 401


def test_sign_in_routes_take_json_only(make_user: Callable[..., User]) -> None:
    user = make_user("staff", email="form@bank.example")
    user.set_password(PASSWORD)
    user.save()
    # A cross-site HTML form posts urlencoded or text/plain bodies; neither signs in,
    # even when a text/plain body is crafted to parse as JSON.
    form = Client().post("/api/v1/auth/login", {"email": "form@bank.example", "password": PASSWORD})
    assert form.status_code in (400, 422)
    crafted = Client().post(
        "/api/v1/auth/login",
        json.dumps({"email": "form@bank.example", "password": PASSWORD}),
        content_type="text/plain",
    )
    assert crafted.status_code == 422


def test_passwords_and_tokens_never_reach_the_audit_log(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    token = as_user(
        admin, "user.invite", email="ama@bank.example", display_name="Ama", auth_provider="local"
    ).invite_token
    post(Client(), "/auth/invite/accept", {"token": token, "password": PASSWORD})
    post(Client(), "/auth/login", {"email": "ama@bank.example", "password": "wrong guess here"})
    dumped = str(list(AuditLog.objects.values_list("payload", flat=True)))
    assert PASSWORD not in dumped and token not in dumped and "wrong guess here" not in dumped


def test_repeated_failures_lock_the_account_and_survive_the_refusal(
    make_user: Callable[..., User], settings: Any
) -> None:
    settings.KPIGO_LOGIN_MAX_ATTEMPTS = 3
    user = make_user("staff", email="lock@bank.example")
    user.set_password(PASSWORD)
    user.save()
    for _ in range(2):
        with pytest.raises(AuthenticationFailed):
            anon("auth.login", email="lock@bank.example", password="not it")
    # The refusals rolled nothing back: the count is in the database.
    assert AppUser.objects.get(email="lock@bank.example").failed_logins == 2
    with pytest.raises(AuthenticationFailed):
        anon("auth.login", email="lock@bank.example", password="not it")
    account = AppUser.objects.get(email="lock@bank.example")
    assert account.locked_until is not None and account.locked_until > timezone.now()
    with pytest.raises(AuthenticationFailed, match="Too many"):
        anon("auth.login", email="lock@bank.example", password=PASSWORD)
    assert AuditLog.objects.filter(action_name="auth.login", event="action.failed").count() == 4
    admin = make_user("admin")
    as_user(admin, "user.update", user_id=str(account.user_id), unlock=True)
    assert anon("auth.login", email="lock@bank.example", password=PASSWORD).user.status == "active"


def test_unknown_email_and_wrong_password_read_the_same(make_user: Callable[..., User]) -> None:
    user = make_user("staff", email="same@bank.example")
    user.set_password(PASSWORD)
    user.save()
    with pytest.raises(AuthenticationFailed) as unknown:
        anon("auth.login", email="nobody@bank.example", password=PASSWORD)
    with pytest.raises(AuthenticationFailed) as wrong:
        anon("auth.login", email="same@bank.example", password="not the one")
    assert unknown.value.message == wrong.value.message


def test_local_login_can_be_switched_off(make_user: Callable[..., User], settings: Any) -> None:
    user = make_user("staff", email="off@bank.example")
    user.set_password(PASSWORD)
    user.save()
    settings.KPIGO_LOCAL_LOGIN = False
    with pytest.raises(AuthenticationFailed):
        anon("auth.login", email="off@bank.example", password=PASSWORD)
    assert anon("auth.providers").password.enabled is False


# ── accounts and contexts ────────────────────────────────────────────────────


def test_no_account_or_inactive_account_gets_no_context(make_user: Callable[..., User]) -> None:
    bare = User.objects.create_user("bare")
    with pytest.raises(NotAuthenticated):
        build_context(bare, caller="cli")
    invited = make_user("admin", status="invited")
    with pytest.raises(NotAuthenticated):
        build_context(invited, caller="cli")


def test_a_user_with_no_role_can_only_see_themself(make_user: Callable[..., User]) -> None:
    nobody = make_user()
    ctx = build_context(nobody, caller="http")
    assert ctx.permissions == {"auth.session"}
    me = as_user(nobody, "auth.me")
    assert me.pages == [] and me.home is None
    with pytest.raises(PermissionDenied):
        as_user(nobody, "platform.hello")


def test_disabling_a_user_ends_their_session_and_keeps_their_history(
    make_user: Callable[..., User],
) -> None:
    admin = make_user("admin")
    staff = make_user("staff")
    staff.set_password(PASSWORD)
    staff.save()
    browser = Client()
    browser.force_login(staff)
    assert get(browser, "/hello").status_code == 200
    account = AppUser.objects.get(auth_user=staff)
    out = as_user(admin, "user.disable", user_id=str(account.user_id), reason="Left")
    assert out.status == "disabled"
    assert get(browser, "/hello").status_code == 401
    assert AppUser.objects.filter(pk=account.pk).exists()  # never hard-deleted
    with pytest.raises(Conflict):
        as_user(admin, "user.disable", user_id=str(account.user_id))
    as_user(admin, "user.enable", user_id=str(account.user_id))
    browser.force_login(staff)
    assert get(browser, "/hello").status_code == 200


def test_the_last_admin_cannot_be_removed(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    account = AppUser.objects.get(auth_user=admin)
    with pytest.raises(Conflict, match="only active Admin"):
        as_user(admin, "user.update", user_id=str(account.user_id), role_codes=["staff"])
    other = make_user("admin")
    with pytest.raises(Conflict, match="yourself"):
        as_user(admin, "user.disable", user_id=str(account.user_id))
    as_user(other, "user.update", user_id=str(account.user_id), role_codes=["staff"])
    assert list(UserRole.objects.filter(app_user=account).values_list("role_code", flat=True)) == [
        "staff"
    ]


def test_invite_links_the_subject_by_email(make_user: Callable[..., User]) -> None:
    subject = run("subject.register", staff_no="E9", full_name="Esi", email="esi@bank.example")
    admin = make_user("admin")
    out = as_user(admin, "user.invite", email="esi@bank.example", display_name="Esi")
    assert out.user.subject_id == subject.subject_id
    with pytest.raises(Conflict):
        as_user(admin, "user.invite", email="ESI@bank.example", display_name="Esi again")
    with pytest.raises(InvalidInput, match="Unknown roles"):
        as_user(admin, "user.invite", email="x@bank.example", display_name="X", role_codes=["root"])


def test_reassign_moves_ownership(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    leaving = make_user("data_steward")
    taking = make_user("data_steward")
    run("feed.register", name="owned", template="actual_monthly", mode="upload")
    Feed.objects.filter(name="owned").update(owner_user_id=leaving.pk)
    out = as_user(
        admin,
        "user.reassign",
        from_user_id=str(AppUser.objects.get(auth_user=leaving).user_id),
        to_user_id=str(AppUser.objects.get(auth_user=taking).user_id),
    )
    assert out.feeds == 1
    assert Feed.objects.get(name="owned").owner_user_id == taking.pk


# ── roles and the page matrix ────────────────────────────────────────────────


def test_clone_a_system_role_then_edit_the_clone(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    clone = as_user(
        admin, "role.clone", source_code="line_manager", code="branch_manager", name="Branch"
    )
    assert clone.is_system is False and clone.cloned_from == "line_manager"
    assert clone.pages["scorecards"] == "view" and "auth.session" in clone.permissions
    with pytest.raises(Conflict, match="fixed"):
        as_user(admin, "role.update", code="line_manager", name="Renamed")
    with pytest.raises(InvalidInput, match="Unknown permissions"):
        as_user(admin, "role.update", code="branch_manager", permissions=["root.everything"])
    with pytest.raises(Conflict):
        as_user(admin, "role.clone", source_code="staff", code="admin", name="Admin 2")
    updated = as_user(
        admin,
        "role.update",
        code="branch_manager",
        permissions=[*clone.permissions, "feed.view"],
        pages={"admin.data_integration": "view"},
    )
    assert updated.pages["admin.data_integration"] == "view"

    manager = make_user("branch_manager")
    ctx = build_context(manager, caller="http")
    assert "feed.view" in ctx.permissions and "feed.manage" not in ctx.permissions
    pages = {p.page_key: p.access for p in as_user(manager, "auth.me").pages}
    assert pages == {
        "scorecards": "view",
        "agent_performance": "view",
        "my_inputs": "edit",
        "admin.data_integration": "view",
    }
    listed = {r.code for r in as_user(admin, "role.list").roles}
    assert {"admin", "staff", "branch_manager"} <= listed


def test_nav_hides_pages_of_unlicensed_modules(
    make_user: Callable[..., User], settings: Any
) -> None:
    settings.KPIGO_ENTITLED_MODULES = ["scorecards"]
    admin = make_user("admin")
    keys = {p.page_key for p in as_user(admin, "auth.me").pages}
    assert "scorecards" in keys
    assert not keys & {"agent_performance", "campaign", "executive", "admin.widgets"}


# ── data scope ──────────────────────────────────────────────────────────────


def test_no_grant_means_no_data_and_the_page_says_so(make_user: Callable[..., User]) -> None:
    run("dimension.define", dimension_type="region", display_name="Region")
    run(
        "dimension.member.upsert",
        dimension_type="region",
        members=[{"member_code": "GA", "member_name": "Greater Accra"}],
    )
    admin = make_user("admin")
    executive = make_user("executive")
    assert build_context(executive, caller="http").data_scopes == ()
    missing = {n.page_key: n.missing for n in as_user(executive, "auth.me").no_access}
    assert "executive" in missing and "grant" in missing["executive"]
    assert "scorecards" in missing  # not linked to a person either

    grant = as_user(
        admin,
        "scope.grant.create",
        role_code="executive",
        module="executive",
        dimension_type="region",
        member_code="GA",
    )
    scopes = build_context(executive, caller="http").data_scopes
    assert [(s.module, s.dimension_type, s.member_code) for s in scopes] == [
        ("executive", "region", "GA")
    ]
    assert "executive" not in {n.page_key for n in as_user(executive, "auth.me").no_access}
    with pytest.raises(Conflict):
        as_user(
            admin,
            "scope.grant.create",
            role_code="executive",
            module="executive",
            dimension_type="region",
            member_code="GA",
        )
    with pytest.raises(InvalidInput):
        as_user(
            admin,
            "scope.grant.create",
            role_code="executive",
            module="executive",
            dimension_type="region",
            member_code="XX",
        )
    tomorrow = timezone.localdate() + timedelta(days=1)
    as_user(admin, "scope.grant.end", grant_id=str(grant.grant_id), effective_to=str(tomorrow))
    assert build_context(executive, caller="http").data_scopes  # still in force today
    listed = as_user(admin, "scope.grant.list", module="executive").grants
    assert [g.effective_to for g in listed] == [tomorrow]


# ── maker-checker ───────────────────────────────────────────────────────────


def test_maker_checker_on_access_changes(make_user: Callable[..., User]) -> None:
    maker = make_user("admin")
    checker = make_user("admin")
    as_user(maker, "approval.policy.set", approval_class="access_change", enabled=True)
    policies = {
        p.approval_class: p.enabled for p in as_user(maker, "approval.policy.list").policies
    }
    assert policies["access_change"] is True and policies["metric_change"] is False

    browser = Client()
    browser.force_login(maker)
    response = post(browser, "/users/invite", {"email": "new@bank.example", "display_name": "New"})
    assert response.status_code == 202, response.content
    request_id = response.json()["approval_request_id"]
    assert not AppUser.objects.filter(email="new@bank.example").exists()
    with pytest.raises(Conflict, match="maker"):
        as_user(maker, "platform.approval.approve", approval_request_id=request_id)
    approved = as_user(checker, "platform.approval.approve", approval_request_id=request_id)
    assert approved.status == "approved"
    assert AppUser.objects.get(email="new@bank.example").status == "invited"

    # Switching the control back off is itself checked while config_change is on.
    as_user(maker, "approval.policy.set", approval_class="config_change", enabled=True)
    off = as_user(maker, "approval.policy.set", approval_class="access_change", enabled=False)
    assert isinstance(off, Proposal)


def test_every_declared_approval_class_is_a_known_one() -> None:
    from kpigo.platform.models import APPROVAL_CLASSES

    declared = {d.requires_approval for d in registry if d.requires_approval}
    assert declared <= set(APPROVAL_CLASSES)


# ── sessions ────────────────────────────────────────────────────────────────


def test_a_sign_in_expires_after_the_absolute_limit(
    make_user: Callable[..., User], settings: Any
) -> None:
    settings.KPIGO_SESSION_MAX_SECONDS = 60
    browser = Client()
    browser.force_login(make_user("staff"))
    assert get(browser, "/auth/me").status_code == 200
    session = browser.session
    session[AUTH_AT_KEY] = time.time() - 120
    session.save()
    response = get(browser, "/auth/me")
    assert response.status_code == 401 and response.json()["error"] == "session_expired"
    assert get(browser, "/auth/me").status_code == 401  # signed out, not just refused


def test_session_writes_need_the_csrf_token(make_user: Callable[..., User]) -> None:
    user = make_user("staff", email="csrf@bank.example")
    user.set_password(PASSWORD)
    user.save()
    browser = Client(enforce_csrf_checks=True)
    # Sign-in itself is JSON-only rather than token-checked: there is no session yet.
    assert (
        post(
            browser, "/auth/login", {"email": "csrf@bank.example", "password": PASSWORD}
        ).status_code
        == 200
    )
    assert post(browser, "/auth/logout").status_code == 403
    token = browser.cookies["csrftoken"].value
    response = browser.post(
        "/api/v1/auth/logout", "{}", content_type="application/json", HTTP_X_CSRFTOKEN=token
    )
    assert response.status_code == 200


def test_idle_timeout_is_configured() -> None:
    from django.conf import settings

    assert settings.SESSION_SAVE_EVERY_REQUEST is True
    assert settings.SESSION_COOKIE_AGE == 30 * 60
    assert settings.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")
