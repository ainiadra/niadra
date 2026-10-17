"""Single sign-on for the Console (5.22): the tenant's OIDC or SAML connection, the trip to the identity
provider and back, the person created just in time with roles from the provider's groups, and the exchange of
the one-time sign-in code for a session.

The browser never carries a session token in a URL. The provider answers the control plane; the control plane
sends the browser back to the Console with a one-time sign-in code in the URL fragment (never sent to any
server), and the Console exchanges it for a token together with a value it kept for this tab, whose SHA-256 it
gave at the start. A code without that value is useless, and each one works once, for ten minutes.
"""

import base64
import binascii
import hmac
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from urllib.parse import urlencode
from uuid import UUID

from niadra.domain.common.errors import (
    Conflict,
    Forbidden,
    InvalidInput,
    NotFound,
    Unauthenticated,
    Unavailable,
)
from niadra.domain.common.ids import uuid7
from niadra.domain.control.credentials import NO_PASSWORD, new_url_token, pkce_challenge, sha256
from niadra.domain.control.records import (
    AccessChange,
    AccessChangeKind,
    Caller,
    OidcSettings,
    Role,
    RoleBinding,
    RoleMapping,
    SamlSettings,
    SsoAttempt,
    SsoAttemptStatus,
    SsoConnection,
    SsoProtocol,
    SsoPurpose,
    User,
)
from niadra.domain.control.sso import (
    IdpIdentity,
    SsoFailure,
    SsoRefused,
    admitted_email,
    check_attribute_name,
    check_https_url,
    check_mappings,
    email_domain,
    normalize_domains,
    roles_from_groups,
)
from niadra.ports.control import (
    CertificateInfo,
    ControlUnitOfWork,
    ControlUnitOfWorkFactory,
    OidcClient,
    SamlService,
    SecretSealer,
)
from niadra.ports.infra import Clock

DEFAULT_SCOPES = ("openid", "email", "profile")


class SessionIssuer(Protocol):
    """What the SSO exchange borrows from the password login (`ControlService`): the second factor and the
    token, and the authenticator enrollment."""

    async def issue_session(
        self,
        uow: ControlUnitOfWork,
        user: User,
        space_id: UUID | None,
        second_factor_required: bool,
        totp: str | None,
        recovery_code: str | None,
    ) -> Any: ...

    async def enrollment_secret(self, uow: ControlUnitOfWork, user: User) -> str: ...


@dataclass(frozen=True, slots=True)
class SsoSettings:
    console_origins: tuple[str, ...] = ()
    """Where the browser may be sent back: the Console's origins, the same list CORS allows."""
    console_path: str = "/console/sso"
    attempt_ttl_s: int = 600
    code_ttl_s: int = 600
    max_code_failures: int = 5
    max_pending_attempts: int = 500


@dataclass(frozen=True, slots=True)
class ConnectionSpec:
    protocol: SsoProtocol
    domains: Sequence[str]
    enabled: bool = True
    enforced: bool = False
    default_role: Role | None = None
    role_mappings: Sequence[RoleMapping] = ()
    groups_attribute: str = "groups"
    require_second_factor: bool = False
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    """None keeps the secret already stored."""
    oidc_scopes: Sequence[str] = ()
    saml_idp_entity_id: str | None = None
    saml_idp_sso_url: str | None = None
    saml_idp_certificate: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceProvider:
    """What the tenant pastes into its identity provider."""

    oidc_redirect_uri: str
    saml_entity_id: str
    saml_acs_url: str
    saml_metadata_url: str


@dataclass(frozen=True, slots=True)
class ConnectionView:
    connection: SsoConnection | None
    service_provider: ServiceProvider
    certificate: CertificateInfo | None


@dataclass(frozen=True, slots=True)
class Discovery:
    sso: bool
    protocol: SsoProtocol | None = None
    enforced: bool = False


@dataclass(frozen=True, slots=True)
class Started:
    attempt_id: UUID
    authorize_url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Landing:
    """Where the browser goes when the identity provider answers: back to the Console, or, for a test run
    from the Console in another tab, a short page that says it is done."""

    redirect: str | None = None
    page: Literal["test_done", "expired"] | None = None


@dataclass(frozen=True, slots=True)
class NewSsoAuthenticator:
    secret: str
    account: str


def oidc_redirect_uri(base: str) -> str:
    return f"{base}/v1/auth/sso/oidc/callback"


def saml_entity_id(tenant_id: UUID) -> str:
    # Independent of the host, so a new control address does not change what the provider trusts.
    return f"urn:niadra:sso:{tenant_id}"


def saml_acs_url(base: str, tenant_id: UUID) -> str:
    return f"{base}/v1/auth/sso/saml/{tenant_id}/acs"


def saml_metadata_url(base: str, tenant_id: UUID) -> str:
    return f"{base}/v1/auth/sso/saml/{tenant_id}/metadata"


class SsoService:
    def __init__(
        self,
        uow: ControlUnitOfWorkFactory,
        clock: Clock,
        sealer: SecretSealer,
        oidc: OidcClient,
        saml: SamlService,
        sessions: SessionIssuer,
        settings: SsoSettings | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._sealer = sealer
        self._oidc = oidc
        self._saml = saml
        self._sessions = sessions
        self._settings = settings or SsoSettings()

    # The tenant's connection (admins)

    def service_provider(self, base: str, tenant_id: UUID) -> ServiceProvider:
        return ServiceProvider(
            oidc_redirect_uri(base),
            saml_entity_id(tenant_id),
            saml_acs_url(base, tenant_id),
            saml_metadata_url(base, tenant_id),
        )

    async def view(self, caller: Caller, base: str) -> ConnectionView:
        _require_admin(caller)
        async with self._uow() as uow:
            connection = await uow.sso.connection_of(caller.tenant_id)
        certificate = None
        if connection and connection.saml:
            certificate = self._saml.certificate(connection.saml.idp_certificate)
        return ConnectionView(connection, self.service_provider(base, caller.tenant_id), certificate)

    async def save(self, caller: Caller, spec: ConnectionSpec, reason: str) -> SsoConnection:
        """Creates or replaces the tenant's connection. An OIDC issuer is checked against its discovery
        document first, unless the connection is being turned off."""
        _require_admin(caller)
        why = _reason(reason)
        domains = normalize_domains(spec.domains)
        mappings = check_mappings(spec.role_mappings, spec.default_role)
        groups_attribute = check_attribute_name(spec.groups_attribute)
        if spec.enabled and not mappings and spec.default_role is None:
            raise InvalidInput("with no default role and no group mapping, nobody could sign in")
        issuer = None
        if spec.protocol is SsoProtocol.OIDC:
            issuer = check_https_url(spec.oidc_issuer or "", "issuer")
            if spec.enabled:
                await self._oidc.discover(issuer)
        async with self._uow() as uow:
            existing = await uow.sso.connection_of(caller.tenant_id)
            now = self._clock.now()
            connection_id = existing.connection_id if existing else uuid7()
            connection = SsoConnection(
                connection_id=connection_id,
                tenant_id=caller.tenant_id,
                protocol=spec.protocol,
                domains=domains,
                enabled=spec.enabled,
                created_at=existing.created_at if existing else now,
                updated_at=now,
                updated_by=caller.user_id,
                default_role=spec.default_role,
                role_mappings=mappings,
                groups_attribute=groups_attribute,
                require_second_factor=spec.require_second_factor,
                enforced=spec.enforced,
                oidc=self._oidc_settings(caller.tenant_id, spec, issuer, existing)
                if spec.protocol is SsoProtocol.OIDC
                else None,
                saml=self._saml_settings(spec) if spec.protocol is SsoProtocol.SAML else None,
            )
            await uow.sso.save_connection(connection)
            await _record(
                uow,
                caller.tenant_id,
                AccessChangeKind.SSO_SAVED,
                now,
                actor_id=caller.user_id,
                reason=why,
                detail={
                    "protocol": spec.protocol.value,
                    "domains": list(domains),
                    "enabled": spec.enabled,
                    "enforced": spec.enforced,
                    "require_second_factor": spec.require_second_factor,
                },
            )
        return connection

    async def remove(self, caller: Caller, reason: str) -> None:
        _require_admin(caller)
        why = _reason(reason)
        async with self._uow() as uow:
            connection = await uow.sso.connection_of(caller.tenant_id)
            if connection is None:
                raise NotFound("the tenant has no single sign-on")
            await uow.sso.delete_connection(connection.connection_id)
            await _record(
                uow,
                caller.tenant_id,
                AccessChangeKind.SSO_REMOVED,
                self._clock.now(),
                actor_id=caller.user_id,
                reason=why,
                detail={"protocol": connection.protocol.value, "domains": list(connection.domains)},
            )

    async def start_test(self, caller: Caller, base: str) -> Started:
        """A sign-in through the provider that only reports what would happen: the e-mail, the groups and the
        roles the person would get. Nobody is created and no role changes. Works on a connection still off."""
        _require_admin(caller)
        async with self._uow() as uow:
            connection = await uow.sso.connection_of(caller.tenant_id)
        if connection is None:
            raise NotFound("the tenant has no single sign-on")
        return await self._start(connection, SsoPurpose.TEST, base, requested_by=caller.user_id)

    async def test_result(self, caller: Caller, attempt_id: UUID) -> SsoAttempt:
        _require_admin(caller)
        async with self._uow() as uow:
            attempt = await uow.sso.get_attempt(attempt_id)
        if attempt is None or attempt.tenant_id != caller.tenant_id or attempt.purpose is not SsoPurpose.TEST:
            raise NotFound("unknown test")
        return attempt

    # The sign-in (public)

    async def discover(self, email: str) -> Discovery:
        """Whether the e-mail's domain signs in through a tenant's SSO. Answers by domain only, so it never
        tells whether a person exists."""
        async with self._uow() as uow:
            connection = await uow.sso.connection_for_domain(email_domain(_normalize_email(email)))
        if connection is None or not connection.enabled:
            return Discovery(sso=False)
        return Discovery(True, connection.protocol, connection.enforced)

    async def start(self, email: str, binding: str, origin: str | None, base: str) -> Started:
        """The address of the identity provider for this e-mail's domain. `binding` is the base64url SHA-256
        of a random value the Console keeps for this tab and shows again at the exchange."""
        if origin is None or origin not in self._settings.console_origins:
            raise Forbidden("the Console origin is not allowed to sign in")
        binding_hash = _binding_hash(binding)
        async with self._uow() as uow:
            connection = await uow.sso.connection_for_domain(email_domain(_normalize_email(email)))
        if connection is None or not connection.enabled:
            raise NotFound("no single sign-on for this e-mail domain")
        return await self._start(
            connection,
            SsoPurpose.LOGIN,
            base,
            binding_hash=binding_hash,
            origin=origin,
            login_hint=_normalize_email(email),
        )

    async def oidc_callback(self, state: str, code: str | None, error: str | None) -> Landing:
        """The provider's redirect back: `code` on success, `error` when the person cancelled or was
        refused."""
        attempt, connection = await self._pending(state)
        if attempt is None or connection is None:
            return Landing(page="expired")
        identity: IdpIdentity | SsoRefused
        if error or not code:
            identity = SsoRefused(SsoFailure.DENIED)
        elif connection.oidc is None:
            identity = SsoRefused(SsoFailure.DISABLED)
        else:
            identity = await self._redeem(connection, attempt, code)
        return await self._finish(state, identity)

    async def saml_acs(self, tenant_id: UUID, saml_response: str, relay_state: str) -> Landing:
        attempt, connection = await self._pending(relay_state)
        if attempt is None or connection is None or attempt.tenant_id != tenant_id:
            return Landing(page="expired")
        identity: IdpIdentity | SsoRefused
        if connection.saml is None or attempt.saml_request_id is None:
            identity = SsoRefused(SsoFailure.DISABLED)
        else:
            try:
                identity = self._saml.identity(
                    saml_response=saml_response,
                    idp_entity_id=connection.saml.idp_entity_id,
                    idp_certificate=connection.saml.idp_certificate,
                    entity_id=saml_entity_id(tenant_id),
                    acs_url=attempt.callback_url,
                    request_id=attempt.saml_request_id,
                    groups_attribute=connection.groups_attribute,
                    now=self._clock.now(),
                )
            except SsoRefused as refused:
                identity = refused
        return await self._finish(relay_state, identity)

    async def saml_metadata(self, tenant_id: UUID, base: str) -> bytes:
        async with self._uow() as uow:
            connection = await uow.sso.connection_of(tenant_id)
        if connection is None or connection.protocol is not SsoProtocol.SAML:
            raise NotFound("the tenant has no SAML single sign-on")
        return self._saml.metadata(entity_id=saml_entity_id(tenant_id), acs_url=saml_acs_url(base, tenant_id))

    async def exchange(
        self,
        code: str,
        verifier: str,
        totp: str | None,
        recovery_code: str | None,
        space_id: UUID | None,
    ) -> Any:
        """The session for a sign-in code, with the value the Console kept. When the tenant asks for the
        Niadra second factor on top of the provider, the code stays valid while the person types it, five
        wrong codes at most."""
        failed: Unauthenticated | None = None
        async with self._uow() as uow:
            attempt, user, connection = await self._signed_in(uow, code, verifier)
            required = connection.require_second_factor if connection else True
            try:
                issued = await self._sessions.issue_session(
                    uow, user, space_id, required, totp, recovery_code
                )
            except Unauthenticated as exc:
                wrong = bool(totp or recovery_code) and exc.detail == "invalid second factor"
                failures = attempt.code_failures + int(wrong)
                spent = failures >= self._settings.max_code_failures
                await uow.sso.update_attempt(
                    replace(
                        attempt,
                        code_failures=failures,
                        status=SsoAttemptStatus.FAILED if spent else attempt.status,
                    )
                )
                failed = exc
            else:
                await uow.sso.update_attempt(
                    replace(attempt, status=SsoAttemptStatus.COMPLETED, completed_at=self._clock.now())
                )
        if failed is not None:
            raise failed
        return issued

    async def second_factor_setup(self, code: str, verifier: str) -> NewSsoAuthenticator:
        """The authenticator enrollment of a person who signed in through SSO, when the tenant asks for the
        Niadra second factor: the sign-in code stands in for the password."""
        async with self._uow() as uow:
            _, user, _ = await self._signed_in(uow, code, verifier)
            secret = await self._sessions.enrollment_secret(uow, user)
        return NewSsoAuthenticator(secret, user.email)

    # Internals

    def _oidc_settings(
        self, tenant_id: UUID, spec: ConnectionSpec, issuer: str | None, existing: SsoConnection | None
    ) -> OidcSettings:
        client_id = (spec.oidc_client_id or "").strip()
        if not 1 <= len(client_id) <= 512:
            raise InvalidInput("the client id is required")
        if spec.oidc_client_secret:
            if len(spec.oidc_client_secret) > 2048:
                raise InvalidInput("the client secret is too long")
            secret = self._sealer.seal(spec.oidc_client_secret, _secret_aad(tenant_id))
        elif existing is not None and existing.oidc is not None:
            secret = existing.oidc.client_secret
        else:
            raise InvalidInput("the client secret is required")
        scopes = tuple(dict.fromkeys([*DEFAULT_SCOPES, *(s.strip() for s in spec.oidc_scopes if s.strip())]))
        if any(not s.isprintable() or " " in s or len(s) > 128 for s in scopes) or len(scopes) > 20:
            raise InvalidInput("invalid scopes")
        assert issuer is not None
        return OidcSettings(issuer, client_id, secret, scopes)

    def _saml_settings(self, spec: ConnectionSpec) -> SamlSettings:
        entity_id = (spec.saml_idp_entity_id or "").strip()
        if not 1 <= len(entity_id) <= 1024:
            raise InvalidInput("the identity provider's entity id is required")
        sso_url = check_https_url(spec.saml_idp_sso_url or "", "single sign-on address")
        certificate = (spec.saml_idp_certificate or "").strip()
        self._saml.certificate(certificate)
        return SamlSettings(entity_id, sso_url, certificate)

    async def _start(
        self,
        connection: SsoConnection,
        purpose: SsoPurpose,
        base: str,
        *,
        binding_hash: bytes | None = None,
        origin: str | None = None,
        login_hint: str | None = None,
        requested_by: UUID | None = None,
    ) -> Started:
        now = self._clock.now()
        expires = now + timedelta(seconds=self._settings.attempt_ttl_s)
        state, nonce, verifier = new_url_token(), new_url_token(), new_url_token()
        request_id = None
        if connection.oidc is not None:
            provider = await self._oidc.discover(connection.oidc.issuer)
            callback = oidc_redirect_uri(base)
            query = {
                "response_type": "code",
                "client_id": connection.oidc.client_id,
                "redirect_uri": callback,
                "scope": " ".join(connection.oidc.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
            if login_hint:
                query["login_hint"] = login_hint
            separator = "&" if "?" in provider.authorization_endpoint else "?"
            url = f"{provider.authorization_endpoint}{separator}{urlencode(query)}"
        elif connection.saml is not None:
            callback = saml_acs_url(base, connection.tenant_id)
            # An NCName: SAML ids may not start with a digit.
            request_id = f"_{uuid7().hex}"
            url = self._saml.login_url(
                idp_sso_url=connection.saml.idp_sso_url,
                entity_id=saml_entity_id(connection.tenant_id),
                acs_url=callback,
                request_id=request_id,
                relay_state=state,
                now=now,
            )
        else:
            raise Conflict("the connection has no protocol settings")
        attempt = SsoAttempt(
            attempt_id=uuid7(),
            tenant_id=connection.tenant_id,
            connection_id=connection.connection_id,
            purpose=purpose,
            state_hash=sha256(state),
            nonce=nonce,
            code_verifier=verifier,
            callback_url=callback,
            created_at=now,
            expires_at=expires,
            binding_hash=binding_hash,
            return_origin=origin,
            saml_request_id=request_id,
            requested_by=requested_by,
        )
        async with self._uow() as uow:
            await uow.sso.purge_attempts(now - timedelta(days=1))
            since = now - timedelta(seconds=self._settings.attempt_ttl_s)
            if (
                await uow.sso.pending_attempts(connection.connection_id, since)
                >= self._settings.max_pending_attempts
            ):
                raise Unavailable("too many sign-ins in progress; try again in a few minutes")
            await uow.sso.add_attempt(attempt)
        return Started(attempt.attempt_id, url, expires)

    async def _pending(self, state: str) -> tuple[SsoAttempt | None, SsoConnection | None]:
        """The attempt a `state` (or SAML RelayState) belongs to, while it waits for the provider."""
        if not state or len(state) > 256:
            return None, None
        async with self._uow() as uow:
            attempt = await uow.sso.attempt_by_state(sha256(state))
            if attempt is None:
                return None, None
            connection = await uow.sso.connection_of(attempt.tenant_id)
        if attempt.status is not SsoAttemptStatus.PENDING or attempt.expires_at <= self._clock.now():
            return None, None
        if connection is None or connection.connection_id != attempt.connection_id:
            return None, None
        return attempt, connection

    async def _redeem(
        self, connection: SsoConnection, attempt: SsoAttempt, code: str
    ) -> IdpIdentity | SsoRefused:
        assert connection.oidc is not None
        try:
            provider = await self._oidc.discover(connection.oidc.issuer)
            secret = self._sealer.unseal(connection.oidc.client_secret, _secret_aad(connection.tenant_id))
            return await self._oidc.redeem(
                provider,
                client_id=connection.oidc.client_id,
                client_secret=secret,
                code=code,
                redirect_uri=attempt.callback_url,
                code_verifier=attempt.code_verifier,
                nonce=attempt.nonce,
                groups_claim=connection.groups_attribute,
                now=self._clock.now(),
            )
        except SsoRefused as refused:
            return refused
        except (InvalidInput, Unavailable):
            return SsoRefused(SsoFailure.UNAVAILABLE)

    async def _finish(self, state: str, identity: IdpIdentity | SsoRefused) -> Landing:
        """Closes the attempt under its row lock, so one answer of the provider is used once."""
        async with self._uow() as uow:
            attempt = await uow.sso.attempt_by_state(sha256(state))
            connection = await uow.sso.connection_of(attempt.tenant_id) if attempt else None
            now = self._clock.now()
            if (
                attempt is None
                or connection is None
                or attempt.status is not SsoAttemptStatus.PENDING
                or attempt.expires_at <= now
            ):
                return Landing(page="expired")
            if attempt.purpose is SsoPurpose.TEST:
                result = await self._test_report(uow, connection, identity)
                status = SsoAttemptStatus.COMPLETED if result["ok"] else SsoAttemptStatus.FAILED
                await uow.sso.update_attempt(replace(attempt, status=status, completed_at=now, result=result))
                return Landing(page="test_done")
            origin = attempt.return_origin or ""
            try:
                if isinstance(identity, SsoRefused):
                    raise identity
                user = await self._admit(uow, connection, identity, now)
            except SsoRefused as refused:
                await uow.sso.update_attempt(
                    replace(
                        attempt,
                        status=SsoAttemptStatus.FAILED,
                        completed_at=now,
                        result={"failure": refused.failure.value},
                    )
                )
                return Landing(
                    redirect=f"{origin}{self._settings.console_path}#error={refused.failure.value}"
                )
            code = new_url_token()
            await uow.sso.update_attempt(
                replace(
                    attempt,
                    status=SsoAttemptStatus.SIGNED_IN,
                    user_id=user.user_id,
                    login_code_hash=sha256(code),
                    code_expires_at=now + timedelta(seconds=self._settings.code_ttl_s),
                )
            )
            await uow.access.touch_sso_login(user.user_id, now)
        return Landing(redirect=f"{origin}{self._settings.console_path}#code={code}")

    async def _admit(
        self, uow: ControlUnitOfWork, connection: SsoConnection, identity: IdpIdentity, now: datetime
    ) -> User:
        """The person behind a valid answer: created on the first sign-in, with the roles of their groups set
        again at every sign-in (the provider is where access is managed)."""
        if not connection.enabled:
            raise SsoRefused(SsoFailure.DISABLED)
        email = admitted_email(identity, connection.domains)
        user = await uow.access.user_by_email(email)
        if user is not None and user.tenant_id != connection.tenant_id:
            raise SsoRefused(SsoFailure.OTHER_TENANT)
        if user is not None and not user.active:
            raise SsoRefused(SsoFailure.DEACTIVATED)
        roles = roles_from_groups(identity.groups, connection.role_mappings, connection.default_role)
        if not roles:
            raise SsoRefused(SsoFailure.NO_ROLE)
        if user is None:
            user = User(uuid7(), connection.tenant_id, email, NO_PASSWORD, now)
            await uow.access.add_user(user)
            await uow.access.set_bindings(user.user_id, _tenant_bindings(user, roles))
            await _record(
                uow,
                connection.tenant_id,
                AccessChangeKind.SSO_PERSON_CREATED,
                now,
                user_id=user.user_id,
                detail={"roles": sorted(r.value for r in roles), "protocol": connection.protocol.value},
            )
            return user
        await self._sync_roles(uow, user, roles, now)
        return user

    async def _sync_roles(
        self, uow: ControlUnitOfWork, user: User, roles: frozenset[Role], now: datetime
    ) -> None:
        current = await uow.access.bindings_of(user.user_id)
        wanted = set(roles)
        kept_admin = False
        was_admin = any(b.role is Role.ADMIN and b.space_id is None for b in current)
        if was_admin and Role.ADMIN not in wanted and await uow.access.count_admins(user.tenant_id) <= 1:
            # The groups would leave the tenant without an admin; the person keeps the role.
            wanted.add(Role.ADMIN)
            kept_admin = True
        before = {(b.role, b.space_id, b.source_ids) for b in current}
        after = {(r, None, frozenset[UUID]()) for r in wanted}
        if before == after:
            return
        await uow.access.set_bindings(user.user_id, _tenant_bindings(user, frozenset(wanted)))
        await _record(
            uow,
            user.tenant_id,
            AccessChangeKind.SSO_ROLES_SYNCED,
            now,
            user_id=user.user_id,
            detail={
                "before": sorted(b.role.value for b in current),
                "after": sorted(r.value for r in wanted),
                "kept_admin": kept_admin,
            },
        )

    async def _test_report(
        self, uow: ControlUnitOfWork, connection: SsoConnection, identity: IdpIdentity | SsoRefused
    ) -> dict[str, Any]:
        """What a real sign-in with this answer would do, without doing it."""
        if isinstance(identity, SsoRefused):
            return {"ok": False, "failure": identity.failure.value, "detail": identity.detail}
        report: dict[str, Any] = {
            "email": identity.email,
            "groups": list(identity.groups)[:100],
            "name": identity.name,
        }
        roles = roles_from_groups(identity.groups, connection.role_mappings, connection.default_role)
        report["roles"] = sorted(r.value for r in roles)
        try:
            email = admitted_email(identity, connection.domains)
            user = await uow.access.user_by_email(email)
            if user is not None and user.tenant_id != connection.tenant_id:
                raise SsoRefused(SsoFailure.OTHER_TENANT)
            if user is not None and not user.active:
                raise SsoRefused(SsoFailure.DEACTIVATED)
            if not roles:
                raise SsoRefused(SsoFailure.NO_ROLE)
        except SsoRefused as refused:
            return report | {"ok": False, "failure": refused.failure.value, "detail": refused.detail}
        return report | {"ok": True, "failure": None, "person_exists": user is not None}

    async def _signed_in(
        self, uow: ControlUnitOfWork, code: str, verifier: str
    ) -> tuple[SsoAttempt, User, SsoConnection | None]:
        attempt = await uow.sso.attempt_by_login_code(sha256(code))
        now = self._clock.now()
        if (
            attempt is None
            or attempt.status is not SsoAttemptStatus.SIGNED_IN
            or attempt.code_expires_at is None
            or attempt.code_expires_at <= now
            or attempt.binding_hash is None
            or not hmac.compare_digest(sha256(verifier), attempt.binding_hash)
            or attempt.user_id is None
        ):
            raise Unauthenticated("the sign-in code is invalid or expired")
        user = await uow.access.get_user(attempt.user_id)
        if user is None or not user.active:
            raise Unauthenticated("the person is no longer active")
        return attempt, user, await uow.sso.connection_of(attempt.tenant_id)


def _tenant_bindings(user: User, roles: frozenset[Role]) -> list[RoleBinding]:
    return [RoleBinding(uuid7(), user.user_id, user.tenant_id, r) for r in sorted(roles)]


async def _record(
    uow: ControlUnitOfWork,
    tenant_id: UUID,
    kind: AccessChangeKind,
    at: datetime,
    *,
    user_id: UUID | None = None,
    actor_id: UUID | None = None,
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await uow.access.add_access_change(
        AccessChange(uuid7(), tenant_id, kind, at, user_id, actor_id, reason, detail or {})
    )


def _require_admin(caller: Caller) -> None:
    if not caller.has(Role.ADMIN):
        raise Forbidden("the role cannot do this")


def _reason(reason: str) -> str:
    reason = reason.strip()
    if not 3 <= len(reason) <= 1000:
        raise InvalidInput("the reason must have 3 to 1000 characters")
    return reason


def _normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise InvalidInput("invalid e-mail")
    return email


def _secret_aad(tenant_id: UUID) -> bytes:
    return f"sso_connections:{tenant_id}:oidc_client_secret".encode()


def _binding_hash(binding: str) -> bytes:
    """The Console sends base64url(SHA-256(value)); anything else is refused."""
    try:
        raw = base64.urlsafe_b64decode(binding + "=" * (-len(binding) % 4))
    except (binascii.Error, ValueError):
        raise InvalidInput("invalid binding") from None
    if len(raw) != 32:
        raise InvalidInput("invalid binding")
    return raw
