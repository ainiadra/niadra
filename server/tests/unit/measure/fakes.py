"""Small stand-ins for the ports measure and review read from other modules."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.domain.extract.result import ExtractionRun
from niadra.domain.ingest.coverage import SourceDay
from niadra.domain.memory.facts import Relation
from niadra.domain.memory.values import SettledValue
from niadra.domain.records import (
    AgentAction,
    Episode,
    EventRecord,
    Fact,
    OpenItem,
    ProfileRecord,
    SessionRecord,
)


@dataclass
class FakeSessions:
    sessions: dict[UUID, SessionRecord] = field(default_factory=dict)

    async def get_session(self, session_id: UUID) -> SessionRecord | None:
        return self.sessions.get(session_id)


@dataclass
class FakeAudit:
    receipts: list[dict[str, Any]] = field(default_factory=list)
    mark: datetime | None = None

    async def receipts_for_conversation(
        self, conversation_id: str, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        return [
            r
            for r in self.receipts
            if r["conversation_id"] == conversation_id and (since is None or r["read_at"] >= since)
        ]

    async def watermark(self) -> datetime | None:
        return self.mark


@dataclass
class FakeExtract:
    runs: dict[UUID, ExtractionRun] = field(default_factory=dict)
    changed_at: datetime | None = None

    async def latest_run(self, session_id: UUID) -> ExtractionRun | None:
        return self.runs.get(session_id)

    async def extractor_state(self) -> tuple[int | None, datetime | None]:
        return (1 if self.changed_at else None), self.changed_at


@dataclass
class FakeCompile:
    manifests: dict[str, Mapping[str, Any]] = field(default_factory=dict)

    async def get_manifest(self, manifest_hash: str) -> Mapping[str, Any] | None:
        return self.manifests.get(manifest_hash)


@dataclass
class FakeIngest:
    events: list[EventRecord] = field(default_factory=list)
    days: dict[UUID, list[SourceDay]] = field(default_factory=dict)

    async def coverage(self, since: datetime) -> dict[UUID, list[SourceDay]]:
        return {k: [d for d in v if d.day >= since.date()] for k, v in self.days.items()}

    async def events_between(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        since: datetime,
        until: datetime,
    ) -> list[EventRecord]:
        return sorted(
            (
                e
                for e in self.events
                if e.conversation_id == conversation_id
                and (task_id is None or e.task_id == task_id)
                and since <= e.occurred_at <= until
            ),
            key=lambda e: e.occurred_at,
        )

    async def recent_messages(self, source_id: UUID, *, limit: int) -> list[EventRecord]:
        return [e for e in self.events if e.source_id == source_id][:limit]


@dataclass
class FakeIdentity:
    profiles: dict[UUID, ProfileRecord] = field(default_factory=dict)

    async def get_profile(self, profile_id: UUID) -> ProfileRecord | None:
        return self.profiles.get(profile_id)


@dataclass
class FakeMemory:
    facts: list[Fact] = field(default_factory=list)
    open_items: list[OpenItem] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    values: list[SettledValue] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    calls: int = 0

    async def relations_for(self, handle_ids: Sequence[UUID], *, origin: str | None = None) -> list[Relation]:
        return [
            r
            for r in self.relations
            if r.a.origin_handle_id in handle_ids and (origin is None or r.origin == origin)
        ]

    async def values_for(
        self, handle_ids: Sequence[UUID], *, types: Any = None, since: Any = None, limit: int = 500
    ) -> list[SettledValue]:
        return [v for v in self.values if v.origin_handle_id in handle_ids][:limit]

    async def open_items_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[Any] = ()
    ) -> list[OpenItem]:
        return [i for i in self.open_items if i.origin_handle_id in handle_ids]

    async def episodes_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        since: datetime | None = None,
        limit: int = 200,
        before: Any = None,
    ) -> list[Episode]:
        return [e for e in self.episodes if e.origin_handle_id in handle_ids][:limit]

    async def facts_for(self, handle_ids: Sequence[UUID], statuses: Sequence[Any] = ()) -> list[Fact]:
        self.calls += 1
        return [f for f in self.facts if f.origin_handle_id in handle_ids]


@dataclass
class FakeObjects:
    actions: list[AgentAction] = field(default_factory=list)

    async def actions_for(
        self, handle_ids: Sequence[UUID], *, since: datetime | None = None, limit: int = 100
    ) -> list[AgentAction]:
        return [a for a in self.actions if since is None or a.occurred_at >= since]
