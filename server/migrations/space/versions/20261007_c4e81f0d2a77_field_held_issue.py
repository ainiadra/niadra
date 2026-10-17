"""space: a data issue for an undeclared field held back from agents

`data_issues.kind` allows `field_held`: a field a company's system sends that its type does not declare is
served to agents by default, unless it may be personal data or free text, and then it is held until declared
and returned to the company as this issue (`domain.objects.undeclared`).

Revision ID: c4e81f0d2a77
Revises: b7d3c1a9e042
Create Date: 2026-10-07 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4e81f0d2a77"
down_revision: str | None = "b7d3c1a9e042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = (
    "'null_field', 'out_of_vocabulary', 'stale_source', 'coverage_drop', 'invalid_value', "
    "'divergence', 'drift', 'rule_conflict', 'type_undeclared'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_data_issues_kind"), "data_issues", type_="check")
    op.create_check_constraint(
        op.f("ck_data_issues_kind"), "data_issues", f"kind IN ({BEFORE}, 'field_held')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM data_issues WHERE kind = 'field_held'")
    op.drop_constraint(op.f("ck_data_issues_kind"), "data_issues", type_="check")
    op.create_check_constraint(op.f("ck_data_issues_kind"), "data_issues", f"kind IN ({BEFORE})")
