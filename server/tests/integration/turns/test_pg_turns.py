"""Turn records on PostgreSQL (24.3.5, 24.6): the acknowledgement is one statement, a worker adds up a group
of 50 requests in at most 7, the day's rows add up across groups, and the kept tier is found, searched,
expired and erased by its own index."""

import dataclasses
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import DeterministicModels, InMemoryObjectStore, StaticConfig
from niadra.adapters.outbound.postgres.turns import PgTurnIntakeLog
from niadra.app.turns import APPLY_GROUP, TurnService
from niadra.config.models import FeatureSettings, WebhookEndpoint
from niadra.config.recording import RecordingSettings
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.records import KeptTurn, NewTurnIntake, TakenIntake, TurnQuery
from niadra.domain.turns.storage import HMAC_PURPOSE, KeptBy
from niadra.domain.vocabulary import Feature
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import CellStack, CloningUnitOfWork
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.turns.records import turn

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
APPLY_BUDGET = 7
"""24.6: at most 7 statements for a group of up to 50 requests, the space pin included."""


@contextmanager
def counting() -> Iterator[list[str]]:
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(" ".join(statement.split()[:4]))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", count)


class Parked:
    """Workers that take the hand-over and do nothing: the test runs the worker's step itself."""

    async def publish(self, tasks: Any) -> None:
        return None


class DownStore(InMemoryObjectStore):
    """Object storage that refuses every write while `down`."""

    down = False

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        if self.down:
            raise ConnectionError("object storage is down")
        await super().put(key, data, content_type)

    async def put_if_absent(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> bool:
        if self.down:
            raise ConnectionError("object storage is down")
        return await super().put_if_absent(key, data, content_type)


def _service(
    pg_uow: CloningUnitOfWork,
    pg_stack: CellStack,
    space: UUID,
    store: InMemoryObjectStore | None = None,
    endpoints: Sequence[WebhookEndpoint] = (),
    models: Any = None,
    **recording: Any,
) -> TurnService:
    config = sample_config(space).model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset({Feature.TURNS})),
            "recording": RecordingSettings(sample_rate=0, **recording),
            "webhook_endpoints": list(endpoints),
        }
    )
    return TurnService(
        pg_uow,  # type: ignore[arg-type]
        PgTurnIntakeLog(pg_stack.registry),
        pg_uow._keys,
        store or InMemoryObjectStore(),
        pg_stack.clock,
        StaticConfig(config),
        RecordingAdminAudit(),  # type: ignore[arg-type]
        workers=Parked(),  # type: ignore[arg-type]
        models=models,
    )


async def test_the_acknowledgement_is_one_statement_and_a_repeat_stores_nothing_more(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    log = PgTurnIntakeLog(pg_stack.registry)
    # The space's engine checks the database's stamp once, on its first use in the process.
    warm = NewTurnIntake(uuid7(), space, NOW, WHATSAPP_SOURCE, 1, b"\x00warm", 1)
    await log.accept(warm)
    intake = NewTurnIntake(uuid7(), space, NOW, WHATSAPP_SOURCE, 2, b"\x00sealed", 1)
    with counting() as statements:
        await log.accept(intake)
    assert len(statements) == 1
    assert statements[0].startswith("SELECT turn_accept")
    await log.accept(intake)
    async with pg_uow(space) as uow:
        taken = await uow.turns.take_intake(10)
        assert taken is not None
        assert [(r.intake_id, r.body_ct) for r in taken.rows][1:] == [(intake.intake_id, b"\x00sealed")]
        copies = (await uow.session.execute(text("SELECT id, task FROM outbox ORDER BY id"))).all()
        assert [(c.id, c.task) for c in copies] == [
            (warm.intake_id, "apply_turns"),
            (intake.intake_id, "apply_turns"),
        ]


async def test_an_intake_set_aside_is_kept_sealed_out_of_the_order_until_retried(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    # An intake that failed every attempt is never deleted.
    space = uuid4()
    await pg_uow.ensure(space)
    log = PgTurnIntakeLog(pg_stack.registry)
    first = NewTurnIntake(uuid7(), space, NOW, WHATSAPP_SOURCE, 1, b"\x00first", 1)
    failed = NewTurnIntake(uuid7(), space, NOW, WHATSAPP_SOURCE, 2, b"\x00sealed", 1)
    for intake in (first, failed):
        await log.accept(intake)

    async with pg_uow(space) as uow:
        await uow.turns.set_aside_intake(failed.intake_id, NOW)
    async with pg_uow(space) as uow:
        taken = await uow.turns.take_intake(10)
        assert taken is not None
        assert [r.intake_id for r in taken.rows] == [first.intake_id]
        [kept] = await uow.turns.failed_intake(10)
        assert (kept.intake_id, kept.failed_at, kept.body_ct) == (failed.intake_id, NOW, b"\x00sealed")
        copies = (await uow.session.execute(text("SELECT id FROM outbox"))).scalars().all()
        assert failed.intake_id not in copies
        assert await uow.turns.failed_intake(10, after=failed.intake_id) == []
        assert await uow.turns.count_failed_intake() == 1
        await uow.turns.rewrite_intake(failed.intake_id, b"\x00resealed", 2, 1)
        turns = await uow.session.scalar(
            text("SELECT turns FROM turn_intake WHERE intake_id = :i"), {"i": failed.intake_id}
        )
        assert turns == 1

    async with pg_uow(space) as uow:
        assert await uow.turns.retry_intake(None) == [failed.intake_id]
    async with pg_uow(space) as uow:
        taken = await uow.turns.take_intake(10)
        assert taken is not None
        assert [(r.intake_id, r.body_ct, r.attempts) for r in taken.rows] == [
            (first.intake_id, b"\x00first", 0),
            (failed.intake_id, b"\x00resealed", 0),
        ]


async def test_a_worker_adds_up_50_requests_in_at_most_7_statements(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    # An endpoint takes `turn.flagged`: the group's own statement adds the task that sends it.
    quality = WebhookEndpoint(
        endpoint_id="quality",
        url="https://quality.example/hooks",
        source_id=WHATSAPP_SOURCE,
        event_types=["turn.flagged"],
        secret_refs=["spaces/x/webhook/quality"],
    )
    service = _service(pg_uow, pg_stack, space, endpoints=[quality])
    caller = principal(WHATSAPP_SOURCE, sample_config(space))
    for i in range(APPLY_GROUP):
        flags = ["handoff"] if i % 10 == 0 else []
        await service.record(
            caller, [turn(f"t-{i}-a", flags=flags), turn(f"t-{i}-b", conversation_id=f"c-{i}")]
        )

    with counting() as statements:
        assert await service._apply_next(space, APPLY_GROUP)
    assert len(statements) <= APPLY_BUDGET, statements
    # The turns module's own three (the pin, the take, the write): the modules that consume share the rest.
    assert [s.split()[0:2] for s in statements] == [
        ["SELECT", "set_config('niadra.space_id',"],
        ["WITH", "lock"],
        ["WITH", "daily"],
    ]
    await service.apply_turns(Task(TaskName.APPLY_TURNS, space, {}))
    async with pg_uow(space) as uow:
        assert (await uow.turns.take_intake(10) or TakenIntake([])).rows == []
        outbox = (await uow.session.execute(text("SELECT task, payload FROM outbox"))).all()
        [row] = await uow.turns.daily(date(2026, 9, 29), date(2026, 9, 29), WHATSAPP_SOURCE)
        kept = await uow.turns.search(TurnQuery(limit=200))
    assert (row.turns, row.tool_calls, row.model_calls, row.tokens_in) == (100, 100, 100, 295_300)
    assert row.claims == {"price|matched": 100}
    assert sorted(k.turn_id for k in kept) == sorted(f"t-{i}-a" for i in range(0, APPLY_GROUP, 10))
    assert {task for task, _ in outbox} == {"notify_flagged_turns"}, "the intakes' copies are gone"
    told = sorted(t["turn_id"] for _, payload in outbox for t in payload["turns"])
    assert told == sorted(k.turn_id for k in kept)


async def test_the_days_rows_add_up_across_groups(pg_uow: CloningUnitOfWork, pg_stack: CellStack) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    service = _service(pg_uow, pg_stack, space)
    caller = principal(WHATSAPP_SOURCE, sample_config(space))
    slow = turn("t-2", latency_ms=40_000)
    slow["claims"][0]["verdict"] = "no_evidence"
    for body in ([turn("t-1")], [slow]):
        await service.record(caller, body)
        await service.apply_turns(Task(TaskName.APPLY_TURNS, space, {}))
    async with pg_uow(space) as uow:
        [row] = await uow.turns.daily(date(2026, 9, 29), date(2026, 9, 30), None)
        assert await uow.turns.daily(date(2026, 9, 30), date(2026, 9, 30), None) == []
    assert row.turns == 2
    assert row.claims == {"price|matched": 1, "price|no_evidence": 1}
    assert sum(row.latency_buckets) == 2
    assert row.latency_buckets[-1] == 1


def _kept(turn_id: str, conversation: bytes, at: datetime, **changes: Any) -> KeptTurn:
    values: dict[str, Any] = {
        "turn_id": turn_id,
        "conversation": conversation,
        "source_id": WHATSAPP_SOURCE,
        "agent": "closing",
        "kind": "message",
        "started_at": at,
        "fidelity": "gold",
        "completeness": "complete",
        "content_mode": "stored",
        "replayable": True,
        "build_hash": b"\x01" * 32,
        "flags": ("handoff",),
        "kept_by": KeptBy.REQUEST,
        "kept_at": NOW,
        "kept_until": NOW + timedelta(days=30),
    }
    return KeptTurn(**{**values, **changes})


async def test_the_kept_tier_is_kept_once_searched_expired_and_erased_by_its_index(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    ana, bruno = b"\xaa" * 32, b"\xbb" * 32
    rows = [
        _kept("k-1", ana, NOW - timedelta(minutes=3)),
        _kept("k-2", ana, NOW - timedelta(minutes=2), flags=("error",), kept_until=NOW + timedelta(days=7)),
        _kept("k-3", bruno, NOW - timedelta(minutes=1), agent="triage", source_id=uuid4()),
    ]
    async with pg_uow(space) as uow:
        assert await uow.turns.keep(rows) == {"k-1", "k-2", "k-3"}
        assert await uow.turns.keep(rows[:1]) == set()
    async with pg_uow(space) as uow:
        assert [k.turn_id for k in await uow.turns.kept(["k-2", "nope"])] == ["k-2"]
        newest = await uow.turns.search(TurnQuery(limit=2))
        after = await uow.turns.search(TurnQuery(limit=2, before=(newest[-1].started_at, newest[-1].turn_id)))
        by_conversation = await uow.turns.search(TurnQuery(conversation=ana))
        errors = await uow.turns.search(TurnQuery(flags=("error",)))
        triage = await uow.turns.search(TurnQuery(agent="triage"))
        mine = await uow.turns.search(TurnQuery(sources=(WHATSAPP_SOURCE,)))
    assert [k.turn_id for k in newest + after] == ["k-3", "k-2", "k-1"]
    assert [k.turn_id for k in by_conversation] == ["k-2", "k-1"]
    assert [k.turn_id for k in errors] == ["k-2"]
    assert [k.turn_id for k in triage] == ["k-3"]
    assert [k.turn_id for k in mine] == ["k-2", "k-1"]
    assert newest[0].conversation == bruno
    assert newest[0].flags == ("handoff",)
    async with pg_uow(space) as uow:
        expired = await uow.turns.expire_kept(NOW + timedelta(days=8), limit=10)
        scope = ErasureScope(
            uuid7(), TargetKind.CONVERSATION, (), None, "c-ana", NOW, conversation_hashes=(ana,)
        )
        erased = await uow.turns.erase_for(scope, limit=10)
    assert [k.turn_id for k in expired] == ["k-2"]
    assert [(r.table, r.key) for r in erased] == [("turns_kept", {"turn_id": "k-1"})]


async def test_a_second_worker_takes_nothing_while_the_first_holds_the_space(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    await PgTurnIntakeLog(pg_stack.registry).accept(
        NewTurnIntake(uuid7(), space, NOW, WHATSAPP_SOURCE, 1, b"x", 1)
    )
    async with pg_uow(space) as first:
        taken = await first.turns.take_intake(10)
        assert taken is not None
        assert len(taken.rows) == 1
        async with pg_uow(space) as second:
            assert await second.turns.take_intake(10) is None


async def test_a_request_of_50_turns_waits_for_one_statement(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    service = _service(pg_uow, pg_stack, space)
    caller = principal(WHATSAPP_SOURCE, sample_config(space))
    await service.record(caller, [turn("warm")])  # the engine's stamp and the space's data key, once
    with counting() as statements:
        result = await service.record(
            caller, [turn(f"t-{i}", conversation_id=f"c-{i % 7}") for i in range(50)]
        )
    assert result.accepted == 50
    assert len(statements) == 1
    assert statements[0].startswith("SELECT turn_accept")


async def test_pointer_turns_taken_during_an_object_storage_outage_keep_both_budgets(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    """The request still waits for one statement, and the worker writes the deferred objects in the same
    three statements of its own: the store's writes are not statements."""
    space = uuid4()
    await pg_uow.ensure(space)
    store = DownStore()
    service = _service(pg_uow, pg_stack, space, store, content_mode="pointer")
    caller = principal(WHATSAPP_SOURCE, sample_config(space))
    await service.record(caller, [turn("warm", mode="pointer")])
    store.down = True
    with counting() as statements:
        result = await service.record(caller, [turn(f"t-{i}", mode="pointer") for i in range(50)])
    await service.record(caller, [turn("t-0", mode="pointer")])
    assert (result.accepted, len(statements)) == (50, 1)
    store.down = False
    await service._apply_next(space, 1)
    with counting() as statements:
        assert await service._apply_next(space, APPLY_GROUP)
    assert [s.split()[0:2] for s in statements] == [
        ["SELECT", "set_config('niadra.space_id',"],
        ["WITH", "lock"],
        ["WITH", "daily"],
    ]
    async with pg_uow(space) as uow:
        daily = await uow.turns.daily(date(2026, 9, 29), date(2026, 9, 29), None)
    assert sum(row.turns for row in daily) == 51, "the turn sent again during the outage is one turn"


async def test_the_pii_pass_a_space_asks_for_rides_on_the_groups_own_statement(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    service = _service(pg_uow, pg_stack, space, models=DeterministicModels(), pii_model=True)
    caller = principal(WHATSAPP_SOURCE, sample_config(space))
    for i in range(APPLY_GROUP):
        await service.record(caller, [turn(f"s-{i}-a"), turn(f"s-{i}-b", mode="pointer")])
    with counting() as statements:
        assert await service._apply_next(space, APPLY_GROUP)
    assert [s.split()[0:2] for s in statements] == [
        ["SELECT", "set_config('niadra.space_id',"],
        ["WITH", "lock"],
        ["WITH", "daily"],
    ]
    async with pg_uow(space) as uow:
        outbox = (await uow.session.execute(text("SELECT task, payload FROM outbox"))).all()
    scanned = [
        t["turn_id"] for task, payload in outbox if task == "scan_turn_arguments" for t in payload["turns"]
    ]
    assert sorted(scanned) == sorted(f"s-{i}-a" for i in range(APPLY_GROUP)), "stored records only"


async def test_an_erasure_while_turns_wait_in_the_intake_fences_them_off(
    pg_uow: CloningUnitOfWork, pg_stack: CellStack
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    service = _service(pg_uow, pg_stack, space)
    caller = principal(WHATSAPP_SOURCE, sample_config(space))
    await service.record(caller, [turn("f-1", flags=["handoff"]), turn("f-2", conversation_id="c-other")])
    conversation = await pg_uow._keys.hmac(space, HMAC_PURPOSE, b"c-8812")
    scope = ErasureScope(uuid7(), TargetKind.CONVERSATION, (), None, "c-8812", NOW)
    await service.fence(space, dataclasses.replace(scope, conversation_hashes=(conversation,)))
    await service.record(caller, [turn("f-3", flags=["handoff"])])
    with counting() as statements:
        assert await service._apply_next(space, APPLY_GROUP)
    assert len(statements) == 3, "the fence rides on the take"
    async with pg_uow(space) as uow:
        kept = await uow.turns.search(TurnQuery(limit=10))
        tombstones = (await uow.session.execute(text("SELECT mark FROM turn_tombstones"))).scalars().all()
    assert [k.turn_id for k in kept] == [], "neither the waiting turn nor the late one is kept"
    assert tombstones == [f"conversation:{conversation.hex()}"]
