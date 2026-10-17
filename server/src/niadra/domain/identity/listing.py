"""The Console's customer list (5.22): profiles by the latest activity on any of their handles.

The list walks handles in activity order and shows a profile at its latest handle, so a page costs what it
shows and never the size of the space. The cursor is the position of the last handle the walk looked at: its
time and its id.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from niadra.domain.common.errors import InvalidInput
from niadra.domain.records import HandleRecord
from niadra.domain.vocabulary import SubjectKind

HANDLES_SHOWN = 3
"""Identifiers a row carries, the latest first; the row also says how many the profile has."""

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ListOrder(StrEnum):
    LAST_SEEN_DESC = "last_seen_desc"
    LAST_SEEN_ASC = "last_seen_asc"


@dataclass(frozen=True, slots=True)
class ListCursor:
    """Where a page ended: the activity time and the id of the last handle it looked at."""

    last_seen: datetime
    handle_id: UUID

    def encode(self) -> str:
        return f"{(self.last_seen - _EPOCH) // timedelta(microseconds=1)}.{self.handle_id.hex}"

    @classmethod
    def decode(cls, cursor: str) -> "ListCursor":
        micros, _, handle = cursor.partition(".")
        try:
            return cls(_EPOCH + timedelta(microseconds=int(micros)), UUID(hex=handle))
        except (ValueError, OverflowError) as exc:
            raise InvalidInput("the cursor is not one this list returned") from exc


@dataclass(frozen=True, slots=True)
class ListedProfile:
    profile_id: UUID
    kind: SubjectKind
    pseudonym: str
    last_seen: datetime
    handles: tuple[HandleRecord, ...]
    """At most `HANDLES_SHOWN`, the latest first."""
    handle_count: int
