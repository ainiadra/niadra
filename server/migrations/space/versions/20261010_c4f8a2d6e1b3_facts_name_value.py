"""space: find a customer by a name the memory holds

The Console's customer search finds a name exactly (case, accents and spacing aside) by the keyed digest of
the name fact's value, which every fact already keeps for its dedupe (`value_hmac`). `ix_facts_name_value`
serves that lookup across the space's handles, only for the predicates that name a customer
(`domain.identity.display.NAME_PREDICATES`; a change to that list comes with a revision of this index).
Created on the partitioned parent, so every partition gets it, whatever the space's modulus.

Revision ID: c4f8a2d6e1b3
Revises: 0e9867ec4ad6
Create Date: 2026-10-10 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f8a2d6e1b3"
down_revision: str | None = "0e9867ec4ad6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMES = (
    "predicate IN ('social_name', 'nome_social', 'name', 'nome', 'full_name', 'nome_completo', "
    "'preferred_name', 'nome_preferido', 'nickname', 'apelido')"
)


def upgrade() -> None:
    op.create_index(
        "ix_facts_name_value",
        "facts",
        ["space_id", "value_hmac"],
        unique=False,
        postgresql_where=sa.text(NAMES),
    )


def downgrade() -> None:
    op.drop_index("ix_facts_name_value", table_name="facts")
