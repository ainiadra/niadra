"""cell: nothing in the stored configuration cuts memory

Memory never cuts. The observed types document kept its notes cut to the last 50: it now keeps every
note since the last record in `unreported`, a record names them, and a limit reached is a data issue; the old
list, already named by the records, leaves the stored documents. An agent's working state has no cap any more:
`max_bytes` and `over_cap` leave the object types' `agent_state` in the space documents the cell holds, which
would no longer validate with them, and so does the agent memory's `max_notes_per_source`, gone since the notes
cap went (the control plane's documents lost it in its revision 582dcf8c10ff).

Revision ID: 3a03ec732593
Revises: 26956bf8720c
Create Date: 2026-10-08 21:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3a03ec732593"
down_revision: str | None = "26956bf8720c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UNCAPPED = """
UPDATE config_documents SET document = jsonb_set(
  document, '{object_types,types}',
  (SELECT coalesce(jsonb_agg(
     CASE WHEN jsonb_typeof(t -> 'agent_state') = 'object'
       THEN jsonb_set(t, '{agent_state}', (t -> 'agent_state') - 'max_bytes' - 'over_cap')
       ELSE t END ORDER BY n), '[]'::jsonb)
   FROM jsonb_array_elements(document #> '{object_types,types}') WITH ORDINALITY AS e(t, n)))
WHERE doc_type = 'space_config' AND jsonb_typeof(document #> '{object_types,types}') = 'array'
  AND EXISTS (
    SELECT 1 FROM jsonb_array_elements(document #> '{object_types,types}') t
    WHERE jsonb_typeof(t -> 'agent_state') = 'object' AND (t -> 'agent_state') ?| array['max_bytes', 'over_cap'])
"""


def upgrade() -> None:
    op.execute(UNCAPPED)
    op.execute(
        "UPDATE config_documents "
        "SET document = jsonb_set(document, '{agent_memory}', (document -> 'agent_memory') - 'max_notes_per_source') "
        "WHERE doc_type = 'space_config' AND jsonb_typeof(document -> 'agent_memory') = 'object' "
        "AND (document -> 'agent_memory') ? 'max_notes_per_source'"
    )
    op.execute(
        "UPDATE config_documents "
        "SET document = (document - 'notes') || jsonb_build_object('unreported', '[]'::jsonb) "
        "WHERE doc_type = 'observed_types' AND jsonb_typeof(document) = 'object' AND document ? 'notes'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE config_documents "
        "SET document = (document - 'unreported') || jsonb_build_object('notes', '[]'::jsonb) "
        "WHERE doc_type = 'observed_types' AND jsonb_typeof(document) = 'object' AND document ? 'unreported'"
    )
