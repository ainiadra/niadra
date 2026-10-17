"""space: a subject's signals keep everything the person said

`subject_signals.size_bytes` may reach 4 MiB instead of 16 KB: what a person said (preferences, attributes,
instructions about an object or an organization), the inferences they deleted and the objects they turned
down are no longer cut to fit, only the derived accumulators keep their 16 KB budget
(`domain.signals.body.DERIVED_BYTES`). The 51st instruction a person gave used to push the first one out.

Revision ID: e6b2d4f8a013
Revises: d1a7f3c9e5b2
Create Date: 2026-10-08 18:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e6b2d4f8a013"
down_revision: str | None = "d1a7f3c9e5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_subject_signals_size_bytes"), "subject_signals", type_="check")
    op.create_check_constraint(
        op.f("ck_subject_signals_size_bytes"), "subject_signals", "size_bytes <= 4194304"
    )


def downgrade() -> None:
    # A body past 16 KB has no older form: it is derived again from the pending entries and the raw segments.
    op.execute("DELETE FROM subject_signals WHERE size_bytes > 16384")
    op.drop_constraint(op.f("ck_subject_signals_size_bytes"), "subject_signals", type_="check")
    op.create_check_constraint(
        op.f("ck_subject_signals_size_bytes"), "subject_signals", "size_bytes <= 16384"
    )
