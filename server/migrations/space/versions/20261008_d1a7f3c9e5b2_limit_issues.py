"""space: a data issue for a row of the company's data set aside

`data_issues.kind` allows `row_dropped`: a row of the company's data that failed every attempt to be taken in
is set aside, and the company hears it in the Console's issues, not only in a log line.

Revision ID: d1a7f3c9e5b2
Revises: d5a1c3e7b902
Create Date: 2026-10-08 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d1a7f3c9e5b2"
down_revision: str | None = "d5a1c3e7b902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = (
    "'null_field', 'out_of_vocabulary', 'stale_source', 'coverage_drop', 'invalid_value', "
    "'divergence', 'drift', 'rule_conflict', 'type_undeclared', 'field_held', 'budget_reached'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_data_issues_kind"), "data_issues", type_="check")
    op.create_check_constraint(
        op.f("ck_data_issues_kind"), "data_issues", f"kind IN ({BEFORE}, 'row_dropped')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM data_issues WHERE kind = 'row_dropped'")
    op.drop_constraint(op.f("ck_data_issues_kind"), "data_issues", type_="check")
    op.create_check_constraint(op.f("ck_data_issues_kind"), "data_issues", f"kind IN ({BEFORE})")
