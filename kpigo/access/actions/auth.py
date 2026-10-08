"""Signing in and out (TDD §9 AuthN, App Flow §1 LOGIN → MFA → APP).

The sign-in actions are public: the only actions an anonymous caller reaches.
Each one ends by asking for a session through ``ctx.session``; the HTTP adapter
creates it. MFA is delegated to the identity provider.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer

from kpigo.access.accounts import hash_token
from kpigo.access.auth import flows, local, oidc, saml, signin
from kpigo.access.identity import app_user_for
from kpigo.access.me import MeOut, build_me
from kpigo.access.models import AppUser
from kpigo.action import (
    ActionContext,
    AuthenticationFailed,
    InvalidInput,
    NotAuthenticated,
    action,
)
from kpigo.action.identity import build_context

OIDC_STATE_KEY = "kpigo_oidc_state"
HIDDEN = "<omitted>"


def _me(account: AppUser, ctx: ActionContext) -> MeOut:
    """The shell for an account just signed in, built with its own rights."""
    user_ctx = build_context(account.auth_user, caller=ctx.caller, session=ctx.session)
    return build_me(account, user_ctx)


# ── auth.providers ──────────────────────────────────────────────────────────


class ProvidersIn(BaseModel):
    pass


class ProviderOut(BaseModel):
    enabled: bool
    label: str


class ProvidersOut(BaseModel):
    password: ProviderOut
    oidc: ProviderOut
    saml: ProviderOut


@action(
    name="auth.providers",
    summary="Which sign-in methods this install offers, for the login screen.",
    schema=ProvidersIn,
    output=ProvidersOut,
    read_only=True,
    public=True,
    http={"method": "GET", "path": "/auth/providers"},
    example={},
)
def providers(params: ProvidersIn, ctx: ActionContext) -> ProvidersOut:
    password = bool(getattr(settings, "KPIGO_LOCAL_LOGIN", True)) or ldap_configured()
    return ProvidersOut(
        password=ProviderOut(enabled=password, label="Email and password"),
        oidc=ProviderOut(enabled=oidc.config() is not None, label=settings.KPIGO_OIDC_LABEL),
        saml=ProviderOut(enabled=saml.config() is not None, label=settings.KPIGO_SAML_LABEL),
    )


def ldap_configured() -> bool:
    return bool(getattr(settings, "KPIGO_LDAP_URL", None))


# ── auth.login / auth.logout / auth.me ───────────────────────────────────────


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)


@action(
    name="auth.login",
    summary="Sign in with email and password: a local account, or an LDAP bind.",
    schema=LoginIn,
    output=MeOut,
    read_only=False,
    public=True,
    audit="auth.login",
    http={"method": "POST", "path": "/auth/login"},
    example={"email": "ama.mensah@bank.example", "password": "correct horse battery"},
)
def login(params: LoginIn, ctx: ActionContext) -> MeOut:
    account = local.authenticate(ctx.org_id, params.email, params.password.get_secret_value())
    signin.finish(account, ctx)
    return _me(account, ctx)


class LogoutIn(BaseModel):
    pass


class LogoutOut(BaseModel):
    signed_out: bool


@action(
    name="auth.logout",
    agent_forbidden=True,
    summary="Sign out of this session.",
    schema=LogoutIn,
    output=LogoutOut,
    permission="auth.session",
    read_only=False,
    audit="auth.logout",
    http={"method": "POST", "path": "/auth/logout"},
    example={},
)
def logout(params: LogoutIn, ctx: ActionContext) -> LogoutOut:
    ctx.session.logout()
    return LogoutOut(signed_out=True)


class MeIn(BaseModel):
    pass


@action(
    name="auth.me",
    summary="The signed-in user: roles, pages, licence banner and why any page is empty.",
    schema=MeIn,
    output=MeOut,
    permission="auth.session",
    read_only=True,
    http={"method": "GET", "path": "/auth/me"},
    example={},
)
def me(params: MeIn, ctx: ActionContext) -> MeOut:
    account = app_user_for(ctx.user, ctx.org_id)
    if account is None:
        raise NotAuthenticated("This sign-in has no kpiGo account.")
    return build_me(account, ctx)


# ── auth.invite.accept / auth.password.change ──────────────────────────────


def _check_password(password: str, user: Any) -> None:
    try:
        password_validation.validate_password(password, user)
    except ValidationError as exc:
        raise InvalidInput("Choose a stronger password.", detail=list(exc.messages)) from None


class InviteAcceptIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_serializer("token", when_used="json")
    def _hide(self, value: str) -> str:
        return HIDDEN


@action(
    name="auth.invite.accept",
    summary="Set a password from an invitation link, activating a local account.",
    schema=InviteAcceptIn,
    output=MeOut,
    read_only=False,
    public=True,
    audit="auth.invite.accepted",
    http={"method": "POST", "path": "/auth/invite/accept"},
    example={"token": "token-from-the-invitation", "password": "correct horse battery"},
)
def accept_invite(params: InviteAcceptIn, ctx: ActionContext) -> MeOut:
    account = (
        AppUser.objects.select_for_update()
        .select_related("auth_user")
        .filter(org_id=ctx.org_id, invite_token_hash=hash_token(params.token))
        .first()
    )
    if (
        account is None
        or account.auth_provider != "local"
        or account.status != "invited"
        or account.invite_expires_at is None
        or account.invite_expires_at <= timezone.now()
    ):
        raise InvalidInput(
            "This invitation is not valid or has expired. Ask an Admin to resend it."
        )
    password = params.password.get_secret_value()
    _check_password(password, account.auth_user)
    account.auth_user.set_password(password)
    account.auth_user.save(update_fields=["password"])
    account.invite_token_hash = None
    account.invite_expires_at = None
    account.save(update_fields=["invite_token_hash", "invite_expires_at"])
    signin.finish(account, ctx)
    return _me(account, ctx)


class PasswordChangeIn(BaseModel):
    current_password: SecretStr
    new_password: SecretStr


class PasswordChangeOut(BaseModel):
    changed: bool


@action(
    name="auth.password.change",
    agent_forbidden=True,
    summary="Change your own password (local accounts only).",
    schema=PasswordChangeIn,
    output=PasswordChangeOut,
    permission="auth.session",
    read_only=False,
    audit="auth.password.changed",
    example={"current_password": "old pass phrase", "new_password": "a new pass phrase"},
)
def change_password(params: PasswordChangeIn, ctx: ActionContext) -> PasswordChangeOut:
    account = app_user_for(ctx.user, ctx.org_id)
    if account is None or account.auth_provider != "local":
        raise InvalidInput("Your password is managed by your organisation's directory.")
    user = account.auth_user
    if not user.check_password(params.current_password.get_secret_value()):
        raise InvalidInput("The current password is incorrect.")
    new = params.new_password.get_secret_value()
    _check_password(new, user)
    user.set_password(new)
    user.save(update_fields=["password"])
    ctx.session.login(user)  # keep this session; the hash change ends the others
    return PasswordChangeOut(changed=True)


# ── OIDC ───────────────────────────────────────────────────────────────────


class SsoStartIn(BaseModel):
    next: str = Field(default="/", max_length=500)


class SsoStartOut(BaseModel):
    redirect_url: str


def _oidc_config() -> oidc.OidcConfig:
    cfg = oidc.config()
    if cfg is None:
        raise InvalidInput("Single sign-on (OIDC) is not configured on this install.")
    return cfg


@action(
    name="auth.oidc.start",
    summary="Begin OIDC sign-in: returns the identity provider URL to send the browser to.",
    schema=SsoStartIn,
    output=SsoStartOut,
    read_only=False,
    public=True,
    http={"method": "POST", "path": "/auth/oidc/start"},
    example={"next": "/"},
)
def oidc_start(params: SsoStartIn, ctx: ActionContext) -> SsoStartOut:
    cfg = _oidc_config()
    nonce, verifier, challenge = oidc.new_pkce()
    flow = flows.new_flow(
        ctx.org_id, "oidc", next_path=params.next, nonce=nonce, code_verifier=verifier
    )
    # The callback must come back to the browser that started it.
    ctx.session.set(OIDC_STATE_KEY, flow.state)
    try:
        url = oidc.authorization_url(cfg, flow, challenge)
    except Exception as exc:
        raise InvalidInput(f"The identity provider could not be reached: {exc}") from None
    return SsoStartOut(redirect_url=url)


class OidcCompleteIn(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=200)

    @field_serializer("code", when_used="json")
    def _hide(self, value: str) -> str:
        return HIDDEN


class SsoCompleteOut(MeOut):
    next: str


@action(
    name="auth.oidc.complete",
    summary="Finish OIDC sign-in with the code and state the identity provider returned.",
    schema=OidcCompleteIn,
    output=SsoCompleteOut,
    read_only=False,
    public=True,
    audit="auth.oidc",
    http={"method": "POST", "path": "/auth/oidc/complete"},
    example={"code": "authorization-code", "state": "state-from-start"},
)
def oidc_complete(params: OidcCompleteIn, ctx: ActionContext) -> SsoCompleteOut:
    cfg = _oidc_config()
    expected = ctx.session.pop(OIDC_STATE_KEY)
    flow = flows.consume(ctx.org_id, "oidc", params.state)
    if expected != params.state:
        raise AuthenticationFailed("This sign-in was started in another browser. Start again.")
    claims = oidc.exchange(cfg, flow, params.code)
    account = signin.sso_account(ctx.org_id, "oidc", str(claims["sub"]), oidc.email_of(cfg, claims))
    signin.finish(account, ctx, external_id=str(claims["sub"]))
    return SsoCompleteOut(**_me(account, ctx).model_dump(), next=flow.next_path)


# ── SAML ───────────────────────────────────────────────────────────────────


def _saml_config() -> saml.SamlConfig:
    cfg = saml.config()
    if cfg is None:
        raise InvalidInput("Single sign-on (SAML) is not configured on this install.")
    return cfg


@action(
    name="auth.saml.start",
    summary="Begin SAML sign-in: returns the identity provider URL to send the browser to.",
    schema=SsoStartIn,
    output=SsoStartOut,
    read_only=False,
    public=True,
    http={"method": "POST", "path": "/auth/saml/start"},
    example={"next": "/"},
)
def saml_start(params: SsoStartIn, ctx: ActionContext) -> SsoStartOut:
    cfg = _saml_config()
    request_id = saml.new_request_id()
    flows.new_flow(ctx.org_id, "saml", next_path=params.next, state=request_id)
    return SsoStartOut(redirect_url=saml.redirect_url(cfg, request_id))


class SamlAcsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    saml_response: str = Field(alias="SAMLResponse", min_length=1, max_length=1_000_000)
    relay_state: str = Field(default="", alias="RelayState", max_length=500)

    @field_serializer("saml_response", when_used="json")
    def _hide(self, value: str) -> str:
        return HIDDEN


class RedirectOut(BaseModel):
    redirect_to: str


@action(
    name="auth.saml.acs",
    summary="The SAML assertion consumer: the identity provider posts the response here.",
    schema=SamlAcsIn,
    output=RedirectOut,
    read_only=False,
    public=True,
    audit="auth.saml",
    http={"method": "POST", "path": "/auth/saml/acs", "form": True, "redirect": True},
    example={"SAMLResponse": "PHNhbWxwOlJlc3BvbnNlLz4=", "RelayState": ""},
)
def saml_acs(params: SamlAcsIn, ctx: ActionContext) -> RedirectOut:
    cfg = _saml_config()
    try:
        identity = saml.parse_response(cfg, params.saml_response)
        flow = flows.consume(ctx.org_id, "saml", identity.in_response_to)
        account = signin.sso_account(ctx.org_id, "saml", identity.name_id, identity.email)
        signin.finish(account, ctx, external_id=identity.name_id)
    except AuthenticationFailed as exc:
        # The browser is mid-redirect from the identity provider: send it to the
        # login screen with a reason rather than a JSON error page.
        ctx.audit("auth.saml.refused", reason=exc.message)
        return RedirectOut(redirect_to="/login?error=sso_refused")
    return RedirectOut(redirect_to=flow.next_path)
