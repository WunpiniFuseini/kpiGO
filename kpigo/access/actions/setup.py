"""First run (App Flow §2): before anyone can sign in, the install needs an Admin.

``setup.bootstrap`` creates that first Admin. It needs the one-time setup token
the infra team sets (``KPIGO_SETUP_TOKEN``) and works only while no active
Admin exists, so it cannot be used to add a second one later.
"""

from __future__ import annotations

import hmac

from django.conf import settings
from pydantic import BaseModel, Field, SecretStr

from kpigo.access.accounts import create_account
from kpigo.access.actions.auth import _check_password, _me
from kpigo.access.auth import signin
from kpigo.access.me import MeOut
from kpigo.access.models import AppUser
from kpigo.action import ActionContext, Conflict, InvalidInput, PermissionDenied, action
from kpigo.licence.state import current, install_fingerprint, product_version


def admin_exists(org_id: str) -> bool:
    return AppUser.objects.filter(org_id=org_id, status="active", roles__role_code="admin").exists()


class SetupStatusIn(BaseModel):
    pass


class SetupStatusOut(BaseModel):
    needs_admin: bool
    setup_token_configured: bool
    licence_state: str
    licence_message: str
    install_fingerprint: str
    product_version: str


@action(
    name="setup.status",
    summary="What first run still needs: an Admin, a licence. Shows the install fingerprint.",
    schema=SetupStatusIn,
    output=SetupStatusOut,
    read_only=True,
    public=True,
    http={"method": "GET", "path": "/setup"},
    example={},
)
def status(params: SetupStatusIn, ctx: ActionContext) -> SetupStatusOut:
    licence = current(ctx.org_id)
    return SetupStatusOut(
        needs_admin=not admin_exists(ctx.org_id),
        setup_token_configured=bool(getattr(settings, "KPIGO_SETUP_TOKEN", None)),
        licence_state=licence.state,
        licence_message=licence.message,
        install_fingerprint=install_fingerprint(),
        product_version=product_version(),
    )


class BootstrapIn(BaseModel):
    setup_token: SecretStr
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str = Field(min_length=1, max_length=200)
    password: SecretStr


@action(
    name="setup.bootstrap",
    summary="Create the first Admin (local account), using the install's one-time setup token.",
    schema=BootstrapIn,
    output=MeOut,
    read_only=False,
    public=True,
    audit="setup.bootstrapped",
    http={"method": "POST", "path": "/setup/bootstrap"},
    example={
        "setup_token": "from-the-install",
        "email": "admin@bank.example",
        "display_name": "First Admin",
        "password": "correct horse battery staple",
    },
)
def bootstrap(params: BootstrapIn, ctx: ActionContext) -> MeOut:
    expected = getattr(settings, "KPIGO_SETUP_TOKEN", None)
    given = params.setup_token.get_secret_value()
    if not expected or not hmac.compare_digest(given.encode(), str(expected).encode()):
        raise PermissionDenied("The setup token is not correct.")
    if admin_exists(ctx.org_id):
        raise Conflict("This install already has an Admin. Sign in instead.")
    account = create_account(
        ctx.org_id,
        email=str(params.email),
        display_name=params.display_name,
        auth_provider="local",
        role_codes=["admin"],
        subject=None,
        created_by=None,
    )
    password = params.password.get_secret_value()
    if not password:
        raise InvalidInput("Choose a password.")
    _check_password(password, account.auth_user)
    account.auth_user.set_password(password)
    account.auth_user.save(update_fields=["password"])
    signin.finish(account, ctx)
    return _me(account, ctx)
