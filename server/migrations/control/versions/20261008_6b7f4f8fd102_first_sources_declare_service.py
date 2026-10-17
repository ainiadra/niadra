"""sources: the bootstrap's first sources declare the starter's purpose

A source that declares its purposes now reads for exactly those: the bootstrap's whatsapp and
voice declared `support` and its billing `billing`, and read what was collected for the starter's `service`
only because every source of a space on the starter purposes was given it. They declare it now, as the
bootstrap does, in the spaces that have not declared their own purposes. Any other source keeps what it
declared.

Revision ID: 6b7f4f8fd102
Revises: bb8c3bdf0715
Create Date: 2026-10-08 13:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6b7f4f8fd102"
down_revision: str | None = "bb8c3bdf0715"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STARTER = """NOT EXISTS (
    SELECT 1 FROM config_versions c
    WHERE c.space_id = sources.space_id AND c.doc_type = 'purposes'
      AND c.document->>'version' IS DISTINCT FROM 'starter-1'
)"""
_FIRST = (
    ("whatsapp", "{support}", "{service,support}"),
    ("voice", "{support}", "{service,support}"),
    ("billing", "{billing}", "{billing,service}"),
)


def upgrade() -> None:
    for name, declared, now in _FIRST:
        op.execute(
            f"UPDATE sources SET purposes = '{now}' WHERE name = '{name}' AND purposes = '{declared}' AND {_STARTER}"
        )


def downgrade() -> None:
    for name, declared, now in _FIRST:
        op.execute(
            f"UPDATE sources SET purposes = '{declared}' WHERE name = '{name}' AND purposes = '{now}' AND {_STARTER}"
        )
