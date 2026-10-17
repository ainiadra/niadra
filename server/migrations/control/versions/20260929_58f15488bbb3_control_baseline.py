"""control: baseline

Everything the control plane's database holds, from an empty database. It replaces the history that led
here; every later revision adds to it.

Revision ID: 58f15488bbb3
Revises:
Create Date: 2026-09-29 21:34:51.886113
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "58f15488bbb3"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signing_keys",
        sa.Column("kid", sa.Text(), nullable=False),
        sa.Column("algorithm", sa.Text(), nullable=False),
        sa.Column("public_jwk", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("kid", name=op.f("pk_signing_keys")),
    )
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("mfa_required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.create_table(
        "cells",
        sa.Column("cell_id", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=True),
        sa.Column("schema_revision", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("token_hash", sa.LargeBinary(), nullable=True),
        sa.CheckConstraint("status IN ('active', 'draining', 'closed')", name=op.f("ck_cells_status")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_cells_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("cell_id", name=op.f("pk_cells")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_cells_token_hash")),
    )
    op.create_table(
        "projects",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payer_tenant_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["payer_tenant_id"], ["tenants.tenant_id"], name=op.f("fk_projects_payer_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_projects_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_projects")),
        sa.UniqueConstraint("tenant_id", "slug", name=op.f("uq_projects_tenant_id_slug")),
    )
    op.create_table(
        "quotas",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("limit", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_quotas_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", "metric", name=op.f("pk_quotas")),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("totp_secret", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_pending_secret", sa.Text(), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sso_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_users_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"], unique=False)
    op.create_table(
        "access_changes",
        sa.Column("change_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('invited', 'invitation_accepted', 'roles_changed', 'deactivated', 'reactivated', 'second_factor_reset', 'sso_person_created', 'sso_roles_synced', 'sso_saved', 'sso_removed', 'approval_decided', 'emergency_access', 'emergency_access_ended', 'party_changed', 'project_transferred')",
            name=op.f("ck_access_changes_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.user_id"], name=op.f("fk_access_changes_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_access_changes_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], name=op.f("fk_access_changes_user_id_users")),
        sa.PrimaryKeyConstraint("change_id", name=op.f("pk_access_changes")),
    )
    op.create_index(
        op.f("ix_access_changes_tenant_id_at"), "access_changes", ["tenant_id", "at"], unique=False
    )
    op.create_index(op.f("ix_access_changes_user_id_at"), "access_changes", ["user_id", "at"], unique=False)
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject_ref", sa.Text(), nullable=False),
        sa.Column("document_hash", sa.LargeBinary(), nullable=False),
        sa.Column("approver_email_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(), nullable=True),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code_failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("codes_sent", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('purposes', 'policy_loosening', 'retention', 'operators', 'transfer', 'access_review', 'emergency_access', 'ripd')",
            name=op.f("ck_approvals_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')", name=op.f("ck_approvals_status")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.project_id"], name=op.f("fk_approvals_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.user_id"], name=op.f("fk_approvals_requested_by_users")
        ),
        sa.PrimaryKeyConstraint("approval_id", name=op.f("pk_approvals")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_approvals_token_hash")),
    )
    op.create_index(op.f("ix_approvals_kind_status"), "approvals", ["kind", "status"], unique=False)
    op.create_index(
        op.f("ix_approvals_project_id_status"), "approvals", ["project_id", "status"], unique=False
    )
    op.create_table(
        "invitations",
        sa.Column("invitation_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.user_id"], name=op.f("fk_invitations_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_invitations_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name=op.f("fk_invitations_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("invitation_id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invitations_token_hash")),
    )
    op.create_index(op.f("ix_invitations_user_id"), "invitations", ["user_id"], unique=False)
    op.create_table(
        "legal_entities",
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=False),
        sa.Column("registry_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("registry_masked", sa.Text(), nullable=False),
        sa.Column("dpo_contact_ct", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("country ~ '^[A-Z]{2}$'", name=op.f("ck_legal_entities_country")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.user_id"], name=op.f("fk_legal_entities_created_by_users")
        ),
        sa.PrimaryKeyConstraint("entity_id", name=op.f("pk_legal_entities")),
        sa.UniqueConstraint("country", "registry_hmac", name=op.f("uq_legal_entities_country_registry_hmac")),
    )
    op.create_table(
        "recovery_codes",
        sa.Column("code_hash", sa.LargeBinary(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name=op.f("fk_recovery_codes_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("code_hash", name=op.f("pk_recovery_codes")),
    )
    op.create_index(op.f("ix_recovery_codes_user_id"), "recovery_codes", ["user_id"], unique=False)
    op.create_table(
        "second_factor_resets",
        sa.Column("reset_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("reset_by", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reset_by"], ["users.user_id"], name=op.f("fk_second_factor_resets_reset_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_second_factor_resets_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name=op.f("fk_second_factor_resets_user_id_users")
        ),
        sa.PrimaryKeyConstraint("reset_id", name=op.f("pk_second_factor_resets")),
    )
    op.create_index(
        op.f("ix_second_factor_resets_user_id"), "second_factor_resets", ["user_id"], unique=False
    )
    op.create_table(
        "snapshots",
        sa.Column("cell_id", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column("kid", sa.Text(), nullable=False),
        sa.Column("documents", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("keys_signature", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cell_id"], ["cells.cell_id"], name=op.f("fk_snapshots_cell_id_cells")),
        sa.PrimaryKeyConstraint("cell_id", "version", name=op.f("pk_snapshots")),
    )
    op.create_table(
        "spaces",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("cell_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kms_key_arn", sa.Text(), nullable=True),
        sa.Column("tier", sa.Text(), server_default="t0", nullable=False),
        sa.Column("instance", sa.Text(), server_default="primary", nullable=False),
        sa.CheckConstraint("environment IN ('sandbox', 'production')", name=op.f("ck_spaces_environment")),
        sa.CheckConstraint("tier IN ('t0', 't1', 't2')", name=op.f("ck_spaces_tier")),
        sa.ForeignKeyConstraint(["cell_id"], ["cells.cell_id"], name=op.f("fk_spaces_cell_id_cells")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.project_id"], name=op.f("fk_spaces_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_spaces_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("space_id", name=op.f("pk_spaces")),
        sa.UniqueConstraint("project_id", "environment", name=op.f("uq_spaces_project_id_environment")),
        sa.UniqueConstraint("region", "slug", name=op.f("uq_spaces_region_slug")),
    )
    op.create_index(op.f("ix_spaces_cell_id"), "spaces", ["cell_id"], unique=False)
    op.create_index(op.f("ix_spaces_tenant_id"), "spaces", ["tenant_id"], unique=False)
    op.create_table(
        "sso_connections",
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("protocol", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("enforced", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("default_role", sa.Text(), nullable=True),
        sa.Column(
            "role_mappings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("groups_attribute", sa.Text(), nullable=False),
        sa.Column("require_second_factor", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("oidc_issuer", sa.Text(), nullable=True),
        sa.Column("oidc_client_id", sa.Text(), nullable=True),
        sa.Column("oidc_client_secret", sa.LargeBinary(), nullable=True),
        sa.Column("oidc_scopes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("saml_idp_entity_id", sa.Text(), nullable=True),
        sa.Column("saml_idp_sso_url", sa.Text(), nullable=True),
        sa.Column("saml_idp_certificate", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "default_role IS NULL OR default_role IN ('admin', 'security', 'integration', 'review', 'analysis')",
            name=op.f("ck_sso_connections_default_role"),
        ),
        sa.CheckConstraint(
            "protocol <> 'oidc' OR (oidc_issuer IS NOT NULL AND oidc_client_id IS NOT NULL AND oidc_client_secret IS NOT NULL)",
            name=op.f("ck_sso_connections_oidc"),
        ),
        sa.CheckConstraint(
            "protocol <> 'saml' OR (saml_idp_entity_id IS NOT NULL AND saml_idp_sso_url IS NOT NULL AND saml_idp_certificate IS NOT NULL)",
            name=op.f("ck_sso_connections_saml"),
        ),
        sa.CheckConstraint("protocol IN ('oidc', 'saml')", name=op.f("ck_sso_connections_protocol")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_sso_connections_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.user_id"], name=op.f("fk_sso_connections_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("connection_id", name=op.f("pk_sso_connections")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_sso_connections_tenant_id")),
    )
    op.create_table(
        "config_diffs",
        sa.Column("diff_id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("base_version", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("author_label", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_version", sa.BigInteger(), nullable=True),
        sa.Column("rollback_of", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'proposed', 'applied', 'rejected')", name=op.f("ck_config_diffs_status")
        ),
        sa.CheckConstraint(
            "author_id IS NOT NULL OR author_label IS NOT NULL", name=op.f("ck_config_diffs_author")
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.user_id"], name=op.f("fk_config_diffs_author_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.user_id"], name=op.f("fk_config_diffs_decided_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.space_id"], name=op.f("fk_config_diffs_space_id_spaces")
        ),
        sa.PrimaryKeyConstraint("diff_id", name=op.f("pk_config_diffs")),
    )
    op.create_index(
        op.f("ix_config_diffs_space_id_status"), "config_diffs", ["space_id", "status"], unique=False
    )
    op.create_table(
        "deletion_receipts",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("cell_id", sa.Text(), nullable=False),
        sa.Column("receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("receipt_hash", sa.LargeBinary(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.space_id"], name=op.f("fk_deletion_receipts_space_id_spaces")
        ),
        sa.PrimaryKeyConstraint("space_id", name=op.f("pk_deletion_receipts")),
    )
    op.create_table(
        "placement_moves",
        sa.Column("move_id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("from_cell", sa.Text(), nullable=False),
        sa.Column("to_cell", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('copying', 'completed')", name=op.f("ck_placement_moves_status")),
        sa.ForeignKeyConstraint(
            ["from_cell"], ["cells.cell_id"], name=op.f("fk_placement_moves_from_cell_cells")
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.space_id"], name=op.f("fk_placement_moves_space_id_spaces")
        ),
        sa.ForeignKeyConstraint(
            ["to_cell"], ["cells.cell_id"], name=op.f("fk_placement_moves_to_cell_cells")
        ),
        sa.PrimaryKeyConstraint("move_id", name=op.f("pk_placement_moves")),
    )
    op.create_index(
        "ix_placement_moves_open",
        "placement_moves",
        ["space_id"],
        unique=True,
        postgresql_where=sa.text("status = 'copying'"),
    )
    op.create_table(
        "project_parties",
        sa.Column("party_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "role IN ('controller', 'operator', 'sub_operator')", name=op.f("ck_project_parties_role")
        ),
        sa.CheckConstraint("status IN ('active', 'ended')", name=op.f("ck_project_parties_status")),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["legal_entities.entity_id"],
            name=op.f("fk_project_parties_entity_id_legal_entities"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.project_id"], name=op.f("fk_project_parties_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_project_parties_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("party_id", name=op.f("pk_project_parties")),
    )
    op.create_index(
        "ix_project_parties_controller",
        "project_parties",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("role = 'controller' AND status = 'active'"),
    )
    op.create_index(op.f("ix_project_parties_entity_id"), "project_parties", ["entity_id"], unique=False)
    op.create_index(
        op.f("ix_project_parties_project_id_role"), "project_parties", ["project_id", "role"], unique=False
    )
    op.create_table(
        "project_transfers",
        sa.Column("transfer_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("from_tenant_id", sa.UUID(), nullable=False),
        sa.Column("to_tenant_id", sa.UUID(), nullable=False),
        sa.Column("approval_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'cancelled')", name=op.f("ck_project_transfers_status")
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.approval_id"],
            name=op.f("fk_project_transfers_approval_id_approvals"),
        ),
        sa.ForeignKeyConstraint(
            ["from_tenant_id"],
            ["tenants.tenant_id"],
            name=op.f("fk_project_transfers_from_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.project_id"], name=op.f("fk_project_transfers_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.user_id"], name=op.f("fk_project_transfers_requested_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["to_tenant_id"], ["tenants.tenant_id"], name=op.f("fk_project_transfers_to_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("transfer_id", name=op.f("pk_project_transfers")),
    )
    op.create_index(
        op.f("ix_project_transfers_project_id"), "project_transfers", ["project_id"], unique=False
    )
    op.create_table(
        "revocations",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('key', 'source')", name=op.f("ck_revocations_kind")),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.space_id"], name=op.f("fk_revocations_space_id_spaces")
        ),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_revocations")),
    )
    op.create_index(op.f("ix_revocations_space_id_seq"), "revocations", ["space_id", "seq"], unique=False)
    op.create_table(
        "role_bindings",
        sa.Column("binding_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=True),
        sa.Column("source_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False),
        sa.CheckConstraint(
            "role IN ('admin', 'security', 'integration', 'review', 'analysis', 'vendor')",
            name=op.f("ck_role_bindings_role"),
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.space_id"], name=op.f("fk_role_bindings_space_id_spaces")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_role_bindings_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name=op.f("fk_role_bindings_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("binding_id", name=op.f("pk_role_bindings")),
    )
    op.create_index(op.f("ix_role_bindings_user_id"), "role_bindings", ["user_id"], unique=False)
    op.create_table(
        "secret_refs",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rotated_by", sa.UUID(), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rotated_by"], ["users.user_id"], name=op.f("fk_secret_refs_rotated_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.space_id"], name=op.f("fk_secret_refs_space_id_spaces")
        ),
        sa.PrimaryKeyConstraint("space_id", "ref", name=op.f("pk_secret_refs")),
    )
    op.create_table(
        "sources",
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("vendor", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("purposes", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("verification_ceiling", sa.Text(), nullable=False),
        sa.Column("confirms_before_acting", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("navigation_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "trusted_action_ops", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("critical", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.space_id"], name=op.f("fk_sources_space_id_spaces")),
        sa.PrimaryKeyConstraint("source_id", name=op.f("pk_sources")),
    )
    op.create_index(op.f("ix_sources_space_id"), "sources", ["space_id"], unique=False)
    op.create_table(
        "sso_attempts",
        sa.Column("attempt_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("callback_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("binding_hash", sa.LargeBinary(), nullable=True),
        sa.Column("return_origin", sa.Text(), nullable=True),
        sa.Column("saml_request_id", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("login_code_hash", sa.LargeBinary(), nullable=True),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code_failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("purpose IN ('login', 'test')", name=op.f("ck_sso_attempts_purpose")),
        sa.CheckConstraint(
            "status IN ('pending', 'signed_in', 'completed', 'failed')", name=op.f("ck_sso_attempts_status")
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["sso_connections.connection_id"],
            name=op.f("fk_sso_attempts_connection_id_sso_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.user_id"], name=op.f("fk_sso_attempts_requested_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.tenant_id"], name=op.f("fk_sso_attempts_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], name=op.f("fk_sso_attempts_user_id_users")),
        sa.PrimaryKeyConstraint("attempt_id", name=op.f("pk_sso_attempts")),
        sa.UniqueConstraint("login_code_hash", name=op.f("uq_sso_attempts_login_code_hash")),
        sa.UniqueConstraint("state_hash", name=op.f("uq_sso_attempts_state_hash")),
    )
    op.create_index(
        op.f("ix_sso_attempts_connection_id_status_created_at"),
        "sso_attempts",
        ["connection_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_sso_attempts_expires_at"), "sso_attempts", ["expires_at"], unique=False)
    op.create_table(
        "sso_domains",
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["sso_connections.connection_id"],
            name=op.f("fk_sso_domains_connection_id_sso_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("domain", name=op.f("pk_sso_domains")),
    )
    op.create_index(op.f("ix_sso_domains_connection_id"), "sso_domains", ["connection_id"], unique=False)
    op.create_table(
        "usage_aggregates",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cell_id", sa.Text(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.space_id"], name=op.f("fk_usage_aggregates_space_id_spaces")
        ),
        sa.PrimaryKeyConstraint("space_id", "period_start", "metric", name=op.f("pk_usage_aggregates")),
    )
    op.create_table(
        "api_keys",
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("fast_hash", sa.LargeBinary(), nullable=False),
        sa.Column("slow_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'revoked')", name=op.f("ck_api_keys_status")),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.source_id"], name=op.f("fk_api_keys_source_id_sources")
        ),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.space_id"], name=op.f("fk_api_keys_space_id_spaces")),
        sa.PrimaryKeyConstraint("key_id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("fast_hash", name=op.f("uq_api_keys_fast_hash")),
    )
    op.create_index(op.f("ix_api_keys_source_id"), "api_keys", ["source_id"], unique=False)
    op.create_index(op.f("ix_api_keys_space_id"), "api_keys", ["space_id"], unique=False)
    op.create_table(
        "config_versions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("diff_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["diff_id"], ["config_diffs.diff_id"], name=op.f("fk_config_versions_diff_id_config_diffs")
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.space_id"], name=op.f("fk_config_versions_space_id_spaces")
        ),
        sa.PrimaryKeyConstraint("space_id", "version", name=op.f("pk_config_versions")),
    )
    op.create_index(
        op.f("ix_config_versions_space_id_doc_type_version"),
        "config_versions",
        ["space_id", "doc_type", "version"],
        unique=False,
    )


def downgrade() -> None:
    raise NotImplementedError("the baseline is where the history starts")
