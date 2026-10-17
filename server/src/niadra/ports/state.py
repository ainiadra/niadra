"""What the typed state needs from outside (24.3; 06, 15.3).

- The agents' working state (24.3.9): the state store, where each write is decided atomically, and the
  confirmed queue its batch writer drains into the space's database.
- The hot copy of the shared objects' working set (24.3.12), in the cache store.
- The subject's interests, which the signals module keeps (24.3.13).
- The working set's door, for another module that learns of a presented or engaged object.
- What watches the reports merged into objects for drift from their declared types.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from niadra.config.models import SpaceConfig
from niadra.domain.records import ObjectKey
from niadra.domain.state.agent_state import DurableState
from niadra.domain.state.observed import Report
from niadra.ports.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class HeldField:
    """One field as the state store holds it: sealed on its own, with the size of its fragment and when it
    was written."""

    sealed: bytes
    size: int
    written_ms: int


@dataclass(frozen=True, slots=True)
class HeldState:
    version: int
    updated_ms: int
    fields: dict[str, HeldField] = field(default_factory=dict)


class WriteResult(StrEnum):
    STORED = "stored"
    CONFLICT = "conflict"
    MISSING = "missing"
    """The store does not hold the state: the caller restores it (or its absence) and writes again."""


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    result: WriteResult
    version: int
    state: HeldState | None = None
    """The whole state after a stored write."""


class AgentStateStore(Protocol):
    """Each state under one key of the state store. Writes are decided there, atomically; a key the store lost
    or let expire is restored from the cache's copy or the database before it is written."""

    async def load(self, key: str) -> HeldState | None: ...

    async def restore(self, key: str, state: HeldState, ttl_s: int) -> None:
        """Puts `state` (version 0 and no field for a state never written) only if the key is absent."""
        ...

    async def write(
        self,
        key: str,
        changes: dict[str, HeldField | None],
        *,
        replace: bool,
        if_version: int | None,
        now_ms: int,
        ttl_s: int,
    ) -> WriteOutcome:
        """`changes` names the fields written (None removes one); `replace` drops every other field. Checks
        the version and stores the state whole: no size is ever a reason to drop it."""
        ...

    async def forget(self, keys: Sequence[str]) -> None: ...

    async def rewrite(self, key: str, state: HeldState, ttl_s: int) -> bool:
        """Puts `state` in place of the one under `key` only while that one is still at `state.version`: an
        erasure redacts a state no writer moved meanwhile. False when the key is gone or moved on."""
        ...

    async def track(self, space_id: UUID, marks: Sequence[bytes], tag: str, ttl_s: int) -> None:
        """Remembers, under each mark, the state `tag` names: an erasure finds by it the states the database
        does not hold yet, and their copies."""
        ...

    async def tracked(self, space_id: UUID, marks: Sequence[bytes]) -> list[str]:
        """The tags remembered under these marks."""
        ...


class DurableBatch(Protocol):
    states: list[DurableState]

    async def ack(self) -> None: ...

    async def retry_later(self) -> None: ...


class AgentStateLog(Protocol):
    """The confirmed queue between a stored write and the database (06, 15.3)."""

    async def publish(self, states: Sequence[DurableState], timeout_s: float) -> bool:
        """True once the broker confirmed every state; False when it did not in time."""
        ...

    def batches(self, max_items: int) -> AsyncIterator[DurableBatch]: ...


class SharedHot(Protocol):
    """The hot copy of a space's working set of shared objects (24.3.12; 06, 15.3), in the cache store: each
    object under its keyed digest, with, per field and source, the version and the sealed observation. A push
    is decided here, atomically per request, and the objects it moved wait in the space's dirty set for the
    consolidator. Losing it costs freshness, never correctness: the database has what was consolidated."""

    async def push(self, space_id: UUID, items: Sequence[tuple[str, int, Mapping[str, bytes]]]) -> list[str]:
        """Each item is (digest, version, {`field|source`: payload}); returns `applied`, `stale_version` or
        `out_of_set` per item, in order, and marks the applied objects dirty."""
        ...

    async def enter(
        self,
        space_id: UUID,
        entries: Sequence[tuple[str, UUID, Mapping[str, tuple[int, bytes]], int, bytes | None, int]],
    ) -> None:
        """Each entry is (digest, object id, what the database holds of it by `field|source` as (version,
        observation), seconds to keep it, its typed stamps, their sequence): an object not held yet is seeded
        with those fields and stamps; one held keeps its own, as new or newer. Either stays for the
        seconds."""
        ...

    async def load(
        self, space_id: UUID, digests: Sequence[str]
    ) -> list[tuple[UUID, dict[str, tuple[int, bytes]], bytes | None] | None]:
        """Each object held in the working set, as its id, by `field|source` (version, observation), and its
        typed stamps."""
        ...

    async def take_dirty(self, space_id: UUID, limit: int) -> list[str]: ...

    async def mark_dirty(self, space_id: UUID, digests: Sequence[str]) -> None: ...

    async def loaded(self, space_id: UUID) -> bool:
        """Whether the working set was loaded into this store since it last came up empty."""
        ...

    async def mark_loaded(self, space_id: UUID) -> None: ...

    async def set_typed(self, space_id: UUID, entries: Sequence[tuple[str, bytes, int]]) -> None:
        """(digest, typed stamps, their sequence): the stamps as a write stored them, on the objects still
        held, unless the held ones have a greater sequence."""
        ...

    async def mark_typed(self, space_id: UUID, object_ids: Sequence[UUID]) -> None:
        """Objects of typed shared types the consolidator wrote, for the typed pass; on the store that evicts
        nothing."""
        ...

    async def take_typed(self, space_id: UUID, limit: int) -> list[UUID]: ...

    async def note_drift(self, space_id: UUID, entries: Sequence[bytes]) -> None:
        """Drift findings for the consolidator to open, on the store that evicts nothing; the same finding
        twice is kept once."""
        ...

    async def take_drift(self, space_id: UUID, limit: int) -> list[bytes]: ...

    async def forget(self, space_id: UUID, digests: Sequence[str]) -> None: ...


@dataclass(frozen=True, slots=True)
class SeenInterest:
    """An object the subject showed interest in, with the values they were shown of its type's tracked
    fields: what `changes_since_seen` compares against."""

    ref: str
    reason: str
    at: datetime
    shown: Mapping[str, Any]
    condition: str | None = None


class SubjectInterests(Protocol):
    """The subject's interests, as the signals module keeps them (24.3.2): on the read path, with no
    statement while warm."""

    async def interests(self, space_id: UUID, handle_ids: Sequence[UUID]) -> list[SeenInterest]: ...


class NoInterests:
    """The interests of a service built without the `signals` module: none."""

    async def interests(self, space_id: UUID, handle_ids: Sequence[UUID]) -> list[SeenInterest]:  # noqa: ARG002
        return []


class WorkingSet(Protocol):
    """What brings a shared object into the working set from another module (24.3.12): a standalone
    interaction that presented or engaged it."""

    async def enter_working_set(self, space_id: UUID, refs: Sequence[str], reason: str) -> int:
        """`refs` as `type:namespace:id`; returns how many are in the working set now. One statement."""
        ...


class WatchRevalidation(Protocol):
    """The objects module's side of a `watch_revalidation` no fresh value answered (24.3.12)."""

    async def unconfirmed(self, space_id: UUID, object_ids: Sequence[UUID]) -> None:
        """The due watches of these objects whose condition holds fire with `revalidated: false`, or are
        dropped, as each type's `unconfirmed_watch` says."""
        ...


class RefreshWants(Protocol):
    """Where the refresh requests a read or a write admitted wait for the database, in the state store
    (24.3.5): the limiter of each object and reason, the queue a per-space loop writes in one statement,
    and the mark of each object whose request is outstanding, with its reservation."""

    async def claim(self, space_id: UUID, key: str, ttl_s: int) -> str | None:
        """Takes the limiter `key` for `ttl_s` seconds; None when taken now, else what the earlier one left
        (`admitted` or `denied`)."""
        ...

    async def settle_claim(self, space_id: UUID, key: str, outcome: str, ttl_s: int) -> None: ...

    async def enqueue(
        self, space_id: UUID, requests: Sequence[bytes], pending: Mapping[str, bytes], ttl_s: int
    ) -> None:
        """Queues the requests for the database and marks their objects outstanding (object id -> the
        reservation), for `ttl_s` seconds."""
        ...

    async def take(self, space_id: UUID, limit: int) -> list[bytes]: ...

    async def requeue(self, space_id: UUID, requests: Sequence[bytes]) -> None: ...

    async def outstanding(self, space_id: UUID, object_ids: Sequence[str]) -> dict[str, bytes]:
        """The objects of these that have a request outstanding, each with its reservation; taken off."""
        ...

    async def answered(self, space_id: UUID, answers: Sequence[bytes], ttl_s: int) -> None:
        """Queues what the pushes that name a request brought, for the consolidator to settle."""
        ...

    async def take_answered(self, space_id: UUID, limit: int) -> list[bytes]: ...

    async def requeue_answered(self, space_id: UUID, answers: Sequence[bytes]) -> None: ...

    async def worker_seen(self, space_id: UUID, ttl_s: int) -> None:
        """A resolver worker took requests: one is there for the next `ttl_s` seconds."""
        ...

    async def has_worker(self, space_id: UUID) -> bool: ...


class ObservedDrift(Protocol):
    async def observed(
        self, uow: UnitOfWork, space: SpaceConfig, key: ObjectKey, report: Report, names: Mapping[UUID, str]
    ) -> None:
        """Inside the transaction that merges `report` into the object `key` of a declared type: what of it
        the type does not declare goes back to the company (object-type spec 3.2.2)."""
        ...


@dataclass(frozen=True, slots=True)
class DriftNoticed:
    """A field, or a state, of a report on an object that its type does not declare: names only."""

    type: str
    field: str
    source: str
    ref: str
    kind: Literal["drift", "field_held"] = "drift"
    """`field_held`: a new field that may be personal data or free text, held from agents until declared."""


class SharedDrift(Protocol):
    """Drift by observation on shared objects, whose pushes are decided with no database statement: what a
    report shows is noticed where it is decided, and opened later, by the consolidator's transaction."""

    async def noticed(
        self, space: SpaceConfig, type_: str, ref: str, report: Report, names: Mapping[UUID, str]
    ) -> list[DriftNoticed]:
        """What of `report` the type does not declare, less what this process reported within the hour."""
        ...

    async def open_noticed(
        self, uow: UnitOfWork, space: SpaceConfig, noticed: Sequence[DriftNoticed]
    ) -> None: ...
