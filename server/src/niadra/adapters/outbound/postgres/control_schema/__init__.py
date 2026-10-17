"""Tables of the control database (5.16, 6). Separate metadata from the cell: its own Alembic history."""

from niadra.adapters.outbound.postgres.control_schema import tables
from niadra.adapters.outbound.postgres.control_schema.base import Base

__all__ = ["Base", "tables"]
