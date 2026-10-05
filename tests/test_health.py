"""The health page (PRD OP-3): services, database, feeds, queue, licence, version."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.contrib.auth.models import User
from django.test import Client

from kpigo.ingestion.models import Feed
from tests.access_support import get
from tests.conftest import run

pytestmark = pytest.mark.django_db


def test_health_reports_services_database_feeds_licence_and_version(
    make_user: Callable[..., User],
) -> None:
    run("feed.register", name="monthly", template="actual_monthly", mode="upload")
    run("feed.register", name="daily", template="actual_daily", mode="upload")
    Feed.objects.filter(name="daily").update(freshness_state="stale")
    browser = Client()
    browser.force_login(make_user("data_steward"))
    response = get(browser, "/health", {"check_workers": "false"})
    assert response.status_code == 200, response.content
    body = response.json()
    services = {s["name"]: s for s in body["services"]}
    assert services["database"]["status"] == "ok"
    assert services["queue"]["status"] in ("ok", "down")
    assert services["feeds"]["status"] == "degraded"
    assert body["status"] == "degraded"
    assert body["feeds"]["total"] == 2 and body["feeds"]["stale"] == 1
    assert body["database"]["size_bytes"] > 0 and body["database"]["pending_migrations"] == 0
    assert body["database"]["largest_tables"]
    assert body["licence"]["state"] == "development"
    assert body["version"]["product_version"]
    assert "access" in body["version"]["migrations"]


def test_staff_do_not_see_the_health_page(make_user: Callable[..., User]) -> None:
    browser = Client()
    browser.force_login(make_user("staff"))
    assert get(browser, "/health").status_code == 403
