"""SAML 2.0 Web Browser SSO for the tenant's identity provider (5.22): the service provider's metadata, the
AuthnRequest in the HTTP-Redirect binding, and the validation of the Response posted to the ACS.

XML signatures are verified by signxml, which returns only what the signature covers; everything read about
the person comes from that verified copy of the assertion, never from the document as posted. The assertion
itself must be signed by the certificate the tenant configured, and must answer the request Niadra sent
(its InResponseTo is inside the signed part), so a signed assertion cannot be replayed into another login.
"""

import base64
import binascii
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from lxml import etree
from signxml.exceptions import InvalidCertificate, InvalidDigest, InvalidSignature
from signxml.exceptions import InvalidInput as SignatureInput
from signxml.verifier import SignatureConfiguration, XMLVerifier

from niadra.domain.common.errors import InvalidInput
from niadra.domain.control.sso import IdpIdentity, SsoFailure, SsoRefused
from niadra.ports.control import CertificateInfo

SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
MD = "urn:oasis:names:tc:SAML:2.0:metadata"
NS = {"samlp": SAMLP, "saml": SAML}
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
EMAIL_FORMAT = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
POST_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
CLOCK_SKEW = timedelta(seconds=120)
MAX_RESPONSE_BYTES = 1024 * 1024

# Where the common providers put the e-mail when the NameID is not one.
EMAIL_ATTRIBUTES = (
    "email",
    "mail",
    "emailaddress",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
)
NAME_ATTRIBUTES = ("name", "displayname", "http://schemas.microsoft.com/identity/claims/displayname")


class SignXmlSamlService:
    def metadata(self, *, entity_id: str, acs_url: str) -> bytes:
        root = etree.Element(f"{{{MD}}}EntityDescriptor", nsmap={"md": MD}, entityID=entity_id)
        sp = etree.SubElement(
            root,
            f"{{{MD}}}SPSSODescriptor",
            AuthnRequestsSigned="false",
            WantAssertionsSigned="true",
            protocolSupportEnumeration=SAMLP,
        )
        etree.SubElement(sp, f"{{{MD}}}NameIDFormat").text = EMAIL_FORMAT
        etree.SubElement(
            sp, f"{{{MD}}}AssertionConsumerService", Binding=POST_BINDING, Location=acs_url, index="0"
        )
        return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True))

    def login_url(
        self,
        *,
        idp_sso_url: str,
        entity_id: str,
        acs_url: str,
        request_id: str,
        relay_state: str,
        now: datetime,
    ) -> str:
        request = etree.Element(
            f"{{{SAMLP}}}AuthnRequest",
            nsmap={"samlp": SAMLP, "saml": SAML},
            ID=request_id,
            Version="2.0",
            IssueInstant=_instant(now),
            Destination=idp_sso_url,
            AssertionConsumerServiceURL=acs_url,
            ProtocolBinding=POST_BINDING,
        )
        etree.SubElement(request, f"{{{SAML}}}Issuer").text = entity_id
        etree.SubElement(request, f"{{{SAMLP}}}NameIDPolicy", Format=EMAIL_FORMAT, AllowCreate="true")
        # HTTP-Redirect binding: raw DEFLATE (no zlib header), then base64, then the query string.
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        deflated = compressor.compress(etree.tostring(request)) + compressor.flush()
        query = urlencode({"SAMLRequest": base64.b64encode(deflated).decode(), "RelayState": relay_state})
        return f"{idp_sso_url}{'&' if '?' in idp_sso_url else '?'}{query}"

    def identity(
        self,
        *,
        saml_response: str,
        idp_entity_id: str,
        idp_certificate: str,
        entity_id: str,
        acs_url: str,
        request_id: str,
        groups_attribute: str,
        now: datetime,
    ) -> IdpIdentity:
        response = _parse_response(saml_response)
        if response.tag != f"{{{SAMLP}}}Response":
            raise SsoRefused(SsoFailure.INVALID, "the answer is not a SAML response")
        destination = response.get("Destination")
        if destination is not None and destination != acs_url:
            raise SsoRefused(SsoFailure.INVALID, "the response is for another address")
        if response.get("InResponseTo") != request_id:
            raise SsoRefused(SsoFailure.INVALID, "the response answers another sign-in")
        status = response.find("samlp:Status/samlp:StatusCode", NS)
        if status is None or status.get("Value") != SUCCESS:
            raise SsoRefused(SsoFailure.DENIED, "the identity provider did not sign the person in")
        issuer = response.find("saml:Issuer", NS)
        if issuer is not None and (issuer.text or "").strip() != idp_entity_id:
            raise SsoRefused(SsoFailure.INVALID, "the response comes from another identity provider")
        if response.find("saml:EncryptedAssertion", NS) is not None:
            raise SsoRefused(
                SsoFailure.INVALID, "encrypted assertions are not supported; turn encryption off"
            )
        assertions = response.findall("saml:Assertion", NS)
        if len(assertions) != 1:
            raise SsoRefused(SsoFailure.INVALID, "the response must carry exactly one assertion")

        assertion = _verified_assertion(response, assertions[0], idp_certificate)
        _check_assertion(assertion, idp_entity_id, entity_id, acs_url, request_id, now)
        attributes = _attributes(assertion)
        name_id = assertion.find("saml:Subject/saml:NameID", NS)
        subject = (name_id.text or "").strip() if name_id is not None else ""
        email = next((attributes[a][0] for a in EMAIL_ATTRIBUTES if attributes.get(a)), None)
        if email is None and name_id is not None and "@" in subject:
            email = subject
        if not email:
            raise SsoRefused(SsoFailure.INVALID, "the assertion has no e-mail")
        name = next((attributes[a][0] for a in NAME_ATTRIBUTES if attributes.get(a)), None)
        return IdpIdentity(
            email=email,
            subject=subject or email,
            groups=tuple(attributes.get(groups_attribute.lower(), ())),
            email_verified=None,
            name=name,
        )

    def certificate(self, pem: str) -> CertificateInfo:
        cert = load_certificate(pem)
        return CertificateInfo(
            subject=cert.subject.rfc4514_string(),
            not_after=cert.not_valid_after_utc,
            sha256_fingerprint=cert.fingerprint(hashes.SHA256()).hex(":").upper(),
        )


def load_certificate(pem: str) -> x509.Certificate:
    """A PEM certificate, or only its base64 body, as identity providers show it in their metadata."""
    text = pem.strip()
    if "-----BEGIN CERTIFICATE-----" not in text:
        body = "".join(text.split())
        text = "-----BEGIN CERTIFICATE-----\n" + body + "\n-----END CERTIFICATE-----"
    try:
        return x509.load_pem_x509_certificate(text.encode())
    except ValueError:
        raise InvalidInput("the identity provider certificate is not a valid X.509 certificate") from None


def _parse_response(encoded: str) -> Any:
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError):
        raise SsoRefused(SsoFailure.INVALID, "the SAML response is not base64") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SsoRefused(SsoFailure.INVALID, "the SAML response is too large")
    # No DTDs, no entities, no network: the answer is untrusted until its signature is checked.
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False, huge_tree=False
    )
    try:
        document = etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        raise SsoRefused(SsoFailure.INVALID, "the SAML response is not XML") from None
    if document.getroottree().docinfo.doctype:
        raise SsoRefused(SsoFailure.INVALID, "the SAML response carries a DTD")
    return document


def _verified_assertion(response: Any, posted: Any, certificate: str) -> Any:
    config = SignatureConfiguration(location=f"./{{{SAML}}}Assertion/", expect_references=1)
    try:
        result = XMLVerifier().verify(response, x509_cert=load_certificate(certificate), expect_config=config)
    except InvalidCertificate:
        raise SsoRefused(
            SsoFailure.INVALID, "the provider's certificate expired or is not valid yet"
        ) from None
    except (InvalidSignature, InvalidDigest, SignatureInput):
        raise SsoRefused(
            SsoFailure.INVALID, "the assertion is not signed by the configured certificate"
        ) from None
    signed = result.signed_xml  # type: ignore[union-attr]
    if signed is None or signed.tag != f"{{{SAML}}}Assertion" or signed.get("ID") != posted.get("ID"):
        raise SsoRefused(SsoFailure.INVALID, "the signature does not cover the assertion")
    return signed


def _check_assertion(
    assertion: Any, idp_entity_id: str, entity_id: str, acs_url: str, request_id: str, now: datetime
) -> None:
    issuer = assertion.find("saml:Issuer", NS)
    if issuer is None or (issuer.text or "").strip() != idp_entity_id:
        raise SsoRefused(SsoFailure.INVALID, "the assertion comes from another identity provider")
    conditions = assertion.find("saml:Conditions", NS)
    if conditions is None:
        raise SsoRefused(SsoFailure.INVALID, "the assertion has no conditions")
    not_before, not_after = conditions.get("NotBefore"), conditions.get("NotOnOrAfter")
    if not_before is not None and _time(not_before) - CLOCK_SKEW > now:
        raise SsoRefused(SsoFailure.INVALID, "the assertion is not valid yet")
    if not_after is not None and _time(not_after) + CLOCK_SKEW <= now:
        raise SsoRefused(SsoFailure.INVALID, "the assertion expired")
    audiences = {
        (a.text or "").strip() for a in conditions.findall("saml:AudienceRestriction/saml:Audience", NS)
    }
    if entity_id not in audiences:
        raise SsoRefused(SsoFailure.INVALID, "the assertion is for another service provider")
    for confirmation in assertion.findall("saml:Subject/saml:SubjectConfirmation", NS):
        data = confirmation.find("saml:SubjectConfirmationData", NS)
        if confirmation.get("Method") != BEARER or data is None:
            continue
        expires = data.get("NotOnOrAfter")
        if (
            data.get("Recipient") == acs_url
            and data.get("InResponseTo") == request_id
            and expires is not None
            and _time(expires) + CLOCK_SKEW > now
        ):
            return
    raise SsoRefused(SsoFailure.INVALID, "the assertion does not confirm this sign-in")


def _attributes(assertion: Any) -> dict[str, list[str]]:
    """Attribute values by name, lowercased, from the signed assertion only."""
    found: dict[str, list[str]] = {}
    for attribute in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
        name = (attribute.get("Name") or "").strip().lower()
        values = [(v.text or "").strip() for v in attribute.findall("saml:AttributeValue", NS)]
        found.setdefault(name, []).extend(v for v in values if v)
    return found


def _time(value: str) -> datetime:
    try:
        at = datetime.fromisoformat(value.strip())
    except ValueError:
        raise SsoRefused(SsoFailure.INVALID, "the assertion has a malformed time") from None
    # SAML times are UTC; a value without the zone is read as UTC rather than refused.
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _instant(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
