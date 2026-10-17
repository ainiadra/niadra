"""The space pinned on the transaction by the unit of work, as a SQL expression.

`current_setting` is stable within a statement, so it is evaluated once and the primary-key
indexes, which all start with `space_id`, stay usable.
"""

from uuid import UUID

from sqlalchemy import cast, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import ColumnElement

CURRENT_SPACE: ColumnElement[UUID] = cast(
    func.current_setting("niadra.space_id"), postgresql.UUID(as_uuid=True)
)
