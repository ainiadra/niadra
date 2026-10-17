"""What one read does on the database (the throughput incident of 24/09).

A space's database is reached through PgBouncer in transaction mode with a pool of 4 server connections,
shared by every process. A read that awaits the cache, the hot layer, the key vault, the receipt queue or
the cell database while its space transaction is open keeps a server connection for all of that time, and
at 25 reads a second the pool runs dry. These tests pin the budget: a cached read runs a fixed, small
number of statements, and awaits nothing but its own statements while a space transaction is open.
"""

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
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
from niadra.adapters.outbound.postgres.uow import PgUnitOfWork, PgUnitOfWorkFactory
from niadra.app.compile import CompileService
from niadra.app.identity import IdentityService
from niadra.app.ingest import IngestService
from niadra.app.pending_proofs import HeldProof, PendingProofs
from niadra.app.serve import ServeService
from niadra.app.sessions import SessionService
from niadra.config.models import CategoryRule
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.ingest import validate_items
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.compile import CompiledPack, profile_target
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.records import BusinessObject, ObjectKey, OpenItem
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import DeliveryPath, HandleType, MemoryAudience, OpenItemStatus, Verification
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space

PHONE = "+5511900005678"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
CACHED_READ_STATEMENTS = 4
"""A cached read in a conversation: the space pin (`set_config`), the handle, its profile and the level
the conversation proved. The coverage and the source trims are kept per process and not counted here."""


@dataclass
class Watch:
    """How many space transactions each task has open, what a task awaited inside one of its own, and
    the statements run. Per task: the retrieval cache loads a customer in a task of its own, with a
    transaction of its own, while the read that started it goes on."""

    open: dict[asyncio.Task[Any] | None, int] = field(default_factory=dict)
    inside: list[str] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)

    def note(self, what: str) -> None:
        if self.open.get(asyncio.current_task()):
            self.inside.append(what)


class WatchedUnitOfWork:
    """The production factory; a cell transaction opened inside a space one is a second connection held."""

    def __init__(self, inner: PgUnitOfWorkFactory, watch: Watch) -> None:
        self._inner = inner
        self._watch = watch

    def __call__(self, space_id: UUID) -> AbstractAsyncContextManager[PgUnitOfWork]:
        return self._space(self._inner(space_id))

    def system(self, space_id: UUID) -> AbstractAsyncContextManager[PgUnitOfWork]:
        return self._space(self._inner.system(space_id))

    def cell(self) -> AbstractAsyncContextManager[PgUnitOfWork]:
        self._watch.note("cell transaction")
        return self._inner.cell()

    @asynccontextmanager
    async def _space(self, transaction: AbstractAsyncContextManager[PgUnitOfWork]) -> AsyncIterator[Any]:
        self._watch.note("space transaction")
        async with transaction as uow:
            task = asyncio.current_task()
            self._watch.open[task] = self._watch.open.get(task, 0) + 1
            try:
                yield uow
            finally:
                self._watch.open[task] -= 1


class Watched:
    """A port whose every coroutine records that it ran, and whether a space transaction was open."""

    def __init__(self, name: str, inner: Any, watch: Watch) -> None:
        self._name = name
        self._inner = inner
        self._watch = watch

    def __getattr__(self, attr: str) -> Any:
        found = getattr(self._inner, attr)
        if not inspect.iscoroutinefunction(found):
            return found

        async def call(*args: Any, **kwargs: Any) -> Any:
            self._watch.note(f"{self._name}.{attr}")
            return await found(*args, **kwargs)

        return call


class ConfirmingReceipts:
    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        return True

    async def mark(self, mark: ProducerMark) -> None:
        return None


@contextmanager
def counting(watch: Watch) -> Iterator[None]:
    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        watch.statements.append(statement.split()[0].upper() + " " + " ".join(statement.split()[1:4]))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield
    finally:
        event.remove(Engine, "before_cursor_execute", count)


@dataclass
class Reads:
    serve: ServeService
    caller: SourcePrincipal
    watch: Watch
    compile: Callable[[], Any]


@pytest.fixture
async def reads(server: PgServer) -> AsyncIterator[Reads]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    config = StaticConfig(sample_config(space_id))
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "budget"))
    watch = Watch()
    inner = PgUnitOfWorkFactory(keys, registry, cell_engine)
    uow = WatchedUnitOfWork(inner, watch)
    clock, cache, hot = FixedClock(), InMemoryCache(), InMemoryHotLayer()
    identity = IdentityService(inner, clock, config)
    ingest = IngestService(
        inner,
        identity,
        SessionService(inner, clock, config),
        keys,
        hot,
        clock,
        config,
        InMemoryObjectStore(),
        DictSecrets(),
    )
    compile_ = CompileService(inner, keys, cache, clock, config, hot=hot)
    serve = ServeService(
        uow,  # type: ignore[arg-type]
        Watched("cache", cache, watch),  # type: ignore[arg-type]
        Watched("hot", hot, watch),  # type: ignore[arg-type]
        Watched("keys", keys, watch),  # type: ignore[arg-type]
        Watched("receipts", ConfirmingReceipts(), watch),  # type: ignore[arg-type]
        compile_,
        DeterministicModels(),
        clock,
        config,
        "read-budget",
        databases=registry,
    )
    caller = principal(WHATSAPP_SOURCE, config.space(space_id))
    item = {
        "type": "event",
        "idempotency_key": "e1",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Oi, é sobre a fatura"},
        "occurred_at": clock.now().isoformat(),
        "verification_hint": "V1",
    }
    valid, errors = validate_items([item])
    await ingest.ingest_items(caller, valid, errors)
    async with inner(space_id) as tx:
        record = await tx.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await tx.identity.profile_of(record.handle_id)
    assert profile is not None
    profile_id = profile.profile_id

    async def compile_profile() -> None:
        await compile_.compile(Task(TaskName.COMPILE, space_id, profile_id=profile_id))

    try:
        yield Reads(serve, caller, watch, compile_profile)
    finally:
        await registry.close()
        await cell_engine.dispose()


def ask(conversation: str, **extra: Any) -> ContextRequest:
    return ContextRequest(
        subject=SUBJECT, conversation_id=conversation, verification=Verification.V1, **extra
    )


async def test_a_cached_read_runs_a_few_statements_and_awaits_nothing_else_inside_its_transaction(
    reads: Reads,
) -> None:
    await reads.compile()
    await reads.serve.context(reads.caller, ask("wa-1"))  # warms the process: trims, coverage, the pin
    await reads.serve.retrieval.drain()  # and the customer's retrieval entry, loaded in a task of its own
    reads.watch.statements.clear()
    reads.watch.inside.clear()
    with counting(reads.watch):
        pinned = await reads.serve.context(reads.caller, ask("wa-1"))
    assert pinned.path is DeliveryPath.T0
    assert reads.watch.inside == []
    assert len(reads.watch.statements) <= CACHED_READ_STATEMENTS, reads.watch.statements

    # The first read of another conversation takes T0 too, with the same budget.
    reads.watch.statements.clear()
    with counting(reads.watch):
        first = await reads.serve.context(reads.caller, ask("wa-2"))
    assert first.path is DeliveryPath.T0
    assert reads.watch.inside == []
    assert len(reads.watch.statements) <= CACHED_READ_STATEMENTS, reads.watch.statements


async def test_no_path_of_a_read_awaits_anything_but_the_database_inside_its_transaction(
    reads: Reads,
) -> None:
    # T2 (nothing compiled yet), T1 (the cache lost the packs), a delta, the live turns and a query.
    reads.watch.inside.clear()
    compiled = await reads.serve.context(reads.caller, ask("c-t2"))
    assert compiled.path is DeliveryPath.T2
    await reads.compile()
    cache = reads.serve._cache._inner  # type: ignore[attr-defined]
    cache.values.clear()
    stored = await reads.serve.context(reads.caller, ask("c-t1"))
    assert stored.path is DeliveryPath.T1
    await reads.serve.context(reads.caller, ask("c-t1", delta=True))
    await reads.serve.context(reads.caller, ask("c-new", delta=True))
    await reads.serve.context(reads.caller, ask("c-q", query="fatura"))
    assert reads.watch.inside == []


async def test_a_read_that_counts_a_held_proof_reads_it_outside_its_transaction(reads: Reads) -> None:
    # A turn acknowledged and not stored yet proves V1 for its conversation (app.pending_proofs).
    proofs = PendingProofs(reads.serve._keys, reads.serve._hot)  # type: ignore[attr-defined]
    await proofs.hold(reads.caller.space.space_id, [HeldProof("c:wa-held", uuid7(), Verification.V1)])
    reads.watch.inside.clear()

    read = await reads.serve.context(reads.caller, ask("wa-held"))

    assert read.verification.effective is Verification.V1
    assert reads.watch.inside == []


async def test_what_other_participants_said_about_an_object_costs_a_cached_read_no_statement(
    reads: Reads,
) -> None:
    # A lawyer's promise about the customer's case is gathered when the pack compiles; the
    # read takes it from the cached pack, with the statements of any cached read and none more.
    space_id = reads.caller.space.space_id
    config = reads.serve._config  # type: ignore[attr-defined]
    space = config.space(space_id)
    lawsuits = CategoryRule(name="lawsuit", min_verification=Verification.V0)
    policy = space.policy.model_copy(update={"categories": [*space.policy.categories, lawsuits]})
    config.put(space.model_copy(update={"policy": policy}))
    lawyer, case = uuid4(), ObjectKey("lawsuit", "court", "case-1")
    async with reads.serve._uow._inner(space_id) as tx:  # type: ignore[attr-defined]
        record = await tx.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await tx.identity.profile_of(record.handle_id)
        assert profile is not None
        obj = await tx.objects.add_object(
            BusinessObject(uuid7(), case, {}, FixedClock().now(), WHATSAPP_SOURCE, record.handle_id)
        )
        await tx.objects.add_participants(obj.object_id, [(lawyer, "lawyer")])
        promise = OpenItem(
            item_id=uuid7(),
            origin_handle_id=lawyer,
            description="Mandar os extratos ao cliente",
            owner="customer",
            status=OpenItemStatus.OPEN,
            created_at=FixedClock().now(),
            origin={},
            object=case,
            audience=MemoryAudience.SHARED,
        )
        assert await tx.memory.insert_open_item(promise)
    await reads.compile()
    async with reads.serve._uow._inner(space_id) as tx:  # type: ignore[attr-defined]
        stored = await tx.compile.packs_for(profile_target(profile.profile_id))
    # The open item waits for the level of the customer's own items: the V1 packs carry it.
    proven = [CompiledPack.from_bytes(p.body) for p in stored if "|V1|" in p.pack_key]
    assert proven
    for pack in proven:
        assert [r.text for m in pack.object_memory for r in m.rows] == ["Mandar os extratos ao cliente"]
        assert "Mandar os extratos ao cliente (lawyer)" in pack.text
    await reads.serve.context(reads.caller, ask("wa-obj"))
    await reads.serve.retrieval.drain()
    reads.watch.statements.clear()
    reads.watch.inside.clear()
    with counting(reads.watch):
        read = await reads.serve.context(reads.caller, ask("wa-obj"))
    assert read.path is DeliveryPath.T0
    assert reads.watch.inside == []
    assert len(reads.watch.statements) <= CACHED_READ_STATEMENTS, reads.watch.statements
