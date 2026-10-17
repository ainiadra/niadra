"""Declarative base of the control database. No row-level security: nothing here is customer data."""

from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import DateTime, LargeBinary, MetaData, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        UUID: PG_UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        bytes: LargeBinary,
        dict[str, Any]: JSONB,
        Any: JSONB,
        list[UUID]: ARRAY(PG_UUID(as_uuid=True)),
        list[str]: ARRAY(Text),
        str: Text,
    }
