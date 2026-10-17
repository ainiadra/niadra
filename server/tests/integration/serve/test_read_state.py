"""The read state on PostgreSQL (`app.read_state`): a warm read runs no statement, and what the triggers
write to `read_invalidations` reaches it at the next poll of the feeds.

A number that changed hands, a moved handle and a closed conversation are the changes a stale entry would
turn into a leak; each is checked end to end, from the writer's transaction to the read after the poll.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import (
    DeterministicModels,
    DictSecrets,
    FixedClock,
    InMemoryCache,
    InMemoryHotLayer,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.compile import CompileService
from niadra.app.identity import IdentityService
from niadra.app.ingest import IngestService
from niadra.app.objects import ObjectsService
from niadra.app.serve import ServeService
from niadra.app.serve_reads import SETTLING
from niadra.app.sessions import SessionService
from niadra.app.state import StateBlock, StateService
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.ingest import validate_items
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.ingest.events import NewEvent
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.records import EventRecord, ObjectKey
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import (
    DeliveryPath,
    EventKind,
    Feature,
    HandleStatus,
    HandleType,
    Speaker,
    Verification,
)
from niadra.testing.cell import VOICE_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space

PHONE = "+5511900007711"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)


class ConfirmingReceipts:
    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        return True

    async def mark(self, mark: ProducerMark) -> None:
        return None


@dataclass
class Reads:
    serve: ServeService
    caller: SourcePrincipal
    uow: PgUnitOfWorkFactory
    sessions: SessionService
    clock: FixedClock
    space_id: UUID
    handle_id: UUID
    profile_id: UUID
    statements: list[str]
    config: StaticConfig
    cache: InMemoryCache
    keys: LocalKeyVault

    async def poll(self) -> None:
        """The loop's next poll of the feeds: 2 s later on the process's clock."""
        self.clock.advance(seconds=2)
        await self.serve.refresh_privacy()

    async def read(self, **fields: Any) -> tuple[DeliveryPath, int, str]:
        """A read's path, the statements it ran before answering and its effective level. The work it
        left to the background (a health mark or settings to renew) runs after, uncounted."""
        before = len(self.statements)
        request = ContextRequest(subject=SUBJECT, conversation_id="call-1", **fields)
        response = await self.serve.context(self.caller, request)
        ran = len(self.statements) - before
        await self.serve.drain()
        return response.path, ran, response.verification.effective.value


@pytest.fixture
async def reads(server: PgServer) -> AsyncIterator[Reads]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    config = StaticConfig(sample_config(space_id))
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "state"))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    clock, cache, hot = FixedClock(), InMemoryCache(), InMemoryHotLayer()
    identity = IdentityService(uow, clock, config)
    sessions = SessionService(uow, clock, config)
    ingest = IngestService(
        uow, identity, sessions, keys, hot, clock, config, InMemoryObjectStore(), DictSecrets()
    )
    compile_ = CompileService(uow, keys, cache, clock, config, hot=hot)
    serve = ServeService(
        uow, cache, hot, keys, ConfirmingReceipts(), compile_, DeterministicModels(), clock, config,
        "read-state", databases=registry,
    )  # fmt: skip
    caller = principal(VOICE_SOURCE, config.space(space_id))
    item = {
        "type": "event",
        "idempotency_key": "e1",
        "channel": "voice",
        "conversation_id": "call-0",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Oi, é sobre a fatura"},
        "occurred_at": clock.now().isoformat(),
    }
    proof = {
        "type": "verify",
        "idempotency_key": "call-1-proof",
        "method": "otp_sms",
        "level": "V1",
        "conversation_id": "call-1",
        "handle": {"type": "phone_e164", "value": PHONE},
        "occurred_at": clock.now().isoformat(),
    }
    valid, errors = validate_items([item, proof])
    await ingest.ingest_items(caller, valid, errors)
    # A customer first seen seconds ago is not kept yet (`serve_reads.SETTLING`): the reads below are later.
    clock.advance(seconds=SETTLING.total_seconds() + 1)
    async with uow(space_id) as tx:
        record = await tx.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await tx.identity.profile_of(record.handle_id)
    assert profile is not None
    await compile_.compile(Task(TaskName.COMPILE, space_id, profile_id=profile.profile_id))
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    # Every engine of the process: the cell's and the space's pools.
    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield Reads(
            serve,
            caller,
            uow,
            sessions,
            clock,
            space_id,
            record.handle_id,
            profile.profile_id,
            statements,
            config,
            cache,
            keys,
        )
    finally:
        event.remove(Engine, "before_cursor_execute", count)
        await registry.close()
        await cell_engine.dispose()


async def test_a_warm_read_runs_no_statement(reads: Reads) -> None:
    path, first, _ = await reads.read()
    assert first > 0  # the first read resolves the customer in the database
    await reads.poll()
    path, warm, _ = await reads.read()
    assert (path, warm) == (DeliveryPath.T0, 0)


async def test_a_warm_read_with_the_state_block_runs_no_statement(reads: Reads) -> None:
    # The customer's order, of a type the space declares, and the state block on (24.3).
    declared = {
        "type": "order",
        "ownership": "subject",
        "mirror_of": {"system": "order_management"},
        "fields": {"status": {"type": "enum"}},
    }
    space = reads.config.space(reads.space_id)
    reads.config.put(
        space.model_copy(
            update={
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
                "object_types": ObjectTypeRegistry.model_validate({"types": [declared]}),
            }
        )
    )
    state = StateService(reads.uow, reads.cache, reads.keys, reads.clock, reads.config)
    reads.serve.add_block(StateBlock(state, reads.serve.guarded))
    order = EventRecord(
        event_id=uuid7(),
        kind=EventKind.SYSTEM_EVENT,
        source_id=reads.caller.source_id,
        channel="erp",
        speaker=Speaker.SYSTEM,
        occurred_at=reads.clock.now(),
        received_at=reads.clock.now(),
        handle_ids=(reads.handle_id,),
        canonical_type="order.shipped",
        fields={"status": "shipped"},
        object_refs=(ObjectKey("order", "shop", "1001"),),
    )
    async with reads.uow(reads.space_id) as tx:
        await tx.ingest.insert_events([NewEvent(reads.space_id, order, "order-1001")])
    objects = ObjectsService(reads.uow, reads.clock, reads.config)
    task = Task(TaskName.APPLY_OBJECT_EVENT, reads.space_id, {"event_id": str(order.event_id)})
    await state.publish(reads.space_id, await objects.apply_object_event(task))
    compile_ = CompileService(reads.uow, reads.keys, reads.cache, reads.clock, reads.config)
    await compile_.compile(Task(TaskName.COMPILE, reads.space_id, profile_id=reads.profile_id))

    # Read as far as the caller proved: the state block goes where the pack's own lines go.
    proven = Verification.V1
    await reads.read(include=["state"], verification=proven)
    await reads.poll()
    await reads.read(include=["state"], verification=proven)  # the health mark aged by the poll is renewed
    before = len(reads.statements)
    request = ContextRequest(
        subject=SUBJECT, conversation_id="call-1", include=["state"], verification=proven
    )
    response = await reads.serve.context(reads.caller, request)
    assert reads.statements[before:] == []
    assert response.path is DeliveryPath.T0
    assert response.state is not None
    [shipped] = response.state.objects
    assert (shipped.ref.id, shipped.fields["status"].v) == ("1001", "shipped")


async def test_a_number_that_changed_hands_reads_as_a_first_contact_after_the_next_poll(reads: Reads) -> None:
    await reads.read()
    await reads.poll()
    async with reads.uow(reads.space_id) as tx:
        await tx.identity.set_status([reads.handle_id], HandleStatus.SUSPECTED_RECYCLED)
    await reads.poll()
    path, statements, _ = await reads.read()
    assert path is DeliveryPath.T4
    assert statements > 0


async def test_a_handle_that_moves_to_another_profile_is_resolved_again(reads: Reads) -> None:
    await reads.read()
    await reads.poll()
    async with reads.uow(reads.space_id) as tx:
        current = await tx.identity.get_profile(reads.profile_id)
        assert current is not None
        other = await tx.identity.create_profile(uuid4(), current.kind, reads.clock.now())
        await tx.identity.set_membership([reads.handle_id], other.profile_id, other.membership_version)
    await reads.poll()
    _, statements, _ = await reads.read()
    assert statements > 0
    await reads.poll()
    # Resolved again, the handle's entry names its new profile.
    target = reads.serve.read_state.target(reads.space_id, ("subject", ("phone_e164", "", PHONE)))
    assert target is None or target.profile.profile_id == other.profile_id


async def test_a_proof_serves_the_conversation_until_it_closes(reads: Reads) -> None:
    async with reads.uow(reads.space_id) as tx:
        session = await reads.sessions.record_verification(
            tx,
            reads.space_id,
            source_id=reads.caller.source_id,
            channel="voice",
            conversation_id="call-1",
            task_id=None,
            level=Verification.V1,
            ceiling=Verification.V3,
            valid_until=None,
            at=reads.clock.now(),
        )
    _, _, level = await reads.read(verification=Verification.V1)
    assert level == "V1"
    await reads.poll()
    _, statements, level = await reads.read(verification=Verification.V1)
    assert (statements, level) == (0, "V1")
    async with reads.uow(reads.space_id) as tx:
        await tx.sessions.close(
            session.session_id, closed_at=reads.clock.now(), reason="ended", initiated_by=None
        )
    await reads.poll()
    _, statements, level = await reads.read(verification=Verification.V1)
    assert statements > 0
    assert level == "V0"


async def test_a_space_whose_feeds_are_not_polled_serves_nothing_from_memory(
    reads: Reads, monkeypatch: pytest.MonkeyPatch
) -> None:
    from niadra.app import read_state

    await reads.read()
    await reads.poll()
    monkeypatch.setattr(read_state, "FRESH_FOR_S", 0.0)
    _, statements, _ = await reads.read()
    assert statements > 0


async def test_the_triggers_write_only_what_the_read_state_holds(reads: Reads) -> None:
    since = reads.clock.now() - timedelta(days=1)
    async with reads.uow(reads.space_id) as tx:
        before = len(await tx.serve.invalidations_since(since))
        # What every batch touches fires nothing.
        await tx.identity.touch_handles([reads.handle_id], reads.clock.now())
        await tx.identity.raise_level([reads.handle_id], Verification.V2)
        await tx.identity.set_status([reads.handle_id], HandleStatus.ACTIVE)  # no change
        assert len(await tx.serve.invalidations_since(since)) == before
        await tx.identity.set_status([reads.handle_id], HandleStatus.BLOCKED)
        await tx.identity.bump_privacy_version(reads.profile_id)
        rows = await tx.serve.invalidations_since(since)
    new = rows[before:] if len(rows) > before else []
    assert len(rows) == before + 2
    assert any(reads.handle_id in r["handle_ids"] for r in rows)
    assert any(reads.profile_id in r["profile_ids"] for r in rows)
    assert all(r["threads"] == [] for r in new)
