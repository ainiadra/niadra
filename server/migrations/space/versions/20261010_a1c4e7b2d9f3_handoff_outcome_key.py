"""space: the key version a handoff's outcome is sealed under

A handoff's outcome detail is sealed when the receiving side reports it, under the space's key of that moment,
which may be newer than the package's: `outcome_dek_version` keeps it, so an erasure can open the detail and
redact a person named in it.

Revision ID: a1c4e7b2d9f3
Revises: e5a8c2f4d1b7
Create Date: 2026-10-10 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7b2d9f3"
down_revision: str | None = "e5a8c2f4d1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("handoffs", sa.Column("outcome_dek_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("handoffs", "outcome_dek_version")
