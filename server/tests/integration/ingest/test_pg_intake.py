"""The intake log on PostgreSQL (5.2): `ingest_accept` claims a batch's keys and stores its sealed row and the
relay's copy in one autocommit statement, as the application role; the rows come back in the order the
database gave them, and a deferred store writes the same events the full transaction writes."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from niadra.adapters.memory.fakes import (
    DictSecrets,
    FixedClock,
    InMemoryBus,
    InMemoryCache,
    InMemoryHotLayer,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.adapters.outbound.postgres.intake import PgIntakeLog
from niadra.adapters.outbound.postgres.probe import PgPipelineProbe
from niadra.app.ingest import IngestService
from niadra.app.sessions import SessionService
from niadra.contracts.ingest import validate_items
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.intake import NewIntake
from niadra.testing.cell import principal, sample_config
from tests.integration.conftest import CellStack, CloningUnitOfWork
from tests.unit.ingest.fakes import FakeIdentity

NOW = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)


def _intake(space: UUID, *keys: str) -> NewIntake:
    return NewIntake(
        intake_id=uuid7(),
        space_id=space,
        received_at=NOW,
        source_id=uuid4(),
        claims=tuple((k, uuid7()) for k in keys),
        body_ct=b"\x00sealed",
        dek_version=1,
    )


def _message(key: str, text_: str) -> dict[str, Any]:
    return {
        "type": "event",
        "idempotency_key": key,
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": "+5511922221111"}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": text_},
        "occurred_at": NOW.isoformat(),
    }


async def test_one_statement_claims_the_keys_and_stores_the_row_and_its_relay_copy(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    log = PgIntakeLog(pg_stack.registry)
    async with pg_uow(space) as uow:
        await uow.ingest.claim_keys(space, [("old", uuid7())], NOW)

    first = _intake(space, "old", "new")
    assert await log.accept(first) == {"old"}
    second = _intake(space, "new", "newer")
    assert await log.accept(second) == {"new"}

    async with pg_uow(space) as uow:
        rows = await uow.ingest.take_intake(10)
        assert [r.intake_id for r in rows] == [first.intake_id, second.intake_id]
        assert [r.skip for r in rows] == [frozenset({"old"}), frozenset({"new"})]
        assert rows[0].body_ct == b"\x00sealed"
        copies = (await uow.session.execute(text("SELECT id, task, payload FROM outbox"))).all()
        assert {(c.id, c.task) for c in copies} == {
            (first.intake_id, "apply_intake"),
            (second.intake_id, "apply_intake"),
        }
        await uow.ingest.drop_intake([first.intake_id])
    async with pg_uow(space) as uow:
        assert [r.intake_id for r in await uow.ingest.take_intake(10)] == [second.intake_id]
        assert (await uow.session.execute(text("SELECT count(*) FROM outbox"))).scalar_one() == 1
        # The full transaction's claim sees what the intake claimed.
        assert (await uow.ingest.claim_keys(space, [("newer", uuid7())], NOW))[1] == {"newer"}


async def test_the_intake_lock_orders_whoever_stores_the_rows(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    await PgIntakeLog(pg_stack.registry).accept(_intake(space, "k1"))
    taken = asyncio.Event()
    release = asyncio.Event()

    async def first() -> None:
        async with pg_uow(space) as uow:
            assert len(await uow.ingest.take_intake(10)) == 1
            taken.set()
            await release.wait()

    async def second() -> int:
        async with pg_uow(space) as uow:
            return len(await uow.ingest.take_intake(10))

    holder = asyncio.create_task(first())
    await taken.wait()
    waiter = asyncio.create_task(second())
    await asyncio.sleep(0.3)
    assert not waiter.done()  # waits for the lock of the one storing the rows
    release.set()
    await holder
    assert await waiter == 1


async def test_a_worker_that_finds_the_lock_held_asks_again_outside_any_transaction(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    """A second worker never holds a server connection blocked on the lock: it is told the lock is busy."""
    space = uuid4()
    await pg_uow.ensure(space)
    await PgIntakeLog(pg_stack.registry).accept(_intake(space, "k1"))
    taken = asyncio.Event()
    release = asyncio.Event()

    async def first() -> None:
        async with pg_uow(space) as uow:
            assert len(await uow.ingest.take_intake(10)) == 1
            taken.set()
            await release.wait()

    holder = asyncio.create_task(first())
    await taken.wait()
    async with pg_uow(space) as uow:
        assert await uow.ingest.try_take_intake(10) is None
    release.set()
    await holder
    async with pg_uow(space) as uow:
        found = await uow.ingest.try_take_intake(10)
    assert found is not None
    assert len(found) == 1


async def test_an_acknowledgement_that_stores_on_the_spot_waits_for_the_lock_outside_any_transaction(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    """A batch stored on the spot (not conversation turns) first stores what was acknowledged before it. While
    a worker holds the intake lock, no backend of its transaction sits blocked on the lock: it asks again."""
    space = uuid4()
    clock = FixedClock(NOW)
    config = StaticConfig(sample_config(space))
    service = IngestService(
        pg_uow,  # type: ignore[arg-type]
        FakeIdentity(),  # type: ignore[arg-type]
        SessionService(pg_uow, clock, config),  # type: ignore[arg-type]
        pg_uow._keys,
        InMemoryHotLayer(),
        clock,
        config,
        InMemoryObjectStore(),
        DictSecrets(),  # type: ignore[arg-type]
        intake=PgIntakeLog(pg_stack.registry),
        cache=InMemoryCache(),
        workers=InMemoryBus(),
    )
    await pg_uow.ensure(space)
    caller = principal(config=sample_config(space))
    items, errors = validate_items([_message("m1", "a fatura veio errada")])
    await service.ingest_items(caller, items, errors)  # acknowledged, waiting for a worker
    taken = asyncio.Event()
    release = asyncio.Event()

    async def worker() -> None:
        async with pg_uow(space) as uow:
            assert await uow.ingest.take_intake(10)
            taken.set()
            await release.wait()

    holder = asyncio.create_task(worker())
    await taken.wait()
    ended = {"type": "conversation.ended", "idempotency_key": "end-1", "conversation_id": "wa-1",
             "occurred_at": NOW.isoformat()}  # fmt: skip
    items, errors = validate_items([ended])
    acknowledging = asyncio.create_task(service.ingest_items(caller, items, errors))
    await asyncio.sleep(0.3)
    async with pg_uow(space) as uow:
        blocked = (
            await uow.session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND wait_event = 'advisory'"
                )
            )
        ).scalar_one()
    assert blocked == 0
    assert not acknowledging.done()
    release.set()
    await holder
    await acknowledging
    await service.close()
    async with pg_uow(space) as uow:
        left = (await uow.session.execute(text("SELECT count(*) FROM ingest_intake"))).scalar_one()
    assert left == 0  # the acknowledged turn was stored before the end, on the spot


async def test_a_deferred_store_writes_what_the_full_transaction_writes(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    async def run(space: UUID, intake: bool) -> list[tuple[Any, ...]]:
        clock = FixedClock(NOW)
        config = StaticConfig(sample_config(space))
        kwargs: dict[str, Any] = (
            {"intake": PgIntakeLog(pg_stack.registry), "cache": InMemoryCache(), "workers": InMemoryBus()}
            if intake
            else {}
        )
        service = IngestService(
            pg_uow,  # type: ignore[arg-type]
            FakeIdentity(),  # type: ignore[arg-type]
            SessionService(pg_uow, clock, config),  # type: ignore[arg-type]
            pg_uow._keys,
            InMemoryHotLayer(),
            clock,
            config,
            InMemoryObjectStore(),
            DictSecrets(),  # type: ignore[arg-type]
            **kwargs,
        )
        await pg_uow.ensure(space)
        caller = principal(config=sample_config(space))
        for batch in (
            [_message("m1", "a fatura veio errada"), _message("m2", "cartao 4111 1111 1111 1111")],
            [_message("m2", "cartao 4111 1111 1111 1111"), _message("m3", "e veio em dobro")],
        ):
            items, errors = validate_items(batch)
            await service.ingest_items(caller, items, errors)
        await service.close()
        await service.settle(space)
        async with pg_uow(space) as uow:
            events = (
                await uow.session.execute(
                    text(
                        "SELECT idempotency_key, kind, channel, conversation_id, speaker, content_meta,"
                        " verification_hint, direction FROM events ORDER BY idempotency_key"
                    )
                )
            ).all()
            stored = await uow.ingest.get_events(
                list(
                    (await uow.session.execute(text("SELECT event_id FROM events ORDER BY idempotency_key")))
                    .scalars()
                    .all()
                )
            )
            conversations = (
                await uow.session.execute(text("SELECT conversation_id, channel FROM conversations"))
            ).all()
            sessions = (
                await uow.session.execute(text("SELECT kind, conversation_id, channel FROM sessions"))
            ).all()
            beats = (
                await uow.session.execute(text("SELECT received, rejected FROM source_heartbeats"))
            ).all()
            left = (await uow.session.execute(text("SELECT count(*) FROM ingest_intake"))).scalar_one()
        return [
            *[tuple(e) for e in events],
            *sorted((r.text or "", r.handle_ids) for r in stored),
            *[tuple(c) for c in conversations],
            *[tuple(s) for s in sessions],
            *[tuple(b) for b in beats],
            ("pending", left),
        ]

    assert await run(uuid4(), intake=True) == await run(uuid4(), intake=False)


async def test_a_batch_set_aside_is_kept_sealed_out_of_the_order_until_retried(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    # A batch that failed every attempt is never deleted.
    space = uuid4()
    await pg_uow.ensure(space)
    log = PgIntakeLog(pg_stack.registry)
    first, failed, third = _intake(space, "a"), _intake(space, "b"), _intake(space, "c")
    for intake in (first, failed, third):
        await log.accept(intake)

    async with pg_uow(space) as uow:
        assert await uow.ingest.fail_intake(failed.intake_id) == 1
        await uow.ingest.set_aside_intake(failed.intake_id, NOW)
    async with pg_uow(space) as uow:
        assert [r.intake_id for r in await uow.ingest.take_intake(10)] == [first.intake_id, third.intake_id]
        [kept] = await uow.ingest.failed_intake(10)
        assert (kept.intake_id, kept.source_id, kept.failed_at, kept.attempts) == (
            failed.intake_id,
            failed.source_id,
            NOW,
            1,
        )
        assert kept.body_ct == b"\x00sealed"
        copies = (await uow.session.execute(text("SELECT id FROM outbox"))).scalars().all()
        assert failed.intake_id not in copies
        assert await uow.ingest.failed_intake(10, after=failed.intake_id) == []
        assert await uow.ingest.failed_intake(10, before=NOW) == []
        assert await uow.ingest.count_failed_intake() == 1
        await uow.ingest.rewrite_intake(failed.intake_id, b"\x00resealed", 2)
    # The relay's probe counts only rows waiting their turn: a batch set aside is not a stuck intake.
    assert (await PgPipelineProbe(pg_stack.registry).due(space)).intake_pending == 2

    async with pg_uow(space) as uow:
        assert await uow.ingest.retry_intake([failed.intake_id]) == [failed.intake_id]
    async with pg_uow(space) as uow:
        rows = await uow.ingest.take_intake(10)
        assert [r.intake_id for r in rows] == [first.intake_id, failed.intake_id, third.intake_id]
        back = next(r for r in rows if r.intake_id == failed.intake_id)
        assert (back.failed_at, back.attempts, back.body_ct, back.dek_version) == (
            None,
            0,
            b"\x00resealed",
            2,
        )
        assert await uow.ingest.failed_intake(10) == []
