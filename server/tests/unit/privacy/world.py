"""A small seeded cell for the erasure cascade: fake owners of the rows privacy erases.

Only the events fake honours `limit`, to exercise batching; the others erase everything at once, which
the cascade tolerates (a batch at least `limit` long just triggers one more, empty, call).

Each fake implements the participant methods of its module's port (`erase_for`, `export_rows`,
`bump_privacy_version`, ...) over plain dicts, with the semantics 8.4 asks of the owner, so the tests
exercise privacy's orchestration without depending on other modules' in-memory repositories. The
governance modules' own tables (firings, review samples, deliveries) use their real in-memory repositories.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from niadra.domain.ingest.intake import IntakeRow
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.records import (
    BusinessObject,
    EventRecord,
    HandleRecord,
    LinkRecord,
    ObjectKey,
    ProfileRecord,
)
from niadra.domain.vocabulary import (
    EventKind,
    HandleStatus,
    HandleType,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE

T0 = datetime.fromisoformat("2026-06-10T12:00:00+00:00")


@dataclass
class World:
    handles: dict[UUID, str] = field(default_factory=dict)
    profiles: dict[UUID, list[UUID]] = field(default_factory=dict)
    privacy_version: dict[UUID, int] = field(default_factory=dict)
    events: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    mentions: set[tuple[UUID, UUID]] = field(default_factory=set)
    facts: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    episodes: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    open_items: dict[UUID, UUID] = field(default_factory=dict)
    trait_evidence: set[tuple[UUID, UUID]] = field(default_factory=set)
    traits: dict[UUID, UUID] = field(default_factory=dict)
    objects: dict[UUID, UUID] = field(default_factory=dict)
    actions: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    links: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    packs: dict[str, UUID] = field(default_factory=dict)
    context_use: dict[str, UUID] = field(default_factory=dict)
    sessions: dict[UUID, UUID] = field(default_factory=dict)
    kept_intake: dict[UUID, IntakeRow] = field(default_factory=dict)
    """Batches set aside after failing every attempt."""
    change_seq: int = 0

    def next_seq(self) -> int:
        self.change_seq += 1
        return self.change_seq

    def person(self, value: str) -> tuple[UUID, UUID]:
        handle, profile = uuid4(), uuid4()
        self.handles[handle] = value
        self.profiles[profile] = [handle]
        self.privacy_version[profile] = 1
        return handle, profile

    def event(self, handle: UUID, conversation: str, text: str, at: datetime = T0) -> UUID:
        event_id = uuid4()
        self.events[event_id] = {
            "handles": {handle},
            "conversation": conversation,
            "text": text,
            "raw_ref": f"raw/{SPACE_ID}/{handle}/{event_id}.json",
            "at": at,
            "seq": self.next_seq(),
        }
        return event_id

    def fact(self, origin: UUID, conversation: str, *evidence: UUID) -> UUID:
        fact_id = uuid4()
        self.facts[fact_id] = {"origin": origin, "conversation": conversation, "seq": self.next_seq()}
        self.mentions |= {(fact_id, e) for e in evidence}
        return fact_id

    def event_record(self, event_id: UUID) -> EventRecord:
        e = self.events[event_id]
        return EventRecord(
            event_id=event_id,
            kind=EventKind.MESSAGE,
            source_id=WHATSAPP_SOURCE,
            channel="whatsapp",
            speaker=Speaker.CUSTOMER,
            occurred_at=e["at"],
            received_at=e["at"],
            handle_ids=tuple(e["handles"]),
            text=e["text"],
            conversation_id=e["conversation"],
        )


def _hits_handle(scope: ErasureScope, handle: UUID) -> bool:
    return scope.kind is not TargetKind.CONVERSATION and handle in scope.handle_ids


class FakeIngest:
    def __init__(self, world: World) -> None:
        self.w = world

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        rows = []
        for event_id, e in self.w.events.items():
            hit = (
                e["conversation"] == scope.conversation_id
                if scope.kind is TargetKind.CONVERSATION
                else bool(e["handles"] & set(scope.handle_ids))
            )
            if hit and e["text"] is not None:
                e["text"] = None
                rows.append(ErasedRow("events", {"event_id": str(event_id)}, (e["raw_ref"],), e["seq"]))
            if len(rows) == limit:
                break
        return rows

    async def known_conversation(self, conversation_id: str) -> tuple[UUID, ...] | None:
        """This world keeps no conversation rows: a conversation is known by its events."""
        return None

    async def failed_intake(
        self, limit: int, *, before: datetime | None = None, after: UUID | None = None
    ) -> list[IntakeRow]:
        assert after is None, "the retention sweep reads from the start"
        rows = [r for r in self.w.kept_intake.values() if before is None or r.received_at < before]
        return rows[:limit]

    async def drop_intake(self, intake_ids: Sequence[UUID]) -> None:
        for intake_id in intake_ids:
            self.w.kept_intake.pop(intake_id, None)

    async def conversation_names(self, scope: ErasureScope) -> list[str]:
        return []

    async def get_events(self, event_ids: Sequence[UUID]) -> list[EventRecord]:
        """Every event of this world is a customer's message: the feedback rewrite finds nothing to do."""
        return [self.w.event_record(i) for i in event_ids if i in self.w.events]

    async def rewrite_content(self, records: Sequence[EventRecord]) -> None:
        assert not records, "no feedback event in this world"

    async def events_between(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        since: datetime,
        until: datetime,
    ) -> list[EventRecord]:
        found = [i for i, e in self.w.events.items() if e["conversation"] == conversation_id]
        return [self.w.event_record(i) for i in found]

    async def events_for_handles(
        self, handle_ids: list[UUID], since: datetime, limit: int, *, before_id: UUID | None = None
    ) -> list[EventRecord]:
        found = sorted(
            (i for i, e in self.w.events.items() if e["handles"] & set(handle_ids)),
            key=lambda i: i.bytes,
            reverse=True,
        )
        found = [i for i in found if before_id is None or i.bytes < before_id.bytes]
        return [self.w.event_record(i) for i in found][:limit]

    async def archivable_events(
        self, *, since: datetime, until: datetime, after_id: UUID | None, limit: int
    ) -> list[EventRecord]:
        found = sorted(
            i
            for i, e in self.w.events.items()
            if since <= e["at"] < until and (after_id is None or i > after_id)
        )
        return [self.w.event_record(i) for i in found[:limit]]

    async def purge_archived(self, *, since: datetime, until: datetime, limit: int) -> int:
        doomed = sorted(i for i, e in self.w.events.items() if since <= e["at"] < until)[:limit]
        for event_id in doomed:
            del self.w.events[event_id]
        return len(doomed)

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        rows = []
        for event_id, e in sorted(self.w.events.items(), key=lambda kv: kv[1]["seq"]):
            if after_seq < e["seq"] <= upto_seq:
                if e["text"] is None:
                    rows.append({"event_id": str(event_id), "change_seq": e["seq"], "erased": True})
                else:
                    rows.append(
                        {
                            "event_id": str(event_id),
                            "text": e["text"],
                            "change_seq": e["seq"],
                            "handle_ids": [str(h) for h in e["handles"]],
                        }
                    )
        return rows


class FakeMemory:
    def __init__(self, world: World) -> None:
        self.w = world

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            return self._drop_evidence(set(scope.event_ids))
        rows = []
        for fact_id, f in list(self.w.facts.items()):
            if _hits_handle(scope, f["origin"]):
                del self.w.facts[fact_id]
                rows.append(ErasedRow("facts", {"fact_id": str(fact_id)}, change_seq=f["seq"]))
        for episode_id, ep in list(self.w.episodes.items()):
            if _hits_handle(scope, ep["origin"]) or ep["conversation"] == scope.conversation_id:
                del self.w.episodes[episode_id]
                rows.append(ErasedRow("episodes", {"episode_id": str(episode_id)}))
        for item_id, origin in list(self.w.open_items.items()):
            if _hits_handle(scope, origin):
                del self.w.open_items[item_id]
                rows.append(ErasedRow("open_items", {"item_id": str(item_id)}))
        return rows

    def _drop_evidence(self, event_ids: set[UUID]) -> list[ErasedRow]:
        dropped = {(f, e) for f, e in self.w.mentions if e in event_ids}
        self.w.mentions -= dropped
        rows = [
            ErasedRow("fact_mentions", {"fact_id": str(f), "event_id": str(e)}) for f, e in sorted(dropped)
        ]
        for fact_id in sorted({f for f, _ in dropped}):
            if fact_id in self.w.facts and not any(f == fact_id for f, _ in self.w.mentions):
                seq = self.w.facts.pop(fact_id)["seq"]
                rows.append(ErasedRow("facts", {"fact_id": str(fact_id)}, change_seq=seq))
        return rows

    async def facts_for(self, handle_ids: list[UUID], statuses: Any = ()) -> list[dict[str, Any]]:
        return [{"fact_id": str(i)} for i, f in self.w.facts.items() if f["origin"] in handle_ids]

    async def episodes_for(
        self, handle_ids: list[UUID], *, since: Any = None, limit: int = 200, before: Any = None
    ) -> list[Any]:
        return [{"episode_id": str(i)} for i, e in self.w.episodes.items() if e["origin"] in handle_ids]

    async def open_items_for(self, handle_ids: list[UUID], statuses: Any = ()) -> list[Any]:
        return [{"item_id": str(i)} for i, o in self.w.open_items.items() if o in handle_ids]

    async def attrs_for(self, handle_ids: list[UUID]) -> list[Any]:
        return []

    async def objection_list(self, handle_ids: list[UUID]) -> list[tuple[UUID, str, Any]]:
        return []

    async def values_for(self, handle_ids: list[UUID], **_: Any) -> list[Any]:
        return []

    # The pages the index of mentions reads once (`app.named_rows`): this world's memory names no one.

    async def facts_page(self, after: Any, limit: int) -> list[Any]:
        return []

    episodes_page = items_page = attrs_page = tails_page = values_page = facts_page


class FakeTraits:
    def __init__(self, world: World) -> None:
        self.w = world

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            dropped = {(t, e) for t, e in self.w.trait_evidence if e in set(scope.event_ids)}
            self.w.trait_evidence -= dropped
            orphans = [
                t for t in {t for t, _ in dropped} if not any(x == t for x, _ in self.w.trait_evidence)
            ]
        else:
            orphans = [
                t
                for t, p in self.w.traits.items()
                if scope.kind is TargetKind.PROFILE and p == scope.profile_id
            ]
        rows = []
        for trait_id in orphans:
            if self.w.traits.pop(trait_id, None) is not None:
                rows.append(ErasedRow("traits", {"trait_id": str(trait_id)}))
        return rows

    async def active_for(self, profile_id: UUID) -> list[Any]:
        return [{"trait_id": str(t)} for t, p in self.w.traits.items() if p == profile_id]

    async def traits_of(self, profile_id: UUID) -> list[Any]:
        return await self.active_for(profile_id)

    async def opt_outs(self, handle_ids: list[UUID]) -> list[tuple[UUID, Any]]:
        return []


class FakeObjects:
    def __init__(self, world: World) -> None:
        self.w = world

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            return []  # no object events in this world
        rows = []
        for object_id, owner in list(self.w.objects.items()):
            if _hits_handle(scope, owner):
                del self.w.objects[object_id]
                rows.append(ErasedRow("business_objects", {"object_id": str(object_id)}))
        for action_id, action in self.w.actions.items():
            if _hits_handle(scope, action["handle"]) and action["result"] is not None:
                action["result"] = None  # operation and time stay; the personal content goes (8.4)
                rows.append(ErasedRow("agent_actions", {"action_id": str(action_id)}))
        return rows

    async def objects_for(self, handle_ids: list[UUID], *, limit: int = 50, before: Any = None) -> list[Any]:
        return [self._object(o, h) for o, h in self.w.objects.items() if h in handle_ids]

    async def get_objects(self, keys: list[ObjectKey]) -> list[BusinessObject]:
        wanted = {k.external_id for k in keys}
        return [self._object(o, h) for o, h in self.w.objects.items() if str(o) in wanted]

    async def participants_of(self, object_ids: list[UUID], *, per_object: int) -> dict[UUID, list[Any]]:
        return {}

    async def objects_page(self, after: Any, limit: int) -> list[Any]:
        return []  # the index of mentions reads it once (`app.named_rows`): this world's objects name no one

    actions_page = objects_page

    @staticmethod
    def _object(object_id: UUID, owner: UUID) -> BusinessObject:
        key = ObjectKey("thing", "world", str(object_id))
        return BusinessObject(object_id, key, {}, datetime(2026, 1, 1, tzinfo=UTC), owner, owner)

    async def actions_for(
        self, handle_ids: list[UUID], *, since: Any = None, limit: int = 100, before: Any = None
    ) -> list[Any]:
        return [{"action_id": str(a), **x} for a, x in self.w.actions.items() if x["handle"] in handle_ids]


class FakeIdentity:
    def __init__(self, world: World) -> None:
        self.w = world

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        rows = []
        for link_id, link in self.w.links.items():
            if _hits_handle(scope, link["person"]) and link["valid_to"] is None:
                link["valid_to"] = scope.requested_at
                rows.append(ErasedRow("profile_links", {"link_id": str(link_id)}))
        return rows

    async def bump_privacy_version(self, profile_id: UUID) -> int:
        self.w.privacy_version[profile_id] = self.w.privacy_version.get(profile_id, 0) + 1
        return self.w.privacy_version[profile_id]

    async def get_profile(self, profile_id: UUID) -> ProfileRecord | None:
        if profile_id not in self.w.profiles:
            return None
        return ProfileRecord(
            profile_id,
            SubjectKind.PERSON,
            f"p-{profile_id.hex[:6]}",
            1,
            self.w.privacy_version[profile_id],
            tuple(self.w.profiles[profile_id]),
        )

    async def find_handle(self, type: HandleType, scope: str, value: str) -> HandleRecord | None:
        found = next((h for h, v in self.w.handles.items() if v == value), None)
        if found is None:
            return None
        return HandleRecord(
            found, type, scope, SubjectKind.PERSON, value, HandleStatus.ACTIVE, Verification.V1, T0, T0
        )

    async def get_handles(self, handle_ids: list[UUID]) -> list[HandleRecord]:
        return [
            HandleRecord(
                h,
                HandleType.PHONE_E164,
                "",
                SubjectKind.PERSON,
                self.w.handles[h],
                HandleStatus.ACTIVE,
                Verification.V1,
                T0,
                T0,
            )
            for h in handle_ids
            if h in self.w.handles
        ]

    async def profile_of(self, handle_id: UUID) -> ProfileRecord | None:
        profile = next((p for p, hs in self.w.profiles.items() if handle_id in hs), None)
        return await self.get_profile(profile) if profile else None

    async def active_links(self, handle_ids: list[UUID]) -> list[LinkRecord]:
        return [
            LinkRecord(i, x["person"], x["org"], "buyer", False, T0, None)
            for i, x in self.w.links.items()
            if x["person"] in handle_ids and x["valid_to"] is None
        ]

    async def memberships(self, handle_ids: list[UUID]) -> dict[UUID, UUID]:
        found = {h: p for h in handle_ids if (p := await self.profile_of(h)) is not None}
        return {h: p.profile_id for h, p in found.items()}


class FakeCompile:
    def __init__(self, world: World) -> None:
        self.w = world

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        doomed = [k for k, p in self.w.packs.items() if p == scope.profile_id][:limit]
        for key in doomed:
            del self.w.packs[key]
        return [ErasedRow("packs", {"pack_key": k}) for k in doomed]


class FakeMeasure:
    def __init__(self, world: World) -> None:
        self.w = world

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        doomed = [k for k, p in self.w.context_use.items() if p == scope.profile_id][:limit]
        for key in doomed:
            del self.w.context_use[key]
        return [ErasedRow("context_use", {"conversation_key": k}) for k in doomed]


class FakeSessions:
    def __init__(self, world: World) -> None:
        self.w = world

    async def session_ids_for(self, scope: ErasureScope) -> list[UUID]:
        return sorted(s for s, p in self.w.sessions.items() if p == scope.profile_id)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.kind is not TargetKind.PROFILE:
            return []  # a handle's erasure keeps the profile's sessions (sessions.rules.erasure_target)
        doomed = [s for s, p in self.w.sessions.items() if p == scope.profile_id][:limit]
        for session_id in doomed:
            del self.w.sessions[session_id]
        return [ErasedRow("sessions", {"session_id": str(s)}) for s in doomed]


def owners(world: World) -> dict[str, Any]:
    """The repositories to patch into the unit of work."""
    return {
        "ingest": FakeIngest(world),
        "memory": FakeMemory(world),
        "traits": FakeTraits(world),
        "objects": FakeObjects(world),
        "identity": FakeIdentity(world),
        "compile": FakeCompile(world),
        "measure": FakeMeasure(world),
        "sessions": FakeSessions(world),
    }


def later(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)
