"""Concurrent reads through a production-sized pool (1 connection, 2 more at most, per space and role).

The incident of 24/09: `live` screening refreshed the privacy feed from inside the read's transaction.
When the feed came due during a burst, the reader that took the refresh lock needed a second connection
of the space's pool while the readers queued behind that lock held the others; each refresh then waited
for the pool timeout, failed quietly, left the feed due, and the next reader in the queue did the same.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from uuid import uuid4

import pytest

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
from niadra.app.serve import ServeService
from niadra.app.sessions import SessionService
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.ingest import validate_items
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.serve import privacy_guard
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import DeliveryPath, HandleType, Verification
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space

PHONE = "+5511900004321"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)


class ConfirmingReceipts:
    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        return True

    async def mark(self, mark: ProducerMark) -> None:
        return None


@pytest.fixture
async def reads(server: PgServer) -> AsyncIterator[tuple[ServeService, SourcePrincipal]]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    config = StaticConfig(sample_config(space_id))
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)  # production's per-space pools (1 + 2)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "pool"))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    clock, cache, hot = FixedClock(), InMemoryCache(), InMemoryHotLayer()
    identity = IdentityService(uow, clock, config)
    ingest = IngestService(
        uow,
        identity,
        SessionService(uow, clock, config),
        keys,
        hot,
        clock,
        config,
        InMemoryObjectStore(),
        DictSecrets(),
    )
    compile_ = CompileService(uow, keys, cache, clock, config, hot=hot)
    serve = ServeService(
        uow, cache, hot, keys, ConfirmingReceipts(), compile_, DeterministicModels(), clock, config,
        "read-pool", databases=registry,
    )  # fmt: skip
    caller = principal(WHATSAPP_SOURCE, config.space(space_id))
    event = {
        "type": "event",
        "idempotency_key": "e1",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Oi, é sobre a fatura"},
        "occurred_at": clock.now().isoformat(),
    }
    valid, errors = validate_items([event])
    await ingest.ingest_items(caller, valid, errors)
    async with uow(space_id) as tx:
        record = await tx.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert record is not None
        profile = await tx.identity.profile_of(record.handle_id)
    assert profile is not None
    await compile_.compile(Task(TaskName.COMPILE, space_id, profile_id=profile.profile_id))
    try:
        yield serve, caller
    finally:
        await registry.close()
        await cell_engine.dispose()


async def test_a_burst_of_reads_never_queues_behind_the_privacy_feed(
    reads: tuple[ServeService, SourcePrincipal], monkeypatch: pytest.MonkeyPatch
) -> None:
    serve, caller = reads
    # Every read finds the feed due, as the readers crossing its 2 s boundary during a burst do.
    monkeypatch.setattr(privacy_guard, "REFRESH_EVERY", timedelta(0))

    async def read(n: int) -> tuple[float, DeliveryPath]:
        started = time.perf_counter()
        request = ContextRequest(subject=SUBJECT, conversation_id=f"c{n}", verification=Verification.V1)
        response = await serve.context(caller, request)
        return time.perf_counter() - started, response.path

    results = await asyncio.wait_for(asyncio.gather(*(read(n) for n in range(20))), 30)
    # Before the fix: "the database connection pool is busy" after 5 s, then one 5 s wait after another.
    assert max(seconds for seconds, _ in results) < 2
    assert {path for _, path in results} <= {DeliveryPath.T0, DeliveryPath.T1}
