"""Keyset paging: the (time, id) of the last row read, as a row value to compare against."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, literal, tuple_
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


def pair(before: tuple[datetime, UUID]) -> Any:
    at, row_id = before
    return tuple_(literal(at, DateTime(timezone=True)), literal(row_id, PG_UUID(as_uuid=True)))
