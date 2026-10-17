"""Small stand-ins for the ports of other modules that the read path reads (rule 5 of the brief).

`World` holds a few profiles' memory and hands out one fake per port, ready for
`PatchedUnitOfWorkFactory(cell.uow, **world.repos())`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.domain.compile import MemorySnapshot
from niadra.domain.compile.model import ItemKind
from niadra.domain.memory.facts import Relation
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.records import (
    AgentAction,
    BusinessObject,
    Episode,
    EventRecord,
    Fact,
    HandleRecord,
    LinkRecord,
    ObjectKey,
    OpenItem,
    ProfileAttr,
    ProfileRecord,
    SessionRecord,
    Trait,
)
from niadra.domain.vocabulary import FactStatus, HandleType, OpenItemStatus, Verification


@dataclass
class World:
    snapshots: list[MemorySnapshot] = field(default_factory=list)
    handles: list[HandleRecord] = field(default_factory=list)
    links: list[LinkRecord] = field(default_factory=list)
    proven: Verification | None = None
    degraded: set[str] = field(default_factory=set)
    feed: list[dict[str, Any]] = field(default_factory=list)
    epoch: int = 0
    last_event_at: dict[UUID, datetime] = field(default_factory=dict)
    vectors: list[ItemVector] = field(default_factory=list)
    """What the write side stored for history search; empty, every row ranks by its words alone."""
    earlier_episodes: list[Episode] = field(default_factory=list)
    """Versions a re-extraction replaced, beside the current ones in the snapshots."""
    conversation_last_event: datetime | None = None
    system_events: list[EventRecord] = field(default_factory=list)
    values: list[SettledValue] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    participants: dict[UUID, list[tuple[UUID, str]]] = field(default_factory=dict)
    """Each object's participants, (handle, role)."""

    def profile(self, profile_id: UUID) -> ProfileRecord | None:
        return next((s.profile for s in self.snapshots if s.profile.profile_id == profile_id), None)

    def replace_profile(self, snapshot: MemorySnapshot) -> None:
        self.snapshots = [s for s in self.snapshots if s.profile.profile_id != snapshot.profile.profile_id]
        self.snapshots.append(snapshot)

    def repos(self) -> dict[str, object]:
        return {
            "identity": FakeIdentity(self),
            "memory": FakeMemory(self),
            "traits": FakeTraits(self),
            "objects": FakeObjects(self),
            "sessions": FakeSessions(self),
            # The hot layer's health mark is read from the cell database.
            "health": FakeSessions(self),
            "privacy": FakePrivacy(self),
            "config": FakeConfigRepo(self),
            "ingest": FakeIngest(self),
        }


_MEMORY_KINDS = frozenset({ItemKind.FACT, ItemKind.EPISODE, ItemKind.OPEN_ITEM})


class FakeIngest:
    """Only the source heartbeats the read path turns into coverage (5.2)."""

    def __init__(self, world: World) -> None:
        self._w = world

    async def source_last_event_at(self, since: datetime) -> dict[UUID, datetime]:
        return {s: at for s, at in self._w.last_event_at.items() if at >= since}

    async def system_events_for_handles(
        self, handle_ids: Sequence[UUID], since: datetime, limit: int
    ) -> list[EventRecord]:
        found = [
            e for e in self._w.system_events if e.received_at >= since and set(e.handle_ids) & set(handle_ids)
        ]
        return sorted(found, key=lambda e: (e.occurred_at, e.event_id), reverse=True)[:limit]


class FakeIdentity:
    def __init__(self, world: World) -> None:
        self._w = world

    async def find_handle(self, type: HandleType, scope: str, value: str) -> HandleRecord | None:
        return next((h for h in self._w.handles if (h.type, h.scope, h.value) == (type, scope, value)), None)

    async def get_handles(self, handle_ids: Sequence[UUID]) -> list[HandleRecord]:
        return [h for h in self._w.handles if h.handle_id in handle_ids]

    async def profile_of(self, handle_id: UUID) -> ProfileRecord | None:
        return next((s.profile for s in self._w.snapshots if handle_id in s.profile.handle_ids), None)

    async def get_profile(self, profile_id: UUID) -> ProfileRecord | None:
        return self._w.profile(profile_id)

    async def get_profiles(self, profile_ids: Sequence[UUID]) -> list[ProfileRecord]:
        return [p for i in sorted(set(profile_ids)) if (p := self._w.profile(i)) is not None]

    async def memberships(self, handle_ids: Sequence[UUID]) -> dict[UUID, UUID]:
        return {
            h: s.profile.profile_id
            for s in self._w.snapshots
            for h in s.profile.handle_ids
            if h in handle_ids
        }

    async def active_links(self, handle_ids: Sequence[UUID]) -> list[LinkRecord]:
        return [
            link
            for link in self._w.links
            if link.valid_to is None
            and (link.person_handle_id in handle_ids or link.org_handle_id in handle_ids)
        ]


class FakeMemory:
    def __init__(self, world: World) -> None:
        self._w = world

    def _all(self) -> list[MemorySnapshot]:
        return self._w.snapshots

    async def facts_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[FactStatus] = (FactStatus.ACTIVE,)
    ) -> list[Fact]:
        return [
            f
            for s in self._all()
            for f in s.facts
            if f.origin_handle_id in handle_ids and f.status in statuses
        ]

    async def episodes_for(
        self, handle_ids: Sequence[UUID], *, since: datetime | None = None, limit: int = 200
    ) -> list[Episode]:
        found = [e for s in self._all() for e in s.episodes if e.origin_handle_id in handle_ids]
        return sorted(found, key=lambda e: e.started_at, reverse=True)[:limit]

    async def get_episode(self, episode_id: UUID) -> Episode | None:
        return next((e for s in self._all() for e in s.episodes if e.episode_id == episode_id), None)

    async def episode_versions(self, origin_handle_id: UUID, episode_id: UUID) -> list[Episode]:
        found = [
            e
            for e in [*self._w.earlier_episodes, *(e for s in self._all() for e in s.episodes)]
            if e.origin_handle_id == origin_handle_id and e.episode_id == episode_id
        ]
        return sorted(found, key=lambda e: e.version)

    async def open_items_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[OpenItemStatus] = (OpenItemStatus.OPEN,)
    ) -> list[OpenItem]:
        return [
            i
            for s in self._all()
            for i in s.open_items
            if i.origin_handle_id in handle_ids and i.status in statuses
        ]

    async def object_facts(
        self, keys: Sequence[ObjectKey], handle_ids: Sequence[UUID], *, per_object: int
    ) -> list[Fact]:
        rows = [f for s in self._all() for f in s.facts]
        return [f for f in rows if f.object_ref in keys and f.origin_handle_id in handle_ids and f.audience]

    async def object_items(
        self,
        keys: Sequence[ObjectKey],
        handle_ids: Sequence[UUID],
        *,
        per_object: int,
        kept_since: datetime | None = None,
    ) -> list[OpenItem]:
        rows = [i for s in self._all() for i in s.open_items]
        return [i for i in rows if i.object in keys and i.origin_handle_id in handle_ids and i.audience]

    async def attrs_for(self, handle_ids: Sequence[UUID]) -> list[ProfileAttr]:
        return [a for s in self._all() for a in s.attrs if a.origin_handle_id in handle_ids]

    async def tail_summary(self, profile_id: UUID, level: str) -> str | None:
        snapshot = next((s for s in self._all() if s.profile.profile_id == profile_id), None)
        return dict(snapshot.summaries).get(level) if snapshot else None

    async def vectors_for(self, handle_ids: Sequence[UUID]) -> list[ItemVector]:
        return [v for v in self._w.vectors if v.kind in _MEMORY_KINDS]

    async def relations_for(self, handle_ids: Sequence[UUID], *, origin: str | None = None) -> list[Relation]:
        return [
            r
            for r in self._w.relations
            if r.a.origin_handle_id in handle_ids and (origin is None or r.origin == origin)
        ]

    async def values_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        types: Sequence[ValueType] | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[SettledValue]:
        found = [
            v
            for v in self._w.values
            if v.origin_handle_id in handle_ids
            and (types is None or v.type in types)
            and (since is None or v.said_at >= since)
        ]
        return sorted(found, key=lambda v: v.said_at, reverse=True)[:limit]

    async def values_by_keys(self, handle_ids: Sequence[UUID], keys: Sequence[str]) -> list[SettledValue]:
        return [v for v in await self.values_for(handle_ids) if v.key in set(keys)]


class FakeTraits:
    def __init__(self, world: World) -> None:
        self._w = world

    async def active_for(self, profile_id: UUID) -> list[Trait]:
        return [t for s in self._w.snapshots for t in s.traits if t.profile_id == profile_id]

    async def vectors_of(self, trait_ids: Sequence[UUID]) -> list[ItemVector]:
        return [v for v in self._w.vectors if v.kind is ItemKind.TRAIT and v.item_id in trait_ids]


class FakeObjects:
    def __init__(self, world: World) -> None:
        self._w = world

    def _objects(self) -> list[BusinessObject]:
        return [o for s in self._w.snapshots for o in s.objects]

    async def get_object(self, key: ObjectKey) -> BusinessObject | None:
        return next((o for o in self._objects() if o.key == key), None)

    async def get_object_by_id(self, object_id: UUID) -> BusinessObject | None:
        return next((o for o in self._objects() if o.object_id == object_id), None)

    async def objects_for(self, handle_ids: Sequence[UUID], *, limit: int = 50) -> list[BusinessObject]:
        return [o for o in self._objects() if o.origin_handle_id in handle_ids][:limit]

    async def participants_of(
        self, object_ids: Sequence[UUID], *, per_object: int
    ) -> dict[UUID, list[tuple[UUID, str]]]:
        return {o: list(p)[:per_object] for o, p in self._w.participants.items() if o in object_ids}

    async def actions_for(
        self, handle_ids: Sequence[UUID], *, since: datetime | None = None, limit: int = 100
    ) -> list[AgentAction]:
        owners = {
            s.profile.profile_id for s in self._w.snapshots if set(handle_ids) & set(s.profile.handle_ids)
        }
        return [a for s in self._w.snapshots if s.profile.profile_id in owners for a in s.actions][:limit]

    async def vectors_of(self, object_ids: Sequence[UUID], action_ids: Sequence[UUID]) -> list[ItemVector]:
        return [
            v
            for v in self._w.vectors
            if (v.kind is ItemKind.OBJECT and v.item_id in object_ids)
            or (v.kind is ItemKind.ACTION and v.item_id in action_ids)
        ]


class FakeSessions:
    def __init__(self, world: World) -> None:
        self._w = world

    async def proven_level(
        self, *, conversation_id: str | None = None, task_id: str | None = None, at: datetime
    ) -> Verification | None:
        return self._w.proven

    async def proof(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        at: datetime,
        minutes_by_channel: Mapping[str, int] | None = None,
    ) -> tuple[Verification, datetime | None] | None:
        return (self._w.proven, None) if self._w.proven is not None else None

    async def is_degraded(self, component: str) -> bool:
        return component in self._w.degraded

    async def open_session(
        self, *, conversation_id: str | None = None, task_id: str | None = None
    ) -> SessionRecord | None:
        at = self._w.conversation_last_event
        if at is None:
            return None
        return SessionRecord(
            uid_session(), "conversation", uid_session(), "whatsapp", at, at, conversation_id
        )


def uid_session() -> UUID:
    return UUID("0192f5a0-0000-7000-8000-00000000f5e5")


class FakePrivacy:
    def __init__(self, world: World) -> None:
        self._w = world

    async def feed_since(self, since: datetime) -> list[dict[str, object]]:
        return [row for row in self._w.feed if row["created_at"] >= since]


class FakeConfigRepo:
    def __init__(self, world: World) -> None:
        self._w = world

    async def cache_epoch(self) -> int:
        return self._w.epoch


@dataclass
class RecordingReceipts:
    """A receipt queue that confirms, refuses or hangs, as the test needs."""

    confirm: bool = True
    hang: bool = False
    sent: list[ReceiptDraft] = field(default_factory=list)
    marks: list[ProducerMark] = field(default_factory=list)

    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        if self.hang:
            import asyncio

            await asyncio.sleep(1)
        if self.confirm:
            self.sent.extend(drafts)
        return self.confirm

    async def mark(self, mark: ProducerMark) -> None:
        self.marks.append(mark)
