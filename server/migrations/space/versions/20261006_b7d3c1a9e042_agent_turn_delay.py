"""space: a task may wait for an agent's reply

`scheduled_tasks.reason` allows `agent_turn`: a customer-facing agent's declared action waits for the agent's
own reply in its conversation before it closes an item (`app.memory`, the delivery gate).

Revision ID: b7d3c1a9e042
Revises: 9a050517b420
Create Date: 2026-10-06 23:10:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d3c1a9e042"
down_revision: str | None = "9a050517b420"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = (
    "'retry', 'lease_busy', 'space_busy', 'safety_net', 'llm_retry', 'llm_budget', 'reextract', 'rate_limited', "
    "'valid_until', 'receipt_watermark', 'transfer_window', 'export_settle', 'delay', 'batch_window'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_scheduled_tasks_reason"), "scheduled_tasks", type_="check")
    op.create_check_constraint(
        op.f("ck_scheduled_tasks_reason"), "scheduled_tasks", f"reason IN ({BEFORE}, 'agent_turn')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM scheduled_tasks WHERE reason = 'agent_turn'")
    op.drop_constraint(op.f("ck_scheduled_tasks_reason"), "scheduled_tasks", type_="check")
    op.create_check_constraint(op.f("ck_scheduled_tasks_reason"), "scheduled_tasks", f"reason IN ({BEFORE})")
