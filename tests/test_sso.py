"""Single sign-on: OIDC, SAML and LDAP, against fakes of each identity provider."""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.contrib.auth.models import User
from django.test import Client
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, SYNC, Connection, Server
from lxml import etree
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.signer import XMLSigner

from kpigo.access.auth import http as sso_http
from kpigo.access.auth import ldap as ldap_module
from kpigo.access.models import AppUser, AuthFlowState
from kpigo.action import AuthenticationFailed, invoke, registry
from kpigo.action.identity import anonymous_context, build_context
from tests.access_support import get, post

pytestmark = pytest.mark.django_db

ISSUER = "https://idp.bank.example"
CLIENT_ID = "kpigo-web"
REDIRECT = "https://kpigo.bank.example/auth/callback"


def sso_user(make_user: Callable[..., User], email: str, provider: str, **extra: Any) -> AppUser:
    user = make_user("staff", email=email, status=extra.pop("status", "invited"))
    account = AppUser.objects.get(auth_user=user)
    account.auth_provider = provider
    for key, value in extra.items():
        setattr(account, key, value)
    account.save()
    return account


# ── OIDC ───────────────────────────────────────────────────────────────────


class FakeOidc:
    """An identity provider: discovery, JWKS, and a token endpoint checking PKCE."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
        self.claims: dict[str, Any] = {}
        self.challenges: dict[str, str] = {}
        self.signing_key: Any = self.key
        self.alg = "RS256"

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if url.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if url.endswith("/token"):
            form = parse_qs(request.content.decode())
            verifier = form["code_verifier"][0]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            if challenge not in self.challenges.values():
                return httpx.Response(400, json={"error": "invalid_grant"})
            flow = AuthFlowState.objects.filter(code_verifier=verifier).first()
            now = int(datetime.now(UTC).timestamp())
            claims = {
                "iss": ISSUER,
                "aud": CLIENT_ID,
                "sub": "entra-oid-1",
                "email": "ama@bank.example",
                "iat": now,
                "exp": now + 300,
                "nonce": flow.nonce if flow else "",
                "amr": ["pwd", "mfa"],
                **self.claims,
            }
            claims = {k: v for k, v in claims.items() if v is not None}
            token = jwt.encode({"alg": self.alg, "kid": "k1"}, claims, self.signing_key)
            return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def idp(settings: Any) -> Iterator[FakeOidc]:
    fake = FakeOidc()
    settings.KPIGO_OIDC_ISSUER = ISSUER
    settings.KPIGO_OIDC_CLIENT_ID = CLIENT_ID
    settings.KPIGO_OIDC_CLIENT_SECRET = "secret"
    settings.KPIGO_OIDC_REDIRECT_URI = REDIRECT
    sso_http.TRANSPORT = httpx.MockTransport(fake.handler)
    yield fake
    sso_http.TRANSPORT = None


def start(browser: Client, idp: FakeOidc, next_path: str = "/scorecards") -> str:
    response = post(browser, "/auth/oidc/start", {"next": next_path})
    assert response.status_code == 200, response.content
    query = parse_qs(urlparse(response.json()["redirect_url"]).query)
    assert query["code_challenge_method"] == ["S256"] and query["redirect_uri"] == [REDIRECT]
    idp.challenges[query["state"][0]] = query["code_challenge"][0]
    return query["state"][0]


def test_oidc_sign_in_activates_the_invited_user(
    idp: FakeOidc, make_user: Callable[..., User]
) -> None:
    sso_user(make_user, "ama@bank.example", "oidc")
    browser = Client()
    state = start(browser, idp)
    response = post(browser, "/auth/oidc/complete", {"code": "c1", "state": state})
    assert response.status_code == 200, response.content
    assert response.json()["next"] == "/scorecards"
    account = AppUser.objects.get(email="ama@bank.example")
    assert account.status == "active" and account.external_id == "entra-oid-1"
    assert get(browser, "/auth/me").json()["user"]["auth_provider"] == "oidc"
    # The state is single use.
    replay = post(Client(), "/auth/oidc/complete", {"code": "c1", "state": state})
    assert replay.status_code == 401


def test_oidc_open_redirects_are_dropped(idp: FakeOidc, make_user: Callable[..., User]) -> None:
    sso_user(make_user, "ama@bank.example", "oidc")
    browser = Client()
    state = start(browser, idp, next_path="//evil.example/x")
    response = post(browser, "/auth/oidc/complete", {"code": "c1", "state": state})
    assert response.json()["next"] == "/"


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"aud": "someone-else"}, "refused"),
        ({"iss": "https://evil.example"}, "refused"),
        ({"nonce": "not-the-nonce"}, "refused"),
        ({"exp": 1_000_000_000}, "refused"),
        ({"email": "nobody@bank.example", "sub": "unknown-sub"}, "no kpiGo account"),
    ],
)
def test_oidc_refuses_bad_tokens(
    idp: FakeOidc, make_user: Callable[..., User], claims: dict[str, Any], reason: str
) -> None:
    sso_user(make_user, "ama@bank.example", "oidc")
    idp.claims = claims
    browser = Client()
    state = start(browser, idp)
    response = post(browser, "/auth/oidc/complete", {"code": "c1", "state": state})
    assert response.status_code == 401
    assert reason in response.json()["message"]
    assert get(browser, "/auth/me").status_code == 401
    assert AppUser.objects.get(email="ama@bank.example").status == "invited"


def test_oidc_refuses_a_shared_secret_token(idp: FakeOidc, make_user: Callable[..., User]) -> None:
    sso_user(make_user, "ama@bank.example", "oidc")
    idp.alg = "HS256"
    idp.signing_key = OctKey.import_key("secret" * 6)
    browser = Client()
    state = start(browser, idp)
    assert post(browser, "/auth/oidc/complete", {"code": "c", "state": state}).status_code == 401


def test_oidc_callback_must_return_to_the_starting_browser(
    idp: FakeOidc, make_user: Callable[..., User]
) -> None:
    sso_user(make_user, "ama@bank.example", "oidc")
    state = start(Client(), idp)
    response = post(Client(), "/auth/oidc/complete", {"code": "c1", "state": state})
    assert response.status_code == 401 and "another browser" in response.json()["message"]


def test_oidc_mfa_is_required_when_configured(
    idp: FakeOidc, make_user: Callable[..., User], settings: Any
) -> None:
    sso_user(make_user, "ama@bank.example", "oidc")
    settings.KPIGO_OIDC_REQUIRE_MFA = True
    idp.claims = {"amr": ["pwd"]}
    browser = Client()
    response = post(browser, "/auth/oidc/complete", {"code": "c", "state": start(browser, idp)})
    assert response.status_code == 401 and "multi-factor" in response.json()["message"]
    idp.claims = {}
    response = post(browser, "/auth/oidc/complete", {"code": "c", "state": start(browser, idp)})
    assert response.status_code == 200


def test_oidc_will_not_hand_an_account_to_a_different_identity(
    idp: FakeOidc, make_user: Callable[..., User]
) -> None:
    sso_user(make_user, "ama@bank.example", "oidc", external_id="someone-else", status="active")
    browser = Client()
    response = post(browser, "/auth/oidc/complete", {"code": "c", "state": start(browser, idp)})
    assert response.status_code == 401


def test_disabled_users_cannot_sign_in_by_sso(
    idp: FakeOidc, make_user: Callable[..., User]
) -> None:
    sso_user(make_user, "ama@bank.example", "oidc", status="disabled")
    browser = Client()
    response = post(browser, "/auth/oidc/complete", {"code": "c", "state": start(browser, idp)})
    assert response.status_code == 401 and "disabled" in response.json()["message"]


def test_providers_lists_what_is_configured(idp: FakeOidc) -> None:
    out = get(Client(), "/auth/providers").json()
    assert out["oidc"]["enabled"] is True and out["saml"]["enabled"] is False


# ── SAML ───────────────────────────────────────────────────────────────────

IDP_ENTITY = "https://sts.bank.example/adfs"
SP_ENTITY = "https://kpigo.bank.example"
ACS = "https://kpigo.bank.example/api/v1/auth/saml/acs"


def _key_and_cert(name: str) -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    pem_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pem_key, cert.public_bytes(serialization.Encoding.PEM)


IDP_KEY, IDP_CERT = _key_and_cert("idp")
OTHER_KEY, OTHER_CERT = _key_and_cert("attacker")


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def assertion(
    request_id: str,
    *,
    name_id: str = "ama@bank.example",
    audience: str = SP_ENTITY,
    issuer: str = IDP_ENTITY,
    lifetime: timedelta = timedelta(minutes=5),
    assertion_id: str = "_a1",
) -> etree._Element:
    now = datetime.now(UTC)
    confirm = f' InResponseTo="{request_id}"' if request_id else ""
    xml = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
        ID="{assertion_id}" Version="2.0" IssueInstant="{_iso(now)}">
      <saml:Issuer>{issuer}</saml:Issuer>
      <saml:Subject>
        <saml:NameID>{name_id}</saml:NameID>
        <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
          <saml:SubjectConfirmationData Recipient="{ACS}"
             NotOnOrAfter="{_iso(now + lifetime)}"{confirm}/>
        </saml:SubjectConfirmation>
      </saml:Subject>
      <saml:Conditions NotBefore="{_iso(now - timedelta(minutes=1))}"
          NotOnOrAfter="{_iso(now + lifetime)}">
        <saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
      </saml:Conditions>
      <saml:AuthnStatement AuthnInstant="{_iso(now)}">
        <saml:AuthnContext>
          <saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef>
        </saml:AuthnContext>
      </saml:AuthnStatement>
    </saml:Assertion>"""
    return etree.fromstring(xml.encode())


def sign(element: etree._Element, key: bytes = IDP_KEY, cert: bytes = IDP_CERT) -> etree._Element:
    # Exclusive canonicalisation, as identity providers sign, so the assertion
    # verifies after it is placed inside the response.
    signer = XMLSigner(
        signature_algorithm=SignatureMethod.RSA_SHA256,
        digest_algorithm=DigestAlgorithm.SHA256,
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    )
    signed: etree._Element = signer.sign(
        element, key=key, cert=cert.decode(), reference_uri=f"#{element.get('ID')}"
    )
    return signed


def response(*assertions: etree._Element, destination: str = ACS) -> str:
    root = etree.fromstring(
        f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
            xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_r1" Version="2.0"
            IssueInstant="{_iso(datetime.now(UTC))}" Destination="{destination}">
          <saml:Issuer>{IDP_ENTITY}</saml:Issuer>
          <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
        </samlp:Response>""".encode()
    )
    for a in assertions:
        root.append(a)
    return base64.b64encode(etree.tostring(root)).decode()


@pytest.fixture
def saml_idp(settings: Any) -> None:
    settings.KPIGO_SAML_IDP_ENTITY_ID = IDP_ENTITY
    settings.KPIGO_SAML_IDP_SSO_URL = f"{IDP_ENTITY}/ls"
    settings.KPIGO_SAML_IDP_CERT = IDP_CERT.decode()
    settings.KPIGO_SAML_SP_ENTITY_ID = SP_ENTITY
    settings.KPIGO_SAML_ACS_URL = ACS


def saml_start(browser: Client) -> str:
    out = post(browser, "/auth/saml/start", {"next": "/executive"})
    assert out.status_code == 200, out.content
    query = parse_qs(urlparse(out.json()["redirect_url"]).query)
    request = zlib.decompress(base64.b64decode(query["SAMLRequest"][0]), -15)
    request_id = query["RelayState"][0]
    assert f'ID="{request_id}"'.encode() in request and ACS.encode() in request
    return request_id


def acs(browser: Client, saml_response: str) -> Any:
    return browser.post("/api/v1/auth/saml/acs", {"SAMLResponse": saml_response, "RelayState": ""})


def test_saml_sign_in_posts_back_and_redirects(
    saml_idp: None, make_user: Callable[..., User]
) -> None:
    sso_user(make_user, "ama@bank.example", "saml")
    browser = Client()
    request_id = saml_start(browser)
    done = acs(browser, response(sign(assertion(request_id))))
    assert done.status_code == 303 and done["Location"] == "/executive"
    assert get(browser, "/auth/me").json()["user"]["email"] == "ama@bank.example"
    assert AppUser.objects.get(email="ama@bank.example").external_id == "ama@bank.example"
    # Posting the same response again is a replay.
    again = acs(Client(), response(sign(assertion(request_id))))
    assert again["Location"] == "/login?error=sso_refused"


def _tampered(request_id: str) -> str:
    signed = sign(assertion(request_id))
    signed.find(".//{urn:oasis:names:tc:SAML:2.0:assertion}NameID").text = "boss@bank.example"
    return response(signed)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda rid: response(sign(assertion(rid, audience="https://other"))), id="audience"
        ),
        pytest.param(
            lambda rid: response(sign(assertion(rid, issuer="https://evil"))), id="issuer"
        ),
        pytest.param(
            lambda rid: response(sign(assertion(rid, lifetime=timedelta(minutes=-10)))),
            id="expired",
        ),
        pytest.param(lambda rid: response(sign(assertion(""))), id="unsolicited"),
        pytest.param(
            lambda rid: response(sign(assertion(rid), OTHER_KEY, OTHER_CERT)), id="foreign-key"
        ),
        pytest.param(lambda rid: response(assertion(rid)), id="unsigned"),
        pytest.param(_tampered, id="tampered"),
        pytest.param(
            lambda rid: response(sign(assertion(rid)), destination="https://other/acs"),
            id="destination",
        ),
    ],
)
def test_saml_refuses_bad_responses(
    saml_idp: None, make_user: Callable[..., User], build: Callable[[str], str]
) -> None:
    sso_user(make_user, "ama@bank.example", "saml")
    sso_user(make_user, "boss@bank.example", "saml")
    browser = Client()
    done = acs(browser, build(saml_start(browser)))
    assert done.status_code == 303 and done["Location"] == "/login?error=sso_refused"
    assert get(browser, "/auth/me").status_code == 401


def test_saml_ignores_an_unsigned_assertion_wrapped_around_a_signed_one(
    saml_idp: None, make_user: Callable[..., User]
) -> None:
    sso_user(make_user, "ama@bank.example", "saml")
    sso_user(make_user, "boss@bank.example", "saml")
    browser = Client()
    request_id = saml_start(browser)
    forged = assertion(request_id, name_id="boss@bank.example", assertion_id="_evil")
    done = acs(browser, response(forged, sign(assertion(request_id))))
    # Only the signed assertion counts: this signs in as Ama, never as the boss.
    if done["Location"] != "/login?error=sso_refused":
        assert get(browser, "/auth/me").json()["user"]["email"] == "ama@bank.example"
    assert AppUser.objects.get(email="boss@bank.example").status == "invited"


def test_saml_refuses_a_doctype(saml_idp: None, make_user: Callable[..., User]) -> None:
    sso_user(make_user, "ama@bank.example", "saml")
    evil = base64.b64encode(
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    ).decode()
    browser = Client()
    saml_start(browser)
    assert acs(browser, evil)["Location"] == "/login?error=sso_refused"


# ── LDAP ───────────────────────────────────────────────────────────────────

BASE = "dc=bank,dc=example"
SERVICE_DN = f"cn=svc,{BASE}"


def ldap_people() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            f"cn=Kwame,ou=staff,{BASE}",
            {
                "employeeID": "M1",
                "mail": "kwame@bank.example",
                "displayName": "Kwame Asante",
                "userPassword": "kwame-pass",
            },
        ),
        (
            f"cn=Ama,ou=staff,{BASE}",
            {
                "employeeID": "E1",
                "mail": "ama@bank.example",
                "displayName": "Ama Mensah",
                "manager": f"cn=Kwame,ou=staff,{BASE}",
                "userPassword": "ama-pass",
            },
        ),
        (
            f"cn=Esi,ou=staff,{BASE}",
            {
                "employeeID": "E2",
                "mail": "esi@bank.example",
                "displayName": "Esi Owusu",
                "manager": f"cn=Kwame,ou=staff,{BASE}",
                "userPassword": "esi-pass",
            },
        ),
        (
            f"cn=Yaw,ou=staff,{BASE}",
            {
                "employeeID": "E3",
                "mail": "yaw@bank.example",
                "displayName": "Yaw Darko",
                "userPassword": "yaw-pass",
            },
        ),
    ]


@pytest.fixture
def directory_server(settings: Any) -> Iterator[Server]:
    server = Server("fake_ad", get_info=OFFLINE_AD_2012_R2)
    seed = Connection(server, user=SERVICE_DN, password="svc-pass", client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(SERVICE_DN, {"objectClass": "person", "userPassword": "svc-pass"})
    for dn, attrs in ldap_people():
        seed.strategy.add_entry(dn, {"objectClass": ["top", "person", "user"], **attrs})
    settings.KPIGO_LDAP_URL = "ldap://fake_ad"
    settings.KPIGO_LDAP_BIND_DN = SERVICE_DN
    settings.KPIGO_LDAP_BIND_PASSWORD = "svc-pass"
    settings.KPIGO_LDAP_USER_BASE = BASE
    ldap_module.SERVER = server
    ldap_module.CLIENT_STRATEGY = MOCK_SYNC
    yield server
    ldap_module.SERVER = None
    ldap_module.CLIENT_STRATEGY = SYNC


def test_ldap_users_sign_in_with_their_directory_password(
    directory_server: Server, make_user: Callable[..., User]
) -> None:
    sso_user(make_user, "ama@bank.example", "ldap")
    browser = Client()
    bad = post(browser, "/auth/login", {"email": "ama@bank.example", "password": "esi-pass"})
    assert bad.status_code == 401
    assert AppUser.objects.get(email="ama@bank.example").failed_logins == 1
    ok = post(browser, "/auth/login", {"email": "ama@bank.example", "password": "ama-pass"})
    assert ok.status_code == 200, ok.content
    assert ok.json()["user"]["status"] == "active"
    assert build_context(AppUser.objects.get(email="ama@bank.example").auth_user, caller="http")
    # kpiGo never stores a directory password.
    assert not AppUser.objects.get(email="ama@bank.example").auth_user.has_usable_password()


def test_ldap_refuses_unknown_people(directory_server: Server) -> None:
    with pytest.raises(AuthenticationFailed):
        invoke(
            registry.get("auth.login"),
            {"email": "kwame@bank.example", "password": "kwame-pass"},
            anonymous_context(caller="cli"),
        )


def test_ldap_test_connection(directory_server: Server, make_user: Callable[..., User]) -> None:
    admin = make_user("admin")
    out: Any = invoke(
        registry.get("directory.test"), {"source": "ldap"}, build_context(admin, caller="cli")
    )
    assert out.ok is True


def test_providers_payload_is_json(directory_server: Server) -> None:
    assert json.loads(get(Client(), "/auth/providers").content)["password"]["enabled"] is True
