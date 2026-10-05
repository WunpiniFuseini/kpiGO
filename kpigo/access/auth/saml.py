"""SAML 2.0 sign-in: SP-initiated, HTTP-Redirect out, HTTP-POST back.

The identity provider must sign the response or the assertion with the
certificate configured here. Every decision is taken from the signed element
only (so a wrapped, unsigned copy is ignored), and the assertion is checked
for issuer, audience, recipient, time window and the request it answers.
Encrypted assertions are not supported: TLS protects the post.
"""

from __future__ import annotations

import base64
import binascii
import secrets
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

from django.conf import settings
from lxml import etree
from signxml.exceptions import InvalidInput as SignxmlInvalidInput
from signxml.exceptions import InvalidSignature
from signxml.verifier import XMLVerifier

from kpigo.action.errors import AuthenticationFailed

SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
NS = {"samlp": SAMLP, "saml": SAML}
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
SKEW = timedelta(minutes=2)
MAX_RESPONSE_BYTES = 512 * 1024


@dataclass(frozen=True)
class SamlConfig:
    idp_entity_id: str
    idp_sso_url: str
    idp_cert: str
    sp_entity_id: str
    acs_url: str
    email_attribute: str
    required_authn_context: str


@dataclass(frozen=True)
class SamlIdentity:
    name_id: str
    email: str
    in_response_to: str


def config() -> SamlConfig | None:
    sso = getattr(settings, "KPIGO_SAML_IDP_SSO_URL", None)
    cert = getattr(settings, "KPIGO_SAML_IDP_CERT", None)
    cert_file = getattr(settings, "KPIGO_SAML_IDP_CERT_FILE", None)
    if not cert and cert_file:
        try:
            cert = Path(cert_file).read_text(encoding="utf-8")
        except OSError:
            cert = None
    if not sso or not cert or not settings.KPIGO_SAML_IDP_ENTITY_ID:
        return None
    return SamlConfig(
        idp_entity_id=settings.KPIGO_SAML_IDP_ENTITY_ID,
        idp_sso_url=sso,
        idp_cert=cert,
        sp_entity_id=settings.KPIGO_SAML_SP_ENTITY_ID or "",
        acs_url=settings.KPIGO_SAML_ACS_URL or "",
        email_attribute=settings.KPIGO_SAML_EMAIL_ATTRIBUTE or "",
        required_authn_context=settings.KPIGO_SAML_REQUIRED_AUTHN_CONTEXT or "",
    )


def new_request_id() -> str:
    return "_" + secrets.token_hex(20)  # an xs:ID must not start with a digit


def _instant(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def redirect_url(cfg: SamlConfig, request_id: str) -> str:
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="{SAMLP}" xmlns:saml="{SAML}" '
        f'ID={quoteattr(request_id)} Version="2.0" '
        f"IssueInstant={quoteattr(_instant(datetime.now(UTC)))} "
        f"Destination={quoteattr(cfg.idp_sso_url)} "
        f"AssertionConsumerServiceURL={quoteattr(cfg.acs_url)} "
        'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
        f"<saml:Issuer>{escape(cfg.sp_entity_id)}</saml:Issuer>"
        '<samlp:NameIDPolicy AllowCreate="false"/>'
        "</samlp:AuthnRequest>"
    )
    deflated = zlib.compress(xml.encode())[2:-4]  # raw DEFLATE, as the binding requires
    query = urlencode(
        {"SAMLRequest": base64.b64encode(deflated).decode(), "RelayState": request_id}
    )
    joiner = "&" if "?" in cfg.idp_sso_url else "?"
    return f"{cfg.idp_sso_url}{joiner}{query}"


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise AuthenticationFailed("The SAML assertion carries an unreadable time.") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _refuse(reason: str) -> AuthenticationFailed:
    return AuthenticationFailed(f"The SAML response was refused: {reason}")


def parse_response(
    cfg: SamlConfig, saml_response: str, now: datetime | None = None
) -> SamlIdentity:
    now = now or datetime.now(UTC)
    try:
        raw = base64.b64decode("".join(saml_response.split()), validate=True)
    except (binascii.Error, ValueError):
        raise _refuse("it is not base64") from None
    if len(raw) > MAX_RESPONSE_BYTES or b"<!DOCTYPE" in raw.upper():
        raise _refuse("it is not an acceptable XML document")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        outer = etree.fromstring(raw, parser=parser)
    except etree.XMLSyntaxError:
        raise _refuse("it is not well-formed XML") from None
    if outer.tag != f"{{{SAMLP}}}Response":
        raise _refuse("it is not a SAML Response")
    status = outer.find("samlp:Status/samlp:StatusCode", NS)
    if status is None or status.get("Value") != SUCCESS:
        raise _refuse("the identity provider reported a failed sign-in")
    destination = outer.get("Destination")
    if destination is not None and destination != cfg.acs_url:
        raise _refuse("it was sent to a different service")
    if outer.find("saml:EncryptedAssertion", NS) is not None:
        raise _refuse("encrypted assertions are not supported; send it unencrypted over TLS")

    try:
        result = XMLVerifier().verify(raw, x509_cert=cfg.idp_cert)
    except (InvalidSignature, SignxmlInvalidInput) as exc:
        raise _refuse(f"the signature does not verify ({exc})") from None
    if isinstance(result, list):
        raise _refuse("it carries more than one signature reference")
    signed = result.signed_xml
    if signed is None:
        raise _refuse("nothing in it is signed")
    if signed.tag == f"{{{SAML}}}Assertion":
        assertions = [signed]
    elif signed.tag == f"{{{SAMLP}}}Response":
        assertions = signed.findall("saml:Assertion", NS)
    else:
        raise _refuse("the signature covers neither the response nor the assertion")
    if len(assertions) != 1:
        raise _refuse("it must carry exactly one assertion")
    return _assertion(cfg, assertions[0], now)


def _assertion(cfg: SamlConfig, assertion: etree._Element, now: datetime) -> SamlIdentity:
    issuer = assertion.findtext("saml:Issuer", namespaces=NS)
    if (issuer or "").strip() != cfg.idp_entity_id:
        raise _refuse("it was issued by an unexpected identity provider")

    conditions = assertion.find("saml:Conditions", NS)
    if conditions is None:
        raise _refuse("it has no conditions")
    not_before = _time(conditions.get("NotBefore"))
    not_after = _time(conditions.get("NotOnOrAfter"))
    if not_before is not None and now + SKEW < not_before:
        raise _refuse("it is not valid yet")
    if not_after is None or now - SKEW >= not_after:
        raise _refuse("it has expired")
    audiences = [
        (a.text or "").strip()
        for a in conditions.findall("saml:AudienceRestriction/saml:Audience", NS)
    ]
    if cfg.sp_entity_id not in audiences:
        raise _refuse("it is addressed to a different service")

    subject = assertion.find("saml:Subject", NS)
    if subject is None:
        raise _refuse("it names no subject")
    confirmation = None
    for candidate in subject.findall("saml:SubjectConfirmation", NS):
        if candidate.get("Method") == BEARER:
            confirmation = candidate.find("saml:SubjectConfirmationData", NS)
    if confirmation is None:
        raise _refuse("it has no bearer confirmation")
    if confirmation.get("Recipient") != cfg.acs_url:
        raise _refuse("it is for a different recipient")
    confirm_after = _time(confirmation.get("NotOnOrAfter"))
    if confirm_after is None or now - SKEW >= confirm_after:
        raise _refuse("its confirmation has expired")
    in_response_to = confirmation.get("InResponseTo") or ""
    if not in_response_to:
        raise _refuse("it does not answer a kpiGo sign-in (unsolicited responses are refused)")

    if cfg.required_authn_context:
        context = assertion.findtext(
            "saml:AuthnStatement/saml:AuthnContext/saml:AuthnContextClassRef", namespaces=NS
        )
        if (context or "").strip() != cfg.required_authn_context:
            raise AuthenticationFailed(
                "Your identity provider did not confirm the sign-in strength kpiGo requires "
                "(for example MFA). Sign in again, or ask your IT team."
            )

    name_id = (subject.findtext("saml:NameID", namespaces=NS) or "").strip()
    if not name_id:
        raise _refuse("it carries no NameID")
    email = name_id
    if cfg.email_attribute:
        email = ""
        for attribute in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
            if attribute.get("Name") == cfg.email_attribute:
                email = (attribute.findtext("saml:AttributeValue", namespaces=NS) or "").strip()
                break
    return SamlIdentity(name_id=name_id, email=email, in_response_to=in_response_to)
