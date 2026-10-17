"""A warm context read with `include` runs no statement (24.2.10, 24.6): the blocks come from the cache
stores, the state store and the process's memory, like the pack."""

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
from niadra.adapters.memory.signals import InMemorySignalOverlay, InMemoryTurnCounters
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.budget import BudgetReadBlock, BudgetService
from niadra.app.compile import CompileService
from niadra.app.identity import IdentityService
from niadra.app.ingest import IngestService
from niadra.app.read_state import Change
from niadra.app.serve import ServeService
from niadra.app.serve_reads import SETTLING
from niadra.app.sessions import SessionService
from niadra.app.signals import ConstraintsReadBlock, SignalsService
from niadra.config.measurement import Arm, ElementExperiment, MeasurementSettings
from niadra.config.models import Experiment, FeatureSettings
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse, Include
from niadra.contracts.ingest import validate_items
from niadra.contracts.turns import TurnRecord
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.measure.experiments import arm_of
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import DeliveryPath, Feature, HandleType, Verification
from niadra.testing.cell import VOICE_SOURCE, WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space

PHONE = "+5511900007722"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
NO_BLACK = {
    "kind": "preference",
    "attr": "item_variant.color",
    "op": "not_in",
    "values": ["preto"],
    "strength": "must",
    "scope": "persistent",
}


class ConfirmingReceipts:
    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        return True

    async def mark(self, mark: ProducerMark) -> None:
        return None


def proven_elsewhere(statements: Sequence[str]) -> int:
    """The statements a read ran, but the lookup of what a new conversation proved, which any read at V1
    runs once per conversation whatever it includes: its transaction pin and the query."""
    proofs = sum(1 for s in statements if s.startswith("SELECT sessions."))
    return len(statements) - 2 * proofs


@dataclass(frozen=True)
class Applied:
    source_id: UUID
    received_at: Any
    record: TurnRecord


@dataclass
class Reads:
    serve: ServeService
    signals: SignalsService
    caller: SourcePrincipal
    uow: PgUnitOfWorkFactory
    clock: FixedClock
    space_id: UUID
    statements: list[tuple[object, str]]
    config: StaticConfig
    profile_id: UUID
    cache: InMemoryCache

    async def poll(self) -> None:
        self.clock.advance(seconds=2)
        await self.serve.refresh_privacy()

    async def read(
        self, conversation: str, include: Sequence[Include] = ("constraints",)
    ) -> tuple[ContextResponse, int]:
        """A read's response and the statements it ran itself; what it left to the background (the hot
        layer's health mark renewed once a second) is another task's."""
        before = len(self.statements)
        request = ContextRequest(
            subject=SUBJECT, conversation_id=conversation, verification=Verification.V1, include=list(include)
        )
        response = await self.serve.context(self.caller, request)
        mine = asyncio.current_task()
        ran = proven_elsewhere([s for task, s in self.statements[before:] if task is mine])
        await self.serve.drain()
        return response, ran


@pytest.fixture
async def reads(server: PgServer) -> AsyncIterator[Reads]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    config = StaticConfig(
        sample_config(space_id).model_copy(
            update={"features": FeatureSettings(enabled=frozenset({Feature.SIGNALS, Feature.TURNS}))}
        )
    )
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "include"))
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
        "read-include", databases=registry,
    )  # fmt: skip
    signals = SignalsService(uow, keys, cache, InMemorySignalOverlay(), InMemoryObjectStore(), clock, config)
    serve.add_block(ConstraintsReadBlock(signals))
    budget = BudgetService(uow, InMemoryTurnCounters(), keys, config)
    serve.add_block(BudgetReadBlock(budget))
    item = {
        "type": "event",
        "idempotency_key": "e1",
        "channel": "whatsapp",
        "conversation_id": "wa-1",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": "Quero um vestido, nada de preto"},
        "occurred_at": clock.now().isoformat(),
    }
    valid, errors = validate_items([item])
    await ingest.ingest_items(principal(WHATSAPP_SOURCE, config.space(space_id)), valid, errors)
    # The voice agent proves who it speaks with in each call: the blocks go only as far as the pack.
    proofs = [
        {
            "type": "verify",
            "idempotency_key": f"call-{n}-proof",
            "method": "otp_sms",
            "level": "V1",
            "conversation_id": f"call-{n}",
            "handle": {"type": "phone_e164", "value": PHONE},
            "occurred_at": clock.now().isoformat(),
        }
        for n in range(1, 5)
    ]
    valid, errors = validate_items(proofs)
    await ingest.ingest_items(principal(VOICE_SOURCE, config.space(space_id)), valid, errors)
    record = TurnRecord.model_validate(
        {
            "turn_id": "t1",
            "conversation_id": "wa-1",
            "agent": {"name": "stylist"},
            "started_at": clock.now().isoformat(),
            "content_mode": "hash_only",
            "interactions": [NO_BLACK],
            "calls": [{"call_id": "m1", "kind": "model", "tokens": {"in": 1200, "cached": 1000, "out": 80}}],
            "cost": {"usd": 0.0021},
        }
    )
    async with uow(space_id) as tx:
        await signals.consume(tx, space_id, [Applied(WHATSAPP_SOURCE, clock.now(), record)])
        await budget.consume(tx, space_id, [Applied(VOICE_SOURCE, clock.now(), record)])
    clock.advance(seconds=SETTLING.total_seconds() + 1)
    async with uow(space_id) as tx:
        found = await tx.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert found is not None
        profile = await tx.identity.profile_of(found.handle_id)
    assert profile is not None
    await compile_.compile(Task(TaskName.COMPILE, space_id, profile_id=profile.profile_id))
    statements: list[tuple[object, str]] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append((asyncio.current_task(), statement))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        caller = principal(VOICE_SOURCE, config.space(space_id))
        yield Reads(
            serve, signals, caller, uow, clock, space_id, statements, config, profile.profile_id, cache
        )
    finally:
        event.remove(Engine, "before_cursor_execute", count)
        await registry.close()
        await cell_engine.dispose()


async def test_a_warm_read_with_the_constraints_block_runs_no_statement(reads: Reads) -> None:
    first, cold = await reads.read("call-1")
    assert cold > 0  # the first read resolves the customer and fills the signals cache
    assert first.constraints is not None
    assert [h.values for h in first.constraints.hard] == [["preto"]]
    await reads.poll()
    warm, ran = await reads.read("call-2")
    assert (warm.path, ran) == (DeliveryPath.T0, 0)
    assert warm.constraints == first.constraints
    # Derived, the block reads the same, still without a statement.
    reads.clock.advance(minutes=6)
    assert await reads.signals.derive_due(reads.space_id) == 1
    await reads.poll()
    derived, ran = await reads.read("call-3")
    assert ran == 0
    assert derived.constraints is not None
    assert [h.values for h in derived.constraints.hard] == [["preto"]]


async def test_a_warm_read_with_the_budget_block_runs_no_statement(reads: Reads) -> None:
    first, cold = await reads.read("wa-1", ["budget", "constraints"])
    assert cold > 0
    assert first.budget is not None
    assert first.budget.counted
    assert first.budget.pack is not None
    assert first.budget.pack.total > 0
    assert first.budget.conversation is not None
    assert (first.budget.conversation.turns, first.budget.conversation.tokens_in) == (1, 1200)
    await reads.poll()
    warm, ran = await reads.read("wa-1", ["budget", "constraints"])
    assert (warm.path, ran) == (DeliveryPath.T0, 0)
    assert warm.budget == first.budget


async def test_an_experiment_that_is_on_keeps_the_warm_read_free_of_the_database(reads: Reads) -> None:
    # A memory experiment and element experiments on the block (24.7): the draws of the
    # person come from the database once and stay in the read state like the target.
    start = reads.clock.now() - timedelta(days=1)
    space = reads.config.space(reads.space_id)
    arms = [Arm(name="control", fraction=0.5), Arm(name="on", fraction=0.5)]
    reads.config.put(
        space.model_copy(
            update={
                "features": FeatureSettings(enabled=frozenset({Feature.SIGNALS, Feature.MEASUREMENT})),
                "experiments": [
                    Experiment(name="pilot", fraction_with_memory=1.0, salt="s", starts_at=start)
                ],
                "measurement": MeasurementSettings(
                    experiments=[
                        ElementExperiment(
                            name="soft_holdout", kind="element", element="soft", arms=arms,
                            salt="salt-soft-holdout", starts_at=start,
                        ),
                        ElementExperiment(
                            name="voice_holdout", kind="agent", agent="whatsapp-bot", arms=arms,
                            salt="salt-voice-holdout", starts_at=start,
                        ),
                    ]
                ),
            }
        )
    )  # fmt: skip
    first, cold = await reads.read("call-1")
    assert cold > 0
    assert first.constraints is not None
    await reads.poll()
    warm, ran = await reads.read("call-2")
    assert (warm.path, ran) == (DeliveryPath.T0, 0)
    assert warm.constraints == first.constraints
    # A change to the person's handles or profile drops the draws with the target: the next read asks again.
    reads.serve.read_state.invalidate(reads.space_id, Change(profile_ids=frozenset({reads.profile_id})))
    _, again = await reads.read("call-3")
    assert again > 0
    await reads.poll()
    _, ran = await reads.read("call-4")
    assert ran == 0


async def test_a_section_held_out_reads_its_own_pack_warm_and_leaves_the_default_pack_alone(
    reads: Reads,
) -> None:
    # The default pack, warm, cached under its own key.
    await reads.read("call-1", ["constraints"])
    await reads.poll()
    _, ran = await reads.read("call-2", ["constraints"])
    assert ran == 0
    packs = {k: v for k, v in reads.cache.values.items() if k.startswith("t0:")}
    assert packs
    # A person in the control arm of an experiment on the episodes section.
    start = reads.clock.now() - timedelta(days=1)
    arms = [Arm(name="control", fraction=0.5), Arm(name="on", fraction=0.5)]
    async with reads.uow(reads.space_id) as tx:
        found = await tx.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
    assert found is not None
    salt = next(
        f"salt-episodes-{i}"
        for i in range(100)
        if arm_of(f"salt-episodes-{i}", arms, found.handle_id) == "control"
    )
    space = reads.config.space(reads.space_id)
    reads.config.put(
        space.model_copy(
            update={
                "features": FeatureSettings(
                    enabled=frozenset({Feature.SIGNALS, Feature.TURNS, Feature.MEASUREMENT})
                ),
                "measurement": MeasurementSettings(
                    experiments=[
                        ElementExperiment(
                            name="episodes_holdout", kind="element", element="pack_section",
                            section="episodes", arms=arms, salt=salt, starts_at=start,
                        )
                    ]
                ),
            }
        )
    )  # fmt: skip
    held, cold = await reads.read("call-3", ["constraints"])
    assert cold > 0  # the draw and the section's own pack, once
    await reads.poll()
    again, ran = await reads.read("call-4", ["constraints"])
    assert (again.path, ran) == (DeliveryPath.T0, 0)
    assert again.etag == held.etag
    # The default pack's entries are where they were, byte for byte; the arm's pack sits beside them.
    assert {k: reads.cache.values.get(k) for k in packs} == packs
    assert [k for k in reads.cache.values if k.startswith("t0:") and k.endswith("episode:*")]
