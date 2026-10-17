"""config: an agent's working state has no cap

Memory never cuts: an agent's state is kept whole, whatever its size. `max_bytes` and `over_cap`,
which dropped a write past the cap and kept the previous state, are gone from the object types' `agent_state`.
A stored document that still names them would no longer validate, so they leave the stored versions and the
diffs. Downgrading leaves them without them: the old defaults applied.

Revision ID: 0aefededa888
Revises: 582dcf8c10ff
Create Date: 2026-10-08 21:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0aefededa888"
down_revision: str | None = "582dcf8c10ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNCAPPED = """
UPDATE {table} SET document = jsonb_set(
  document, '{{types}}',
  (SELECT coalesce(jsonb_agg(
     CASE WHEN jsonb_typeof(t -> 'agent_state') = 'object'
       THEN jsonb_set(t, '{{agent_state}}', (t -> 'agent_state') - 'max_bytes' - 'over_cap')
       ELSE t END ORDER BY n), '[]'::jsonb)
   FROM jsonb_array_elements(document -> 'types') WITH ORDINALITY AS e(t, n)))
WHERE doc_type = 'object-types' AND jsonb_typeof(document -> 'types') = 'array'
  AND EXISTS (
    SELECT 1 FROM jsonb_array_elements(document -> 'types') t
    WHERE jsonb_typeof(t -> 'agent_state') = 'object' AND (t -> 'agent_state') ?| array['max_bytes', 'over_cap'])
"""


def upgrade() -> None:
    for table in ("config_versions", "config_diffs"):
        op.execute(UNCAPPED.format(table=table))


def downgrade() -> None:
    pass
