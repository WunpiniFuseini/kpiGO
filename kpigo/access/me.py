"""What the signed-in user's shell needs: who they are, their pages, and why one is empty."""

from __future__ import annotations

import uuid
from datetime import datetime

from django.conf import settings
from pydantic import BaseModel

from kpigo.access.identity import page_access, role_codes
from kpigo.access.models import AppUser
from kpigo.access.pages import PAGES
from kpigo.action.context import ActionContext
from kpigo.hierarchy.models import VisibilityClosure
from kpigo.hierarchy.scope import current_period_key
from kpigo.licence.state import current as licence_state

ASK_ADMIN = "an Admin, under Administer → Users & access"


class UserOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    auth_provider: str
    status: str
    subject_id: uuid.UUID | None
    roles: list[str]
    last_login_at: datetime | None


class PageOut(BaseModel):
    page_key: str
    label: str
    group: str
    access: str


class NoAccessOut(BaseModel):
    page_key: str
    missing: str
    ask: str


class LicenceBanner(BaseModel):
    state: str
    mode: str
    message: str
    days_left: int | None


class SessionOut(BaseModel):
    idle_timeout_seconds: int
    max_age_seconds: int


class MeOut(BaseModel):
    user: UserOut
    permissions: list[str]
    pages: list[PageOut]
    home: str | None
    no_access: list[NoAccessOut]
    licence: LicenceBanner
    session: SessionOut


def user_out(account: AppUser) -> UserOut:
    return UserOut(
        user_id=account.user_id,
        email=account.email,
        display_name=account.display_name,
        auth_provider=account.auth_provider,
        status=account.status,
        subject_id=account.subject_id,
        roles=sorted(role_codes(account)),
        last_login_at=account.last_login_at,
    )


def build_me(account: AppUser, ctx: ActionContext) -> MeOut:
    org_id = str(account.org_id)
    licence = licence_state(org_id)
    codes = role_codes(account)
    matrix = page_access(org_id, codes)
    licensed = set(licence.modules) | {"platform"}
    pages = [
        PageOut(page_key=p.key, label=p.label, group=p.group, access=matrix[p.key])
        for p in PAGES
        if matrix.get(p.key, "none") != "none" and p.module in licensed
    ]
    shown = {p.page_key for p in pages}
    return MeOut(
        user=user_out(account),
        permissions=sorted(ctx.permissions),
        pages=pages,
        home=pages[0].page_key if pages else None,
        no_access=_no_access(account, shown, ctx),
        licence=LicenceBanner(
            state=licence.state,
            mode=licence.mode,
            message=licence.message,
            days_left=licence.days_left,
        ),
        session=SessionOut(
            idle_timeout_seconds=int(settings.SESSION_COOKIE_AGE),
            max_age_seconds=int(getattr(settings, "KPIGO_SESSION_MAX_SECONDS", 12 * 3600)),
        ),
    )


def _no_access(account: AppUser, shown: set[str], ctx: ActionContext) -> list[NoAccessOut]:
    """Pages the user may open whose data scope is absent (App Flow §9, NO ACCESS)."""
    found: list[NoAccessOut] = []
    if "scorecards" in shown:
        if account.subject_id is None:
            found.append(
                NoAccessOut(
                    page_key="scorecards",
                    missing="Your account is not linked to a person in the hierarchy.",
                    ask=ASK_ADMIN,
                )
            )
        else:
            period = current_period_key(str(account.org_id))
            built = VisibilityClosure.objects.filter(
                org_id=account.org_id, period_key=period, viewer_subject_id=account.subject_id
            ).exists()
            if not built:
                found.append(
                    NoAccessOut(
                        page_key="scorecards",
                        missing=f"Visibility for {period} has not been built for you.",
                        ask="a Data Steward, under Administer → Business calendar",
                    )
                )
    granted = {g.module for g in ctx.data_scopes}
    for module, label in (("executive", "Executive"), ("campaign", "Campaign")):
        if module in shown and module not in granted:
            found.append(
                NoAccessOut(
                    page_key=module,
                    missing=f"You have no {label} data-scope grant.",
                    ask=ASK_ADMIN,
                )
            )
    return found
