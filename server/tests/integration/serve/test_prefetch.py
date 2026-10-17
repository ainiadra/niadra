"""The prefetch of a turn over PostgreSQL (11, 3.5): the read that follows answers as fast or
faster than a read that had no prefetch, with the same bytes.

Two read processes share the database, the pack cache and the hot layer, as two replicas do. One only
reads; the other gets each turn's partial transcript first. Both encode the turn with an encoder that
takes `ENCODE_S`, like the models pod under load, and both already hold the customer's retrieval entry,
so the comparison is of the turn's own work.
"""

import asyncio
import statistics
import time
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import (
    DeterministicDecider,
    DeterministicModels,
    DictSecrets,
    FixedClock,
    InMemoryCache,
    InMemoryHotLayer,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.app.compile import CompileService
from niadra.app.decide import DecideService
from niadra.app.identity import IdentityService
from niadra.app.ingest import IngestService
from niadra.app.memory import MemoryService
from niadra.app.retrieval import RetrievalCache, RetrievalService
from niadra.app.serve import ServeService
from niadra.app.sessions import SessionService
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse, PrefetchRequest
from niadra.contracts.ingest import validate_items
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.records import Episode
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import DeliveryPath, HandleType, Speaker, Verification
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import CellStack, CloningUnitOfWork
from tests.integration.serve.test_retrieval_cache import Encoder384
from tests.latency import within

PHONE = "+5511900007788"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)
QUESTION = "qual foi o protocolo que me passaram por e-mail?"
ENCODE_S = 0.02
TURNS = 15
TOPICS = (
    ("technical_visit", "O técnico visitou a casa e trocou o roteador da sala"),
    ("billing", "Pediu a segunda via da fatura de {month} e o boleto chegou por e-mail"),
    ("internet_down", "A internet caiu à noite durante {n} horas no bairro"),
    ("plan_change", "Perguntou sobre mudar para o plano de 500 megas com desconto"),
    ("cancellation", "Ameaçou cancelar por causa da lentidão no fim de semana"),
    ("address", "Informou a mudança de endereço para a rua das Flores"),
)
MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto")


async def remember(pg_uow: CloningUnitOfWork, space: UUID, origin: UUID, now: datetime) -> None:
    """Forty conversations over eight months, each its own summary, and a protocol said by e-mail in one."""
    episodes = []
    async with pg_uow(space) as uow:
        for n in range(40):
            category, summary = TOPICS[n % len(TOPICS)]
            at = now - timedelta(days=n * 6 + 1)
            episode = Episode(
                uuid7(),
                1,
                origin,
                uuid7(),
                "email" if n == 8 else "whatsapp",
                WHATSAPP_SOURCE,
                summary.format(month=MONTHS[n % len(MONTHS)], n=n % 5 + 2) + f" (atendimento {n + 1})",
                "support",
                category,
                category,
                "resolved",
                at,
                at,
            )
            await uow.memory.add_episode_version(episode)
            episodes.append(episode)
        said = episodes[8]  # about the internet: billing conversations are held back at V1
        await uow.memory.insert_values(
            [
                SettledValue(
                    uuid7(),
                    origin,
                    ValueType.PROTOCOL,
                    "81220",
                    said.started_at,
                    Speaker.AI_AGENT,
                    said.session_id,
                    "email",
                    WHATSAPP_SOURCE,
                    said.started_at,
                    episode_id=said.episode_id,
                )
            ]
        )
    config = StaticConfig(sample_config(space))
    models = Encoder384()
    memory = MemoryService(
        pg_uow,
        models,
        DecideService(pg_uow, models, FixedClock(), decider=DeterministicDecider()),
        FixedClock(),
        config,
    )
    while await memory.backfill_vectors(space):
        pass


class SlowEncoder(Encoder384):
    async def embed_query(self, text):  # type: ignore[no-untyped-def]
        await asyncio.sleep(ENCODE_S)
        return await super().embed_query(text)


class Receipts:
    def __init__(self) -> None:
        self.sent: list[ReceiptDraft] = []

    async def emit(self, drafts: list[ReceiptDraft], timeout_s: float) -> bool:
        self.sent.extend(drafts)
        return True

    async def mark(self, mark: ProducerMark) -> None:
        return None


def answer(response: ContextResponse) -> dict[str, Any]:
    return response.model_dump(mode="json", exclude={"timing", "lag_seconds"})


def ask(conversation: str) -> ContextRequest:
    return ContextRequest(
        subject=SUBJECT,
        conversation_id=conversation,
        verification=Verification.V1,
        view="chat",
        query=QUESTION,
        format="json",
    )


@pytest.mark.parametrize("semantic", [True, False], ids=["semantic", "rules_only"])
async def test_the_read_after_a_prefetch_is_as_fast_or_faster_and_the_same_bytes(
    pg_stack: CellStack, pg_uow: CloningUnitOfWork, semantic: bool
) -> None:
    """With the semantic channel on (an encoder of `ENCODE_S`) and off, as production runs by default."""
    space = uuid7()
    base = sample_config(space)
    config = StaticConfig(base)
    clock, cache, hot, keys = FixedClock(), InMemoryCache(), InMemoryHotLayer(), pg_stack.vault
    identity = IdentityService(pg_uow, clock, config)
    ingest = IngestService(
        pg_uow,
        identity,
        SessionService(pg_uow, clock, config),
        keys,
        hot,
        clock,
        config,
        InMemoryObjectStore(),
        DictSecrets(),
    )
    caller = principal(WHATSAPP_SOURCE, config.space(space))
    # Each turn is its own conversation, where the customer wrote from the number (V1).
    items = [
        {
            "type": "event",
            "idempotency_key": conversation,
            "channel": "whatsapp",
            "conversation_id": conversation,
            "handles": [{"type": "phone_e164", "value": PHONE}],
            "speaker": {"role": "customer"},
            "content": {"type": "text", "text": "Oi, a internet caiu de novo"},
            "occurred_at": clock.now().isoformat(),
            "verification_hint": "V1",
        }
        for conversation in ("warmup", *(f"turn-{n}" for n in range(TURNS)))
    ]
    valid, errors = validate_items(items)
    await ingest.ingest_items(caller, valid, errors)
    async with pg_uow(space) as tx:
        handle = await tx.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        assert handle is not None
        profile = await tx.identity.profile_of(handle.handle_id)
    assert profile is not None
    await remember(pg_uow, space, handle.handle_id, clock.now())
    compiler = CompileService(pg_uow, keys, cache, clock, config, hot=hot)
    await compiler.compile(Task(TaskName.COMPILE, space, profile_id=profile.profile_id))

    def process(receipts: Receipts) -> ServeService:
        retrieval = RetrievalService(
            pg_uow,
            config,
            clock,
            RetrievalCache(64 << 20),
            SlowEncoder() if semantic else None,
            deadline_s=0.5,
        )
        return ServeService(
            pg_uow,  # type: ignore[arg-type]
            cache,
            hot,
            keys,
            receipts,
            compiler,
            DeterministicModels(),
            clock,
            config,
            "prefetch-test",
            databases=pg_stack.registry,
            retrieval=retrieval,
        )

    receipts = Receipts()
    plain, prefetching = process(Receipts()), process(receipts)
    for serve in (plain, prefetching):
        # A turn of another conversation first: each process holds the customer's entry and its caches.
        warmup = ask("warmup").model_copy(update={"query": "bom dia"})
        await serve.context(caller, warmup)
        await serve.retrieval.drain()

    cold_ms: list[float] = []
    warm_ms: list[float] = []
    cold_slots_ms: list[float] = []
    warm_slots_ms: list[float] = []
    cold = warm = None
    for n in range(TURNS):
        started = time.perf_counter()
        # Both processes answer the same conversation, so its pin and its live turns are the same.
        cold = await plain.context(caller, ask(f"turn-{n}"))
        cold_ms.append((time.perf_counter() - started) * 1000)
        cold_slots_ms.append(cold.timing["slots"])

        partial = ask(f"turn-{n}")
        queued = prefetching.prefetch(
            caller,
            PrefetchRequest(
                subject=SUBJECT,
                conversation_id=partial.conversation_id,
                verification=Verification.V1,
                view="chat",
                query=QUESTION,
            ),
        )
        assert queued
        await prefetching.prefetches.drain()
        started = time.perf_counter()
        warm = await prefetching.context(caller, partial)
        warm_ms.append((time.perf_counter() - started) * 1000)
        warm_slots_ms.append(warm.timing["slots"])

    assert cold is not None
    assert warm is not None
    assert warm.path is cold.path is DeliveryPath.T0
    assert "81220" in (warm.slots or "")
    assert answer(warm) == answer(cold)
    reused = [r for r in receipts.sent if (r.detail or {}).get("prefetched") == "1"]
    assert len(reused) == TURNS
    label = "semantic on" if semantic else "rules only"
    print(
        f"\n{label}, read with query, median of {TURNS}: "
        f"cold {statistics.median(cold_ms):.1f} ms (slots {statistics.median(cold_slots_ms):.2f} ms), "
        f"after prefetch {statistics.median(warm_ms):.1f} ms "
        f"(slots {statistics.median(warm_slots_ms):.2f} ms)"
    )
    # The stage the prefetch replaces; the whole read too when the encoding is most of it (on a test
    # machine a rules-only read differs by less than its noise, so only its stage is held to it).
    within(f"{label}, slots after prefetch, median", statistics.median(warm_slots_ms) / 1000,
           max(statistics.median(cold_slots_ms), 0.01) / 1000)  # fmt: skip
    if semantic:
        within(f"{label}, read after prefetch, median", statistics.median(warm_ms) / 1000,
               statistics.median(cold_ms) / 1000)  # fmt: skip
