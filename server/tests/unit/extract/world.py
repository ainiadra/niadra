"""A small world of sessions, events, profiles and objects behind fakes of other modules' ports.

The extract and memory tests inject these through `PatchedUnitOfWorkFactory`, so they never depend
on another module's in-memory repository.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.adapters.memory.fakes import DeterministicDecider, DeterministicModels, ScriptedLlm
from niadra.app.decide import DecideService
from niadra.app.extract import ExtractService
from niadra.app.memory import MemoryService
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.vectors import PendingVector
from niadra.domain.records import (
    AgentAction,
    BusinessObject,
    EventRecord,
    HandleRecord,
    LinkRecord,
    ObjectKey,
    ProfileRecord,
    SessionRecord,
)
from niadra.domain.sessions.rules import session_of
from niadra.domain.vocabulary import (
    ActionStatus,
    EventKind,
    HandleStatus,
    HandleType,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, PatchedUnitOfWorkFactory


@dataclass
class World:
    now: datetime
    sessions: dict[UUID, SessionRecord] = field(default_factory=dict)
    events: list[EventRecord] = field(default_factory=list)
    profiles: dict[UUID, ProfileRecord] = field(default_factory=dict)
    handles: dict[UUID, HandleRecord] = field(default_factory=dict)
    links: list[LinkRecord] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    objects: list[BusinessObject] = field(default_factory=list)
    participants: dict[UUID, list[tuple[UUID, str]]] = field(default_factory=dict)
    actions: list[AgentAction] = field(default_factory=list)

    def customer(self, *types: HandleType, level: Verification = Verification.V2) -> ProfileRecord:
        handle_ids = []
        for t in types or (HandleType.PHONE_E164,):
            handle = HandleRecord(
                uuid7(),
                t,
                "",
                SubjectKind.PERSON,
                f"v-{len(self.handles)}",
                HandleStatus.ACTIVE,
                level,
                self.now,
                self.now,
            )
            self.handles[handle.handle_id] = handle
            handle_ids.append(handle.handle_id)
        profile = ProfileRecord(
            uuid7(), SubjectKind.PERSON, f"p{len(self.profiles)}", 1, 1, tuple(handle_ids)
        )
        self.profiles[profile.profile_id] = profile
        return profile

    def session(
        self,
        profile: ProfileRecord,
        turns: Sequence[tuple[Speaker, str] | EventRecord],
        *,
        channel: str = "whatsapp",
        source_id: UUID = WHATSAPP_SOURCE,
        start: datetime | None = None,
        level: Verification | None = Verification.V2,
    ) -> SessionRecord:
        start = start or self.now - timedelta(minutes=30)
        conversation = f"c-{len(self.sessions)}"
        at = start
        for turn in turns:
            at += timedelta(minutes=1)
            if isinstance(turn, EventRecord):
                event = turn
            else:
                speaker, text = turn
                event = EventRecord(
                    event_id=uuid7(),
                    kind=EventKind.MESSAGE,
                    source_id=source_id,
                    channel=channel,
                    speaker=speaker,
                    occurred_at=at,
                    received_at=at,
                    handle_ids=profile.handle_ids[:1],
                    text=text,
                )
            self.events.append(_with_conversation(event, conversation))
        session = SessionRecord(
            session_id=uuid7(),
            kind="conversation",
            source_id=source_id,
            channel=channel,
            opened_at=start,
            last_event_at=at,
            conversation_id=conversation,
            profile_id=profile.profile_id,
            closed_at=at + timedelta(minutes=5),
            proven_level=level,
        )
        self.sessions[session.session_id] = session
        return session

    def session_events(self, session: SessionRecord) -> list[EventRecord]:
        return sorted(
            (e for e in self.events if e.conversation_id == session.conversation_id),
            key=lambda e: e.occurred_at,
        )

    def patch(self, cell: Cell) -> PatchedUnitOfWorkFactory:
        return PatchedUnitOfWorkFactory(
            cell.uow,
            sessions=FakeSessions(self),
            ingest=FakeEvents(self),
            identity=FakeIdentity(self),
            audit=FakeAudit(self),
            objects=FakeObjects(self),
        )


def _with_conversation(event: EventRecord, conversation: str) -> EventRecord:
    return replace(event, conversation_id=conversation)


class FakeSessions:
    def __init__(self, world: World) -> None:
        self.w = world

    async def get_session(self, session_id: UUID) -> SessionRecord | None:
        return self.w.sessions.get(session_id)

    async def profiles_active_since(self, since: datetime, *, limit: int) -> list[UUID]:
        found = {s.profile_id for s in self.w.sessions.values() if s.profile_id and s.last_event_at >= since}
        return sorted(found)[:limit]

    async def closed_for_profile(
        self, profile_id: UUID, closed_since: datetime, *, limit: int
    ) -> list[SessionRecord]:
        found = [
            s
            for s in self.w.sessions.values()
            if s.profile_id == profile_id and s.closed_at is not None and s.closed_at >= closed_since
        ]
        return sorted(found, key=lambda s: (s.closed_at, s.session_id))[-limit:]

    async def sessions_of(self, events: Sequence[EventRecord]) -> dict[UUID, UUID]:
        found = {}
        for event in events:
            if (session := session_of(event, self.w.sessions.values())) is not None:
                found[event.event_id] = session.session_id
        return found


class FakeEvents:
    def __init__(self, world: World) -> None:
        self.w = world

    async def get_events(self, event_ids: Sequence[UUID]) -> list[EventRecord]:
        wanted = set(event_ids)
        return [e for e in self.w.events if e.event_id in wanted]

    async def mark_content(self, event_ids: Sequence[UUID], marks: dict[str, Any]) -> None:
        wanted = set(event_ids)
        self.w.events = [
            replace(e, content_meta={**(e.content_meta or {}), **marks}) if e.event_id in wanted else e
            for e in self.w.events
        ]

    async def events_between(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        since: datetime,
        until: datetime,
    ) -> list[EventRecord]:
        found = [
            e
            for e in self.w.events
            if (e.conversation_id == conversation_id if conversation_id else e.task_id == task_id)
            and since <= e.occurred_at <= until
        ]
        return sorted(found, key=lambda e: e.occurred_at)

    async def system_events_for_handles(
        self, handle_ids: Sequence[UUID], since: datetime, limit: int
    ) -> list[EventRecord]:
        wanted = set(handle_ids)
        found = [
            e
            for e in self.w.events
            if e.kind is EventKind.SYSTEM_EVENT and wanted & set(e.handle_ids) and e.received_at >= since
        ]
        return sorted(found, key=lambda e: e.received_at, reverse=True)[:limit]


class FakeIdentity:
    def __init__(self, world: World) -> None:
        self.w = world

    async def get_profile(self, profile_id: UUID) -> ProfileRecord | None:
        return self.w.profiles.get(profile_id)

    async def get_handles(self, handle_ids: Sequence[UUID]) -> list[HandleRecord]:
        return [self.w.handles[h] for h in handle_ids if h in self.w.handles]

    async def profile_of(self, handle_id: UUID) -> ProfileRecord | None:
        return next((p for p in self.w.profiles.values() if handle_id in p.handle_ids), None)

    async def memberships(self, handle_ids: Sequence[UUID]) -> dict[UUID, UUID]:
        return {h: p.profile_id for p in self.w.profiles.values() for h in p.handle_ids if h in handle_ids}

    async def active_links(self, handle_ids: Sequence[UUID]) -> list[LinkRecord]:
        wanted = set(handle_ids)
        return [l for l in self.w.links if l.person_handle_id in wanted or l.org_handle_id in wanted]  # noqa: E741


class FakeAudit:
    def __init__(self, world: World) -> None:
        self.w = world

    async def receipts_for_conversation(
        self, conversation_id: str, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        return [r for r in self.w.receipts if r.get("conversation_id") == conversation_id]


class FakeObjects:
    def __init__(self, world: World) -> None:
        self.w = world

    async def get_object(self, key: ObjectKey) -> BusinessObject | None:
        return next((o for o in self.w.objects if o.key == key), None)

    async def objects_for(self, handle_ids: Sequence[UUID], *, limit: int = 50) -> list[BusinessObject]:
        return self.w.objects[:limit]

    async def get_objects(self, keys: Sequence[ObjectKey]) -> list[BusinessObject]:
        return [o for o in self.w.objects if o.key in keys]

    async def add_participants(self, object_id: UUID, participants: Sequence[tuple[UUID, str]]) -> None:
        mine = self.w.participants.setdefault(object_id, [])
        mine += [p for p in participants if p not in mine]

    async def participants_of(
        self, object_ids: Sequence[UUID], *, per_object: int
    ) -> dict[UUID, list[tuple[UUID, str]]]:
        return {o: list(p)[:per_object] for o, p in self.w.participants.items() if o in object_ids}

    async def actions_for(
        self, handle_ids: Sequence[UUID], *, since: datetime | None = None, limit: int = 100
    ) -> list[AgentAction]:
        return [a for a in self.w.actions if since is None or a.occurred_at >= since][:limit]

    async def set_action_status(
        self, action_id: UUID, status: ActionStatus, *, confirmed_by_event_id: UUID | None = None
    ) -> None:
        self.w.actions = [
            replace(a, status=status) if a.action_id == action_id else a for a in self.w.actions
        ]

    async def unembedded(
        self, *, model: str | None, handle_ids: Sequence[UUID] | None = None, limit: int
    ) -> list[PendingVector]:
        # The objects module's vectors are its own tests' concern; here nothing waits for one.
        return []


def add_action(
    world: World,
    key: ObjectKey,
    operation: str,
    at: datetime,
    *,
    source_id: UUID,
    status: ActionStatus = ActionStatus.CONFIRMED,
    closes_item_id: UUID | None = None,
    event_id: UUID | None = None,
    result: str | None = None,
) -> AgentAction:
    obj = next((o for o in world.objects if o.key == key), None)
    if obj is None:
        obj = BusinessObject(uuid7(), key, {}, at, source_id)
        world.objects.append(obj)
    action = AgentAction(
        action_id=uuid7(),
        event_id=event_id or uuid7(),
        source_id=source_id,
        operation=operation,
        status=status,
        occurred_at=at,
        object_id=obj.object_id,
        result=result,
        closes_item_id=closes_item_id,
    )
    world.actions.append(action)
    return action


def closing_payload(action: AgentAction, key: ObjectKey | None, **extra: Any) -> dict[str, Any]:
    """What the objects module enqueues with `close_items` (app/objects.py)."""
    return {
        "event_id": str(action.event_id),
        "action_id": str(action.action_id),
        "object": {"type": key.type, "namespace": key.namespace, "id": key.external_id} if key else None,
        "operation": action.operation,
        "closes": None,
        "handle_ids": [],
        "occurred_at": action.occurred_at.isoformat(),
        **extra,
    }


@dataclass
class Services:
    world: World
    cell: Cell
    decide: DecideService
    extract: ExtractService
    memory: MemoryService

    @property
    def space_id(self) -> UUID:
        return SPACE_ID


def services(
    cell: Cell, world: World, llm: ScriptedLlm | None = None, models: Any = None, decider: Any = None
) -> Services:
    uow = world.patch(cell)
    decide = DecideService(
        uow, models or DeterministicModels(), cell.clock, decider=decider or DeterministicDecider()
    )
    llm = llm or cell.llm
    return Services(
        world=world,
        cell=cell,
        decide=decide,
        extract=ExtractService(uow, llm, decide, cell.clock, cell.config),
        memory=MemoryService(uow, models or DeterministicModels(), decide, cell.clock, cell.config),
    )
