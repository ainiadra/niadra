from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.identity import InMemoryIdentityRepository
from niadra.adapters.memory.pipeline import InMemoryTaskStore
from niadra.app.collaborators import AttachResult
from niadra.app.identity import IdentityService
from niadra.contracts.common import Handle
from niadra.domain.records import HandleRecord, ProfileRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, HandleType, SubjectKind, Verification
from niadra.testing.cell import SPACE_ID, Cell, PatchedUnitOfWorkFactory


@dataclass
class FakePrivacy:
    """Stands in for the privacy module's port: identity only writes feed rows."""

    feed: list[dict[str, Any]] = field(default_factory=list)

    async def record_feed(
        self, *, kind: str, profile_ids: Sequence[UUID], handle_ids: Sequence[UUID], privacy_version: int
    ) -> None:
        self.feed.append(
            {
                "kind": kind,
                "profile_ids": list(profile_ids),
                "handle_ids": list(handle_ids),
                "version": privacy_version,
            }
        )

    async def feed_since(self, since: datetime) -> list[dict[str, object]]:
        return []


def handle(
    type: HandleType | str, value: str, scope: str | None = None, kind: SubjectKind | None = None
) -> Handle:
    return Handle(type=HandleType(type), value=value, scope=scope, subject_kind=kind)


def cpf(nine: str) -> str:
    digits = [int(d) for d in nine]
    for size in (9, 10):
        total = sum(n * w for n, w in zip(digits, range(size + 1, 1, -1), strict=False))
        digits.append((total * 10 % 11) % 10)
    return "".join(map(str, digits))


@dataclass
class Env:
    cell: Cell
    service: IdentityService
    privacy: FakePrivacy
    uow: PatchedUnitOfWorkFactory

    @property
    def repo(self) -> InMemoryIdentityRepository:
        return InMemoryIdentityRepository(self.cell.uow.store)

    @property
    def outbox(self) -> InMemoryTaskStore:
        return InMemoryTaskStore(self.cell.uow.store)

    async def attach(
        self,
        *handles: Handle,
        level: Verification = Verification.V0,
        method: AssertionMethod = AssertionMethod.SAME_EVENT,
        kind: SubjectKind = SubjectKind.PERSON,
        at: datetime | None = None,
        event_id: UUID | None = None,
        merge_after_commit: bool = False,
    ) -> AttachResult:
        async with self.uow(SPACE_ID) as uow:
            return await self.service.attach(
                uow,
                list(handles),
                subject_kind=kind,
                method=method,
                origin_event_id=event_id,
                level=level,
                at=at or self.cell.clock.now(),
                merge_after_commit=merge_after_commit,
            )

    def apply_tasks(self) -> list[Task]:
        return self.outbox.take(TaskName.IDENTITY_APPLY)

    def scheduled_applies(self) -> list[tuple[Task, datetime, str]]:
        return [row for row in self.outbox.scheduled if row[0].name is TaskName.IDENTITY_APPLY]

    async def drain(self) -> int:
        """Runs queued `identity_apply` tasks until none are left; returns how many ran."""
        ran = 0
        while tasks := self.apply_tasks():
            for task in tasks:
                await self.service.identity_apply(task)
                ran += 1
        return ran

    async def find(self, h: Handle) -> HandleRecord:
        from niadra.domain.identity.handles import normalize_handle

        key = normalize_handle(h.type, h.value, h.scope, h.subject_kind).key
        record = await self.repo.find_handle(key.type, key.scope, key.value)
        assert record is not None
        return record

    async def profile(self, h: Handle) -> ProfileRecord:
        record = await self.find(h)
        profile = await self.repo.profile_of(record.handle_id)
        assert profile is not None
        return profile

    async def same(self, *handles: Handle) -> bool:
        return len({(await self.profile(h)).profile_id for h in handles}) == 1

    async def verify(self, h: Handle, level: Verification) -> None:
        await self.repo.raise_level([(await self.find(h)).handle_id], level)


def make_env(cell: Cell) -> Env:
    privacy = FakePrivacy()
    uow = PatchedUnitOfWorkFactory(cell.uow, privacy=privacy)
    return Env(cell, IdentityService(uow, cell.clock, cell.config), privacy, uow)


@pytest.fixture
def env(cell: Cell) -> Env:
    return make_env(cell)
