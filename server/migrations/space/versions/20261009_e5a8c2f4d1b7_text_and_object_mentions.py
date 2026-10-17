"""space: what derived rows and turn recordings name a person by

`text_mentions` keeps, per row of memory a person's identifiers can be written in (facts, episodes, open
items, profile slots, tail summaries, agent notes, extraction results, subject signals), the keyed hashes of
the phones, e-mails, documents and ids its text writes (`domain.privacy.raw_index`), and `object_mentions` the
same per turn recording and per archived event in object storage. An erasure looks the target up in them, and
redacts the target out of the rows and objects of others they name, without reading the space. Rows and
objects written before this revision are indexed once, by the first erasure of the space.

Revision ID: e5a8c2f4d1b7
Revises: d4e7f1a2b9c3
Create Date: 2026-10-09 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5a8c2f4d1b7"
down_revision: str | None = "d4e7f1a2b9c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "text_mentions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("store", sa.Text(), nullable=False),
        sa.Column("row_key", sa.Text(), nullable=False),
        sa.Column("tokens", postgresql.ARRAY(sa.LargeBinary()), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "store", "row_key", name=op.f("pk_text_mentions")),
    )
    op.create_index(
        op.f("ix_text_mentions_tokens"), "text_mentions", ["tokens"], unique=False, postgresql_using="gin"
    )
    op.execute("SELECT enable_space_rls('text_mentions')")
    op.create_table(
        "object_mentions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("tokens", postgresql.ARRAY(sa.LargeBinary()), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "key", name=op.f("pk_object_mentions")),
    )
    op.create_index(
        op.f("ix_object_mentions_tokens"), "object_mentions", ["tokens"], unique=False, postgresql_using="gin"
    )
    op.execute("SELECT enable_space_rls('object_mentions')")


def downgrade() -> None:
    op.drop_table("object_mentions")
    op.drop_table("text_mentions")
    op.execute(
        "DELETE FROM space_state WHERE key IN ('text_mentions_ready', 'object_mentions_ready', "
        "'signals_mentions_ready', 'archive_mentions_ready')"
    )
