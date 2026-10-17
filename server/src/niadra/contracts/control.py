"""Public models of the control plane API (7.4), used by the Console, the CLI and the cells."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import Model, ShortStr
from niadra.domain.control.records import (
    AccessChangeKind,
    CellStatus,
    DiffStatus,
    KeyStatus,
    MoveStatus,
    Role,
)
from niadra.domain.vocabulary import Audience, Environment, Scope, Verification

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
Password = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
TotpCode = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]


class LoginRequest(Model):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    totp: str | None = Field(default=None, pattern=r"^\d{6}$")
    recovery_code: str | None = Field(
        default=None,
        min_length=10,
        max_length=32,
        description="One of the person's recovery codes, in place of `totp` when the authenticator app is"
        " lost. Case, spaces and dashes do not matter. Each code works once.",
    )
    space_id: UUID | None = None

    @model_validator(mode="after")
    def _one_second_factor(self) -> "LoginRequest":
        if self.totp is not None and self.recovery_code is not None:
            raise ValueError("send `totp` or `recovery_code`, not both")
        return self


class SecondFactorRequest(Model):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class SecondFactorOut(Model):
    secret: str = Field(description="Base32 TOTP secret, for typing into an authenticator app.")
    totp_uri: str = Field(description="otpauth:// address of the same secret, for a QR code.")


class PasswordRequest(Model):
    password: Password | None = Field(
        default=None,
        description="The person's password once more: a session alone does not change the second factor.",
    )
    totp: TotpCode | None = Field(
        default=None,
        description="In place of the password, only for a person the tenant's SSO created, who has none: the"
        " current code of the authenticator app.",
    )

    @model_validator(mode="after")
    def _one_proof(self) -> "PasswordRequest":
        if (self.password is None) == (self.totp is None):
            raise ValueError("send `password` or `totp`")
        return self


class SecondFactorConfirmRequest(Model):
    totp: TotpCode = Field(description="The current code of the new authenticator app.")


class SecondFactorStatusOut(Model):
    confirmed_at: datetime | None = Field(
        description="When the authenticator in use was confirmed; null while there is none."
    )
    recovery_codes_left: int = Field(description="Recovery codes not used yet.")


class RecoveryCodesOut(Model):
    recovery_codes: list[str] = Field(
        description="Ten single-use codes, `XXXXX-XXXXX`. Shown once; Niadra keeps only a keyed hash of"
        " each, and the earlier codes of the person no longer work."
    )


class SecondFactorResetRequest(Model):
    reason: Reason = Field(description="Why, kept with the reset.")


class SecondFactorResetOut(Model):
    reset_id: UUID
    user_id: UUID
    reset_by: UUID = Field(description="The admin who reset it.")
    reason: str
    at: datetime


class TokenRequest(Model):
    space_id: UUID | None = None


class TokenOut(Model):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105
    expires_in: int
    space_id: UUID
    recovery_codes: list[str] | None = Field(
        default=None,
        description="Shown once: the ten recovery codes, when this login confirmed the authenticator."
        " Null otherwise.",
    )
    recovery_codes_left: int | None = Field(
        default=None, description="How many recovery codes remain, when this login used one. Null otherwise."
    )


class TenantCreate(Model):
    name: ShortStr
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
    mfa_required: bool = True


class TenantOut(Model):
    tenant_id: UUID
    name: str
    slug: str
    mfa_required: bool
    created_at: datetime


class ProjectCreate(Model):
    name: ShortStr
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
    region: str = Field(pattern=r"^[a-z]{2}(-[a-z]+)+-\d{1,2}$")


class SpaceOut(Model):
    space_id: UUID
    project_id: UUID
    environment: Environment
    slug: str
    region: str
    cell_id: str


class ProjectOut(Model):
    project_id: UUID
    name: str
    slug: str
    region: str
    spaces: list[SpaceOut]


class CellCreate(Model):
    cell_id: str = Field(pattern=r"^[a-z0-9-]{3,63}$")
    region: str = Field(pattern=r"^[a-z]{2}(-[a-z]+)+-\d{1,2}$")
    address: str = Field(pattern=r"^https://")
    capacity: int = Field(ge=1)


class CellOut(Model):
    cell_id: str
    region: str
    address: str
    capacity: int
    status: CellStatus
    code_version: str | None = None
    schema_revision: str | None = None


class MoveRequest(Model):
    cell_id: str
    reason: Reason


class MoveOut(Model):
    move_id: UUID
    from_cell: str
    to_cell: str
    status: MoveStatus
    requested_at: datetime
    completed_at: datetime | None = None


class PlacementOut(Model):
    space_id: UUID
    cell_id: str
    move: MoveOut | None = None


class DeletionReceiptOut(Model):
    """What the cell deleted, as it reported it. The data keys are destroyed, so any copy of the space's
    data that still exists (the database server's own snapshots, until `instance_snapshot_expiry`) can
    no longer be read."""

    space_id: UUID
    cell_id: str
    db_name: str = Field(description="The space's database, dropped.")
    dropped_at: datetime
    dek_versions_destroyed: list[int] = Field(description="Versions of the space's data keys destroyed.")
    objects_deleted: int = Field(
        description="Stored objects deleted: raw payloads, media, imports, exports, event archives and audit "
        "anchors."
    )
    backups_deleted: int = Field(description="Nightly dumps of the space's database deleted.")
    secrets_deleted: int = Field(description="Secret versions deleted (webhook and export credentials).")
    instance_snapshot_expiry: datetime = Field(
        description="When the last automatic snapshot of the database server that may hold the space's "
        "encrypted data expires."
    )
    receipt_hash: str = Field(description="SHA-256 of the receipt's canonical JSON, in hexadecimal.")
    received_at: datetime


class SpaceDeletionOut(Model):
    space_id: UUID
    deleted_at: datetime
    status: Literal["deleting", "deleted"] = Field(
        description="`deleting` until the cell reports what it deleted; then `deleted`, with the receipt."
    )
    receipt: DeletionReceiptOut | None = None


class DeletionReceiptIn(Model):
    cell_id: str = Field(pattern=r"^[a-z0-9-]{3,63}$")
    receipt: dict[str, Any] = Field(
        description="The receipt as the cell drafted it; `receipt_hash` is the SHA-256 of its canonical JSON."
    )
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SpaceKeyIn(Model):
    """The customer's KMS key of a space, under contract (10, D.1); null returns new data keys to the
    cell's own key."""

    kms_key_arn: Annotated[str, StringConstraints(max_length=256)] | None = None


class SpaceKeyOut(Model):
    space_id: UUID
    kms_key_arn: str | None = None


class QuotaIn(Model):
    tenant_id: UUID
    metric: ShortStr
    limit: int = Field(ge=0)
    period: str = "month"


class SourceCreate(Model):
    space_id: UUID
    name: ShortStr
    audience: Audience
    vendor: ShortStr | None = None
    channel: ShortStr | None = None
    purposes: list[ShortStr] = Field(default_factory=list, max_length=32)
    verification_ceiling: Verification = Verification.V2
    confirms_before_acting: bool = False
    navigation_enabled: bool = True
    trusted_action_ops: list[ShortStr] = Field(default_factory=list, max_length=64)
    critical: bool = False


class SourceOut(Model):
    source_id: UUID
    space_id: UUID
    name: str
    audience: Audience
    vendor: str | None
    channel: str | None
    purposes: list[str]
    verification_ceiling: Verification
    confirms_before_acting: bool
    navigation_enabled: bool
    trusted_action_ops: list[str]
    critical: bool
    revoked_at: datetime | None


class KeyCreate(Model):
    scopes: list[Scope] | None = Field(default=None, description="Defaults follow the source's audience.")


class KeyRotate(Model):
    grace_seconds: int | None = Field(default=None, ge=0, le=7 * 24 * 3600)


class RevokeRequest(Model):
    reason: Reason


class KeyOut(Model):
    key_id: str
    source_id: UUID
    scopes: list[Scope]
    status: KeyStatus
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


class IssuedKeyOut(Model):
    key: KeyOut
    secret: str = Field(description="The full key. Shown once; Niadra keeps only two hashes of it.")


class RevocationOut(Model):
    seq: int
    space_id: UUID
    kind: str
    source_id: UUID
    key_id: str | None
    at: datetime


class BindingIn(Model):
    role: Role
    space_id: UUID | None = None
    source_ids: list[UUID] = Field(default_factory=list)


class UserCreate(Model):
    email: str = Field(min_length=3, max_length=320)
    password: str | None = Field(
        default=None,
        min_length=12,
        max_length=1024,
        description="Optional. Without it the person receives an invitation: a single-use link to choose a"
        " password, then the authenticator at the first sign-in. A person whose e-mail domain belongs to the"
        " tenant's SSO gets no invitation and signs in through the identity provider.",
    )
    roles: list[BindingIn] = Field(min_length=1, max_length=32)
    with_totp: bool = Field(
        default=True,
        description="Only with `password`: creates the authenticator secret now and returns it in `totp_uri`."
        " Without a password the person sets up the authenticator at the first sign-in.",
    )


class RolesSet(Model):
    roles: list[BindingIn] = Field(min_length=1, max_length=32)
    reason: Reason | None = Field(default=None, description="Why, kept in the person's access history.")


class UserStateRequest(Model):
    reason: Reason = Field(description="Why, kept in the person's access history.")


class InvitationOut(Model):
    token: str = Field(
        description="Shown once. The Console turns it into the link `<console>/console/convite#<token>`,"
        " which works once and until `expires_at`. Niadra keeps only a keyed hash of it."
    )
    expires_at: datetime


class InvitationTokenRequest(Model):
    token: str = Field(min_length=20, max_length=128)


class InvitationPreviewOut(Model):
    email: str
    tenant_name: str
    expires_at: datetime


class InvitationAcceptRequest(Model):
    token: str = Field(min_length=20, max_length=128)
    password: str = Field(min_length=12, max_length=1024)


class InvitationAcceptOut(Model):
    email: str = Field(description="The person's e-mail, for the sign-in that follows.")


class AccessChangeOut(Model):
    change_id: UUID
    kind: AccessChangeKind
    at: datetime
    user_id: UUID | None
    actor_id: UUID | None = Field(
        description="Who made the change; null when the tenant's identity provider made it at a sign-in."
    )
    reason: str | None
    detail: dict[str, Any]


class BindingOut(Model):
    role: Role
    space_id: UUID | None
    source_ids: list[UUID]


class UserOut(Model):
    user_id: UUID
    email: str
    active: bool
    roles: list[BindingOut]
    last_login_at: datetime | None = None
    totp_uri: str | None = Field(default=None, description="Shown once, at creation, for enrollment.")
    created_at: datetime
    deactivated_at: datetime | None = None
    password_set: bool = Field(description="False while an invited person has not chosen a password.")
    sso: bool = Field(description="The e-mail domain belongs to the tenant's SSO connection.")
    last_sso_login_at: datetime | None = None
    second_factor_confirmed_at: datetime | None = Field(
        default=None, description="When the authenticator in use was confirmed; null while there is none."
    )
    recovery_codes_left: int = 0
    invitation_expires_at: datetime | None = Field(
        default=None,
        description="Until when the person's open invitation link works; null when none is open.",
    )
    invitation: InvitationOut | None = Field(
        default=None, description="Shown once: at creation, for a person created without a password."
    )


class SpaceRoles(Model):
    space_id: UUID
    project_id: UUID
    environment: Environment
    slug: str
    region: str
    roles: list[Role]


class PreferenceIn(Model):
    value: Any = Field(description="Any JSON value but null, at most 4096 bytes once serialized.")


class PreferencesOut(Model):
    """What the Console keeps for the signed-in person across browsers, by key."""

    preferences: dict[str, Any]


class MeOut(Model):
    user_id: UUID
    tenant_id: UUID
    email: str
    name: str | None = Field(default=None, description="Not collected yet; reserved for SSO profiles.")
    password_set: bool = Field(
        default=True, description="False for a person the tenant's SSO created: they sign in only through it."
    )
    sso: bool = Field(default=False, description="The e-mail domain belongs to the tenant's SSO connection.")
    spaces: list[SpaceRoles] = Field(
        description="Every space the person can open, with the roles held there."
    )


class ConfigTypeOut(Model):
    type: str
    description: str
    roles: list[Role] = Field(description="Roles that may write and approve it, besides admin.")


class RoleOut(Model):
    role: Role
    description: str


class DocumentOut(Model):
    space_id: UUID
    type: str
    version: int
    document: Any


class DiffCreate(Model):
    space_id: UUID
    type: str
    document: Any
    reason: Reason


class ProposalCreate(DiffCreate):
    cell_id: str


class RollbackRequest(Model):
    reason: Reason


class DiffOut(Model):
    diff_id: UUID
    space_id: UUID
    type: str
    status: DiffStatus
    reason: str
    base_version: int
    author_id: UUID | None
    author_label: str | None = Field(
        description="Who proposed it, to show: the person's e-mail, or the label of an author that is not "
        "a person (`assistant@<cell>`, `niadra:observed`). Null only for a person who no longer exists."
    )
    decided_by: UUID | None
    decided_by_label: str | None = Field(
        default=None, description="The e-mail of the person who approved or rejected it."
    )
    decided_at: datetime | None
    applied_version: int | None
    rollback_of: UUID | None
    created_at: datetime
    document: Any


class VersionOut(Model):
    version: int
    type: str
    diff_id: UUID
    created_at: datetime
    document: Any


class SnapshotOut(Model):
    """`signature` covers the canonical JSON of `manifest`; `keys_signature` that of `keys` (both Ed25519,
    base64url). Each document's SHA-256 over its canonical JSON must match the manifest (4.7)."""

    manifest: dict[str, Any]
    signature: str
    kid: str
    documents: dict[str, Any]
    keys: dict[str, Any]
    keys_signature: str


class RevocationFeedOut(Model):
    """`body` is `{"cell_id", "seq", "keys": [{"space_id", "key_id"}], "sources": [...]}`, the cell's
    `RevocationFeed`; `signature` (Ed25519, base64url) covers its canonical JSON. Pass `after=seq` next."""

    body: dict[str, Any]
    kid: str
    signature: str


class UsageItem(Model):
    space_id: UUID
    period_start: datetime
    metric: ShortStr
    quantity: int = Field(ge=0)
    cost_micros: int = Field(default=0, ge=0)


class UsagePush(Model):
    cell_id: str
    items: list[UsageItem] = Field(max_length=5000)


class UsageOut(Model):
    items: list[UsageItem]
    quotas: list[QuotaIn]


class UsageStatementLine(Model):
    space_id: UUID
    project_id: UUID
    environment: str
    slug: str
    billable: int = Field(description="Conversations and tasks of the month, the unit the price is set per.")
    ai_cost_micros: int = Field(
        description="What the space's AI calls cost Niadra, in millionths of a dollar."
    )
    metrics: dict[str, int] = Field(description="Every metric of the month, by name.")


class UsageStatementOut(Model):
    month: str
    lines: list[UsageStatementLine] = Field(
        description="One line per space of the tenant that existed in the month, with or without usage."
    )


class SecretRefIn(Model):
    space_id: UUID
    ref: ShortStr
    kind: ShortStr
    version: int = Field(ge=1)


class SecretRefOut(SecretRefIn):
    rotated_by: UUID
    rotated_at: datetime
