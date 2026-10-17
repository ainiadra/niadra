"""identity: organization parents are gone

An organization stands alone: its context carries its own facts only, and nothing places it under another
organization. `org_parents` goes with its indexes, its `change_seq` trigger and its row-level security;
the links between people and organizations stay. Downgrading brings the table back empty.

Revision ID: 0e9867ec4ad6
Revises: b7d2e9c4f1a6
Create Date: 2026-10-10 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0e9867ec4ad6"
down_revision: str | None = "b7d2e9c4f1a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("org_parents")


def downgrade() -> None:
    op.create_table(
        "org_parents",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("parent_link_id", sa.UUID(), nullable=False),
        sa.Column("org_handle_id", sa.UUID(), nullable=False),
        sa.Column("parent_handle_id", sa.UUID(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "parent_link_id", name=op.f("pk_org_parents")),
    )
    op.create_index(
        op.f("ix_org_parents_space_id_org_handle_id"),
        "org_parents",
        ["space_id", "org_handle_id"],
        unique=False,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index(
        op.f("ix_org_parents_space_id_parent_handle_id"),
        "org_parents",
        ["space_id", "parent_handle_id"],
        unique=False,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.execute(
        "CREATE TRIGGER org_parents_change_seq BEFORE UPDATE ON org_parents "
        "FOR EACH ROW EXECUTE FUNCTION bump_change_seq()"
    )
    op.execute("SELECT enable_space_rls('org_parents')")
