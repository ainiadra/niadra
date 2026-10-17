"""The typed state's own tables (24.3.6 to 24.3.8): computed values' versions, timers and the inputs of
derived objects. Owned by the `objects` module."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.state.shared import SharedKey
from niadra.domain.state.typed import NewValue, ScanMark, TimerWrite


@dataclass(frozen=True, slots=True)
class FiredTimer:
    timer_id: UUID
    object_id: UUID
    name: str
    due_at: datetime | None
    fired_at: datetime
    value_version: int | None
    supersedes_firing_id: UUID | None


@dataclass(frozen=True, slots=True)
class SharedRow:
    """A shared object as `shared_objects` keeps it: `state` and `meta` are `{field: {source: ...}}`."""

    object_id: UUID
    key: SharedKey
    state: dict[str, Any]
    meta: dict[str, Any]
    hot_until: datetime | None
    last_referenced_at: datetime
    watch_count: int = 0
    derived_status: str | None = None


@dataclass(frozen=True, slots=True)
class SharedWrite:
    """What a typed pass writes of one shared object besides its values and timers: its typed stamps
    (`shared_part`), for a derived object the moment it was last computed (`since`), and, when they changed,
    the (input object, input name) pairs it is bound to."""

    stamps: Mapping[str, Any]
    since: datetime | None = None
    bound: Sequence[tuple[UUID, str]] | None = None
    expired_by: Sequence[str] = ()
    """Inputs it is bound to in this write that changed in the same pass: it expires by them at once, since
    nothing tells whether it was computed before or after the change."""


@dataclass(frozen=True, slots=True)
class SharedStamps:
    """A shared object's typed stamps as a write left them stored, for its hot copy."""

    object_id: UUID
    key: SharedKey
    stamps: dict[str, Any]
    derived_status: str | None
    expired: bool = False
    """The write expired it, since an input changed."""


@dataclass(frozen=True, slots=True)
class WatchRow:
    watch_id: UUID
    object_id: UUID
    handle_id: UUID
    condition: str
    consent_event_key: str
    expires_at: datetime
    last_fired_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RefreshRow:
    """A request for the company's resolver worker to fetch an object again (24.3.5)."""

    request_id: UUID
    object_id: UUID
    key: SharedKey
    reason: str
    priority: str
    budget_units: int
    requested_at: datetime
    reservation: dict[str, Any] | None = None
    lease_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class ScanRow:
    """The scan state of one content, as `content_scans` keeps it."""

    content_ref: str
    object_id: UUID
    field: str
    sha256: str
    state: str
    reason: str | None = None


def content_ref(object_id: UUID, field: str, sha256: str) -> str:
    """The content a field held, named by its object, its field and its digest."""
    return f"{object_id}/{field}/{sha256[:32]}"


class TypedRepository(Protocol):
    async def add_values(self, values: Mapping[UUID, Sequence[NewValue]], event_id: UUID | None) -> None:
        """Records the versions of each object's values in one statement, each value sealed; a version already
        recorded stays."""
        ...

    async def write_timers(self, writes: Mapping[UUID, Sequence[TimerWrite]], now: datetime) -> None:
        """Arms, moves or cancels each object's timers in one statement. A timer the sweep already fired keeps
        its firing: only an armed row moves."""
        ...

    async def fire_due(self, now: datetime, *, limit: int) -> list[FiredTimer]:
        """Fires up to `limit` armed timers of the customers' objects due by `now`, in one statement, and
        returns them."""
        ...

    async def fire_shared(self, now: datetime, *, limit: int) -> list[FiredTimer]:
        """Fires up to `limit` armed timers of the shared objects due by `now` and marks each fired in its
        object's typed stamps, where they still name that firing, in one statement. A timer or an object a
        typed pass is writing is left for the next sweep, so the two never wait on each other."""
        ...

    async def bind_inputs(
        self, dependent_id: UUID, inputs: Sequence[tuple[UUID, str]], now: datetime
    ) -> None:
        """Replaces what a derived object depends on, in one statement: (input object, input name) pairs, the
        name as the type declares it (`lead.city`)."""
        ...

    async def enter_working_set(
        self, entries: Sequence[tuple[UUID, SharedKey, datetime, datetime]]
    ) -> list[SharedRow]:
        """(object id, key, referenced at, kept until): each object joins the working set, or stays in it
        with the later reference, in one statement; returns them as stored."""
        ...

    async def consolidate(
        self, objects: Sequence[tuple[UUID, dict[str, Any], dict[str, Any]]]
    ) -> list[tuple[UUID, int, str]]:
        """(object id, state, meta): one statement writes, per field and source, what carries a greater
        version than stored, into the objects still in the working set; returns the objects it moved, each
        with how many watches it has and its type."""
        ...

    async def write_shared(
        self,
        writes: Mapping[UUID, SharedWrite],
        values: Mapping[UUID, Sequence[NewValue]],
        timers: Mapping[UUID, Sequence[TimerWrite]],
        changed: Mapping[UUID, Sequence[str]],
        now: datetime,
    ) -> list[SharedStamps]:
        """What a typed pass of shared objects wrote, in one statement: each object's typed stamps (`META_KEY`
        of its `field_meta`), its values' versions and its timers, as `add_values` and `write_timers` do, its
        bindings; and the derived shared objects that name an input in `changed` (input object, input names)
        expire. The stamps merge with the stored ones as the row stands when the statement reaches it: a
        timer the sweep fired meanwhile stays fired, an expiry stays unless the object was computed after it,
        and the stamps' sequence moves. Returns the stamps as stored, of every object it wrote."""
        ...

    async def shared_by_id(self, object_ids: Sequence[UUID]) -> list[SharedRow]:
        """In one statement."""
        ...

    async def working_set(self, now: datetime, *, after: UUID | None, limit: int) -> list[SharedRow]:
        """The objects kept past `now`, a page in object id order after `after`."""
        ...

    async def leave_working_set(
        self, type_: str, unreferenced_since: datetime, *, limit: int, keep: Collection[UUID] = ()
    ) -> list[UUID]:
        """Up to `limit` objects of the type with no reference since the moment leave with their armed timers,
        in one statement; the objects in `keep` (under a legal hold) stay."""
        ...

    async def expire_versions(
        self, before: datetime, *, limit: int, keep_objects: Collection[UUID], keep_handles: Collection[UUID]
    ) -> int:
        """Up to `limit` versions of computed values that a newer version superseded and that were recorded
        before `before` (24.5.1: old versions are kept 90 days), in one statement; the versions of the objects
        in `keep_objects` and of the objects owned by `keep_handles` stay. The latest version always stays."""
        ...

    async def drop_orphans(self, *, limit: int) -> int:
        """The versions, timers, dependencies, refresh requests and content scans of objects that no longer
        exist, up to `limit` rows."""
        ...

    async def watch(self, watches: Sequence[WatchRow], now: datetime) -> None:
        """Keeps each watch (a newer one of the same subject and object replaces it) and counts the new ones
        on their objects, in one statement."""
        ...

    async def unwatch(self, pairs: Sequence[tuple[UUID, UUID]]) -> None:
        """(object, handle): the subject's watches of the objects go, and their counts with them."""
        ...

    async def watches_of(self, object_ids: Sequence[UUID], now: datetime) -> list[WatchRow]:
        """The watches of these objects that have not expired."""
        ...

    async def watches_fired(self, watch_ids: Sequence[UUID], at: datetime) -> None: ...

    async def watches_for(self, handle_ids: Sequence[UUID]) -> list[WatchRow]:
        """Every watch these customers asked for, expired ones too: their data package (24.5.3)."""
        ...

    async def request_refreshes(self, rows: Sequence[RefreshRow]) -> None:
        """One statement: each request is kept, and one of the same object and reason is renewed."""
        ...

    async def lease_refreshes(
        self, owner: str, now: datetime, lease_until: datetime, *, limit: int
    ) -> list[RefreshRow]:
        """One statement: up to `limit` requests not leased (or whose lease ran out), the higher priorities
        and the older first, leased to `owner`."""
        ...

    async def refreshed(self, object_ids: Sequence[UUID]) -> list[dict[str, Any]]:
        """The requests of objects that came back fresh leave, in one statement; returns their
        reservations."""
        ...

    async def give_up_refreshes(self, now: datetime, attempts: int, *, limit: int) -> list[RefreshRow]:
        """Requests leased `attempts` times whose last lease ran out leave, in one statement."""
        ...

    async def settle_refreshes(self, request_ids: Sequence[UUID]) -> list[RefreshRow]:
        """The requests the resolver worker answered or released leave, by id, in one statement."""
        ...

    async def record_scans(
        self, object_id: UUID, owner: UUID | None, scans: Mapping[str, ScanMark], now: datetime
    ) -> None:
        """The verdict on each new content of the object, in one statement."""
        ...

    async def pending_scans(self, *, limit: int) -> list[ScanRow]:
        """The oldest content still waiting for its full scan."""
        ...

    async def decide_scans(self, decisions: Sequence[ScanRow], by: str, at: datetime) -> None:
        """Each content's new state, in one statement."""
        ...

    async def release_scan(self, ref: str, by: str, reason: str, at: datetime) -> ScanRow | None:
        """A person clears flagged or pending content; None when there is no such content."""
        ...

    async def field_stamps(self, types: Sequence[str], *, limit: int) -> list[tuple[str, dict[str, Any]]]:
        """The stamps (`{field: {source: stamps}}`, never a value) of the objects of these types, customers'
        and shared, the `limit` changed last of each kind: what the coverage of the types is measured on. One
        statement."""
        ...

    async def values_named(self, rows: Sequence[tuple[UUID, str, int]]) -> dict[tuple[UUID, str, int], Any]:
        """Of these (object, name, version), the computed values that hold one, opened."""
        ...

    async def values_page(
        self, after: tuple[UUID, str, int] | None, limit: int
    ) -> list[tuple[tuple[UUID, str, int], Any]]:
        """The computed values that hold one, by (object, name, version) after `after`, opened."""
        ...

    async def rewrite_value(self, object_id: UUID, name: str, version: int, value: Any) -> None:
        """One version of a computed value in place, its stamps kept: an erasure redacts someone named in
        it."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4), after the objects' step: the target's content scans and watches, and
        the versions, timers, dependencies and refresh requests of objects that no longer exist."""
        ...
