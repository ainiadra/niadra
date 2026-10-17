"""Control tables: directory, access, configuration, distribution and usage."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.control_schema.base import Base


class TenantRow(Base):
    __tablename__ = "tenants"
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str]
    slug: Mapped[str] = mapped_column(unique=True)
    mfa_required: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime]


class ProjectRow(Base):
    __tablename__ = "projects"
    project_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"))
    name: Mapped[str]
    slug: Mapped[str]
    region: Mapped[str]
    created_at: Mapped[datetime]
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)


class CellRow(Base):
    __tablename__ = "cells"
    cell_id: Mapped[str] = mapped_column(primary_key=True)
    region: Mapped[str]
    address: Mapped[str]
    capacity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str]
    code_version: Mapped[str | None]
    schema_revision: Mapped[str | None]
    created_at: Mapped[datetime]
    # A cell dedicated to one tenant (plan Regulado), and the SHA-256 of its own credential.
    tenant_id: Mapped[UUID | None] = mapped_column(ForeignKey("tenants.tenant_id"))
    token_hash: Mapped[bytes | None] = mapped_column(unique=True)
    __table_args__ = (CheckConstraint("status IN ('active', 'draining', 'closed')", name="status"),)


class SpaceRow(Base):
    __tablename__ = "spaces"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"), index=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.project_id"))
    environment: Mapped[str]
    slug: Mapped[str]
    region: Mapped[str]
    cell_id: Mapped[str] = mapped_column(ForeignKey("cells.cell_id"), index=True)
    created_at: Mapped[datetime]
    # Set when an admin deletes the space: it leaves the snapshot's spaces for its `removed` list, and
    # the cell drops its database and sends back a deletion receipt.
    deleted_at: Mapped[datetime | None]
    kms_key_arn: Mapped[str | None]
    # Where the cell keeps the space's database: the instance by name, and the
    # tier the cell creates it with (t2 on a cell dedicated to the tenant).
    tier: Mapped[str] = mapped_column(server_default="t0")
    instance: Mapped[str] = mapped_column(server_default="primary")
    __table_args__ = (
        UniqueConstraint("region", "slug"),
        UniqueConstraint("project_id", "environment"),
        CheckConstraint("environment IN ('sandbox', 'production')", name="environment"),
        CheckConstraint("tier IN ('t0', 't1', 't2')", name="tier"),
    )


class DeletionReceiptRow(Base):
    """What the cell reports after dropping a deleted space's database (the Console shows it)."""

    __tablename__ = "deletion_receipts"
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.space_id"), primary_key=True)
    cell_id: Mapped[str]
    receipt: Mapped[dict[str, Any]]
    receipt_hash: Mapped[bytes]
    received_at: Mapped[datetime]


class PlacementMoveRow(Base):
    __tablename__ = "placement_moves"
    move_id: Mapped[UUID] = mapped_column(primary_key=True)
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.space_id"))
    from_cell: Mapped[str] = mapped_column(ForeignKey("cells.cell_id"))
    to_cell: Mapped[str] = mapped_column(ForeignKey("cells.cell_id"))
    status: Mapped[str]
    reason: Mapped[str]
    requested_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("status IN ('copying', 'completed')", name="status"),
        # At most one move in flight per space.
        Index(
            "ix_placement_moves_open", "space_id", unique=True, postgresql_where=text("status = 'copying'")
        ),
    )


class QuotaRow(Base):
    __tablename__ = "quotas"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"), primary_key=True)
    metric: Mapped[str] = mapped_column(primary_key=True)
    limit: Mapped[int] = mapped_column(BigInteger)
    period: Mapped[str]


class SourceRow(Base):
    __tablename__ = "sources"
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.space_id"), index=True)
    name: Mapped[str]
    vendor: Mapped[str | None]
    channel: Mapped[str | None]
    purposes: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    audience: Mapped[str]
    verification_ceiling: Mapped[str]
    confirms_before_acting: Mapped[bool] = mapped_column(server_default=text("false"))
    navigation_enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    trusted_action_ops: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    critical: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]


class ApiKeyRow(Base):
    """Two hashes and never the secret (5.1)."""

    __tablename__ = "api_keys"
    key_id: Mapped[str] = mapped_column(primary_key=True)
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.space_id"), index=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.source_id"), index=True)
    scopes: Mapped[list[str]]
    fast_hash: Mapped[bytes] = mapped_column(unique=True)
    slow_hash: Mapped[str]
    status: Mapped[str]
    version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    __table_args__ = (CheckConstraint("status IN ('active', 'revoked')", name="status"),)


class RevocationRow(Base):
    """The revocation feed: cells pull it by `seq` every 2 s (4.7)."""

    __tablename__ = "revocations"
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.space_id"))
    kind: Mapped[str]
    source_id: Mapped[UUID]
    key_id: Mapped[str | None]
    reason: Mapped[str]
    at: Mapped[datetime]
    __table_args__ = (
        CheckConstraint("kind IN ('key', 'source')", name="kind"),
        Index(None, "space_id", "seq"),
    )


class UserRow(Base):
    """Console people: operator identity, not end-customer data (5.16)."""

    __tablename__ = "users"
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"), index=True)
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    totp_secret: Mapped[str | None]
    active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime]
    last_login_at: Mapped[datetime | None]
    totp_confirmed_at: Mapped[datetime | None]
    totp_pending_secret: Mapped[str | None]
    deactivated_at: Mapped[datetime | None]
    last_sso_login_at: Mapped[datetime | None]
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)


class RecoveryCodeRow(Base):
    """A keyed hash of each single-use code, never the code (5.22)."""

    __tablename__ = "recovery_codes"
    code_hash: Mapped[bytes] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class UserPreferenceRow(Base):
    """What the Console remembers for a person across browsers (tutorial progress, the welcome): a few
    small JSON values by key, never customer data."""

    __tablename__ = "user_preferences"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[Any]


class SecondFactorResetRow(Base):
    """Every admin reset of a person's second factor, with who did it and why."""

    __tablename__ = "second_factor_resets"
    reset_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"), index=True)
    reset_by: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"))
    reason: Mapped[str]
    at: Mapped[datetime]


class InvitationRow(Base):
    """A keyed hash of each single-use link to choose a password, never the token (5.22)."""

    __tablename__ = "invitations"
    invitation_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[bytes] = mapped_column(unique=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class AccessChangeRow(Base):
    """Who changed a person's access or the tenant's SSO, and why: kept for the tenant's history."""

    __tablename__ = "access_changes"
    change_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"))
    kind: Mapped[str]
    at: Mapped[datetime]
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.user_id"))
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.user_id"))
    reason: Mapped[str | None]
    detail: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    __table_args__ = (
        CheckConstraint(
            "kind IN ('invited', 'invitation_accepted', 'roles_changed', 'deactivated', 'reactivated',"
            " 'second_factor_reset', 'sso_person_created', 'sso_roles_synced', 'sso_saved', 'sso_removed')",
            name="kind",
        ),
        Index(None, "tenant_id", "at"),
        Index(None, "user_id", "at"),
    )


class SsoConnectionRow(Base):
    """The tenant's single sign-on, OIDC or SAML. The OIDC client secret is sealed (5.22)."""

    __tablename__ = "sso_connections"
    connection_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"), unique=True)
    protocol: Mapped[str]
    enabled: Mapped[bool]
    enforced: Mapped[bool] = mapped_column(server_default=text("false"))
    default_role: Mapped[str | None]
    role_mappings: Mapped[Any] = mapped_column(server_default=text("'[]'::jsonb"))
    groups_attribute: Mapped[str]
    require_second_factor: Mapped[bool] = mapped_column(server_default=text("false"))
    oidc_issuer: Mapped[str | None]
    oidc_client_id: Mapped[str | None]
    oidc_client_secret: Mapped[bytes | None]
    oidc_scopes: Mapped[list[str] | None]
    saml_idp_entity_id: Mapped[str | None]
    saml_idp_sso_url: Mapped[str | None]
    saml_idp_certificate: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"))
    __table_args__ = (
        CheckConstraint("protocol IN ('oidc', 'saml')", name="protocol"),
        CheckConstraint(
            "default_role IS NULL"
            " OR default_role IN ('admin', 'security', 'integration', 'review', 'analysis')",
            name="default_role",
        ),
        CheckConstraint(
            "protocol <> 'oidc' OR (oidc_issuer IS NOT NULL AND oidc_client_id IS NOT NULL"
            " AND oidc_client_secret IS NOT NULL)",
            name="oidc",
        ),
        CheckConstraint(
            "protocol <> 'saml' OR (saml_idp_entity_id IS NOT NULL AND saml_idp_sso_url IS NOT NULL"
            " AND saml_idp_certificate IS NOT NULL)",
            name="saml",
        ),
    )


class SsoDomainRow(Base):
    """An e-mail domain belongs to one tenant's connection at most: the primary key says so."""

    __tablename__ = "sso_domains"
    domain: Mapped[str] = mapped_column(primary_key=True)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("sso_connections.connection_id", ondelete="CASCADE"), index=True
    )


class SsoAttemptRow(Base):
    """One trip to the identity provider, kept for minutes: hashes of what the browser holds, the PKCE
    verifier and the nonce, and the sign-in code's hash once the provider answered."""

    __tablename__ = "sso_attempts"
    attempt_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"))
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("sso_connections.connection_id", ondelete="CASCADE")
    )
    purpose: Mapped[str]
    state_hash: Mapped[bytes] = mapped_column(unique=True)
    nonce: Mapped[str]
    code_verifier: Mapped[str]
    callback_url: Mapped[str]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str]
    binding_hash: Mapped[bytes | None]
    return_origin: Mapped[str | None]
    saml_request_id: Mapped[str | None]
    requested_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.user_id"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.user_id"))
    login_code_hash: Mapped[bytes | None] = mapped_column(unique=True)
    code_expires_at: Mapped[datetime | None]
    code_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    completed_at: Mapped[datetime | None]
    result: Mapped[dict[str, Any] | None]
    __table_args__ = (
        CheckConstraint("purpose IN ('login', 'test')", name="purpose"),
        CheckConstraint("status IN ('pending', 'signed_in', 'completed', 'failed')", name="status"),
        Index(None, "connection_id", "status", "created_at"),
    )


class RoleBindingRow(Base):
    __tablename__ = "role_bindings"
    binding_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.tenant_id"))
    role: Mapped[str]
    space_id: Mapped[UUID | None] = mapped_column(ForeignKey("spaces.space_id"))
    source_ids: Mapped[list[UUID]] = mapped_column(server_default=text("'{}'"))
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'security', 'integration', 'review', 'analysis', 'vendor')", name="role"
        ),
    )


class ConfigDiffRow(Base):
    __tablename__ = "config_diffs"
    diff_id: Mapped[UUID] = mapped_column(primary_key=True)
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.space_id"))
    doc_type: Mapped[str]
    document: Mapped[Any]
    reason: Mapped[str]
    status: Mapped[str]
    base_version: Mapped[int] = mapped_column(BigInteger)
    author_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.user_id"))
    author_label: Mapped[str | None]
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.user_id"))
    decided_at: Mapped[datetime | None]
    applied_version: Mapped[int | None] = mapped_column(BigInteger)
    rollback_of: Mapped[UUID | None]
    created_at: Mapped[datetime]
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'proposed', 'applied', 'rejected')", name="status"),
        CheckConstraint("author_id IS NOT NULL OR author_label IS NOT NULL", name="author"),
        Index(None, "space_id", "status"),
    )


class ConfigVersionRow(Base):
    """One applied diff per version; the current configuration is the newest version of each type."""

    __tablename__ = "config_versions"
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.space_id"), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_type: Mapped[str]
    document: Mapped[Any]
    diff_id: Mapped[UUID] = mapped_column(ForeignKey("config_diffs.diff_id"))
    created_at: Mapped[datetime]
    __table_args__ = (Index(None, "space_id", "doc_type", "version"),)


class SnapshotRow(Base):
    __tablename__ = "snapshots"
    cell_id: Mapped[str] = mapped_column(ForeignKey("cells.cell_id"), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    manifest: Mapped[dict[str, Any]]
    signature: Mapped[bytes]
    kid: Mapped[str]
    documents: Mapped[dict[str, Any]]
    keys: Mapped[dict[str, Any]]
    keys_signature: Mapped[bytes]
    created_at: Mapped[datetime]


class SigningKeyRow(Base):
    """Public halves only; the private key lives in a file or a secret manager."""

    __tablename__ = "signing_keys"
    kid: Mapped[str] = mapped_column(primary_key=True)
    algorithm: Mapped[str]
    public_jwk: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
    retired_at: Mapped[datetime | None]


class SecretRefRow(Base):
    __tablename__ = "secret_refs"
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.space_id"), primary_key=True)
    ref: Mapped[str] = mapped_column(primary_key=True)
    kind: Mapped[str]
    version: Mapped[int] = mapped_column(Integer)
    rotated_by: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"))
    rotated_at: Mapped[datetime]


class UsageAggregateRow(Base):
    __tablename__ = "usage_aggregates"
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.space_id"), primary_key=True)
    period_start: Mapped[datetime] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(primary_key=True)
    quantity: Mapped[int] = mapped_column(BigInteger)
    cost_micros: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    cell_id: Mapped[str]
    reported_at: Mapped[datetime]
