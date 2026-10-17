"""config: the agent memory document has no notes cap

Memory never cuts: an agent keeps every note, and `max_notes_per_source`, which refused the
next one, is gone from the document. A stored document that still names it would no longer validate, so it
leaves the stored versions and the diffs. Downgrading leaves them without it: the field's default applied.

Revision ID: 582dcf8c10ff
Revises: 6b7f4f8fd102
Create Date: 2026-10-08 15:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "582dcf8c10ff"
down_revision: str | None = "6b7f4f8fd102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("config_versions", "config_diffs"):
        op.execute(
            f"UPDATE {table} SET document = document - 'max_notes_per_source' "
            "WHERE doc_type = 'agent-memory' AND jsonb_typeof(document) = 'object' "
            "AND document ? 'max_notes_per_source'"
        )


def downgrade() -> None:
    pass
