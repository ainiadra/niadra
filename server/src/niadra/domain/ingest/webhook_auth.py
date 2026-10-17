"""Authentication of the generic webhook (5.2): bearer, HMAC, URL token or a client certificate.

Without it, anyone who learned the URL could inject history, an action that closes a promise
or a paid invoice. Every comparison is constant-time.

A client certificate (`mtls`) is checked at the edge's mTLS host: its TLS handshake proves the client
holds the certificate's key, and only that route forwards the certificate, in `CLIENT_CERT_HEADER`; every
other route of the edge removes the header, so the cell compares it with the pinned fingerprint.
"""

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import unquote

from niadra.domain.common.errors import Unauthenticated

QUERY_CREDENTIAL = "token"
CLIENT_CERT_HEADER = "x-forwarded-tls-client-cert"
"""What Traefik's passTLSClientCert middleware sets: each certificate as base64 DER, URL-escaped, comma
separated, the client's own first."""


def check_mtls(headers: Mapping[str, str], fingerprint: str) -> None:
    """The SHA-256 of the client certificate's DER against the fingerprint the source registered (hex,
    with or without colons)."""
    forwarded = headers.get(CLIENT_CERT_HEADER, "")
    der = _certificate_der(forwarded.split(",", 1)[0]) if forwarded else None
    if der is None:
        raise Unauthenticated("no client certificate reached the webhook; use the mTLS address")
    expected = fingerprint.replace(":", "").strip().lower()
    if not hmac.compare_digest(hashlib.sha256(der).hexdigest(), expected):
        raise Unauthenticated("the client certificate is not the one registered")


def _certificate_der(value: str) -> bytes | None:
    text = unquote(value).strip()
    for armor in ("-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"):
        text = text.replace(armor, "")
    try:
        return base64.b64decode("".join(text.split()), validate=True) or None
    except (binascii.Error, ValueError):
        return None


def check_bearer(headers: Mapping[str, str], header: str, secret: str) -> None:
    value = headers.get(header.lower(), "")
    scheme, _, token = value.partition(" ")
    presented = token if scheme.lower() == "bearer" else value
    _require(presented, secret)


def check_url_token(query: Mapping[str, str], secret: str) -> None:
    _require(query.get(QUERY_CREDENTIAL, ""), secret)


def check_hmac(
    *,
    headers: Mapping[str, str],
    body: bytes,
    secret: str,
    header: str,
    algorithm: str,
    signed_payload: str,
    timestamp_header: str | None,
    tolerance_seconds: int,
    now: datetime,
) -> None:
    signed = body
    if signed_payload == "timestamp.body":
        stamp = headers.get((timestamp_header or "").lower(), "")
        if not stamp.isdigit() or abs(now.timestamp() - int(stamp)) > tolerance_seconds:
            raise Unauthenticated("webhook timestamp outside the tolerance")
        signed = stamp.encode() + b"." + body
    digest = hmac.new(secret.encode(), signed, getattr(hashlib, algorithm)).digest()
    presented = _decode_signature(headers.get(header.lower(), ""), algorithm)
    if presented is None or not hmac.compare_digest(presented, digest):
        raise Unauthenticated("webhook signature does not match")


def _decode_signature(value: str, algorithm: str) -> bytes | None:
    # Providers send `sha256=<hex>`, bare hex or base64; accept the three.
    value = value.strip()
    if value.lower().startswith(f"{algorithm}="):
        value = value[len(algorithm) + 1 :]
    try:
        return bytes.fromhex(value)
    except ValueError:
        pass
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _require(presented: str, secret: str) -> None:
    if not presented or not hmac.compare_digest(presented.encode(), secret.encode()):
        raise Unauthenticated("webhook credential does not match")
