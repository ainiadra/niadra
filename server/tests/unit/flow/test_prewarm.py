"""Predictive prewarm (13, 6.4): a customer who writes and then calls within 72 hours gets the
voice context warmed before the call, within the space's cap per minute, never above the retrieval cache's
headroom, and never for a customer without the pattern."""

import tracemalloc
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

from niadra.app.prewarm import MAX_WATCHED, Prewarmer
from niadra.app.retrieval import RetrievalCache
from niadra.config.models import SpaceConfig
from niadra.contracts.context import ContextRequest
from niadra.domain.compile.model import EpisodeRef, MemorySnapshot
from niadra.domain.serve.retrieval import build_entry
from niadra.domain.vocabulary import Verification
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, Cell, principal, sample_config
from tests.unit.compile.factories import PROFILE, T0, episode, marina
from tests.unit.serve.harness import SUBJECT, Harness, default_world

WHATSAPP_AGENT = principal()
VOICE_AGENT = principal(VOICE_SOURCE)


def config(per_minute: int = 5) -> SpaceConfig:
    base = sample_config()
    settings = base.settings.model_copy(update={"prewarm_per_minute": per_minute})
    return base.model_copy(update={"settings": settings})


def calls_after_writing() -> MemorySnapshot:
    """Twice in the last months the customer wrote on WhatsApp and called within three days."""
    return replace(
        marina(),
        episodes=(
            episode(1, "technical_visit", days_ago=0.2),
            episode(2, "technical_visit", days_ago=20, channel="voice"),
            episode(3, "technical_visit", days_ago=22),
            episode(4, "technical_visit", days_ago=60, channel="voice"),
            episode(5, "technical_visit", days_ago=61.5),
        ),
    )


def harness(cell: Cell, snapshot: MemorySnapshot, **settings: object) -> Harness:
    cell.config.put(config(**settings))  # type: ignore[arg-type]
    world = default_world()
    world.snapshots = [snapshot]
    return Harness(cell, world)


def read(**extra: object) -> ContextRequest:
    defaults = {"subject": SUBJECT, "verification": Verification.V1, "conversation_id": "wa-1"}
    return ContextRequest(**(defaults | extra))  # type: ignore[arg-type]


async def settled(h: Harness) -> None:
    await h.serve.prewarm.drain()
    await h.serve.retrieval.drain()


async def test_a_whatsapp_read_of_a_customer_who_calls_after_writing_warms_the_voice_context(
    cell: Cell,
) -> None:
    h = harness(cell, calls_after_writing())
    await h.compile()
    await h.serve.context(WHATSAPP_AGENT, read(query="A internet caiu de novo"))
    await settled(h)
    assert len(h.serve.prewarm) == 1
    assert h.serve.prewarm.stats.loaded == 1
    held = h.serve.retrieval.cache.peek(SPACE_ID, PROFILE)
    assert held is not None

    # The conversation is extracted and compiled: the next tick loads the new generation.
    await h.compile()
    generation = (await h.serve._generations([(SPACE_ID, PROFILE)]))[0]
    assert generation is not None
    assert generation > held.generation
    assert await h.serve.prewarm.tick() == 1
    await settled(h)
    assert h.serve.prewarm.stats.loaded == 2
    warmed = h.serve.retrieval.cache.peek(SPACE_ID, PROFILE)
    assert warmed is not None
    assert warmed.generation == generation
    assert await h.serve.prewarm.tick() == 0  # nothing moved since

    # The call: its first turn finds the customer's rows already opened.
    before = h.serve.prewarm.stats.as_dict()
    await h.serve.context(VOICE_AGENT, read(view="voice", conversation_id="call-1", query="E a visita?"))
    assert h.serve.retrieval.cache.peek(SPACE_ID, PROFILE) is warmed
    assert h.serve.prewarm.stats.as_dict() == before  # a voice read never starts a watch


async def test_without_the_pattern_nothing_is_watched(cell: Cell) -> None:
    h = harness(cell, marina())  # four WhatsApp conversations, never a call
    await h.compile()
    await h.serve.context(WHATSAPP_AGENT, read(query="A internet caiu de novo"))
    await settled(h)
    assert len(h.serve.prewarm) == 0
    assert h.serve.prewarm.stats.as_dict()["watched"] == 0


async def test_off_unless_the_space_sets_a_cap(cell: Cell) -> None:
    h = harness(cell, calls_after_writing(), per_minute=0)
    await h.compile()
    await h.serve.context(WHATSAPP_AGENT, read(query="A internet caiu de novo"))
    await settled(h)
    assert len(h.serve.prewarm) == 0


async def test_a_space_that_turns_it_off_drops_its_watches_at_the_next_tick(cell: Cell) -> None:
    h = harness(cell, calls_after_writing())
    await h.compile()
    await h.serve.context(WHATSAPP_AGENT, read(query="A internet caiu de novo"))
    await settled(h)
    assert len(h.serve.prewarm) == 1
    cell.config.put(config(0))
    await h.serve.prewarm.tick()
    assert len(h.serve.prewarm) == 0


async def test_a_watch_ends_72_hours_after_it_started(cell: Cell) -> None:
    h = harness(cell, calls_after_writing())
    await h.compile()
    await h.serve.context(WHATSAPP_AGENT, read(query="A internet caiu de novo"))
    await settled(h)
    cell.clock.advance(hours=72)
    await h.serve.prewarm.tick()
    assert len(h.serve.prewarm) == 0
    assert h.serve.prewarm.stats.expired == 1


# The bounds, on the prewarmer alone.


def contacts() -> list[EpisodeRef]:
    days = ((1.0, "whatsapp"), (0.5, "voice"), (30.0, "whatsapp"), (29.0, "voice"), (0.1, "whatsapp"))
    return [
        EpisodeRef(f"episode:{n}", "c", "complaint", T0 - timedelta(days=d), channel)
        for n, (d, channel) in enumerate(days)
    ]


class Loads:
    """Loads that put an empty entry in the cache, as a real load puts the customer's rows."""

    def __init__(self, cache_bytes: int = 1 << 20) -> None:
        self.done: list[tuple[UUID, UUID]] = []
        self.generation = 1
        self.cache = RetrievalCache(cache_bytes)

    async def load(self, space_id: UUID, profile_id: UUID) -> int | None:
        self.done.append((space_id, profile_id))
        self.cache.put(space_id, build_entry(profile_id, self.generation, (), 1, [], []))
        return self.generation

    async def generations(self, keys: object) -> list[int | None]:
        return [self.generation for _ in keys]  # type: ignore[attr-defined]


def prewarmer(cell: Cell, loads: Loads) -> Prewarmer:
    return Prewarmer(cell.cache, loads.cache, cell.config, cell.clock, loads.load, loads.generations)


def consider(p: Prewarmer, n: int, per_minute: int = 1) -> bool:
    return p.consider(
        config(per_minute),
        profile_id=UUID(int=n + 1),
        generation=1,
        reader_channel="whatsapp",
        confirmed_reader=True,
        episodes=contacts(),
        now=T0,
    )


async def test_the_cap_per_minute_is_respected_and_the_rest_waits_for_the_next_minute(cell: Cell) -> None:
    cell.config.put(config(1))
    cell.clock.at = T0
    loads = Loads()
    p = prewarmer(cell, loads)
    assert all(consider(p, n) for n in range(3))
    await p.drain()
    assert len(loads.done) == 1  # one load per minute in this space
    assert p.stats.capped == 2
    cell.clock.advance(minutes=1)
    assert await p.tick() == 2  # the two that waited are due again
    await p.drain()
    assert len(loads.done) == 2
    cell.clock.advance(minutes=1)
    await p.tick()
    await p.drain()
    assert len(loads.done) == 3
    assert await p.tick() == 0


async def test_a_reader_through_an_unconfirmed_link_or_on_the_voice_channel_starts_nothing(
    cell: Cell,
) -> None:
    cell.config.put(config(5))
    p = prewarmer(cell, Loads())
    kwargs = {"profile_id": PROFILE, "generation": 1, "episodes": contacts(), "now": T0}
    assert not p.consider(config(5), reader_channel="whatsapp", confirmed_reader=False, **kwargs)  # type: ignore[arg-type]
    assert not p.consider(config(5), reader_channel="voice", confirmed_reader=True, **kwargs)  # type: ignore[arg-type]
    no_voice = config(5).model_copy(
        update={"sources": [s for s in config(5).sources if s.channel != "voice"]}
    )
    assert not p.consider(no_voice, reader_channel="whatsapp", confirmed_reader=True, **kwargs)  # type: ignore[arg-type]
    assert len(p) == 0


async def test_prewarm_never_loads_into_a_retrieval_cache_above_its_headroom(cell: Cell) -> None:
    cell.config.put(config(5))
    loads = Loads(cache_bytes=64 << 10)
    p = prewarmer(cell, loads)
    full = build_entry(UUID(int=999), 1, (), 1, [], [])
    for n in range(40):
        p.retrieval_cache.put(SPACE_ID, replace(full, profile_id=UUID(int=10_000 + n), nbytes=1500))
    assert p.retrieval_cache.bytes_used > p.retrieval_cache.max_bytes * p.headroom
    assert consider(p, 0, per_minute=5)
    await p.drain()
    assert loads.done == []
    assert p.stats.full == 1


async def test_the_watch_list_is_bounded_and_small(cell: Cell) -> None:
    cell.config.put(config(5))
    p = prewarmer(cell, Loads())
    p.queue = type(p.queue)(lambda job: _noop(), max_threads=1)  # measure the watches, not the loads
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for n in range(MAX_WATCHED + 500):
        consider(p, n, per_minute=5)
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    grown = sum(s.size_diff for s in after.compare_to(before, "filename"))
    assert len(p) == MAX_WATCHED
    # Watches and the checked-pattern memo together stay well under 2 MiB per process.
    assert grown < 2 << 20, grown


async def _noop() -> None:
    return None
