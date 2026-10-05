"""OpenID Connect sign-in: authorization code with PKCE (Entra ID, Okta, Keycloak...).

kpiGo starts the flow and returns the identity provider's URL; the browser
comes back to the frontend's callback route, which posts the code and state to
``auth.oidc.complete``. MFA is the identity provider's job (App Flow §1); with
``KPIGO_OIDC_REQUIRE_MFA`` kpiGo refuses an ID token that does not say it
happened.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from django.conf import settings
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from kpigo.access.auth.http import client
from kpigo.access.models import AuthFlowState
from kpigo.action.errors import AuthenticationFailed

# Asymmetric only: never "none", never a shared-secret HMAC the client also holds.
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]
# ``amr`` values that mean more than one factor (RFC 8176).
MFA_METHODS = {"mfa", "otp", "hwk", "swk", "sms", "tel", "fido", "face", "fpt", "iris", "retina"}
LEEWAY_SECONDS = 60


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str
    email_claim: str
    require_mfa: bool
    acr_values: str


def config() -> OidcConfig | None:
    issuer = getattr(settings, "KPIGO_OIDC_ISSUER", None)
    if not issuer or not settings.KPIGO_OIDC_CLIENT_ID:
        return None
    return OidcConfig(
        issuer=issuer.rstrip("/"),
        client_id=settings.KPIGO_OIDC_CLIENT_ID,
        client_secret=settings.KPIGO_OIDC_CLIENT_SECRET or "",
        redirect_uri=settings.KPIGO_OIDC_REDIRECT_URI or "",
        scopes=settings.KPIGO_OIDC_SCOPES,
        email_claim=settings.KPIGO_OIDC_EMAIL_CLAIM,
        require_mfa=bool(settings.KPIGO_OIDC_REQUIRE_MFA),
        acr_values=settings.KPIGO_OIDC_ACR_VALUES or "",
    )


def _get_json(http: httpx.Client, url: str) -> dict[str, Any]:
    response = http.get(url)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"{url} did not return a JSON object.")
    return data


def discover(cfg: OidcConfig, http: httpx.Client) -> dict[str, Any]:
    doc = _get_json(http, f"{cfg.issuer}/.well-known/openid-configuration")
    if str(doc.get("issuer", "")).rstrip("/") != cfg.issuer:
        raise ValueError("The discovery document names a different issuer.")
    return doc


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorization_url(cfg: OidcConfig, flow: AuthFlowState, challenge: str) -> str:
    with client() as http:
        endpoint = str(discover(cfg, http)["authorization_endpoint"])
    query = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "scope": cfg.scopes,
        "state": flow.state,
        "nonce": flow.nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if cfg.acr_values:
        query["acr_values"] = cfg.acr_values
    return f"{endpoint}?{urlencode(query)}"


def new_pkce() -> tuple[str, str, str]:
    """(nonce, code_verifier, code_challenge)."""
    verifier, challenge = _pkce()
    return secrets.token_urlsafe(24), verifier, challenge


def exchange(cfg: OidcConfig, flow: AuthFlowState, code: str) -> dict[str, Any]:
    """Trade the code for an ID token and return its verified claims."""
    try:
        with client() as http:
            doc = discover(cfg, http)
            response = http.post(
                str(doc["token_endpoint"]),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": cfg.redirect_uri,
                    "client_id": cfg.client_id,
                    "client_secret": cfg.client_secret,
                    "code_verifier": flow.code_verifier,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            tokens = response.json()
            keys = _get_json(http, str(doc["jwks_uri"]))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise AuthenticationFailed(f"The identity provider could not be reached: {exc}") from None
    id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if not isinstance(id_token, str):
        raise AuthenticationFailed("The identity provider returned no ID token.")
    return verify_id_token(cfg, id_token, keys, flow.nonce)


def verify_id_token(
    cfg: OidcConfig, id_token: str, jwks: dict[str, Any], nonce: str
) -> dict[str, Any]:
    try:
        keys = KeySet.import_key_set(jwks)  # type: ignore[arg-type]
        token = jwt.decode(id_token, keys, algorithms=ALGORITHMS)
        jwt.JWTClaimsRegistry(
            leeway=LEEWAY_SECONDS,
            iss={"essential": True, "value": cfg.issuer},
            aud={"essential": True, "value": cfg.client_id},
            exp={"essential": True},
            iat={"essential": True},
            sub={"essential": True},
            nonce={"essential": True, "value": nonce},
        ).validate(token.claims)
    except (JoseError, ValueError) as exc:
        raise AuthenticationFailed(f"The ID token was refused: {exc}") from None
    claims = dict(token.claims)
    audience = claims.get("aud")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != cfg.client_id:
        raise AuthenticationFailed("The ID token was issued to another client.")
    if cfg.require_mfa:
        methods = claims.get("amr") or []
        if not isinstance(methods, list) or not MFA_METHODS & {str(m) for m in methods}:
            raise AuthenticationFailed(
                "Your identity provider did not confirm multi-factor sign-in, which kpiGo "
                "requires. Sign in again with MFA, or ask your IT team."
            )
    return claims


def email_of(cfg: OidcConfig, claims: dict[str, Any]) -> str:
    value = claims.get(cfg.email_claim) or claims.get("email") or ""
    return str(value)
