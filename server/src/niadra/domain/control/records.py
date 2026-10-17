"""What the control database holds. Everything here is operator data, never customer data (5.16)."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from niadra.domain.spaces.directory import PRIMARY_INSTANCE, Tier
from niadra.domain.vocabulary import Audience, Environment, Scope, Verification


class Role(StrEnum):
    """Console roles, combinable (5.22)."""

    ADMIN = "admin"
    SECURITY = "security"
    INTEGRATION = "integration"
    REVIEW = "review"
    ANALYSIS = "analysis"
    VENDOR = "vendor"


class CellStatus(StrEnum):
    ACTIVE = "active"
    DRAINING = "draining"
    CLOSED = "closed"


class KeyStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class DiffStatus(StrEnum):
    PENDING = "pending"
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"


class MoveStatus(StrEnum):
    COPYING = "copying"
    COMPLETED = "completed"


class RevocationKind(StrEnum):
    KEY = "key"
    SOURCE = "source"


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: UUID
    name: str
    slug: str
    created_at: datetime
    mfa_required: bool = True


@dataclass(frozen=True, slots=True)
class Project:
    project_id: UUID
    tenant_id: UUID
    name: str
    slug: str
    region: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Space:
    """A project in one environment; the unit of isolation in a cell (6), with a database of its own."""

    space_id: UUID
    tenant_id: UUID
    project_id: UUID
    environment: Environment
    slug: str
    region: str
    cell_id: str
    created_at: datetime
    deleted_at: datetime | None = None
    kms_key_arn: str | None = None
    """The customer's own key in KMS, under contract (10, D.1): the space's data keys are generated under
    it, and without access to it the space's data cannot be read."""
    tier: Tier = Tier.T0
    """T0 shares the cell's database instance; T1 has an instance of its own in the cell; T2 is a cell
    dedicated to the tenant, plan Regulado."""
    instance: str = PRIMARY_INSTANCE
    """The database instance of the cell that holds the space's database, by name (NIADRA_DB_INSTANCES)."""


@dataclass(frozen=True, slots=True)
class DeletionReceipt:
    """What a cell reported after deleting a space: the document as the cell sent it and its SHA-256."""

    space_id: UUID
    cell_id: str
    receipt: dict[str, Any]
    receipt_hash: bytes
    received_at: datetime


@dataclass(frozen=True, slots=True)
class Cell:
    cell_id: str
    region: str
    address: str
    capacity: int
    status: CellStatus
    created_at: datetime
    code_version: str | None = None
    schema_revision: str | None = None
    tenant_id: UUID | None = None
    """Set on a cell dedicated to one tenant (plan Regulado): it takes that tenant's spaces and
    no one else's, and every new space of the tenant in the region goes there."""
    token_hash: bytes | None = field(default=None, repr=False)
    """SHA-256 of the cell's own credential: such a cell reaches the control plane only with it, and
    the credential the other cells share cannot act for it."""

    @property
    def dedicated(self) -> bool:
        return self.tenant_id is not None


@dataclass(frozen=True, slots=True)
class PlacementMove:
    move_id: UUID
    space_id: UUID
    from_cell: str
    to_cell: str
    status: MoveStatus
    reason: str
    requested_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Quota:
    tenant_id: UUID
    metric: str
    limit: int
    period: str = "month"


@dataclass(frozen=True, slots=True)
class Source:
    source_id: UUID
    space_id: UUID
    name: str
    audience: Audience
    created_at: datetime
    vendor: str | None = None
    channel: str | None = None
    purposes: frozenset[str] = frozenset()
    verification_ceiling: Verification = Verification.V2
    confirms_before_acting: bool = False
    navigation_enabled: bool = True
    trusted_action_ops: frozenset[str] = frozenset()
    critical: bool = False
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ApiKey:
    """A source key. Only the two hashes are stored; the secret is shown once (5.1)."""

    key_id: str
    space_id: UUID
    source_id: UUID
    scopes: frozenset[Scope]
    fast_hash: bytes
    slow_hash: str
    status: KeyStatus
    version: int
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Revocation:
    seq: int
    space_id: UUID
    kind: RevocationKind
    source_id: UUID
    at: datetime
    reason: str
    key_id: str | None = None


@dataclass(frozen=True, slots=True)
class User:
    user_id: UUID
    tenant_id: UUID
    email: str
    password_hash: str
    created_at: datetime
    totp_secret: str | None = None
    active: bool = True
    last_login_at: datetime | None = None
    totp_confirmed_at: datetime | None = None
    """None while the person has not typed a first valid code: the Console still shows the enrollment."""
    totp_pending_secret: str | None = None
    """A replacement authenticator started from the account page. The current one keeps working until a
    first code of this one confirms it."""
    deactivated_at: datetime | None = None
    last_sso_login_at: datetime | None = None
    """The last sign-in through the tenant's SSO; None for a person who never used it."""
    totp_last_step: int | None = None
    """The 30-second step of the last authenticator code accepted: a code of this step or an earlier one
    is refused, so a code seen once never works again (RFC 6238, 5.2)."""


@dataclass(frozen=True, slots=True)
class RecoveryCode:
    """A single-use way in for a person who lost the authenticator. Only a keyed hash is kept; the code
    itself is shown once (5.22)."""

    user_id: UUID
    code_hash: bytes
    created_at: datetime
    used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SecondFactorReset:
    """An admin cleared a person's second factor: who, whose, why and when."""

    reset_id: UUID
    tenant_id: UUID
    user_id: UUID
    reset_by: UUID
    reason: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Invitation:
    """A single-use link that lets a person choose a password: for a new person, or for one who forgot it.
    Only a keyed hash of the token is kept; the token is shown once to the admin who asked for it (5.22)."""

    invitation_id: UUID
    tenant_id: UUID
    user_id: UUID
    token_hash: bytes
    created_by: UUID
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None


class AccessChangeKind(StrEnum):
    INVITED = "invited"
    INVITATION_ACCEPTED = "invitation_accepted"
    ROLES_CHANGED = "roles_changed"
    DEACTIVATED = "deactivated"
    REACTIVATED = "reactivated"
    SECOND_FACTOR_RESET = "second_factor_reset"
    SSO_PERSON_CREATED = "sso_person_created"
    SSO_ROLES_SYNCED = "sso_roles_synced"
    SSO_SAVED = "sso_saved"
    SSO_REMOVED = "sso_removed"


@dataclass(frozen=True, slots=True)
class AccessChange:
    """Who changed a person's access or the tenant's SSO, and why. `actor_id` is None when the tenant's
    identity provider made the change at a sign-in (a person created or roles synced from groups)."""

    change_id: UUID
    tenant_id: UUID
    kind: AccessChangeKind
    at: datetime
    user_id: UUID | None = None
    actor_id: UUID | None = None
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class SsoProtocol(StrEnum):
    OIDC = "oidc"
    SAML = "saml"


@dataclass(frozen=True, slots=True)
class RoleMapping:
    """People in `group` (a value of the groups claim or attribute) get `role` on the whole tenant."""

    group: str
    role: Role


@dataclass(frozen=True, slots=True)
class OidcSettings:
    issuer: str
    client_id: str
    client_secret: bytes
    """Sealed with the control's secret key; never returned by the API."""
    scopes: tuple[str, ...] = ("openid", "email", "profile")


@dataclass(frozen=True, slots=True)
class SamlSettings:
    idp_entity_id: str
    idp_sso_url: str
    idp_certificate: str
    """PEM of the certificate that signs the identity provider's assertions."""


@dataclass(frozen=True, slots=True)
class SsoConnection:
    """The tenant's single sign-on: one connection per tenant, OIDC or SAML (5.22)."""

    connection_id: UUID
    tenant_id: UUID
    protocol: SsoProtocol
    domains: tuple[str, ...]
    enabled: bool
    created_at: datetime
    updated_at: datetime
    updated_by: UUID
    default_role: Role | None = None
    role_mappings: tuple[RoleMapping, ...] = ()
    groups_attribute: str = "groups"
    require_second_factor: bool = False
    """Ask for the Niadra authenticator code after the identity provider, on top of its own MFA."""
    enforced: bool = False
    """People of the domains sign in only through SSO; a tenant-wide admin keeps the password as a way
    in when the identity provider fails."""
    oidc: OidcSettings | None = None
    saml: SamlSettings | None = None


class SsoPurpose(StrEnum):
    LOGIN = "login"
    TEST = "test"


class SsoAttemptStatus(StrEnum):
    PENDING = "pending"
    """Sent to the identity provider, waiting for its answer."""
    SIGNED_IN = "signed_in"
    """The provider answered well; the sign-in code waits for the Console to exchange it."""
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SsoAttempt:
    """One trip to the identity provider. `state_hash`, `binding_hash` and `login_code_hash` are SHA-256
    of values only the browser holds; the PKCE verifier and the nonce live here for ten minutes."""

    attempt_id: UUID
    tenant_id: UUID
    connection_id: UUID
    purpose: SsoPurpose
    state_hash: bytes
    nonce: str
    code_verifier: str
    callback_url: str
    created_at: datetime
    expires_at: datetime
    status: SsoAttemptStatus = SsoAttemptStatus.PENDING
    binding_hash: bytes | None = None
    return_origin: str | None = None
    saml_request_id: str | None = None
    requested_by: UUID | None = None
    user_id: UUID | None = None
    login_code_hash: bytes | None = None
    code_expires_at: datetime | None = None
    code_failures: int = 0
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RoleBinding:
    """A role for a person, on every space of the tenant (`space_id` None) or on one.
    Vendor bindings name the sources the person may see (5.22)."""

    binding_id: UUID
    user_id: UUID
    tenant_id: UUID
    role: Role
    space_id: UUID | None = None
    source_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class ConfigDiff:
    """A proposed change of one configuration document of a space (4.7)."""

    diff_id: UUID
    space_id: UUID
    doc_type: str
    document: Any
    reason: str
    status: DiffStatus
    base_version: int
    created_at: datetime
    author_id: UUID | None = None
    author_label: str | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    applied_version: int | None = None
    rollback_of: UUID | None = None


@dataclass(frozen=True, slots=True)
class ConfigVersion:
    space_id: UUID
    version: int
    doc_type: str
    document: Any
    diff_id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One signed snapshot of a cell. The manifest and the key list are signed separately so a cell
    can verify each with the same public key (4.7)."""

    cell_id: str
    version: int
    manifest: dict[str, Any]
    signature: bytes
    kid: str
    documents: dict[str, Any]
    keys: dict[str, Any]
    keys_signature: bytes
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SigningKey:
    kid: str
    algorithm: str
    public_jwk: dict[str, str]
    created_at: datetime
    retired_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SecretRef:
    """Metadata of a secret that lives in a cell vault; the value never reaches the control (4.7)."""

    space_id: UUID
    ref: str
    kind: str
    version: int
    rotated_by: UUID
    rotated_at: datetime


@dataclass(frozen=True, slots=True)
class UsageAggregate:
    space_id: UUID
    period_start: datetime
    metric: str
    quantity: int
    cell_id: str
    reported_at: datetime
    cost_micros: int = 0


@dataclass(frozen=True, slots=True)
class Caller:
    """A Console person authenticated by a control-issued JWT."""

    user_id: UUID
    tenant_id: UUID
    space_id: UUID
    roles: frozenset[Role]
    source_ids: frozenset[UUID] | None = None

    def has(self, *roles: Role) -> bool:
        return Role.ADMIN in self.roles or bool(self.roles & set(roles))
