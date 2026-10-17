"""cell: initial schema

The cell database after the split into one database per space: the space directory, wrapped data keys,
secrets, the configuration copy, source keys, health, dead tasks, receipt watermarks and deletion receipts
waiting for the control plane.

The cell reaches this database as `niadra_system` (the owner migrates it), so no table has row-level
security. It gets the grants block of a space database (DATABASE_GRANTS, as db-init.sql runs it), so
`niadra_app` may connect too; no code does.

Revision ID: 26956bf8720c
Revises:
Create Date: 2026-09-24 04:01:47.741053
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "26956bf8720c"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cell_health",
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.CheckConstraint("state IN ('healthy', 'degraded')", name=op.f("ck_cell_health_state")),
        sa.PrimaryKeyConstraint("component", name=op.f("pk_cell_health")),
    )
    op.create_table(
        "cell_state",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_cell_state")),
    )
    op.create_table(
        "config_documents",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "doc_type", name=op.f("pk_config_documents")),
    )
    op.create_table(
        "config_snapshot",
        sa.Column("cell_id", sa.Text(), nullable=False),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("slot IN ('active', 'previous')", name=op.f("ck_config_snapshot_slot")),
        sa.PrimaryKeyConstraint("cell_id", "slot", name=op.f("pk_config_snapshot")),
    )
    op.create_table(
        "data_keys",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("kek_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "version", name=op.f("pk_data_keys")),
    )
    op.create_table(
        "dead_tasks",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("queue", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "id", name=op.f("pk_dead_tasks")),
    )
    op.create_table(
        "deletion_receipts_outbox",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("space_id", name=op.f("pk_deletion_receipts_outbox")),
    )
    op.create_table(
        "receipt_watermarks",
        sa.Column("producer_id", sa.Text(), nullable=False),
        sa.Column("contiguous_seq", sa.BigInteger(), nullable=False),
        sa.Column("watermark_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("last_heard_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pending_seqs", postgresql.ARRAY(sa.BigInteger()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('active', 'closed', 'expired')", name=op.f("ck_receipt_watermarks_state")
        ),
        sa.PrimaryKeyConstraint("producer_id", name=op.f("pk_receipt_watermarks")),
    )
    op.create_table(
        "secrets",
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("value_ct", sa.LargeBinary(), nullable=False),
        sa.Column("kek_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("ref", "version", name=op.f("pk_secrets")),
    )
    op.create_table(
        "source_keys",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("fast_hash", sa.LargeBinary(), nullable=False),
        sa.Column("slow_hash", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'revoked')", name=op.f("ck_source_keys_status")),
        sa.PrimaryKeyConstraint("space_id", "key_id", name=op.f("pk_source_keys")),
        sa.UniqueConstraint("fast_hash", name=op.f("uq_source_keys_fast_hash")),
    )
    op.create_table(
        "space_databases",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("db_name", sa.Text(), nullable=False),
        sa.Column("instance", sa.Text(), server_default="primary", nullable=False),
        sa.Column("tier", sa.Text(), server_default="t0", nullable=False),
        sa.Column("hash_modulus", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=True),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "environment IN ('sandbox', 'production')", name=op.f("ck_space_databases_environment")
        ),
        sa.CheckConstraint(
            "status IN ('creating', 'migrating', 'ready', 'maintenance', 'failed', 'deleting', 'deleted')",
            name=op.f("ck_space_databases_status"),
        ),
        sa.CheckConstraint("tier IN ('t0', 't1', 't2')", name=op.f("ck_space_databases_tier")),
        sa.PrimaryKeyConstraint("space_id", name=op.f("pk_space_databases")),
        sa.UniqueConstraint("db_name", name=op.f("uq_space_databases_db_name")),
    )
    op.execute("INSERT INTO cell_state (key, value) VALUES ('cache_epoch', 1)")


def downgrade() -> None:
    op.drop_table("space_databases")
    op.drop_table("source_keys")
    op.drop_table("secrets")
    op.drop_table("receipt_watermarks")
    op.drop_table("deletion_receipts_outbox")
    op.drop_table("dead_tasks")
    op.drop_table("data_keys")
    op.drop_table("config_snapshot")
    op.drop_table("config_documents")
    op.drop_table("cell_state")
    op.drop_table("cell_health")
