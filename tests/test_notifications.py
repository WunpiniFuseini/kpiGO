"""The notification centre and the daily digest (PRD NT-1..5)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import User

from kpigo.access.identity import app_user_for
from kpigo.action import invoke, registry
from kpigo.action.identity import build_context
from kpigo.platform import notify
from tests.conftest import ORG_ID

pytestmark = pytest.mark.django_db


def as_user(user: User, action_name: str, /, **payload: Any) -> Any:
    return invoke(registry.get(action_name), payload, build_context(user, caller="cli"))


def seed(user: User, **kw: Any) -> Any:
    account = app_user_for(user, ORG_ID)
    assert account is not None
    return notify.notify(ORG_ID, account, kw.pop("category", "system"), kw.pop("title", "Hi"), **kw)


@pytest.fixture
def admin(make_user: Callable[..., User]) -> User:
    return make_user("admin")


@pytest.fixture
def relay(settings: Any) -> None:
    settings.KPIGO_EMAIL_HOST = "relay.bank.example"
    settings.KPIGO_PUBLIC_URL = "https://kpigo.bank.example"


def test_list_shows_own_notices_newest_first_with_unread_count(admin: User) -> None:
    seed(admin, title="Older")
    seed(admin, title="Newer", level="warning")
    out = as_user(admin, "notification.list")
    assert out.unread_count == 2
    assert [n.title for n in out.notifications] == ["Newer", "Older"]


def test_unread_only_filters(admin: User) -> None:
    first = seed(admin, title="One")
    seed(admin, title="Two")
    as_user(admin, "notification.mark_read", notification_id=str(first.notification_id))
    out = as_user(admin, "notification.list", unread_only=True)
    assert [n.title for n in out.notifications] == ["Two"]
    assert out.unread_count == 1


def test_mark_read_one_then_all(admin: User) -> None:
    a = seed(admin, title="A")
    seed(admin, title="B")
    one = as_user(admin, "notification.mark_read", notification_id=str(a.notification_id))
    assert one.marked == 1 and one.unread_count == 1
    allout = as_user(admin, "notification.mark_read")
    assert allout.marked == 1 and allout.unread_count == 0


def test_a_person_sees_only_their_own(make_user: Callable[..., User]) -> None:
    alice = make_user("admin", username="alice")
    bob = make_user("staff", username="bob")
    seed(alice, title="For Alice")
    seed(bob, title="For Bob")
    assert [n.title for n in as_user(alice, "notification.list").notifications] == ["For Alice"]
    assert [n.title for n in as_user(bob, "notification.list").notifications] == ["For Bob"]


def test_everyone_can_read_their_centre(make_user: Callable[..., User]) -> None:
    # A plain staff account, not an admin, still has notification.view.
    staff = make_user("staff", username="rm")
    seed(staff, title="Yours")
    assert as_user(staff, "notification.list").unread_count == 1


def test_digest_without_a_relay_is_a_clean_no_op(admin: User, mailoutbox: list[Any]) -> None:
    seed(admin, title="Owed", digest=True)
    out = as_user(admin, "notification.digest")
    assert out.relay_configured is False and out.emailed == 0
    assert mailoutbox == []
    # The in-app notice is untouched — the centre is always on.
    assert app_user_for(admin, ORG_ID) is not None
    from kpigo.platform.models import Notification

    assert Notification.objects.get(title="Owed").emailed_at is None


def test_digest_emails_one_batched_message_and_marks_sent(
    admin: User, relay: None, mailoutbox: list[Any]
) -> None:
    seed(admin, title="Scorecard ready", category="scorecard_ready", digest=True)
    seed(admin, title="Feed quarantined", category="load_quarantined", digest=True, level="warning")
    account = app_user_for(admin, ORG_ID)
    assert account is not None
    out = as_user(admin, "notification.digest")
    assert out.emailed == 1 and out.recipients == 1
    (message,) = mailoutbox
    assert message.to == [account.email]
    assert "2 new notification(s)" in message.subject
    assert "Scorecard ready" in message.body and "Feed quarantined" in message.body
    assert "https://kpigo.bank.example/notifications" in message.body
    # No value or score leaks into the email body — titles and counts only.
    from kpigo.platform.models import Notification

    assert all(n.emailed_at is not None for n in Notification.objects.filter(digest=True))
    # A second run sends nothing more.
    assert as_user(admin, "notification.digest").emailed == 0
    assert len(mailoutbox) == 1


def test_digest_leaves_producer_emailed_notices_alone(
    admin: User, relay: None, mailoutbox: list[Any]
) -> None:
    # Escalation sends its own mail, so its notices are digest=False and the batch
    # run must not email them again.
    seed(admin, title="Escalated", category="escalation", digest=False)
    out = as_user(admin, "notification.digest")
    assert out.emailed == 0 and mailoutbox == []


def test_digest_dry_run_sends_nothing(admin: User, relay: None, mailoutbox: list[Any]) -> None:
    seed(admin, title="Owed", digest=True)
    invoke(
        registry.get("notification.digest"),
        {},
        build_context(admin, caller="cli", dry_run=True),
    )
    assert mailoutbox == []


def test_feed_problem_notifies_the_stewards(make_user: Callable[..., User]) -> None:
    # The NT-2 producer: a quarantined load reaches everyone who can load feeds.
    from kpigo.ingestion.actions.runs import _notify_feed_problem
    from kpigo.platform.models import Notification

    steward = make_user("admin", username="steward")  # admin holds feed.run
    ctx = build_context(steward, caller="cli")
    feed = type("F", (), {"name": "monthly_actuals"})()
    _notify_feed_problem(ctx, feed, "quarantined")

    account = app_user_for(steward, ORG_ID)
    assert account is not None
    notices = Notification.objects.filter(
        recipient_id=str(account.user_id), category="load_quarantined"
    )
    assert notices.count() == 1
    row = notices.get()
    assert row.subject_ref == "monthly_actuals" and row.level == "warning"
    assert "quarantined" in row.title and row.digest is True
