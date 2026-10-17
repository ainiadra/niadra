"""Identity providers in memory for the SSO tests: an OIDC provider behind the egress port, and a SAML
provider that signs assertions with its own certificate. Both play the browser's part too."""

import base64
import hashlib
import json
import secrets
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml.algorithms import CanonicalizationMethod
from signxml.signer import XMLSigner

from niadra.domain.common.errors import Unavailable
from niadra.ports.infra import HttpResponse

ISSUER = "https://login.acme-idp.test/tenant"
CLIENT_ID = "niadra-console"
CLIENT_SECRET = "a-client-secret-only-the-provider-knows"


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class FakeOidcProvider:
    """Discovery at `<issuer>/.well-known/openid-configuration`, keys at `/keys`, tokens at `/token`. The
    token endpoint checks the client's secret (Basic), the redirect URI and the PKCE verifier, like a real
    one."""

    now: Any
    """A callable returning the current time: the tests' clock."""
    issuer: str = ISSUER
    key: rsa.RSAPrivateKey = field(default_factory=new_key)
    kid: str = "key-1"
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    discovery_issuer: str | None = None
    down: bool = False

    async def get(self, url: str, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
        self.calls.append(f"GET {url}")
        if self.down:
            raise Unavailable("the destination timed out")
        if url == f"{self.issuer}/.well-known/openid-configuration":
            return _json(
                {
                    "issuer": self.discovery_issuer or self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/keys",
                    "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
                }
            )
        if url == f"{self.issuer}/keys":
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            return _json({"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]})
        return HttpResponse(404)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
        self.calls.append(f"POST {url}")
        if url != f"{self.issuer}/token":
            return HttpResponse(404)
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if headers.get("authorization") != f"Basic {basic}":
            return _json({"error": "invalid_client"}, 401)
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None or form.get("redirect_uri") != grant["redirect_uri"]:
            return _json({"error": "invalid_grant"}, 400)
        verifier = form.get("code_verifier", "")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        if challenge != grant["challenge"]:
            return _json({"error": "invalid_grant"}, 400)
        return _json({"access_token": "opaque", "token_type": "Bearer", "id_token": grant["id_token"]})

    def authorize(
        self,
        authorize_url: str,
        *,
        email: str,
        groups: tuple[str, ...] = (),
        claims: Mapping[str, Any] | None = None,
        key: rsa.RSAPrivateKey | None = None,
        kid: str | None = None,
    ) -> tuple[str, str]:
        """The person signs in at the provider: returns the (state, code) the provider sends back."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        assert authorize_url.startswith(f"{self.issuer}/authorize?")
        assert query["response_type"] == "code"
        assert query["client_id"] == CLIENT_ID
        assert query["code_challenge_method"] == "S256"
        assert "openid" in query["scope"].split()
        at = int(self.now().timestamp())
        body = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "sub": f"idp-{email}",
            "iat": at,
            "exp": at + 300,
            "nonce": query["nonce"],
            "email": email,
            "groups": list(groups),
            "name": "Pessoa de Teste",
        } | dict(claims or {})
        token = jwt.encode(body, key or self.key, algorithm="RS256", headers={"kid": kid or self.kid})
        code = secrets.token_urlsafe(24)
        self.codes[code] = {
            "redirect_uri": query["redirect_uri"],
            "challenge": query["code_challenge"],
            "id_token": token,
        }
        return query["state"], code


def _json(body: Any, status: int = 200) -> HttpResponse:
    return HttpResponse(status, json.dumps(body).encode(), {"content-type": "application/json"})


SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
IDP_ENTITY = "https://idp.acme-saml.test/metadata"
IDP_SSO_URL = "https://idp.acme-saml.test/sso"


def self_signed(common_name: str, not_after: datetime | None = None) -> tuple[rsa.RSAPrivateKey, str]:
    key = new_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
        .not_valid_after(not_after or datetime(2040, 1, 1, tzinfo=UTC))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


@dataclass
class SamlRequest:
    request_id: str
    acs_url: str
    relay_state: str
    issuer: str


@dataclass
class FakeSamlProvider:
    key: rsa.RSAPrivateKey
    certificate: str

    @classmethod
    def create(cls) -> "FakeSamlProvider":
        return cls(*self_signed("idp.acme-saml.test"))

    @staticmethod
    def read_request(login_url: str) -> SamlRequest:
        assert login_url.startswith(IDP_SSO_URL + "?")
        query = {k: v[0] for k, v in parse_qs(urlsplit(login_url).query).items()}
        xml = zlib.decompress(base64.b64decode(query["SAMLRequest"]), -15)
        request = etree.fromstring(xml)
        assert request.tag == f"{{{SAMLP}}}AuthnRequest"
        return SamlRequest(
            request.get("ID"),
            request.get("AssertionConsumerServiceURL"),
            query["RelayState"],
            request.find(f"{{{SAML}}}Issuer").text,
        )

    def response(
        self,
        request: SamlRequest,
        now: datetime,
        *,
        email: str,
        groups: tuple[str, ...] = (),
        sign: bool = True,
        signer: tuple[rsa.RSAPrivateKey, str] | None = None,
        audience: str | None = None,
        recipient: str | None = None,
        in_response_to: str | None = None,
        not_on_or_after: datetime | None = None,
        issuer: str = IDP_ENTITY,
        status: str = "urn:oasis:names:tc:SAML:2.0:status:Success",
        extra_assertion: bool = False,
        encrypted: bool = False,
    ) -> str:
        """A Response with one assertion signed by the provider (or by `signer`), base64 as posted."""
        until = not_on_or_after or now + timedelta(minutes=5)
        answer_to = in_response_to or request.request_id
        response = etree.Element(
            f"{{{SAMLP}}}Response",
            nsmap={"samlp": SAMLP, "saml": SAML},
            ID=f"_{uuid4().hex}",
            Version="2.0",
            IssueInstant=_instant(now),
            Destination=request.acs_url,
            InResponseTo=request.request_id,
        )
        etree.SubElement(response, f"{{{SAML}}}Issuer").text = issuer
        status_el = etree.SubElement(response, f"{{{SAMLP}}}Status")
        etree.SubElement(status_el, f"{{{SAMLP}}}StatusCode", Value=status)
        if encrypted:
            etree.SubElement(response, f"{{{SAML}}}EncryptedAssertion")
        assertion = self._assertion(
            now,
            until,
            email,
            groups,
            issuer,
            audience or request.issuer,
            recipient or request.acs_url,
            answer_to,
        )
        if sign:
            key, cert = signer or (self.key, self.certificate)
            assertion = XMLSigner(
                c14n_algorithm=CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0
            ).sign(assertion, key=key, cert=cert, reference_uri=assertion.get("ID"))
        if extra_assertion:
            # A wrapping attempt: an unsigned assertion for someone else, placed before the signed one.
            forged = self._assertion(
                now,
                until,
                "intruder@acme.com",
                ("admins",),
                issuer,
                request.issuer,
                request.acs_url,
                answer_to,
            )
            response.append(forged)
        response.append(assertion)
        return base64.b64encode(etree.tostring(response)).decode()

    @staticmethod
    def _assertion(
        now: datetime,
        until: datetime,
        email: str,
        groups: tuple[str, ...],
        issuer: str,
        audience: str,
        recipient: str,
        in_response_to: str,
    ) -> Any:
        assertion = etree.Element(
            f"{{{SAML}}}Assertion",
            nsmap={"saml": SAML},
            ID=f"_{uuid4().hex}",
            Version="2.0",
            IssueInstant=_instant(now),
        )
        etree.SubElement(assertion, f"{{{SAML}}}Issuer").text = issuer
        subject = etree.SubElement(assertion, f"{{{SAML}}}Subject")
        name_id = etree.SubElement(
            subject, f"{{{SAML}}}NameID", Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
        )
        name_id.text = email
        confirmation = etree.SubElement(
            subject, f"{{{SAML}}}SubjectConfirmation", Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"
        )
        etree.SubElement(
            confirmation,
            f"{{{SAML}}}SubjectConfirmationData",
            NotOnOrAfter=_instant(until),
            Recipient=recipient,
            InResponseTo=in_response_to,
        )
        conditions = etree.SubElement(
            assertion,
            f"{{{SAML}}}Conditions",
            NotBefore=_instant(now - timedelta(seconds=30)),
            NotOnOrAfter=_instant(until),
        )
        restriction = etree.SubElement(conditions, f"{{{SAML}}}AudienceRestriction")
        etree.SubElement(restriction, f"{{{SAML}}}Audience").text = audience
        statement = etree.SubElement(assertion, f"{{{SAML}}}AttributeStatement")
        mail = etree.SubElement(statement, f"{{{SAML}}}Attribute", Name="email")
        etree.SubElement(mail, f"{{{SAML}}}AttributeValue").text = email
        if groups:
            attribute = etree.SubElement(statement, f"{{{SAML}}}Attribute", Name="groups")
            for g in groups:
                etree.SubElement(attribute, f"{{{SAML}}}AttributeValue").text = g
        return assertion


def _instant(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
