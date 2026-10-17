"""console: user preferences

What the Console remembers for a person across browsers: a few small JSON values by key (the tutorials
done, the welcome seen). One new, empty table that goes with its person.

Revision ID: bb8c3bdf0715
Revises: 58f15488bbb3
Create Date: 2026-10-01 00:26:52.035367
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bb8c3bdf0715"
down_revision: str | None = "58f15488bbb3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name=op.f("fk_user_preferences_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "key", name=op.f("pk_user_preferences")),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
