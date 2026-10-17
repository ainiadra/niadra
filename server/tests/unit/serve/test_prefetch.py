"""The prefetch of a turn (11, 3.5): the read that follows with the same question takes the
slots the prefetch computed, the same bytes a read without it computes, and nothing else changes."""

import asyncio
import json
import time
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import DeterministicModels
from niadra.app.compile import compile_settings
from niadra.app.prefetch import PrefetchQueue, PreparedTurn, PreparedTurns
from niadra.app.retrieval import RetrievalCache, RetrievalService
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse, PrefetchRequest
from niadra.domain.common.errors import Forbidden, ScopeMissing
from niadra.domain.compile import CompileInput, Variant
from niadra.domain.compile.candidates import CandidateBuilder
from niadra.domain.compile.templates import Formatter
from niadra.domain.memory.vectors import ItemKind, ItemVector
from niadra.domain.serve.cache_keys import pin_key
from niadra.domain.serve.history import history_docs
from niadra.domain.vocabulary import Audience, HandleType, ReceiptSurface, Scope, Verification
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.compile.factories import PROFILE
from tests.unit.serve.harness import SUBJECT, Harness
from tests.unit.serve.test_read_slots import harness, history

AGENT = principal()
QUESTION = "Qual foi o número do registro da visita?"


def prefetch(**extra: object) -> PrefetchRequest:
    defaults = {
        "subject": SUBJECT,
        "verification": Verification.V1,
        "conversation_id": "voz-1",
        "view": "chat",
        "query": QUESTION,
    }
    return PrefetchRequest(**(defaults | extra))  # type: ignore[arg-type]


def read(**extra: object) -> ContextRequest:
    defaults = {
        "subject": SUBJECT,
        "verification": Verification.V1,
        "conversation_id": "voz-1",
        "view": "chat",
        "query": QUESTION,
        "format": "json",
    }
    return ContextRequest(**(defaults | extra))  # type: ignore[arg-type]


def answer(response: ContextResponse) -> dict[str, object]:
    """What the agent gets, without what differs between any two reads (the stage timings)."""
    return response.model_dump(mode="json", exclude={"timing", "lag_seconds"})


def slots_receipts(h: Harness) -> list[dict[str, str]]:
    return [dict(r.detail) for r in h.receipts.sent if r.surface is ReceiptSurface.SLOTS]


async def prepared(h: Harness, request: PrefetchRequest | None = None) -> bool:
    queued = h.serve.prefetch(AGENT, request or prefetch())
    await h.serve.prefetches.drain()
    await h.serve.retrieval.drain()
    return queued


async def test_the_read_after_a_prefetch_takes_its_slots_and_they_are_the_cold_bytes(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    assert await prepared(h)
    assert h.receipts.sent == []  # the prefetch alone leaves no receipt
    warm = await h.serve.context(AGENT, read())
    assert "88280" in (warm.slots or "")
    assert slots_receipts(h)[-1]["prefetched"] == "1"

    other = Harness(cell, h.world)
    cold = await other.serve.context(AGENT, read())
    assert "prefetched" not in slots_receipts(other)[-1]
    assert answer(warm) == answer(cold)
    assert slots_receipts(h)[-1]["slots"] == slots_receipts(other)[-1]["slots"]
    assert slots_receipts(h)[-1]["analysis"] == slots_receipts(other)[-1]["analysis"]


async def test_a_prefetch_outside_a_conversation_is_queued_by_customer(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    # Outside a conversation nothing is proven (V0): the turn is prepared all the same, for that level.
    assert await prepared(h, prefetch(conversation_id=None))
    assert len(h.serve._prepared) == 1


async def test_a_prefetch_never_pins_the_pack(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    await prepared(h)
    key = pin_key(h.epoch.value, SPACE_ID, PROFILE, "voz-1", "chat", WHATSAPP_SOURCE)
    assert await cell.cache.get(key) is None
    await h.serve.context(AGENT, read())
    assert await cell.cache.get(key) is not None  # the read pins, as always


async def test_a_prefetch_warms_the_customer_so_a_longer_turn_reads_a_warm_cache(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    assert len(h.serve.retrieval.cache) == 0
    await prepared(h, prefetch(query="Qual foi o número"))
    assert len(h.serve.retrieval.cache) == 1
    longer = await h.serve.context(AGENT, read())
    assert "prefetched" not in slots_receipts(h)[-1]  # another question: its own slots
    cold = await Harness(cell, h.world).serve.context(AGENT, read(conversation_id="voz-2"))
    assert longer.slots == cold.slots


async def test_a_read_whose_inputs_changed_computes_its_own_slots(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    await prepared(h)
    # Another level proven in between: another variant, so the prepared slots are not this read's.
    h.world.proven = Verification.V2
    response = await h.serve.context(AGENT, read(verification=Verification.V2))
    assert response.slots is not None
    assert "prefetched" not in slots_receipts(h)[-1]


async def test_a_question_read_against_the_moment_keeps_only_the_warm_cache(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    count = "Quantas vezes eu já reclamei da internet caindo?"
    await prepared(h, prefetch(query=count))
    warm = await h.serve.context(AGENT, read(query=count))
    assert "prefetched" not in slots_receipts(h)[-1]
    cold = await Harness(cell, h.world).serve.context(AGENT, read(query=count))
    assert warm.slots == cold.slots


async def test_a_view_without_slots_has_nothing_to_prepare(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    assert h.serve.prefetch(AGENT, prefetch(view="brief")) is False
    assert len(h.serve.prefetches) == 0
    assert len(h.serve.retrieval.cache) == 0


async def test_a_prefetch_is_refused_as_the_read_would_be(cell: Cell) -> None:
    h = harness(cell)
    with pytest.raises(ScopeMissing):
        h.serve.prefetch(principal(scopes=frozenset({Scope.TRACK})), prefetch())
    with pytest.raises(Forbidden):
        h.serve.prefetch(AGENT, prefetch(view="full"))
    assert AGENT.audience is Audience.CUSTOMER_AGENT


async def test_a_prefetch_for_nobody_known_prepares_nothing_and_raises_nothing(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    stranger = Handle(type=HandleType.PHONE_E164, value="+55 11 90000-0000")
    assert await prepared(h, prefetch(subject=stranger))
    assert len(h.serve.retrieval.cache) == 0
    assert h.receipts.sent == []


class CountingEncoder(DeterministicModels):
    def __init__(self) -> None:
        self.encoded: list[str] = []

    async def embed_query(self, text: str) -> tuple[list[float], str]:
        self.encoded.append(text)
        return await super().embed_query(text)


def history_vectors(cell: Cell) -> list[ItemVector]:
    snapshot = history()
    settings = compile_settings(cell.config.space(SPACE_ID))
    fmt = Formatter(settings.timezone, settings.locale)
    candidates = CandidateBuilder(
        CompileInput(snapshot, Variant(verification=Verification.V2), settings), fmt
    ).build()
    models = DeterministicModels()
    vectors = []
    for doc in history_docs(snapshot, candidates):
        kind, _, raw = doc.id.partition(":")
        vectors.append(ItemVector(ItemKind(kind), UUID(raw), models._embed(doc.text), "hash-64"))
    return vectors


async def test_the_read_ranks_with_the_encoding_the_prefetch_made(cell: Cell) -> None:
    h = harness(cell)
    h.world.vectors = history_vectors(cell)
    encoder = CountingEncoder()
    h.serve.retrieval = RetrievalService(h.uow, cell.config, cell.clock, RetrievalCache(64 << 20), encoder)
    await h.compile()
    paraphrase = "a conexão parou de funcionar"
    await prepared(h, prefetch(query=paraphrase))
    assert encoder.encoded == [paraphrase]
    warm = await h.serve.context(AGENT, read(query=paraphrase))
    assert encoder.encoded == [paraphrase]  # the read encoded nothing
    detail = slots_receipts(h)[-1]
    assert detail["prefetched"] == "1"
    semantic = json.loads(detail["slots"])["channels"]["semantic"]
    assert semantic["hits"] > 0
    assert "skipped" not in semantic

    other = Harness(cell, h.world)
    other.serve.retrieval = RetrievalService(
        other.uow, cell.config, cell.clock, RetrievalCache(64 << 20), CountingEncoder()
    )
    await other.serve.context(AGENT, read(query="primeira leitura", conversation_id="voz-0"))
    await other.serve.retrieval.drain()  # the first read of a conversation warms the customer
    cold = await other.serve.context(AGENT, read(query=paraphrase, conversation_id="voz-3"))
    assert warm.slots == cold.slots


async def test_the_queue_runs_one_job_per_conversation_and_the_newest_waiting_one_wins() -> None:
    ran: list[str] = []
    gate = asyncio.Event()

    async def run(job: str) -> None:
        if job == "first":
            await gate.wait()
        ran.append(job)

    queue = PrefetchQueue(run, max_threads=2)
    assert queue.submit("a", "first")
    assert queue.submit("a", "second")
    assert queue.submit("a", "third")  # replaces "second", still waiting
    assert queue.submit("b", "other")
    assert not queue.submit("c", "dropped")  # two conversations tracked already
    await asyncio.sleep(0)
    gate.set()
    await queue.drain()
    assert ran == ["other", "first", "third"]
    assert len(queue) == 0


async def test_a_job_past_its_deadline_or_failing_is_dropped_and_the_next_runs() -> None:
    ran: list[str] = []

    async def run(job: str) -> None:
        if job == "slow":
            await asyncio.sleep(1)
        if job == "broken":
            raise RuntimeError("down")
        ran.append(job)

    queue = PrefetchQueue(run, deadline_s=0.01)
    queue.submit("a", "slow")
    queue.submit("a", "broken")
    queue.submit("b", "fine")
    await queue.drain()
    assert ran == ["fine"]


def test_prepared_turns_hold_only_the_newest_query_while_fresh() -> None:
    turns = PreparedTurns(max_entries=2, ttl_s=60)
    turns.put("a", PreparedTurn("oi", (), None, None, True, at=0.0))
    assert turns.get("a", "oi") is None  # prepared long ago
    fresh = PreparedTurn("oi", (), None, None, True, at=time.monotonic())
    turns.put("a", fresh)
    assert turns.get("a", "oi, tudo bem") is None
    turns.put("b", fresh)
    turns.put("c", fresh)
    assert len(turns) == 2
    assert turns.get("a", "oi") is None  # the least recently used went first
