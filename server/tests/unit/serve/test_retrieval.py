"""The per-customer retrieval cache and the semantic channel of the read path (11, WP3)."""

import asyncio
import gc
import random
import time
import tracemalloc
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from niadra.adapters.memory.fakes import DeterministicModels
from niadra.app.retrieval import RetrievalCache, RetrievalService
from niadra.contracts.context import ContextRequest
from niadra.domain.common.errors import Unavailable
from niadra.domain.compile.model import ItemKind
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.policy import ItemAttrs
from niadra.domain.records import EventRecord, ObjectKey, ProfileRecord
from niadra.domain.serve.history import HistoryDoc
from niadra.domain.serve.relative_time import SystemMark
from niadra.domain.serve.retrieval import (
    ChannelStatus,
    ProfileEntry,
    build_entry,
    cosine_scores,
)
from niadra.domain.serve.search import cosine
from niadra.domain.vocabulary import (
    DeliveryPath,
    EventKind,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell, principal
from tests.latency import within
from tests.unit.compile.factories import PHONE, PROFILE, T0
from tests.unit.serve.harness import SUBJECT, Harness
from tests.unit.serve.test_navigation import marina_docs, vectors_of_their_text

AGENT = principal()
MODELS = DeterministicModels()


def doc(n: int, text: str, *, kind: ItemKind = ItemKind.EPISODE, obj: str | None = None) -> HistoryDoc:
    attrs = ItemAttrs(kind=kind.value, category="billing", profile_id=PROFILE)
    key = ObjectKey("order", "erp", obj) if obj else None
    return HistoryDoc(f"{kind.value}:{UUID(int=n)}", kind, text, T0, attrs, object_key=key)


def vector_of(d: HistoryDoc, model: str = "hash-64") -> ItemVector:
    kind, _, raw = d.id.partition(":")
    return ItemVector(ItemKind(kind), UUID(raw), MODELS._embed(d.text), model)


DOCS = [
    doc(1, "Pediu a segunda via da fatura de agosto"),
    doc(2, "Internet caiu de novo, técnico agendado para sábado"),
    doc(3, "Reclamou da fatura duplicada, crédito de R$ 40"),
    doc(4, "pedido 45778-204 · status: entregue", kind=ItemKind.OBJECT, obj="45778-204"),
    doc(5, "Visita técnica remarcada, internet ainda caindo"),
]


def entry(
    docs: list[HistoryDoc] = DOCS,
    *,
    generation: int = 1,
    profile_id: UUID = PROFILE,
    handles: frozenset[UUID] = frozenset(),
    privacy: int = 1,
    vectors: list[ItemVector] | None = None,
) -> ProfileEntry:
    return build_entry(
        profile_id,
        generation,
        handles,
        privacy,
        docs,
        [vector_of(d) for d in docs] if vectors is None else vectors,
    )


def profile(profile_id: UUID = PROFILE, handles: tuple[UUID, ...] = (), privacy: int = 1) -> ProfileRecord:
    return ProfileRecord(profile_id, SubjectKind.PERSON, "p", 1, privacy, handles)


# The entry and its channels.


def test_the_cosine_over_half_precision_ranks_as_the_full_vectors_do() -> None:
    e = entry()
    question = MODELS._embed("fatura de agosto")
    scores = cosine_scores(e, question)
    assert scores is not None
    full = {d.id: cosine(question, MODELS._embed(d.text)) for d in DOCS}
    assert scores == pytest.approx({i: s for i, s in full.items() if s > 0}, abs=2e-3)
    only = {DOCS[0].id}
    assert set(cosine_scores(e, question, only) or {}) <= only


def test_a_cosine_past_its_deadline_returns_nothing_rather_than_half_a_ranking() -> None:
    assert cosine_scores(entry(), MODELS._embed("fatura"), expired=lambda: True) is None


def test_rows_of_another_encoder_rank_by_their_words_alone() -> None:
    vectors = [vector_of(d) for d in DOCS[:3]] + [vector_of(d, "older") for d in DOCS[3:4]]
    e = entry(vectors=vectors)
    assert e.vector_model == "hash-64"
    assert set(e.vector_rows) == {d.id for d in DOCS[:3]}
    assert cosine_scores(e, [1.0, 2.0]) == {}  # a question of another dimension ranks nothing


def settled(n: int, type: ValueType, raw: str, days_ago: int, channel: str = "whatsapp") -> SettledValue:
    at = T0 - timedelta(days=days_ago)
    return SettledValue(
        UUID(int=0x900 + n), PROFILE, type, raw, at, Speaker.CUSTOMER, UUID(int=n), channel, PROFILE, at
    )


def test_the_entry_indexes_settled_values() -> None:
    values = [
        settled(1, ValueType.PROTOCOL, "81220", 3, "email"),
        settled(2, ValueType.PROTOCOL, "90.001", 0),
        settled(3, ValueType.POSTAL_CODE, "01310-100", 1),
    ]
    e = build_entry(PROFILE, 1, (), 1, DOCS, [], values)
    protocols = [e.values[i] for i in e.values_by_type[ValueType.PROTOCOL]]
    assert [v.raw for v in protocols] == ["90.001", "81220"]  # newest first, as `values_for` reads them
    assert [e.values[i].type for i in e.values_by_key["01310100"]] == [ValueType.POSTAL_CODE]


def test_the_estimate_follows_what_python_allocates() -> None:
    rng = random.Random(7)
    words = ["fatura", "internet", "técnico", "visita", "pedido", "entrega", "reembolso", "cobrança"]

    def history() -> tuple[list[HistoryDoc], list[ItemVector]]:
        docs = []
        for _ in range(60):
            text = " ".join(
                rng.choice(words) if rng.random() > 0.2 else str(rng.randint(1000, 99999)) for _ in range(25)
            )
            attrs = ItemAttrs(
                kind="episode", category=rng.choice(["billing", "technical"]), profile_id=PROFILE
            )
            docs.append(
                HistoryDoc(f"episode:{uuid4()}", ItemKind.EPISODE, text, T0, attrs, channel="whatsapp")
            )
        vectors = [
            ItemVector(
                ItemKind.EPISODE,
                UUID(d.id.partition(":")[2]),
                [rng.uniform(-0.1, 0.1) for _ in range(384)],
                "m",
            )
            for d in docs
        ]
        return docs, vectors

    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        entries = []
        for _ in range(10):
            docs, vectors = history()
            entries.append(build_entry(uuid4(), 1, (), 1, docs, vectors))
            del docs, vectors
        gc.collect()
        allocated = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    estimated = sum(e.nbytes for e in entries)
    assert 0.75 < estimated / allocated < 1.33, (estimated, allocated)


# The cache.


def test_the_cache_stays_under_its_byte_cap_and_evicts_the_least_recently_used() -> None:
    one = entry(profile_id=UUID(int=1))
    cache = RetrievalCache(max_bytes=one.nbytes * 8 + 10)
    ids = [UUID(int=i) for i in range(1, 12)]
    for i, profile_id in enumerate(ids[:8]):
        assert cache.put(SPACE_ID, entry(profile_id=profile_id, generation=i + 1))
    assert cache.get(SPACE_ID, profile(ids[0]), 1) is not None  # the oldest is used again
    for profile_id in ids[8:]:
        cache.put(SPACE_ID, entry(profile_id=profile_id))
    assert cache.bytes_used <= cache.max_bytes
    assert len(cache) == 8
    held = {pid for pid in ids if cache.peek(SPACE_ID, pid) is not None}
    assert ids[0] in held
    assert {ids[1], ids[2], ids[3]}.isdisjoint(held)


def test_an_entry_serves_only_its_generation_handles_and_privacy_version() -> None:
    cache = RetrievalCache(max_bytes=10 << 20)
    cache.put(SPACE_ID, entry(generation=3, privacy=2))
    assert cache.get(SPACE_ID, profile(privacy=2), 2) is not None  # newer than the read saw: fine
    assert cache.get(SPACE_ID, profile(privacy=2), 3) is not None
    assert cache.get(SPACE_ID, profile(privacy=2), 4) is None  # a compilation landed: stale, dropped
    assert cache.peek(SPACE_ID, PROFILE) is None

    cache.put(SPACE_ID, entry(generation=3, privacy=2))
    assert cache.get(SPACE_ID, profile(handles=(uuid4(),), privacy=2), 3) is None  # a merge moved handles
    cache.put(SPACE_ID, entry(generation=3, privacy=2))
    assert cache.get(SPACE_ID, profile(privacy=3), 3) is None  # a forget request since
    cache.put(SPACE_ID, entry(generation=3, privacy=2))
    assert cache.get(SPACE_ID, profile(privacy=2), 3, min_privacy=3) is None  # the feed is ahead
    assert cache.bytes_used == 0


def test_a_slow_older_load_never_replaces_a_newer_entry_and_a_huge_one_stays_out() -> None:
    newer = entry(generation=5)
    cache = RetrievalCache(max_bytes=newer.nbytes * 16)
    assert cache.put(SPACE_ID, newer)
    assert not cache.put(SPACE_ID, entry(generation=4))
    assert cache.peek(SPACE_ID, PROFILE) is newer
    small = RetrievalCache(max_bytes=newer.nbytes * 4)  # an entry may take an eighth at most
    assert not small.put(SPACE_ID, newer)
    assert len(small) == 0


# The service over the in-memory cell.


class SlowModels(DeterministicModels):
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s

    async def embed_query(self, text: str) -> tuple[list[float], str]:
        await asyncio.sleep(self.delay_s)
        return await super().embed_query(text)


class FailingModels(DeterministicModels):
    async def embed_query(self, text: str) -> tuple[list[float], str]:
        raise Unavailable("models: /v1/embed answered 503")


def service(h: Harness, encoder: object = None, **extra: object) -> RetrievalService:
    return RetrievalService(h.uow, h.cell.config, h.cell.clock, RetrievalCache(64 << 20), encoder, **extra)  # type: ignore[arg-type]


def marina_record(h: Harness) -> ProfileRecord:
    record = h.world.profile(PROFILE)
    assert record is not None
    return record


async def test_a_miss_loads_the_rows_the_navigation_shows_and_the_next_read_hits(cell: Cell) -> None:
    h = Harness(cell)
    h.world.vectors = vectors_of_their_text(marina_docs(cell))
    retrieval = service(h, DeterministicModels())
    loads = 0
    read = retrieval._read

    async def counted(load):  # type: ignore[no-untyped-def]
        nonlocal loads
        loads += 1
        return await read(load)

    retrieval._read = counted  # type: ignore[method-assign]
    marina = marina_record(h)
    first, second = await asyncio.gather(
        retrieval.entry(SPACE_ID, marina, 0), retrieval.entry(SPACE_ID, marina, 0)
    )
    assert loads == 1  # one load per profile, whoever asks
    assert first is second is not None
    assert set(first.vector_rows) == {d.id for d in marina_docs(cell)}
    assert first.vector_model == "hash-64"
    assert await retrieval.entry(SPACE_ID, marina, 0) is first
    assert loads == 1

    await h.compile()  # a new generation: the next read loads again
    again = await retrieval.entry(SPACE_ID, marina, None)
    assert again is not None
    assert again.generation > first.generation
    assert loads == 2


async def test_the_entry_keeps_the_system_events_as_temporal_marks(cell: Cell) -> None:
    h = Harness(cell)
    inspected = T0 - timedelta(days=12)
    event = EventRecord(
        UUID(int=0x980),
        EventKind.SYSTEM_EVENT,
        BILLING_SOURCE,
        "erp",
        Speaker.SYSTEM,
        inspected,
        inspected,
        (PHONE,),
        canonical_type="inspection.completed",
        object_refs=(ObjectKey("claim", "erp", "21889"),),
    )
    handoff = replace(event, event_id=UUID(int=0x981), canonical_type=None)
    h.world.system_events += [event, handoff]
    loaded = await service(h).entry(SPACE_ID, marina_record(h), 0)
    assert loaded is not None
    # The inspection stays an anchor after its claim moved on to other events.
    assert SystemMark("inspection.completed", inspected) in loaded.marks
    assert [m.at for m in loaded.marks] == sorted(m.at for m in loaded.marks)


async def test_a_read_that_cannot_wait_goes_on_and_the_load_lands_for_the_next_turn(cell: Cell) -> None:
    h = Harness(cell)
    retrieval = service(h)
    read = retrieval._read

    async def slow(load):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.05)
        return await read(load)

    retrieval._read = slow  # type: ignore[method-assign]
    marina = marina_record(h)
    assert await retrieval.entry(SPACE_ID, marina, 0, wait_s=0.001) is None
    await retrieval.drain()
    assert retrieval.cache.peek(SPACE_ID, PROFILE) is not None


async def test_a_failed_load_leaves_the_read_without_the_cache(cell: Cell) -> None:
    h = Harness(cell)
    retrieval = service(h)

    async def broken(load):  # type: ignore[no-untyped-def]
        raise ConnectionError("database unreachable")

    retrieval._read = broken  # type: ignore[method-assign]
    assert await retrieval.entry(SPACE_ID, marina_record(h), 0) is None


async def test_the_semantic_channel_ranks_inside_its_deadline(cell: Cell) -> None:
    h = Harness(cell)
    h.world.vectors = vectors_of_their_text(marina_docs(cell))
    marina = marina_record(h)
    loaded = await service(h, DeterministicModels()).entry(SPACE_ID, marina, 0)
    assert loaded is not None

    off = await service(h).semantic(loaded, "fatura")
    assert off.status is ChannelStatus.OFF
    assert not off.scores

    ran = await service(h, DeterministicModels()).semantic(loaded, "fatura de agosto")
    assert ran.status is ChannelStatus.RAN
    assert ran.scores
    assert ran.reason is None

    empty = await service(h, DeterministicModels()).semantic(loaded, "   ")
    assert empty.status is ChannelStatus.EMPTY


@pytest.mark.parametrize(
    ("encoder", "reason"), [(SlowModels(5.0), "deadline"), (FailingModels(), "unavailable")]
)
async def test_a_slow_or_failing_encoder_marks_the_channel_skipped_within_the_deadline(
    cell: Cell, encoder: DeterministicModels, reason: str
) -> None:
    h = Harness(cell)
    h.world.vectors = vectors_of_their_text(marina_docs(cell))
    loaded = await service(h, DeterministicModels()).entry(SPACE_ID, marina_record(h), 0)
    assert loaded is not None
    started = time.perf_counter()
    result = await service(h, encoder).semantic(loaded, "fatura")
    elapsed = time.perf_counter() - started
    assert (result.status, result.reason, dict(result.scores)) == (ChannelStatus.SKIPPED, reason, {})
    # The deadline and room for the call: even ten times that is below the slow encoder's 5 s.
    within(f"a channel skipped for {reason}", elapsed, 0.030 + 0.030)


async def test_the_slots_semantic_call_never_waits_for_a_load(cell: Cell) -> None:
    h = Harness(cell)
    h.world.vectors = vectors_of_their_text(marina_docs(cell))
    marina = marina_record(h)
    retrieval = service(h, DeterministicModels())
    cold = await retrieval.semantic_for(SPACE_ID, marina, "fatura", generation=0)
    assert (cold.status, cold.reason) == (ChannelStatus.SKIPPED, "cold")
    await retrieval.drain()  # the miss started the load for the next turn
    warm = await retrieval.semantic_for(SPACE_ID, marina, "fatura", generation=0)
    assert warm.status is ChannelStatus.RAN
    off = await service(h).semantic_for(SPACE_ID, marina, "fatura", generation=0)
    assert off.status is ChannelStatus.OFF


async def test_an_encoder_of_another_model_ranks_nothing(cell: Cell) -> None:
    h = Harness(cell)
    h.world.vectors = [replace(v, model="older") for v in vectors_of_their_text(marina_docs(cell))]
    loaded = await service(h, DeterministicModels()).entry(SPACE_ID, marina_record(h), 0)
    assert loaded is not None
    result = await service(h, DeterministicModels()).semantic(loaded, "fatura")
    assert (result.status, result.reason) == (ChannelStatus.EMPTY, "encoder")


async def test_the_first_read_of_a_conversation_warms_the_cache(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    ask = ContextRequest(subject=SUBJECT, conversation_id="c1", verification=Verification.V1)
    served = await h.serve.context(AGENT, ask)
    assert served.path is DeliveryPath.T0
    await h.serve.retrieval.drain()
    assert h.serve.retrieval.cache.peek(SPACE_ID, PROFILE) is not None


async def test_a_read_that_no_turn_follows_starts_no_load(cell: Cell) -> None:
    h = Harness(cell)
    await h.compile()
    alone = await h.serve.context(AGENT, ContextRequest(subject=SUBJECT, verification=Verification.V1))
    brief = ContextRequest(subject=SUBJECT, conversation_id="c1", view="brief", verification=Verification.V1)
    await h.serve.context(AGENT, brief)
    assert alone.path is DeliveryPath.T0
    await h.serve.retrieval.drain()
    # Outside a conversation, or in a view without slots, nothing reads the entry: a load would only hold
    # one of the space's few connections while the reads behind it wait.
    assert h.serve.retrieval.cache.peek(SPACE_ID, PROFILE) is None


async def test_an_on_demand_compilation_hands_its_memory_to_the_cache(
    cell: Cell,
) -> None:
    h = Harness(cell)
    h.compiler._on_snapshot = h.serve.retrieval.offer
    read = h.serve.retrieval._read
    from_snapshot: list[bool] = []

    async def watched(load):  # type: ignore[no-untyped-def]
        from_snapshot.append(load.snapshot is not None)
        return await read(load)

    h.serve.retrieval._read = watched  # type: ignore[method-assign]
    served = await h.serve.context(AGENT, ContextRequest(subject=SUBJECT, verification=Verification.V1))
    assert served.path is DeliveryPath.T2  # nothing compiled yet
    await h.serve.retrieval.drain()
    assert from_snapshot[0] is True
    assert h.serve.retrieval.cache.peek(SPACE_ID, PROFILE) is not None


# Without a channel that ranks by vectors, an entry is the values and the anchors.


def marina_with_values_and_events(h: Harness) -> None:
    h.world.vectors = vectors_of_their_text(marina_docs(h.cell))
    h.world.values = [
        replace(settled(1, ValueType.PROTOCOL, "81220", 3), origin_handle_id=PHONE),
        replace(settled(2, ValueType.POSTAL_CODE, "01310-100", 1), origin_handle_id=PHONE),
    ]
    inspected = T0 - timedelta(days=2)
    h.world.system_events = [
        EventRecord(
            UUID(int=0x990),
            EventKind.SYSTEM_EVENT,
            BILLING_SOURCE,
            "erp",
            Speaker.SYSTEM,
            inspected,
            inspected,
            (PHONE,),
            canonical_type="inspection.completed",
        )
    ]


async def test_without_vectors_to_rank_the_entry_holds_the_values_and_anchors_only(cell: Cell) -> None:
    h = Harness(cell)
    marina_with_values_and_events(h)
    marina = marina_record(h)
    light = await service(h).entry(SPACE_ID, marina, 0)
    full = await service(h, DeterministicModels()).entry(SPACE_ID, marina, 0)
    assert light is not None
    assert full is not None
    assert (light.with_vectors, light.dim, dict(light.vector_rows)) == (False, 0, {})
    assert full.with_vectors
    assert full.vector_rows
    # What the values channel, the guards and the time words read is the same either way.
    assert light.values == full.values
    assert light.values_by_type == full.values_by_type
    assert light.values_by_key == full.values_by_key
    assert light.marks == full.marks
    assert light.marks
    assert light.nbytes < full.nbytes
