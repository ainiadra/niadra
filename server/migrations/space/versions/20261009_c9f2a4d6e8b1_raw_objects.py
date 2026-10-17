"""space: raw content indexed as it is written

`raw_objects` holds each raw payload, media upload and raw interaction batch by object key, with the handle
its key names and when it was written, so the `raw_content` retention reads only what expired; a payload under
no handle is also indexed by the keyed hashes of what it names a person by, so an erasure looks the target up
instead of reading every payload (`domain.privacy.raw_index`). What was written before this revision is
indexed once, by the first retention pass or erasure of the space, which then sets `raw_index_ready` in
`space_state`.

Revision ID: c9f2a4d6e8b1
Revises: b3e8d1f4a6c2
Create Date: 2026-10-09 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9f2a4d6e8b1"
down_revision: str | None = "b3e8d1f4a6c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_objects",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "tokens", postgresql.ARRAY(sa.LargeBinary()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "object_key", name=op.f("pk_raw_objects")),
    )
    op.create_index(
        op.f("ix_raw_objects_space_id_written_at"), "raw_objects", ["space_id", "written_at"], unique=False
    )
    op.create_index(op.f("ix_raw_objects_space_id_owner"), "raw_objects", ["space_id", "owner"], unique=False)
    op.create_index(
        op.f("ix_raw_objects_tokens"), "raw_objects", ["tokens"], unique=False, postgresql_using="gin"
    )
    op.execute("SELECT enable_space_rls('raw_objects')")


def downgrade() -> None:
    op.drop_table("raw_objects")
    op.execute("DELETE FROM space_state WHERE key = 'raw_index_ready'")
