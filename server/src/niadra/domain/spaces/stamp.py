"""The stamp every space database carries (`space_identity`), checked when a process first opens it.

The directory decides where a space lives; the stamp is the check, never the routing: a directory
row pointing at another space's database fails loudly instead of serving the wrong rows.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SpaceStamp:
    space_id: UUID
    tenant_id: UUID
    region: str
    environment: str
    cell_id: str
    hash_modulus: int


class StampMismatch(RuntimeError):
    """The database a directory row points at belongs to another space, or carries no stamp."""


def check_stamp(expected_space: UUID, db_name: str, stamp: SpaceStamp | None) -> SpaceStamp:
    if stamp is None:
        raise StampMismatch(f"database {db_name} has no stamp")
    if stamp.space_id != expected_space:
        raise StampMismatch(f"database {db_name} belongs to another space")
    return stamp
