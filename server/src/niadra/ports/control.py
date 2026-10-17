"""Ports of the control plane: its own database (never a cell's) and its signing key (5.16)."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.control.records import (
    AccessChange,
    ApiKey,
    Cell,
    CellStatus,
    ConfigDiff,
    ConfigVersion,
    DeletionReceipt,
    DiffStatus,
    Invitation,
    PlacementMove,
    Project,
    Quota,
    RecoveryCode,
    Revocation,
    RevocationKind,
    RoleBinding,
    SecondFactorReset,
    SecretRef,
    SigningKey,
    Snapshot,
    Source,
    Space,
    SsoAttempt,
    SsoConnection,
    Tenant,
    UsageAggregate,
    User,
)
from niadra.domain.control.sso import IdpIdentity
from niadra.ports.infra import HttpResponse


class DirectoryRepository(Protocol):
    """Tenants, projects, spaces, cells, placement moves and quotas."""

    async def add_tenant(self, tenant: Tenant) -> None:
        """Raises Conflict when the slug is taken."""
        ...

    async def get_tenant(self, tenant_id: UUID) -> Tenant | None: ...

    async def tenant_by_slug(self, slug: str) -> Tenant | None: ...

    async def add_project(self, project: Project) -> None:
        """Raises Conflict when the tenant already has a project with the slug."""
        ...

    async def projects_of(self, tenant_id: UUID) -> list[Project]: ...

    async def get_project(self, project_id: UUID) -> Project | None: ...

    async def spaces_of_project(self, project_id: UUID) -> list[Space]:
        """The project's spaces that are not deleted."""
        ...

    async def add_space(self, space: Space) -> None:
        """Raises Conflict when the slug is taken in the region."""
        ...

    async def set_space_key(self, space_id: UUID, kms_key_arn: str | None) -> None:
        """The customer-managed key of a space, set by an operator under contract; None goes back to the
        cell's own key for new data keys."""
        ...

    async def get_space(self, space_id: UUID, *, lock: bool = False) -> Space | None:
        """`lock` holds the row until commit, which serializes config versions of the space. Deleted
        spaces are returned too, with `deleted_at`."""
        ...

    async def spaces_of_tenant(self, tenant_id: UUID) -> list[Space]:
        """The tenant's spaces that are not deleted."""
        ...

    async def spaces_in_cell(self, cell_id: str, *, include_deleted: bool = False) -> list[Space]: ...

    async def mark_space_deleted(self, space_id: UUID, at: datetime) -> None: ...

    async def add_deletion_receipt(self, receipt: DeletionReceipt) -> None:
        """Idempotent by space: the first receipt stays."""
        ...

    async def deletion_receipts(self, space_ids: Sequence[UUID]) -> dict[UUID, DeletionReceipt]: ...

    async def set_space_cell(self, space_id: UUID, cell_id: str) -> None: ...

    async def add_cell(self, cell: Cell) -> None: ...

    async def get_cell(self, cell_id: str, *, lock: bool = False) -> Cell | None:
        """`lock` serializes snapshot versions of the cell."""
        ...

    async def list_cells(self) -> list[Cell]: ...

    async def set_cell_token(self, cell_id: str, token_hash: bytes) -> None:
        """The SHA-256 of a cell's own credential."""
        ...

    async def set_cell_status(self, cell_id: str, status: CellStatus) -> None: ...

    async def placed_counts(self) -> dict[str, int]:
        """Spaces per cell."""
        ...

    async def add_move(self, move: PlacementMove) -> None: ...

    async def open_move(self, space_id: UUID) -> PlacementMove | None: ...

    async def moved_from(self, cell_id: str, since: datetime) -> dict[UUID, str]:
        """Spaces that finished moving out of the cell since `since`, with the cell they went to."""
        ...

    async def complete_move(self, move_id: UUID, at: datetime) -> None: ...

    async def set_quota(self, quota: Quota) -> None: ...

    async def quotas_of(self, tenant_id: UUID) -> list[Quota]: ...


class AccessRepository(Protocol):
    """Sources, keys, revocations, people, role bindings and the public signing keys."""

    async def add_source(self, source: Source) -> None: ...

    async def get_source(self, source_id: UUID) -> Source | None: ...

    async def sources_of(self, space_id: UUID) -> list[Source]: ...

    async def revoke_source(self, source_id: UUID, at: datetime) -> None: ...

    async def add_key(self, key: ApiKey) -> None: ...

    async def get_key(self, key_id: str) -> ApiKey | None: ...

    async def keys_of_source(self, source_id: UUID) -> list[ApiKey]: ...

    async def keys_of_spaces(self, space_ids: Sequence[UUID]) -> list[ApiKey]: ...

    async def revoke_key(self, key_id: str, at: datetime) -> None:
        """Also bumps the key version: cells only replace a key row with a newer version."""
        ...

    async def expire_key(self, key_id: str, at: datetime) -> None:
        """Sets the end of a rotated key's grace period, bumping the version like `revoke_key`."""
        ...

    async def add_revocation(
        self,
        space_id: UUID,
        kind: RevocationKind,
        source_id: UUID,
        key_id: str | None,
        reason: str,
        at: datetime,
    ) -> Revocation:
        """Appends to the feed; the sequence is assigned here and only grows."""
        ...

    async def revocations_after(
        self, seq: int, space_ids: Sequence[UUID], limit: int
    ) -> list[Revocation]: ...

    async def add_user(self, user: User) -> None:
        """Raises Conflict when the e-mail is taken."""
        ...

    async def get_user(self, user_id: UUID) -> User | None: ...

    async def user_by_email(self, email: str) -> User | None: ...

    async def users_of(self, tenant_id: UUID) -> list[User]: ...

    async def touch_login(self, user_id: UUID, at: datetime) -> None: ...

    async def set_second_factor(
        self, user_id: UUID, secret: str | None, confirmed_at: datetime | None
    ) -> None:
        """The TOTP secret and when its first valid code arrived (None: enrollment pending). A replacement
        still waiting for its first code is dropped."""
        ...

    async def set_pending_second_factor(self, user_id: UUID, secret: str) -> None:
        """A replacement TOTP secret, kept beside the current one until its first code."""
        ...

    async def use_totp_step(self, user_id: UUID, step: int) -> bool:
        """Records `step` as the person's last accepted authenticator step when it is later than the one
        kept, in one compare-and-set, and says whether it was: of two uses of one code, one wins."""
        ...

    async def recovery_codes_of(self, user_id: UUID) -> list[RecoveryCode]:
        """Used ones too."""
        ...

    async def replace_recovery_codes(self, user_id: UUID, codes: Sequence[RecoveryCode]) -> None:
        """Deletes every code of the person, used or not, and keeps these."""
        ...

    async def use_recovery_code(self, user_id: UUID, code_hash: bytes, at: datetime) -> bool:
        """Marks an unused code as used. False when it was used already, by a login that got there first."""
        ...

    async def add_second_factor_reset(self, reset: SecondFactorReset) -> None: ...

    async def preferences_of(self, user_id: UUID) -> dict[str, Any]:
        """What the Console keeps for the person, by key."""
        ...

    async def set_preference(self, user_id: UUID, key: str, value: Any) -> None:
        """Writes one key, replacing its value."""
        ...

    async def unused_recovery_codes(self, user_ids: Sequence[UUID]) -> dict[UUID, int]:
        """Recovery codes not used yet, per person; a person without codes is left out."""
        ...

    async def set_active(self, user_id: UUID, active: bool, at: datetime | None) -> None:
        """`at` is when the person was deactivated; None when reactivated."""
        ...

    async def set_password(self, user_id: UUID, password_hash: str) -> None: ...

    async def touch_sso_login(self, user_id: UUID, at: datetime) -> None: ...

    async def add_invitation(self, invitation: Invitation) -> None: ...

    async def invitation_by_token(self, token_hash: bytes) -> Invitation | None:
        """Locks the row until commit, so two acceptances of one link race to one winner."""
        ...

    async def open_invitations(self, user_ids: Sequence[UUID], now: datetime) -> dict[UUID, Invitation]:
        """The newest invitation of each person that is neither used nor expired."""
        ...

    async def close_invitations(self, user_id: UUID, at: datetime) -> None:
        """Marks every unused invitation of the person as used: a new link or a deactivation ends the old."""
        ...

    async def add_access_change(self, change: AccessChange) -> None: ...

    async def access_changes(self, tenant_id: UUID, user_id: UUID | None, limit: int) -> list[AccessChange]:
        """Newest first; with `user_id`, only that person's."""
        ...

    async def access_changes_since(
        self, tenant_ids: Sequence[UUID], after: datetime, limit: int
    ) -> list[AccessChange]:
        """Oldest first, strictly after `after`: the feed a cell turns into receipts."""
        ...

    async def set_bindings(self, user_id: UUID, bindings: Sequence[RoleBinding]) -> None:
        """Replaces every binding of the person."""
        ...

    async def bindings_of(self, user_id: UUID) -> list[RoleBinding]: ...

    async def count_admins(self, tenant_id: UUID) -> int:
        """Active people with the admin role on the whole tenant."""
        ...

    async def save_signing_key(self, key: SigningKey) -> None:
        """Idempotent by `kid`."""
        ...

    async def signing_keys(self) -> list[SigningKey]: ...


class ConfigStore(Protocol):
    """Config diffs and versions, signed snapshots and secret metadata (4.7)."""

    async def add_diff(self, diff: ConfigDiff) -> None: ...

    async def get_diff(self, diff_id: UUID) -> ConfigDiff | None: ...

    async def update_diff(self, diff: ConfigDiff) -> None: ...

    async def diffs_of(
        self, space_id: UUID, status: DiffStatus | None = None, limit: int = 100
    ) -> list[ConfigDiff]:
        """Newest first."""
        ...

    async def add_version(self, version: ConfigVersion) -> None: ...

    async def current_version(self, space_id: UUID) -> int:
        """0 before the first version."""
        ...

    async def latest_documents(self, space_id: UUID) -> dict[str, ConfigVersion]:
        """The newest version of each document type."""
        ...

    async def history(self, space_id: UUID, doc_type: str | None, limit: int) -> list[ConfigVersion]:
        """Newest first."""
        ...

    async def version_before(self, space_id: UUID, doc_type: str, version: int) -> ConfigVersion | None: ...

    async def changed_since(self, space_id: UUID, doc_type: str, version: int) -> bool: ...

    async def latest_snapshot(self, cell_id: str) -> Snapshot | None: ...

    async def add_snapshot(self, snapshot: Snapshot) -> None: ...

    async def put_secret_ref(self, ref: SecretRef) -> None:
        """Upsert by (space, ref)."""
        ...

    async def secret_refs(self, space_id: UUID) -> list[SecretRef]: ...


class SsoRepository(Protocol):
    """The tenant's SSO connection, the e-mail domains it claims and the trips to the identity provider."""

    async def connection_of(self, tenant_id: UUID) -> SsoConnection | None: ...

    async def connection_for_domain(self, domain: str) -> SsoConnection | None: ...

    async def save_connection(self, connection: SsoConnection) -> None:
        """Insert or replace the tenant's connection and its domains. Raises Conflict when another tenant
        already claims one of the domains."""
        ...

    async def delete_connection(self, connection_id: UUID) -> None:
        """The attempts of the connection go with it."""
        ...

    async def add_attempt(self, attempt: SsoAttempt) -> None: ...

    async def attempt_by_state(self, state_hash: bytes) -> SsoAttempt | None:
        """Locks the row until commit: one answer of the provider is used once."""
        ...

    async def attempt_by_login_code(self, code_hash: bytes) -> SsoAttempt | None:
        """Locks the row until commit: one sign-in code is exchanged once."""
        ...

    async def get_attempt(self, attempt_id: UUID) -> SsoAttempt | None: ...

    async def update_attempt(self, attempt: SsoAttempt) -> None: ...

    async def pending_attempts(self, connection_id: UUID, since: datetime) -> int: ...

    async def purge_attempts(self, before: datetime) -> int:
        """Deletes attempts that expired before `before`."""
        ...


class UsageStore(Protocol):
    async def upsert(self, aggregates: Sequence[UsageAggregate]) -> None:
        """Cells push running totals per period; the last report of a (space, period, metric) wins."""
        ...

    async def usage(
        self, space_ids: Sequence[UUID], since: datetime, until: datetime
    ) -> list[UsageAggregate]: ...


class ControlUnitOfWork(Protocol):
    @property
    def directory(self) -> DirectoryRepository: ...

    @property
    def access(self) -> AccessRepository: ...

    @property
    def config(self) -> ConfigStore: ...

    @property
    def usage(self) -> UsageStore: ...

    @property
    def sso(self) -> SsoRepository: ...


class ControlUnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[ControlUnitOfWork]:
        """One transaction; leaving the block commits and an exception rolls back."""
        ...


class Signer(Protocol):
    """The control's Ed25519 key: signs snapshots and the revocation feed, issues person JWTs (4.7, 5.22).

    Loaded from a file or the environment; production never generates one silently.
    """

    @property
    def kid(self) -> str: ...

    @property
    def algorithm(self) -> str: ...

    def sign(self, payload: bytes) -> bytes: ...

    def public_jwks(self) -> list[dict[str, str]]:
        """The current key first, then keys kept only to verify tokens issued before a rotation."""
        ...

    def encode_jwt(self, claims: Mapping[str, Any]) -> str: ...

    def decode_jwt(self, token: str, audience: str, issuer: str) -> dict[str, Any]:
        """Raises Unauthenticated for a bad signature, an unknown `kid` or the wrong audience or issuer.
        Expiry is left to the caller, which owns the clock."""
        ...


class IdpHttp(Protocol):
    """HTTPS to an identity provider: public addresses only, no redirects, a cap on the answer's size."""

    async def get(self, url: str, headers: Mapping[str, str], timeout_s: float) -> HttpResponse: ...

    async def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float
    ) -> HttpResponse: ...


class SecretSealer(Protocol):
    """Seals the few secrets the control plane itself must use, like an OIDC client secret. `aad` ties a
    sealed value to where it is stored."""

    def seal(self, value: str, aad: bytes) -> bytes: ...

    def unseal(self, sealed: bytes, aad: bytes) -> str: ...


@dataclass(frozen=True, slots=True)
class OidcProvider:
    """An OIDC provider as its discovery document describes it."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    token_auth_methods: tuple[str, ...] = ("client_secret_basic",)


@dataclass(frozen=True, slots=True)
class CertificateInfo:
    subject: str
    not_after: datetime
    sha256_fingerprint: str


class OidcClient(Protocol):
    """The OIDC half of the identity provider port (5.22): discovery, the token endpoint and the ID token."""

    async def discover(self, issuer: str) -> OidcProvider:
        """Reads `<issuer>/.well-known/openid-configuration`. Raises InvalidInput when it is missing or names
        another issuer, Unavailable when the provider does not answer."""
        ...

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
        """Exchanges the authorization code (with the PKCE verifier) and validates the ID token: signature by
        the provider's keys, issuer, audience, expiry and nonce. Raises SsoRefused on a bad answer."""
        ...


class SamlService(Protocol):
    """The SAML 2.0 half of the identity provider port: the service provider's metadata and requests, and the
    validation of the provider's signed answer."""

    def metadata(self, *, entity_id: str, acs_url: str) -> bytes: ...

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
        """The provider's address with an AuthnRequest in the HTTP-Redirect binding."""
        ...

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
        """Validates a base64 SAMLResponse: an assertion signed by the configured certificate, the issuer,
        audience, recipient, time window and the request it answers. Raises SsoRefused otherwise."""
        ...

    def certificate(self, pem: str) -> CertificateInfo:
        """Raises InvalidInput when the text is not an X.509 certificate."""
        ...


class ControlPlaneClient(Protocol):
    """How a cell reaches the control plane (4.7): mTLS in deployment, plus the cell token."""

    async def snapshot(self, cell_id: str, after: int | None, wait_s: float) -> dict[str, Any] | None:
        """`GET /v1/snapshot/{cell}`; with `after`, a long poll that returns None when nothing is newer."""
        ...

    async def revocations(self, cell_id: str, after: int) -> dict[str, Any]:
        """`GET /v1/revocations?cell_id=&after=`: `{"body", "kid", "signature"}`."""
        ...

    async def access_changes(self, cell_id: str, after: datetime | None) -> dict[str, Any]:
        """`GET /v1/access-changes?cell_id=&after=`: `{"body", "kid", "signature"}`."""
        ...

    async def post_deletion_receipt(self, cell_id: str, space_id: UUID, receipt: dict[str, Any]) -> None:
        """`POST /v1/spaces/{space_id}/deletion-receipt`: what the cell deleted; raises `Unavailable`
        until the control plane has stored it (an identical receipt sent again is accepted)."""
        ...

    async def push_usage(self, cell_id: str, items: Sequence[Mapping[str, Any]]) -> int:
        """`POST /v1/usage`: hourly usage per space and metric, at most 5,000 items; sending an hour again
        replaces it. Returns how many the control plane accepted; raises `Unavailable` when it is away."""
        ...
