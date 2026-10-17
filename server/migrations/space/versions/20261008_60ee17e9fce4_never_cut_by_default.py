"""space: nothing Niadra keeps is cut by a default

Memory never cuts. `data_issues.kind` allows `inference_limit`: a limit of the observed types
(types, fields or states of a type, operations) reached, a type left out or a name not safe to use is said to
the company in the Console's issues, counted per kind and type; it was a note kept in the observed document, cut
to its last 50. `agent_state.valid_until` may be null: a state whose type names no retention is kept until it
names one, where it went 30 days after its last write.

Revision ID: 60ee17e9fce4
Revises: f7c3e1a9b5d4
Create Date: 2026-10-08 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "60ee17e9fce4"
down_revision: str | None = "f7c3e1a9b5d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = (
    "'null_field', 'out_of_vocabulary', 'stale_source', 'coverage_drop', 'invalid_value', "
    "'divergence', 'drift', 'rule_conflict', 'type_undeclared', 'field_held', 'budget_reached', 'row_failed'"
)


def upgrade() -> None:
    op.alter_column("agent_state", "valid_until", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.drop_constraint(op.f("ck_data_issues_kind"), "data_issues", type_="check")
    op.create_check_constraint(
        op.f("ck_data_issues_kind"), "data_issues", f"kind IN ({BEFORE}, 'inference_limit')"
    )


def downgrade() -> None:
    # A state kept with no retention takes the 30 days it had before this revision, from its last write.
    op.execute(
        "UPDATE agent_state SET valid_until = updated_at + interval '30 days' WHERE valid_until IS NULL"
    )
    op.alter_column("agent_state", "valid_until", existing_type=sa.DateTime(timezone=True), nullable=False)
    # The kind the older schema cannot hold: its issues are Niadra's notes, found again on the next runs.
    op.execute("DELETE FROM data_issues WHERE kind = 'inference_limit'")
    op.drop_constraint(op.f("ck_data_issues_kind"), "data_issues", type_="check")
    op.create_check_constraint(op.f("ck_data_issues_kind"), "data_issues", f"kind IN ({BEFORE})")
