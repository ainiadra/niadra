"""space: a subject's signals decay, what they said stays; an interaction without a subject waits for one

`subject_signals.decayed_at`: thirteen months after a subject's last activity, the retention pass used to
delete their row, and with it what they said (preferences, attributes, instructions and restrictions about an
object or an organization), the inferences they deleted and the objects they turned down. Now it rewrites the
body with those alone (`domain.signals.body.told`) and stamps the row; the derived accumulators go, as
inferences recomputed from the raw segments if the person comes back. What a person said leaves memory only by
an erasure or a retention the customer set.

`signal_intake.checked_at`: an interaction whose conversation named no subject was dropped an hour after it
arrived. Now it stays, stamped each time the loop looks for its subject and finds none, and is looked at again
later (`domain.signals.records.recheck_due`) until its conversation names one or a link resolves who it was.

Revision ID: f7c3e1a9b5d4
Revises: 954a906d01f3
Create Date: 2026-10-08 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7c3e1a9b5d4"
down_revision: str | None = "954a906d01f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("subject_signals", sa.Column("decayed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("signal_intake", sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("signal_intake", "checked_at")
    op.drop_column("subject_signals", "decayed_at")
