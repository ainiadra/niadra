"""What a module that writes a subject's data outside its transaction (a batch writer's queue, a deferred
task, a store beside the database) offers an erasure, so that what was on its way when the erasure ran never
comes back (5.5; 24.5.5).

`fence` runs before the cascade's first row goes: the module stops taking writes about the target, for longer
than its queue takes to drain, and drops the copies it keeps outside the database. `purge` runs once the
cascade is done: what slipped in between is removed. An erasure task that runs again calls both again, so both
must be safe to repeat.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from niadra.domain.privacy.erasure import ErasureScope
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.mentions import Redacted


class ErasureFence(Protocol):
    async def fence(self, space_id: UUID, scope: ErasureScope) -> None: ...

    async def purge(self, space_id: UUID, scope: ErasureScope) -> None: ...


@runtime_checkable
class KeptAside(Protocol):
    """A module that keeps rows aside after they failed every attempt to be taken in: an
    erasure reaches them through its fence, and the subject's access package reads them here, unsealed."""

    async def kept_for(self, space_id: UUID, scope: ErasureScope) -> list[dict[str, Any]]:
        """The subject's records in the rows set aside, each marked `kept_aside` with the row it sits in."""
        ...


@runtime_checkable
class Recordings(Protocol):
    """A module that keeps recordings of a subject's conversations (turn records): kept until an erasure or
    the retention the customer sets, they are the subject's to access as everything else (5.14)."""

    async def recorded_for(self, space_id: UUID, scope: ErasureScope) -> list[dict[str, Any]]:
        """The subject's recordings in every tier, unsealed, one per turn."""
        ...


@runtime_checkable
class NamedInText(Protocol):
    """A module whose rows write people's identifiers in their text (an event's words, fields and result, a
    fact, a summary, a note, a recording), and keeps an index of what each row names, keyed-hashed as the
    index of raw content is (`domain.privacy.raw_index`). An erasure finds by it the rows of others that name
    the target, and they keep with the target redacted out of them (5.14.0)."""

    async def redact_named(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], tokens: frozenset[bytes], held: Held
    ) -> Redacted:
        """Redacts the target, named by `plain` (`handle_tokens`) and found by `tokens`, out of the rows kept
        for anyone else, except a row under a legal hold, which keeps as it is and is counted. Safe to
        repeat."""
        ...


class ErasureMarks(Protocol):
    """Where every fence keeps what an erasure reached and when, in a store that never evicts: keyed digests,
    never an id. A record made at or before that time is dropped; one made after it is new activity, and
    lands. Raises `CacheUnavailable` when the store does not answer: the erasure waits, and a writer sends its
    batch back to its queue."""

    async def fence(self, space_id: UUID, marks: Sequence[bytes], at: datetime, ttl_s: int) -> None:
        """Marks these as erased at `at`, for `ttl_s`. A mark set again takes the newer time."""
        ...

    async def fenced(self, space_id: UUID, marks: Sequence[bytes]) -> dict[bytes, datetime]:
        """When each mark among these was erased, for the ones whose fence did not expire."""
        ...
