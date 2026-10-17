"""Declarative bases of the two kinds of cell database.

`CellBase`: the cell database (`niadra_cell`), one per cell: the space directory, wrapped data keys,
secrets, the configuration copy, source keys, health, dead tasks and receipt watermarks. Only the
system role reaches it, so it has no row-level security.

`SpaceBase`: one database per space (`s_<space id hex>`), everything that belongs to the customer.

Conventions of the space tables (architecture section 6):
- every key starts with `space_id`, and every table gets row-level security on it, even though the
  database holds one space: a mis-wired directory then fails as zero rows, never as another space's;
- `*_ct` columns hold AES-256-GCM ciphertext under the space's data key, with `dek_version`;
- exportable tables carry `change_seq`, bumped on insert and update (22.3);
- `events`, `event_handles` and `receipts` are range-partitioned by their UUIDv7 id, and the
  memory tables are hash-partitioned by the origin handle, which is part of every key; the number
  of hash partitions is per database (`space_identity.hash_modulus`), so no code may assume it.
Partitions, policies, functions and triggers are created by the Alembic revisions.
"""

from datetime import date, datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    Sequence,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REAL
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
}

TYPES: dict[Any, Any] = {
    UUID: PG_UUID(as_uuid=True),
    datetime: DateTime(timezone=True),
    date: Date,
    bytes: LargeBinary,
    dict[str, Any]: JSONB,
    list[Any]: JSONB,
    list[UUID]: ARRAY(PG_UUID(as_uuid=True)),
    list[str]: ARRAY(Text),
    list[int]: ARRAY(BigInteger),
    float: REAL,
    str: Text,
}


class CellBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = TYPES


class SpaceBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = TYPES


CHANGE_SEQ = Sequence("change_seq", metadata=SpaceBase.metadata, cache=1)


def change_seq() -> Mapped[int]:
    return mapped_column(BigInteger, server_default=CHANGE_SEQ.next_value(), nullable=False)


def dek_version() -> Mapped[int]:
    return mapped_column(Integer, nullable=False)


def now_default() -> Mapped[datetime]:
    return mapped_column(server_default=text("now()"), nullable=False)
