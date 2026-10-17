"""The retrieval cache over PostgreSQL, and the semantic channel against a models server over HTTP
(11, WP3).

The load opens a customer's sealed rows and vectors through the production repositories; the channel
talks to a small HTTP server that answers `/v1/embed` like the models pod, as slowly as a test wants.
"""

import asyncio
import json
import statistics
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import DeterministicDecider, DeterministicModels, FixedClock, StaticConfig
from niadra.adapters.outbound.models.http import HttpModelsClient
from niadra.app.decide import DecideService
from niadra.app.memory import MemoryService
from niadra.app.retrieval import RetrievalCache, RetrievalService
from niadra.domain.common.ids import uuid7
from niadra.domain.memory.facts import normalize_value
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.records import BusinessObject, Episode, Fact, ObjectKey, OpenItem, ProfileRecord
from niadra.domain.serve.retrieval import ChannelStatus
from niadra.domain.vocabulary import FactStatus, OpenItemStatus, Sensitivity, Speaker, SubjectKind
from niadra.testing.cell import sample_config
from tests.integration.conftest import CloningUnitOfWork
from tests.latency import within

T0 = datetime(2026, 9, 22, 17, tzinfo=UTC)
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")
DIM = 384
SUMMARY = (
    "Cliente relatou que a internet caiu de novo depois da visita técnica; pediu a segunda via da fatura "
    "de agosto e confirmou o protocolo {n} enviado por e-mail. Técnico remarcado para sábado de manhã."
)


class Encoder384(DeterministicModels):
    """Hash vectors of the pod's dimension, so the entry holds what production holds."""

    dim = DIM

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        vectors, _ = await super().embed(texts)
        return vectors, "hash-384"

    async def embed_query(self, text):  # type: ignore[no-untyped-def]
        vector, _ = await super().embed_query(text)
        return vector, "hash-384"


async def seed(pg_uow: CloningUnitOfWork, space: UUID, origin: UUID, rows: int) -> None:
    """A customer with `rows` navigable rows: mostly episodes, then facts, open items and objects."""
    async with pg_uow(space) as uow:
        for n in range(rows):
            at = T0 - timedelta(hours=n)
            match n % 10:
                case 0 | 1:
                    fact = Fact(
                        uuid7(),
                        origin,
                        "note",
                        f"prefere contato pela manhã, caso {n}",
                        "contact",
                        Sensitivity.NORMAL,
                        Speaker.CUSTOMER,
                        0.75,
                        at,
                        at,
                        FactStatus.ACTIVE,
                        at,
                    )
                    await uow.memory.insert_fact(fact, normalize_value(fact.value))
                case 2:
                    item = OpenItem(
                        uuid7(), origin, f"Enviar a segunda via {n}", "company", OpenItemStatus.OPEN, at, {}
                    )
                    await uow.memory.insert_open_item(item)
                case 3:
                    key = ObjectKey("order", "erp", f"4577{n:04d}")
                    await uow.objects.add_object(
                        BusinessObject(uuid7(), key, {"status": "open"}, at, SOURCE, origin)
                    )
                case _:
                    episode = Episode(
                        uuid7(),
                        1,
                        origin,
                        uuid7(),
                        "whatsapp",
                        SOURCE,
                        SUMMARY.format(n=81000 + n),
                        "support",
                        "technical",
                        "technical",
                        "resolved",
                        at,
                        at,
                    )
                    await uow.memory.add_episode_version(episode)
        await uow.memory.insert_values(
            [
                SettledValue(
                    uuid7(),
                    origin,
                    ValueType.PROTOCOL,
                    "81220",
                    T0,
                    Speaker.CUSTOMER,
                    uuid7(),
                    "email",
                    SOURCE,
                    T0,
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


def retrieval(pg_uow: CloningUnitOfWork, space: UUID, encoder: object = None) -> RetrievalService:
    return RetrievalService(
        pg_uow,
        StaticConfig(sample_config(space)),
        FixedClock(),
        RetrievalCache(64 << 20),
        encoder,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("rows", [50, 200])
async def test_a_load_opens_every_row_and_vector_once_and_reports_its_cost(
    pg_uow: CloningUnitOfWork, rows: int
) -> None:
    space, origin = uuid7(), uuid7()
    await seed(pg_uow, space, origin, rows)
    profile = ProfileRecord(uuid7(), SubjectKind.PERSON, "p", 1, 0, (origin,))
    service = retrieval(pg_uow, space, Encoder384())
    timings = []
    for _ in range(5):
        service.cache.drop(space, profile.profile_id)
        started = time.perf_counter()
        entry = await service.entry(space, profile, 0)
        timings.append((time.perf_counter() - started) * 1000)
        assert entry is not None
    # Without the semantic channel a load reads the values and the anchors only.
    plain = retrieval(pg_uow, space)
    light_timings = []
    for _ in range(5):
        plain.cache.drop(space, profile.profile_id)
        started = time.perf_counter()
        light = await plain.entry(space, profile, 0)
        light_timings.append((time.perf_counter() - started) * 1000)
        assert light is not None
    assert (light.with_vectors, light.dim) == (False, 0)
    assert (light.values, light.marks) == (entry.values, entry.marks)
    # Every row but the structured facts carries a vector (15.7): here the notes are free text.
    assert len(entry.vector_rows) == rows
    assert entry.vector_model == "hash-384"
    assert entry.dim == DIM
    assert [entry.values[i].raw for i in entry.values_by_key["81220"]] == ["81220"]
    started = time.perf_counter()
    assert await service.entry(space, profile, 0) is entry
    hit_ms = (time.perf_counter() - started) * 1000
    print(
        f"\nretrieval load, {rows} rows: median {statistics.median(timings):.1f} ms, "
        f"max {max(timings):.1f} ms; hit {hit_ms:.2f} ms; "
        f"entry {entry.nbytes / 1024:.0f} KiB; without vectors median "
        f"{statistics.median(light_timings):.1f} ms, entry {light.nbytes / 1024:.1f} KiB"
    )


@pytest.fixture
async def models_server() -> AsyncIterator[tuple[str, dict[str, float]]]:
    """Answers `POST /v1/embed` after `delay` seconds, like a models pod busy with the write side."""
    knobs = {"delay": 0.0}
    encoder = Encoder384()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":")[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length")
                )
                body = json.loads(await reader.readexactly(length))
                await asyncio.sleep(knobs["delay"])
                vectors, model = await encoder.embed(body["texts"])
                out = json.dumps({"vectors": vectors, "dim": DIM, "model_version": model}).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(out)}\r\n\r\n".encode()
                    + out
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", knobs
    finally:
        server.close()
        await server.wait_closed()


async def test_the_semantic_channel_over_http_ranks_in_time_and_skips_past_the_deadline(
    pg_uow: CloningUnitOfWork, models_server: tuple[str, dict[str, float]]
) -> None:
    url, knobs = models_server
    space, origin = uuid7(), uuid7()
    await seed(pg_uow, space, origin, 50)
    profile = ProfileRecord(uuid7(), SubjectKind.PERSON, "p", 1, 0, (origin,))
    client = HttpModelsClient(url)
    try:
        service = retrieval(pg_uow, space, client)
        entry = await service.entry(space, profile, 0)
        assert entry is not None
        await service.semantic(entry, "aquecimento")  # the first call opens the connection
        ran = await service.semantic(entry, "a internet caiu de novo")
        assert ran.status is ChannelStatus.RAN
        assert ran.scores

        knobs["delay"] = 1.0  # the pod is busy with a batch of item embeds
        started = time.perf_counter()
        late = await service.semantic(entry, "a internet caiu de novo")
        elapsed = time.perf_counter() - started
        assert (late.status, late.reason) == (ChannelStatus.SKIPPED, "deadline")
        assert not late.scores
        # The 30 ms deadline and room for the request: even ten times that is below the pod's second.
        within("the semantic channel past its deadline", elapsed, 0.060)

        knobs["delay"] = 0.0
        await asyncio.sleep(0.25)  # the abandoned request finishes on the server side
        again = await service.semantic(entry, "fatura de agosto")
        assert again.status is ChannelStatus.RAN
    finally:
        await client.aclose()
