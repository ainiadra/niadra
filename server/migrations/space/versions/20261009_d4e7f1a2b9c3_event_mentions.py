"""space: what each event's text names a person by

`event_mentions` keeps, beside each event, the keyed hashes of the phones, e-mails, documents and ids its
text, fields and result write (`domain.privacy.raw_index`), so an erasure finds the events of others that name
the target and redacts the target out of them, without reading the log. Events written before this revision
are indexed once, by the first erasure of the space, which then sets `event_mentions_ready` in `space_state`.

Revision ID: d4e7f1a2b9c3
Revises: c9f2a4d6e8b1
Create Date: 2026-10-09 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e7f1a2b9c3"
down_revision: str | None = "c9f2a4d6e8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_mentions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("tokens", postgresql.ARRAY(sa.LargeBinary()), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "event_id", name=op.f("pk_event_mentions")),
    )
    op.create_index(
        op.f("ix_event_mentions_tokens"), "event_mentions", ["tokens"], unique=False, postgresql_using="gin"
    )
    op.execute("SELECT enable_space_rls('event_mentions')")


def downgrade() -> None:
    op.drop_table("event_mentions")
    op.execute("DELETE FROM space_state WHERE key = 'event_mentions_ready'")
