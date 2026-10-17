"""Who a read is about, kept in the read process between reads (5.11; 06, 3).

A warm read answers without the database: the customer behind a handle (or a profile id), what the draws of
the space's experiments need of that customer (6.4), and the proof a conversation holds come from here, and
only a miss reads PostgreSQL. What makes that safe is the feed of
changes each space's database keeps (`read_invalidations`, written by triggers on handles, profiles,
memberships and sessions, so no writer can forget it), polled with the privacy feed every 2 s:

- a change drops every entry it touches at the next poll, so a number that changed hands, a split, an
  erasure or a closed conversation reaches warm reads within one poll, the bound the privacy feed already
  gives cached packs;
- a space whose feeds were not polled in the last `FRESH_FOR_S` serves nothing from here (a failing poll
  sends reads back to the database, never to stale state), and a pause longer than `GAP_S` between two
  polls of a space drops everything it held, since the feed's window may no longer cover the pause;
- a load that raced a change (read before it, stored after its invalidation) is refused on the way in.

A handle in doubt, an unknown one or a customer seen for the first time is never kept: only a read that
found an active handle, or a proof, is. A proof is kept only to serve reads that need no more than it
proves; a read that asks for more reads the database, so a `verify` counts at once.

Tests on the in-memory unit of work call `invalidate` from its commit (`synchronous=True`), which stands in
for the triggers and the poll.
"""

import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from niadra.domain.vocabulary import Verification

FRESH_FOR_S = 5.0
"""A space's cached targets are served only while its feeds were polled this recently (2.5 polls)."""
GAP_S = 30.0
"""A longer pause between two polls of a space drops what it cached: the feed's window (120 s) minus the
longest write transaction (60 s) leaves this margin, with room to spare."""
SERVED_FOR_S = 600.0
"""A space read within this is polled even when its connection pool went idle: warm reads never open one."""
TOUCHED_FOR_S = 300.0
MAX_TARGETS = 50_000
MAX_PROOFS = 50_000

K = TypeVar("K")
V = TypeVar("V")


def thread_key(conversation_id: str | None, task_id: str | None) -> str | None:
    """The session a proof belongs to, as `open_session` finds it: the task wins over the conversation."""
    if task_id is not None:
        return f"t:{task_id}"
    if conversation_id is not None:
        return f"c:{conversation_id}"
    return None


@dataclass(frozen=True, slots=True)
class Change:
    """One row of a space's change feed: what it touched, by id."""

    handle_ids: frozenset[UUID] = frozenset()
    profile_ids: frozenset[UUID] = frozenset()
    threads: frozenset[str] = frozenset()

    @staticmethod
    def of_row(row: Mapping[str, Any]) -> "Change":
        return Change(
            frozenset(UUID(str(h)) for h in row.get("handle_ids") or ()),
            frozenset(UUID(str(p)) for p in row.get("profile_ids") or ()),
            frozenset(str(t) for t in row.get("threads") or ()),
        )


@dataclass(frozen=True, slots=True)
class _Entry:
    value: Any
    handle_ids: frozenset[UUID]
    profile_id: UUID
    loaded: float


@dataclass(frozen=True, slots=True)
class _Proof:
    level: Verification
    until: datetime | None
    loaded: float


@dataclass(slots=True)
class _Space:
    polled: float | None = None
    read: float = 0.0
    targets: "OrderedDict[Any, _Entry]" = field(default_factory=OrderedDict)
    by_id: dict[UUID, set[Any]] = field(default_factory=dict)
    """Every handle and profile id to the targets that hold it, so a change finds them without a scan."""
    proofs: "OrderedDict[str, _Proof]" = field(default_factory=OrderedDict)
    touched: dict[Any, float] = field(default_factory=dict)
    seen: dict[UUID, float] = field(default_factory=dict)
    """Feed rows applied, by id, for the overlap of two polls' windows."""


class ReadState:
    def __init__(self, *, synchronous: bool = False, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._synchronous = synchronous
        self._now = monotonic
        self._spaces: dict[UUID, _Space] = {}

    # Reads.

    def started(self) -> float:
        """The moment a load from the database starts; `store` refuses it if a change came after."""
        return self._now()

    def target(self, space_id: UUID, key: Any) -> Any | None:
        space = self._serving(space_id)
        if space is None:
            return None
        entry = space.targets.get(key)
        if entry is None:
            return None
        space.targets.move_to_end(key)
        return entry.value

    def store_target(
        self,
        space_id: UUID,
        key: Any,
        value: Any,
        *,
        handle_ids: Iterable[UUID],
        profile_id: UUID,
        started: float,
    ) -> None:
        space = self._space(space_id)
        handles = frozenset(handle_ids)
        if self._raced(space, started, (*handles, profile_id)):
            return
        _drop(space, key)
        space.targets[key] = _Entry(value, handles, profile_id, self._now())
        for held in (*handles, profile_id):
            space.by_id.setdefault(held, set()).add(key)
        while len(space.targets) > MAX_TARGETS:
            _drop(space, next(iter(space.targets)))

    def proof(self, space_id: UUID, thread: str, needed: Verification, at: datetime) -> Verification | None:
        """The conversation's proof when it covers `needed` and is still valid at `at`; else None, and the
        read asks the database."""
        space = self._serving(space_id)
        if space is None:
            return None
        found = space.proofs.get(thread)
        if found is None or found.level.rank < needed.rank or (found.until is not None and found.until <= at):
            return None
        return found.level

    def store_proof(
        self, space_id: UUID, thread: str, level: Verification, until: datetime | None, *, started: float
    ) -> None:
        space = self._space(space_id)
        if self._raced(space, started, (thread,)):
            return
        space.proofs[thread] = _Proof(level, until, self._now())
        space.proofs.move_to_end(thread)
        _trim(space.proofs, MAX_PROOFS)

    def read(self, space_id: UUID) -> None:
        """A read of the space: it stays in the polled set while reads keep coming."""
        self._space(space_id).read = self._now()

    # The feed.

    def spaces_to_poll(self, active: Iterable[UUID]) -> list[UUID]:
        """The spaces with an open pool and the ones read recently; the others' entries leave."""
        now = self._now()
        for space_id, space in list(self._spaces.items()):
            if now - space.read > SERVED_FOR_S:
                del self._spaces[space_id]
        return sorted({*active, *self._spaces})

    def polled(self, space_id: UUID, rows: Iterable[Mapping[str, Any]]) -> None:
        """A successful poll of the space's feeds: every row not applied yet drops what it touches."""
        now = self._now()
        space = self._space(space_id)
        if space.polled is None or now - space.polled > GAP_S:
            # The first poll of the space, or one after a long pause: its window reaches back far enough for
            # what was loaded in the last `GAP_S` (a change committed after a load started less than 60 s
            # before it, the longest write transaction), and no further; what is older is dropped.
            for key in [k for k, e in space.targets.items() if e.loaded < now - GAP_S]:
                _drop(space, key)
            for thread in [t for t, p in space.proofs.items() if p.loaded < now - GAP_S]:
                del space.proofs[thread]
        for row in rows:
            row_id = UUID(str(row["id"]))
            if row_id in space.seen:
                continue
            space.seen[row_id] = now
            self._apply(space, Change.of_row(row), now)
        space.polled = now
        _expire(space.seen, now - 2 * TOUCHED_FOR_S)
        _expire(space.touched, now - TOUCHED_FOR_S)

    def invalidate(self, space_id: UUID, change: Change) -> None:
        """A change applied at once: the in-memory unit of work's commit, or the privacy feed's rows."""
        self._apply(self._space(space_id), change, self._now())

    def __len__(self) -> int:
        return sum(len(s.targets) + len(s.proofs) for s in self._spaces.values())

    # Internals.

    def _serving(self, space_id: UUID) -> _Space | None:
        space = self._spaces.get(space_id)
        if space is None:
            return None
        if not self._synchronous and (space.polled is None or self._now() - space.polled > FRESH_FOR_S):
            return None
        return space

    def _space(self, space_id: UUID) -> _Space:
        space = self._spaces.get(space_id)
        if space is None:
            space = self._spaces[space_id] = _Space(read=self._now())
        return space

    @staticmethod
    def _raced(space: _Space, started: float, keys: Iterable[Any]) -> bool:
        return any(space.touched.get(k, -1.0) >= started for k in keys)

    @staticmethod
    def _apply(space: _Space, change: Change, now: float) -> None:
        for key in (*change.handle_ids, *change.profile_ids, *change.threads):
            space.touched[key] = now
        for held in (*change.handle_ids, *change.profile_ids):
            for key in list(space.by_id.get(held, ())):
                _drop(space, key)
        for thread in change.threads:
            space.proofs.pop(thread, None)


def _drop(space: _Space, key: Any) -> None:
    entry = space.targets.pop(key, None)
    if entry is None:
        return
    for held in (*entry.handle_ids, entry.profile_id):
        keys = space.by_id.get(held)
        if keys is not None:
            keys.discard(key)
            if not keys:
                del space.by_id[held]


def _trim(table: "OrderedDict[K, V]", limit: int) -> None:
    while len(table) > limit:
        table.popitem(last=False)


def _expire(table: dict[Any, float], before: float) -> None:
    for key in [k for k, at in table.items() if at < before]:
        del table[key]
