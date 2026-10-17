"""space: a profile may be staff

`profiles.kind` allows `staff`: a person who works for the company, named by a system id of its own systems.
A conversation with staff is internal; one with anyone else is customer-facing, whatever the key.

Revision ID: c4e8a2f1d6b3
Revises: c4e81f0d2a77
Create Date: 2026-10-07 10:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4e8a2f1d6b3"
down_revision: str | None = "c4e81f0d2a77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = "'person', 'account', 'partner'"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_profiles_kind"), "profiles", type_="check")
    op.create_check_constraint(op.f("ck_profiles_kind"), "profiles", f"kind IN ({BEFORE}, 'staff')")


def downgrade() -> None:
    op.execute("UPDATE profiles SET kind = 'person' WHERE kind = 'staff'")
    op.drop_constraint(op.f("ck_profiles_kind"), "profiles", type_="check")
    op.create_check_constraint(op.f("ck_profiles_kind"), "profiles", f"kind IN ({BEFORE})")
