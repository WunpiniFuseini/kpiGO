"""Directory import with a reviewed diff, and the R0 exit: sign in, see only your subtree."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone
from ldap3 import Server

from kpigo.access.auth import http as sso_http
from kpigo.access.models import AppUser, DirectoryImport
from kpigo.action import Conflict, InvalidInput, invoke, registry
from kpigo.action.context import NoSubjects
from kpigo.action.identity import build_context
from kpigo.hierarchy.models import ReportingEdge, Subject
from tests import test_sso
from tests.access_support import post

pytestmark = pytest.mark.django_db
directory_server = test_sso.directory_server  # the fake LDAP server fixture


def as_user(user: User, action_name: str, /, **payload: Any) -> Any:
    return invoke(registry.get(action_name), payload, build_context(user, caller="cli"))


def roster(rows: list[list[str]]) -> dict[str, str]:
    text = "staff_no,full_name,email,manager_staff_no\n" + "".join(",".join(r) + "\n" for r in rows)
    return {"filename": "roster.csv", "content_base64": base64.b64encode(text.encode()).decode()}


BASE_ROSTER = [
    ["M1", "Kwame Asante", "kwame@bank.example", ""],
    ["E1", "Ama Mensah", "ama@bank.example", "M1"],
    ["E2", "Esi Owusu", "esi@bank.example", "M1"],
]


def preview(admin: User, rows: list[list[str]], **extra: Any) -> Any:
    return as_user(admin, "directory.import.preview", source="file", file=roster(rows), **extra)


def test_first_import_creates_people_and_lines(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    out = preview(admin, [*BASE_ROSTER, ["", "No Number", "x@bank.example", ""]])
    assert out.summary == {
        "new": 3,
        "changed": 0,
        "manager_changes": 0,
        "leavers": 0,
        "unchanged": 0,
        "rejected": 1,
    }
    assert out.rejected[0]["problem"] == "no staff number"
    assert not Subject.objects.exists()  # a preview writes nothing to the hierarchy
    applied = as_user(admin, "directory.import.apply", import_id=str(out.import_id))
    assert applied.status == "applied" and applied.applied["created"] == 3
    assert applied.applied["lines_added"] == 2
    managers = dict(ReportingEdge.objects.values_list("subject__staff_no", "manager__staff_no"))
    assert managers == {"E1": "M1", "E2": "M1"}
    with pytest.raises(Conflict):
        as_user(admin, "directory.import.apply", import_id=str(out.import_id))


def test_resync_shows_changes_moves_and_leavers(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    first = preview(admin, [*BASE_ROSTER, ["M2", "Abena Boateng", "abena@bank.example", ""]])
    yesterday = timezone.localdate() - timedelta(days=1)
    as_user(
        admin,
        "directory.import.apply",
        import_id=str(first.import_id),
        effective_from=str(yesterday),
    )
    esi = make_user("staff", email="esi@bank.example")
    assert AppUser.objects.get(auth_user=esi).subject is not None

    second = preview(
        admin,
        [
            ["M1", "Kwame Asante", "kwame@bank.example", ""],
            ["E1", "Ama Mensah-Osei", "ama@bank.example", "M2"],
            ["M2", "Abena Boateng", "abena@bank.example", ""],
            ["E4", "Kojo New", "kojo@bank.example", "M1"],
        ],
    )
    diff = second.diff
    assert [n["staff_no"] for n in diff["new"]] == ["E4"]
    assert diff["changed"] == [
        {"staff_no": "E1", "changes": {"full_name": ["Ama Mensah", "Ama Mensah-Osei"]}}
    ]
    assert diff["manager_changes"] == [{"staff_no": "E1", "from": "M1", "to": "M2"}]
    assert diff["leavers"] == [{"staff_no": "E2", "full_name": "Esi Owusu"}]

    out = as_user(
        admin,
        "directory.import.apply",
        import_id=str(second.import_id),
        provision_role="staff",
        provision_provider="oidc",
    )
    assert out.applied["left"] == 1 and out.applied["accounts_disabled"] == 1
    assert out.applied["accounts_invited"] == 1
    assert Subject.objects.get(staff_no="E2").status == "inactive"
    assert AppUser.objects.get(auth_user=esi).status == "disabled"
    assert AppUser.objects.get(email="kojo@bank.example").auth_provider == "oidc"
    today = timezone.localdate()
    current = ReportingEdge.objects.filter(subject__staff_no="E1", effective_to__isnull=True)
    assert [e.manager.staff_no for e in current] == ["M2"]
    assert (
        ReportingEdge.objects.get(subject__staff_no="E1", manager__staff_no="M1").effective_to
        == today
    )


def test_a_stale_preview_cannot_be_applied(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    out = preview(admin, BASE_ROSTER)
    from tests.conftest import run

    run("subject.register", staff_no="E1", full_name="Someone Else", email="other@bank.example")
    with pytest.raises(Conflict, match="Preview again"):
        as_user(admin, "directory.import.apply", import_id=str(out.import_id))
    assert DirectoryImport.objects.get(import_id=out.import_id).status == "previewed"
    as_user(admin, "directory.import.discard", import_id=str(out.import_id))
    assert as_user(admin, "directory.import.list", status="discarded").imports


def test_a_roster_without_the_required_columns_is_refused(make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    bad = {"filename": "r.csv", "content_base64": base64.b64encode(b"name\nx\n").decode()}
    with pytest.raises(InvalidInput, match="columns"):
        as_user(admin, "directory.import.preview", source="file", file=bad)


def test_r0_exit_sign_in_against_the_directory_and_see_only_your_subtree(
    directory_server: Server,
    make_user: Callable[..., User],
) -> None:
    """Import the roster from LDAP, invite the manager, sign in by LDAP bind."""
    admin = make_user("admin")
    out = as_user(admin, "directory.import.preview", source="ldap")
    assert out.summary["new"] == 4
    as_user(
        admin,
        "directory.import.apply",
        import_id=str(out.import_id),
        effective_from=str(timezone.localdate().replace(day=1)),
    )
    invited = as_user(
        admin,
        "user.invite",
        email="kwame@bank.example",
        display_name="Kwame",
        role_codes=["line_manager"],
        auth_provider="ldap",
    )
    assert invited.user.subject_id is not None
    browser = Client()
    signed_in = post(
        browser, "/auth/login", {"email": "kwame@bank.example", "password": "kwame-pass"}
    )
    assert signed_in.status_code == 200, signed_in.content
    assert signed_in.json()["no_access"] == []

    kwame = AppUser.objects.get(email="kwame@bank.example").auth_user
    scope = build_context(kwame, caller="http").visible_subjects
    ids = dict(Subject.objects.values_list("staff_no", "subject_id"))
    assert all(scope.contains(str(ids[s])) for s in ("M1", "E1", "E2"))
    assert not scope.contains(str(ids["E3"]))  # Yaw reports to nobody under Kwame
    stranger = make_user("staff")
    assert isinstance(build_context(stranger, caller="http").visible_subjects, NoSubjects)


# ── Entra ──────────────────────────────────────────────────────────────────


@pytest.fixture
def graph(settings: Any) -> Iterator[list[str]]:
    settings.KPIGO_ENTRA_TENANT_ID = "tenant-1"
    settings.KPIGO_ENTRA_CLIENT_ID = "app-1"
    settings.KPIGO_ENTRA_CLIENT_SECRET = "secret"
    seen: list[str] = []
    users = [
        {
            "id": "u1",
            "displayName": "Kwame",
            "mail": "kwame@bank.example",
            "employeeId": "M1",
            "accountEnabled": True,
        },
        {
            "id": "u2",
            "displayName": "Ama",
            "mail": None,
            "userPrincipalName": "ama@bank.example",
            "employeeId": "E1",
            "accountEnabled": True,
            "manager": {"id": "u1"},
        },
        {
            "id": "u3",
            "displayName": "Gone",
            "mail": "gone@bank.example",
            "employeeId": "E9",
            "accountEnabled": False,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "t", "token_type": "Bearer"})
        assert request.headers["Authorization"] == "Bearer t"
        if "skiptoken" in str(request.url):
            return httpx.Response(200, json={"value": users[2:]})
        return httpx.Response(
            200,
            json={
                "value": users[:2],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=2",
            },
        )

    sso_http.TRANSPORT = httpx.MockTransport(handler)
    yield seen
    sso_http.TRANSPORT = None


def test_entra_roster_reads_every_page(graph: list[str], make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    out = as_user(admin, "directory.import.preview", source="entra")
    staff = {n["staff_no"]: n for n in out.diff["new"]}
    assert set(staff) == {"M1", "E1"}  # the disabled account is not imported
    assert staff["E1"]["manager_staff_no"] == "M1"
    assert staff["E1"]["email"] == "ama@bank.example"
    assert any("skiptoken" in url for url in graph)
    assert as_user(admin, "directory.test", source="entra").ok is True
