"""space: a kept turn has no end until a retention is set

Memory never cuts what it was given: a turn recording is kept until an erasure or the retention the customer
sets (`recording.kept_days`, `scenario_days`). `turns_kept.kept_until` may be null, kept until one is set,
where the kept tier ended a turn 30 days (at most 90) after it was kept and a scenario's 180 days after it
last ran.

Revision ID: b3e8d1f4a6c2
Revises: 60ee17e9fce4
Create Date: 2026-10-09 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e8d1f4a6c2"
down_revision: str | None = "60ee17e9fce4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("turns_kept", "kept_until", existing_type=sa.DateTime(timezone=True), nullable=True)


def downgrade() -> None:
    # A turn kept with no end takes the end it had before this revision: 30 days after it was kept, 180 for a
    # scenario's.
    op.execute(
        "UPDATE turns_kept SET kept_until = kept_at + CASE WHEN kept_by = 'scenario'"
        " THEN interval '180 days' ELSE interval '30 days' END WHERE kept_until IS NULL"
    )
    op.alter_column("turns_kept", "kept_until", existing_type=sa.DateTime(timezone=True), nullable=False)
