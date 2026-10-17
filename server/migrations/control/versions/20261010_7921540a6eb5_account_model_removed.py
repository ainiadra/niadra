"""access: the account model is gone

The company that signs up is the only party of its projects and acts through its own roles: there are no
legal entities, project parties, approvals by link, emergency access, access reviews or operator transfers,
and no one but the tenant pays for a project. Their tables and `projects.payer_tenant_id` go, and so do the
access changes of their kinds, which nothing reads any more. Downgrading brings the tables and the column
back empty.

Revision ID: 7921540a6eb5
Revises: 0aefededa888
Create Date: 2026-10-10 18:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7921540a6eb5"
down_revision: str | None = "0aefededa888"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "'invited', 'invitation_accepted', 'roles_changed', 'deactivated', 'reactivated', 'second_factor_reset',"
    " 'sso_person_created', 'sso_roles_synced', 'sso_saved', 'sso_removed'"
)
GONE = (
    "'approval_decided', 'emergency_access', 'emergency_access_ended', 'party_changed', 'project_transferred'"
)


def upgrade() -> None:
    op.drop_table("project_transfers")
    op.drop_table("approvals")
    op.drop_table("project_parties")
    op.drop_table("legal_entities")
    op.drop_constraint(op.f("fk_projects_payer_tenant_id_tenants"), "projects", type_="foreignkey")
    op.drop_column("projects", "payer_tenant_id")
    op.execute(f"DELETE FROM access_changes WHERE kind IN ({GONE})")
    op.drop_constraint(op.f("ck_access_changes_kind"), "access_changes", type_="check")
    op.create_check_constraint(op.f("ck_access_changes_kind"), "access_changes", f"kind IN ({KINDS})")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_access_changes_kind"), "access_changes", type_="check")
    op.create_check_constraint(op.f("ck_access_changes_kind"), "access_changes", f"kind IN ({KINDS}, {GONE})")
    op.add_column("projects", sa.Column("payer_tenant_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_projects_payer_tenant_id_tenants"), "projects", "tenants", ["payer_tenant_id"], ["tenant_id"]
    )
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
            "kind IN ('purposes', 'policy_loosening', 'retention', 'operators', 'transfer', 'access_review',"
            " 'emergency_access', 'ripd')",
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
