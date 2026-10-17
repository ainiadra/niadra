"""Opaque keyset cursors for the governance lists (firings, deliveries).

The cursor is the (timestamp, id) of the last row of the page, so paging is stable while new rows
arrive; it carries no personal data, which matters because cursors travel in query strings (7.0).
"""

import base64
from datetime import datetime
from uuid import UUID

from niadra.domain.common.errors import InvalidInput


def encode_cursor(at: datetime, row_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{row_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, row_id = raw.split("|", 1)
        return datetime.fromisoformat(at), UUID(row_id)
    except ValueError:
        raise InvalidInput("malformed cursor") from None
