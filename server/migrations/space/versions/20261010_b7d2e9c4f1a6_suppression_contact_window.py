"""space: the local hours a suppression holds in

A person's own contact hours ("não liguem antes das 9h") are a suppression that holds only in a window of
local hours (the suppression-list spec, 6.1): `contact_window` keeps it as `<from>;<to>;<days>;<zone>`, and
null keeps a suppression for every hour, as before.

Revision ID: b7d2e9c4f1a6
Revises: a1c4e7b2d9f3
Create Date: 2026-10-10 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2e9c4f1a6"
down_revision: str | None = "a1c4e7b2d9f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("suppressions", sa.Column("contact_window", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("suppressions", "contact_window")
