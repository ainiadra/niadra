"""Small stand-ins for the ports of identity, ingest, memory and sessions, shared by the operate tests
(objects, traits, triggers, notify). Other modules' in-memory repositories are never used here."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.config.models import SpaceConfig, WebhookEndpoint
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.coverage import SourceDay
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.records import (
    Episode,
    EventRecord,
    HandleRecord,
    LinkRecord,
    ObjectKey,
    OpenItem,
    ProfileRecord,
)
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import (
    EventKind,
    HandleStatus,
    HandleType,
    OpenItemStatus,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell, PatchedUnitOfWorkFactory, sample_config

T0 = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)
CRM_SECRET = "whsec_" + "c2VjcmV0LWtleS1mb3ItdGVzdHM="  # base64("secret-key-for-tests")


def at(minutes: float = 0, *, days: float = 0) -> datetime:
    return T0 + timedelta(minutes=minutes, days=days)


def handle(
    type: HandleType = HandleType.PHONE_E164,
    kind: SubjectKind = SubjectKind.PERSON,
    status: HandleStatus = HandleStatus.ACTIVE,
) -> HandleRecord:
    return HandleRecord(uuid7(), type, "", kind, "value", status, Verification.V1, T0, T0)


class FakeIdentity:
    def __init__(self) -> None:
        self.handles: dict[UUID, HandleRecord] = {}
        self.profiles: dict[UUID, ProfileRecord] = {}
        self.links: list[LinkRecord] = []

    def add_profile(
        self,
        *handles: HandleRecord,
        kind: SubjectKind = SubjectKind.PERSON,
        profile_id: UUID | None = None,
        membership_version: int = 1,
    ) -> ProfileRecord:
        profile_id = profile_id or uuid7()
        for h in handles:
            self.handles[h.handle_id] = h
        profile = ProfileRecord(
            profile_id,
            kind,
            f"pse_{str(profile_id)[-6:]}",
            membership_version,
            1,
            tuple(h.handle_id for h in handles),
        )
        self.profiles[profile_id] = profile
        return profile

    async def get_handles(self, handle_ids: Sequence[UUID]) -> list[HandleRecord]:
        return [self.handles[h] for h in handle_ids if h in self.handles]

    async def profile_of(self, handle_id: UUID) -> ProfileRecord | None:
        return next((p for p in self.profiles.values() if handle_id in p.handle_ids), None)

    async def get_profile(self, profile_id: UUID) -> ProfileRecord | None:
        return self.profiles.get(profile_id)

    async def active_links(self, handle_ids: Sequence[UUID]) -> list[LinkRecord]:
        return [
            link
            for link in self.links
            if link.person_handle_id in handle_ids or link.org_handle_id in handle_ids
        ]


@dataclass
class FakeIngest:
    events: dict[UUID, EventRecord] = field(default_factory=dict)
    last_seen: dict[UUID, datetime | None] | None = None
    days: dict[UUID, list[SourceDay]] = field(default_factory=dict)
    primary: dict[str, UUID] = field(default_factory=dict)
    """Each conversation's primary handle."""

    async def primary_handles(self, conversation_ids: Sequence[str]) -> dict[str, UUID]:
        return {c: self.primary[c] for c in conversation_ids if c in self.primary}

    def add(self, *events: EventRecord) -> None:
        for e in events:
            self.events[e.event_id] = e

    async def get_events(self, event_ids: Sequence[UUID]) -> list[EventRecord]:
        return [self.events[e] for e in event_ids if e in self.events]

    async def events_for_handles(
        self, handle_ids: Sequence[UUID], since: datetime, limit: int
    ) -> list[EventRecord]:
        found = [
            e for e in self.events.values() if set(e.handle_ids) & set(handle_ids) and e.occurred_at >= since
        ]
        return sorted(found, key=lambda e: e.occurred_at, reverse=True)[:limit]

    async def coverage(self, since: datetime) -> dict[UUID, list[SourceDay]]:
        return {s: [d for d in days if d.day >= since.date()] for s, days in self.days.items()}

    async def source_last_event_at(self, since: datetime) -> dict[UUID, datetime]:
        return {s: last for s, last in (self.last_seen or {}).items() if last is not None and last >= since}


@dataclass
class FakeMemory:
    episodes: list[Episode] = field(default_factory=list)
    items: list[OpenItem] = field(default_factory=list)

    async def episodes_for(
        self, handle_ids: Sequence[UUID], *, since: datetime | None = None, limit: int = 200
    ) -> list[Episode]:
        found = [
            e
            for e in self.episodes
            if e.origin_handle_id in handle_ids and (since is None or e.started_at >= since)
        ]
        return sorted(found, key=lambda e: e.started_at, reverse=True)[:limit]

    async def open_items_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[OpenItemStatus] = (OpenItemStatus.OPEN,)
    ) -> list[OpenItem]:
        return [i for i in self.items if i.origin_handle_id in handle_ids and i.status in statuses]


class FakeMemoryWithDue(FakeMemory):
    async def open_items_due(self, until: datetime, *, limit: int) -> list[OpenItem]:
        due = [i for i in self.items if i.due_at and i.due_at <= until and i.status is OpenItemStatus.OPEN]
        return sorted(due, key=lambda i: (i.due_at, i.item_id), reverse=True)[:limit]


@dataclass
class FakeSessions:
    active: list[UUID] = field(default_factory=list)

    async def profiles_active_since(self, since: datetime, *, limit: int) -> list[UUID]:
        return self.active[:limit]


@dataclass
class FakePipeline:
    """The `TaskStore` contract, coalescing included (5.5)."""

    outbox: list[Task] = field(default_factory=list)
    scheduled: list[tuple[Task, datetime, str]] = field(default_factory=list)
    work: dict[tuple[UUID, UUID, TaskName], tuple[int, int]] = field(default_factory=dict)

    async def enqueue(self, tasks: Sequence[Task]) -> None:
        self.outbox.extend(tasks)

    async def schedule(self, task: Task, due_at: datetime, reason: DelayReason) -> None:
        self.scheduled.append((task, due_at, DelayReason(reason)))  # the column's CHECK refuses others

    async def request_work(self, task: Task) -> None:
        assert task.profile_id is not None
        key = (task.space_id, task.profile_id, task.name)
        requested, done = self.work.get(key, (0, 0))
        self.work[key] = (requested + 1, done)
        if requested == done:
            self.outbox.append(task)

    async def begin_work(self, space_id: UUID, profile_id: UUID, name: TaskName) -> int:
        return self.work.get((space_id, profile_id, name), (0, 0))[0]

    async def finish_work(self, space_id: UUID, profile_id: UUID, name: TaskName, seen: int) -> bool:
        requested, _ = self.work.get((space_id, profile_id, name), (seen, seen))
        self.work[(space_id, profile_id, name)] = (requested, seen)
        return requested > seen

    async def work_done(self, space_id: UUID, profile_id: UUID, name: TaskName) -> int:
        return self.work.get((space_id, profile_id, name), (0, 0))[1]

    def take(self, name: TaskName) -> list[Task]:
        taken = [t for t in self.outbox if t.name is name]
        self.outbox = [t for t in self.outbox if t.name is not name]
        return taken


def event(
    kind: EventKind,
    occurred_at: datetime,
    handles: Sequence[HandleRecord],
    *,
    objects: Sequence[ObjectKey] = (),
    canonical_type: str | None = None,
    fields: dict[str, Any] | None = None,
    action: dict[str, Any] | None = None,
    source_id: UUID = BILLING_SOURCE,
    channel: str = "erp",
) -> EventRecord:
    speaker = Speaker.CUSTOMER if kind is EventKind.MESSAGE else Speaker.SYSTEM
    return EventRecord(
        event_id=uuid7(),
        kind=kind,
        source_id=source_id,
        channel=channel,
        speaker=speaker,
        occurred_at=occurred_at,
        received_at=occurred_at,
        handle_ids=tuple(h.handle_id for h in handles),
        canonical_type=canonical_type,
        fields=fields or {},
        object_refs=tuple(objects),
        action=action,
    )


def episode(
    origin: HandleRecord,
    started_at: datetime,
    *,
    intent: str = "complaint",
    category: str = "technical_visit",
    channel: str = "whatsapp",
    outcome: str = "resolved",
    sentiment: float | None = None,
    initiated_by: str | None = "customer",
) -> Episode:
    return Episode(
        episode_id=uuid7(),
        version=1,
        origin_handle_id=origin.handle_id,
        session_id=uuid7(),
        channel=channel,
        source_id=BILLING_SOURCE,
        summary="s",
        intent=intent,
        category=category,
        normalized_category=category,
        outcome=outcome,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=10),
        sentiment=sentiment,
        initiated_by=initiated_by,
    )


def open_item(origin: HandleRecord, due_at: datetime | None, *, owner: str = "company") -> OpenItem:
    return OpenItem(
        item_id=uuid7(),
        origin_handle_id=origin.handle_id,
        description="d",
        owner=owner,
        status=OpenItemStatus.OPEN,
        created_at=T0 - timedelta(days=1),
        origin={},
        due_at=due_at,
    )


CRM_REF = f"spaces/{SPACE_ID}/webhook/crm"
CRM = WebhookEndpoint(
    endpoint_id="crm",
    url="https://crm.example/hooks",
    source_id=BILLING_SOURCE,
    event_types=["*"],
    secret_refs=[CRM_REF],
)


def config_with(**update: Any) -> SpaceConfig:
    return sample_config().model_copy(update={"webhook_endpoints": [CRM], **update})


@dataclass
class World:
    """A cell whose foreign ports are fakes; the operate modules' own repositories stay in memory."""

    cell: Cell
    identity: FakeIdentity = field(default_factory=FakeIdentity)
    ingest: FakeIngest = field(default_factory=FakeIngest)
    memory: FakeMemoryWithDue = field(default_factory=FakeMemoryWithDue)
    sessions: FakeSessions = field(default_factory=FakeSessions)
    pipeline: FakePipeline = field(default_factory=FakePipeline)

    @property
    def uow(self) -> PatchedUnitOfWorkFactory:
        return PatchedUnitOfWorkFactory(
            self.cell.uow,
            identity=self.identity,
            ingest=self.ingest,
            memory=self.memory,
            sessions=self.sessions,
            pipeline=self.pipeline,
        )
