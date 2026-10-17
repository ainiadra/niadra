"""space: a data issue when the day's AI ceiling is spent

`data_issues.kind` allows `budget_reached`: when a space spends its day's AI ceiling for a purpose, memory waits
for the next UTC day, and the company hears it in the Console's issues as well as by webhook.

Revision ID: d5a1c3e7b902
Revises: c4e8a2f1d6b3
Create Date: 2026-10-08 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d5a1c3e7b902"
down_revision: str | None = "c4e8a2f1d6b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = (
    "'null_field', 'out_of_vocabulary', 'stale_source', 'coverage_drop', 'invalid_value', "
    "'divergence', 'drift', 'rule_conflict', 'type_undeclared', 'field_held'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_data_issues_kind"), "data_issues", type_="check")
    op.create_check_constraint(
        op.f("ck_data_issues_kind"), "data_issues", f"kind IN ({BEFORE}, 'budget_reached')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM data_issues WHERE kind = 'budget_reached'")
    op.drop_constraint(op.f("ck_data_issues_kind"), "data_issues", type_="check")
    op.create_check_constraint(op.f("ck_data_issues_kind"), "data_issues", f"kind IN ({BEFORE})")
