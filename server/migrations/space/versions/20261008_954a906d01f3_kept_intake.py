"""space: a row of the company's data that failed every attempt is kept, never deleted

`ingest_intake` and `turn_intake` gain `failed_at`: a row that failed every attempt to be taken in is set
aside there, sealed as it came, out of the space's order, instead of being deleted. `data_issues.kind` names
it `row_failed` (it was `row_dropped`), so the company hears it in the Console's issues, and an admin retries
it. `pipeline_due` counts only rows waiting their turn, so a row set aside does not read as a stuck intake.

Revision ID: 954a906d01f3
Revises: e6b2d4f8a013
Create Date: 2026-10-08 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "954a906d01f3"
down_revision: str | None = "e6b2d4f8a013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "'null_field', 'out_of_vocabulary', 'stale_source', 'coverage_drop', 'invalid_value', "
    "'divergence', 'drift', 'rule_conflict', 'type_undeclared', 'field_held', 'budget_reached'"
)
DUE = """
CREATE OR REPLACE FUNCTION pipeline_due(
  p_space uuid, OUT outbox_oldest timestamptz, OUT scheduled_due timestamptz, OUT schedule_due timestamptz,
  OUT intake_pending bigint, OUT intake_oldest timestamptz
) LANGUAGE plpgsql AS $$
BEGIN
  PERFORM set_config('niadra.space_id', p_space::text, true);
  SELECT min(created_at) INTO outbox_oldest FROM outbox;
  SELECT min(due_at) INTO scheduled_due FROM scheduled_tasks;
  SELECT min(next_run) INTO schedule_due FROM schedules;
  SELECT count(*), min(received_at) INTO intake_pending, intake_oldest FROM ingest_intake{where};
END $$;
"""


def _kinds(last: str, before: str) -> None:
    op.drop_constraint(op.f("ck_data_issues_kind"), "data_issues", type_="check")
    op.execute(f"UPDATE data_issues SET kind = '{last}' WHERE kind = '{before}'")
    op.create_check_constraint(op.f("ck_data_issues_kind"), "data_issues", f"kind IN ({KINDS}, '{last}')")


def upgrade() -> None:
    for table in ("ingest_intake", "turn_intake"):
        op.add_column(table, sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
    _kinds("row_failed", "row_dropped")
    op.execute(DUE.format(where=" WHERE failed_at IS NULL"))


def downgrade() -> None:
    # Nothing kept is deleted on the way down: a row set aside goes back in the order, as before this revision.
    op.execute(DUE.format(where=""))
    _kinds("row_dropped", "row_failed")
    for table in ("turn_intake", "ingest_intake"):
        op.drop_column(table, "failed_at")
