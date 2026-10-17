"""OpenID Connect against the tenant's identity provider (5.22): discovery, the authorization code grant
with PKCE and the validation of the ID token.

Every call goes through the egress client, so the provider must be a public https address and nothing
follows a redirect. The provider's keys are cached for five minutes and read again once when a token names
a key the cache does not have, which is how a provider's key rotation reaches us.
"""

import base64
import json
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import jwt

from niadra.domain.common.errors import Forbidden, InvalidInput, Unavailable
from niadra.domain.control.sso import IdpIdentity, SsoFailure, SsoRefused
from niadra.ports.control import IdpHttp, OidcProvider

# Asymmetric only: an HS256 token would be signed with the client secret, which the provider shares with us.
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA")
CLOCK_SKEW_S = 60
KEYS_TTL_S = 300.0


class HttpOidcClient:
    def __init__(self, http: IdpHttp, timeout_s: float = 5.0) -> None:
        self._http = http
        self._timeout = timeout_s
        self._keys: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def discover(self, issuer: str) -> OidcProvider:
        url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        document = await self._json(url, InvalidInput("the issuer has no OpenID discovery document"))
        found = document.get("issuer")
        # The ID token's `iss` must equal the document's issuer (OIDC Discovery 4.3); a trailing slash typed
        # in the Console is the only difference forgiven.
        if not isinstance(found, str) or found.rstrip("/") != issuer.rstrip("/"):
            raise InvalidInput("the discovery document names another issuer")
        endpoints = {}
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = document.get(name)
            if not isinstance(value, str) or not value.startswith("https://"):
                raise InvalidInput(f"the discovery document has no https {name}")
            endpoints[name] = value
        methods = document.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        return OidcProvider(
            issuer=found,
            authorization_endpoint=endpoints["authorization_endpoint"],
            token_endpoint=endpoints["token_endpoint"],
            jwks_uri=endpoints["jwks_uri"],
            token_auth_methods=tuple(m for m in methods if isinstance(m, str)),
        )

    async def redeem(
        self,
        provider: OidcProvider,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
        groups_claim: str,
        now: datetime,
    ) -> IdpIdentity:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        headers = {"content-type": "application/x-www-form-urlencoded", "accept": "application/json"}
        if "client_secret_basic" in provider.token_auth_methods or not provider.token_auth_methods:
            pair = f"{_form_quote(client_id)}:{_form_quote(client_secret)}"
            headers["authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        else:
            form |= {"client_id": client_id, "client_secret": client_secret}
        try:
            response = await self._http.post(
                provider.token_endpoint, urlencode(form).encode(), headers, self._timeout
            )
        except (Unavailable, Forbidden):
            raise SsoRefused(SsoFailure.UNAVAILABLE) from None
        if response.status != 200:
            raise SsoRefused(SsoFailure.INVALID, f"the token endpoint answered {response.status}")
        id_token = _parse(response.body).get("id_token")
        if not isinstance(id_token, str):
            raise SsoRefused(SsoFailure.INVALID, "the token endpoint sent no ID token")
        claims = await self._verify(provider, id_token, client_id, now)
        if claims.get("nonce") != nonce:
            raise SsoRefused(SsoFailure.INVALID, "the ID token answers another sign-in")
        return _identity(claims, groups_claim)

    async def _verify(
        self, provider: OidcProvider, token: str, client_id: str, now: datetime
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise SsoRefused(SsoFailure.INVALID, "the ID token is malformed") from None
        algorithm = header.get("alg")
        if algorithm not in ALGORITHMS:
            raise SsoRefused(SsoFailure.INVALID, "the ID token uses an algorithm Niadra does not accept")
        failure = "the ID token names a key the provider does not publish"
        for refresh in (False, True):
            for key in await self._keys_for(provider.jwks_uri, header.get("kid"), algorithm, refresh):
                try:
                    claims: dict[str, Any] = jwt.decode(
                        token,
                        key,
                        algorithms=[algorithm],
                        audience=client_id,
                        issuer=provider.issuer,
                        # Time is checked below against the service's clock, not the machine's.
                        options={
                            "require": ["iss", "aud", "exp", "iat", "sub"],
                            "verify_exp": False,
                            "verify_iat": False,
                            "verify_nbf": False,
                        },
                    )
                except jwt.InvalidSignatureError:
                    failure = "the ID token signature does not match the provider's keys"
                    continue
                except jwt.PyJWTError as exc:
                    raise SsoRefused(
                        SsoFailure.INVALID, f"the ID token failed: {type(exc).__name__}"
                    ) from None
                _check_times(claims, now)
                audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
                if len(audiences) > 1 and claims.get("azp") != client_id:
                    raise SsoRefused(SsoFailure.INVALID, "the ID token was issued to another client")
                return claims
        raise SsoRefused(SsoFailure.INVALID, failure)

    async def _keys_for(self, jwks_uri: str, kid: Any, algorithm: str, refresh: bool) -> list[Any]:
        keys = [
            k
            for k in await self._jwks(jwks_uri, refresh)
            if (kid is None or k.get("kid") == kid) and k.get("use") in (None, "sig")
        ]
        usable = []
        for k in keys:
            try:
                usable.append(jwt.PyJWK(k, algorithm).key)
            except jwt.PyJWTError:
                continue
        return usable

    async def _jwks(self, uri: str, refresh: bool) -> list[dict[str, Any]]:
        cached = self._keys.get(uri)
        if cached and not refresh and time.monotonic() - cached[0] < KEYS_TTL_S:
            return cached[1]
        document = await self._json(uri, SsoRefused(SsoFailure.UNAVAILABLE))
        keys = document.get("keys")
        if not isinstance(keys, list):
            raise SsoRefused(SsoFailure.INVALID, "the provider's key set is malformed")
        found = [k for k in keys if isinstance(k, dict)]
        self._keys[uri] = (time.monotonic(), found)
        return found

    async def _json(self, url: str, error: Exception) -> dict[str, Any]:
        try:
            response = await self._http.get(url, {"accept": "application/json"}, self._timeout)
        except Forbidden:
            raise InvalidInput("the address is not a public https address") from None
        except Unavailable:
            raise error from None
        if response.status != 200:
            raise error
        return _parse(response.body)


def _parse(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _form_quote(value: str) -> str:
    # RFC 6749 2.3.1: the client id and secret are form-encoded before they go into Basic.
    return urlencode({"": value})[1:]


def _check_times(claims: Mapping[str, Any], now: datetime) -> None:
    at = now.timestamp()
    try:
        expires, issued = float(claims["exp"]), float(claims["iat"])
        not_before = float(claims.get("nbf", issued))
    except (TypeError, ValueError):
        raise SsoRefused(SsoFailure.INVALID, "the ID token has malformed times") from None
    if expires + CLOCK_SKEW_S < at:
        raise SsoRefused(SsoFailure.INVALID, "the ID token expired")
    if min(issued, not_before) - CLOCK_SKEW_S > at:
        raise SsoRefused(SsoFailure.INVALID, "the ID token is not valid yet")


def _identity(claims: Mapping[str, Any], groups_claim: str) -> IdpIdentity:
    email = claims.get("email")
    if not isinstance(email, str):
        raise SsoRefused(SsoFailure.INVALID, "the ID token has no email claim")
    verified = claims.get("email_verified")
    if isinstance(verified, str):
        verified = verified.lower() == "true"
    raw = claims.get(groups_claim)
    if isinstance(raw, str):
        groups: tuple[str, ...] = (raw,)
    elif isinstance(raw, list):
        groups = tuple(g for g in raw if isinstance(g, str))
    else:
        groups = ()
    name = claims.get("name")
    return IdpIdentity(
        email=email,
        subject=str(claims["sub"]),
        groups=groups,
        email_verified=verified if isinstance(verified, bool) else None,
        name=name if isinstance(name, str) else None,
    )
