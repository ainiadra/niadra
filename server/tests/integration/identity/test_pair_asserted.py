"""A rejected assertion never stands for its pair: the in-memory and the PostgreSQL repositories agree, so
a pair rejected once can still become a pending merge when new evidence arrives (5.3)."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from niadra.adapters.memory.fakes import FixedClock, StaticConfig
from niadra.adapters.memory.uow import InMemoryUnitOfWorkFactory
from niadra.app.identity import IdentityService
from niadra.contracts.common import Handle
from niadra.domain.common.errors import Conflict
from niadra.domain.identity.model import AssertionRecord, AssertionState
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification
from niadra.testing.cell import PatchedUnitOfWorkFactory, sample_config
from tests.integration.identity.test_pg_identity import NOW, Recorder

M = AssertionMethod
PHONE = Handle(type=HandleType.PHONE_E164, value="+5511987654321")
EMAIL = Handle(type=HandleType.EMAIL, value="ana.silva@acme.com.br")
CRM_ID = Handle(type=HandleType.SYSTEM_ID, value="48213", scope="crm")
LATER = NOW + timedelta(days=210)


class Identity:
    """The identity service over one backend, with the privacy and pipeline ports recorded."""

    def __init__(self, base: Any) -> None:
        self.space_id = uuid4()
        self.base = base
        self.recorder = Recorder()
        self.uow = PatchedUnitOfWorkFactory(base, privacy=self.recorder, pipeline=self.recorder)
        config = StaticConfig(sample_config(self.space_id))
        self.service = IdentityService(self.uow, FixedClock(NOW), config)

    async def attach(self, *handles: Handle, at: Any, method: AssertionMethod = M.SAME_EVENT) -> bool:
        async with self.uow(self.space_id) as uow:
            result = await self.service.attach(
                uow,
                list(handles),
                subject_kind=SubjectKind.PERSON,
                method=method,
                origin_event_id=uuid4(),
                level=Verification.V0,
                at=at,
            )
        return result.pending_merge

    async def apply(self) -> None:
        for task in self.recorder.take(TaskName.IDENTITY_APPLY):
            for _ in range(10):
                try:
                    await self.service.identity_apply(task)
                    break
                except Conflict:
                    await asyncio.sleep(0.01)

    async def handle_id(self, h: Handle) -> UUID:
        async with self.base(self.space_id) as uow:
            record = await uow.identity.find_handle(h.type, h.scope or "", h.value)
            assert record is not None
            return record.handle_id

    async def status(self, h: Handle) -> HandleStatus:
        async with self.base(self.space_id) as uow:
            [record] = await uow.identity.get_handles([await self.handle_id(h)])
            return record.status

    async def same(self, a: Handle, b: Handle) -> bool:
        async with self.base(self.space_id) as uow:
            members = await uow.identity.memberships([await self.handle_id(a), await self.handle_id(b)])
        return len(set(members.values())) == 1

    async def pair_asserted(self, a: Handle, b: Handle) -> bool:
        first, second = await self.handle_id(a), await self.handle_id(b)
        async with self.base(self.space_id) as uow:
            return await uow.identity.pair_asserted(first, second)

    async def assertions(self, state: AssertionState) -> list[AssertionRecord]:
        async with self.base(self.space_id) as uow:
            return await uow.identity.list_assertions(None, state, None, 50)


@pytest.fixture(params=["memory", "postgres"])
def identity(request: pytest.FixtureRequest) -> Identity:
    if request.param == "memory":
        return Identity(InMemoryUnitOfWorkFactory())
    return Identity(request.getfixturevalue("pg_uow"))


async def test_a_pair_the_recycled_number_rule_rejected_can_merge_later(identity: Identity) -> None:
    await identity.attach(PHONE, CRM_ID, at=NOW)
    await identity.attach(EMAIL, at=NOW)
    assert await identity.attach(PHONE, EMAIL, at=NOW)
    # Before the merge runs, the number comes back alone after seven months of silence: it may have
    # changed hands, so its handle is suspended, the queued merge is refused, and the number starts over
    # as a new handle.
    await identity.attach(PHONE, at=LATER)
    assert await identity.status(PHONE) is HandleStatus.ACTIVE
    await identity.apply()
    [rejected] = await identity.assertions(AssertionState.REJECTED)
    assert rejected.reason == "handle_not_active"
    assert not await identity.same(PHONE, EMAIL)
    assert not await identity.pair_asserted(PHONE, EMAIL)
    # The CRM ties the number to its record again, and the customer writes from it with the e-mail.
    await identity.attach(PHONE, CRM_ID, at=LATER + timedelta(hours=1), method=M.EXPLICIT_IDENTIFY)
    assert await identity.status(PHONE) is HandleStatus.ACTIVE
    assert await identity.attach(PHONE, EMAIL, at=LATER + timedelta(hours=2))
    assert await identity.pair_asserted(PHONE, EMAIL)
    await identity.apply()
    assert await identity.same(PHONE, EMAIL)


async def test_a_pair_rejected_at_once_becomes_pending_on_stronger_evidence(identity: Identity) -> None:
    await identity.attach(PHONE, at=NOW)
    await identity.attach(EMAIL, at=NOW)
    # A channel rotation only ever makes an alias: the pair is rejected on the spot, with no task.
    assert not await identity.attach(PHONE, EMAIL, at=NOW, method=M.CHANNEL_ROTATION)
    [rejected] = await identity.assertions(AssertionState.REJECTED)
    assert rejected.reason == "rotation_is_alias_only"
    assert not await identity.pair_asserted(PHONE, EMAIL)
    assert await identity.attach(PHONE, EMAIL, at=NOW + timedelta(minutes=5))
    assert len(await identity.assertions(AssertionState.PENDING)) == 1
    # Once pending, the same evidence again adds nothing.
    assert await identity.attach(PHONE, EMAIL, at=NOW + timedelta(minutes=6))
    assert len(await identity.assertions(AssertionState.PENDING)) == 1
    await identity.apply()
    assert await identity.same(PHONE, EMAIL)
