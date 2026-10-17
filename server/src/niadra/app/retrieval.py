"""The per-customer retrieval cache of the read process and the semantic channel (11, 3.3).

A customer's navigable rows are sealed row by row; opening them on every turn is the read's real cost, not
the ranking. The cache keeps each profile's opened rows (`ProfileEntry`) for one generation, in a
least-recently-used map capped in bytes, not in entries: one customer with a long history weighs as much
as a hundred new ones, and the node's memory is what runs out.

Only the semantic channel reads the rows and their vectors. A process without it, the default, loads the
values and the anchors alone: a few short queries in place of the whole memory opened, and an entry of a few
KiB.

An entry is valid only for the generation it was loaded at, the profile's handles at the time and a
privacy version at least the one the read requires, the same three checks the compiled packs pass: a
compilation, a merge or a forget request makes the next read load again, and a stale entry is never
served. Loads run one at a time per profile, at most `load_concurrency` at once, each in a short
transaction of its own; a read that cannot wait for one goes on without the channels that need it.

The semantic channel encodes the turn and ranks by exact cosine inside one hard deadline (30 ms by
default). Past it, or when the encoder fails, the result says `skipped` and the read goes on: the
channel never fails or slows a read beyond its budget.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from niadra.app.compile import compile_settings, load_snapshot
from niadra.app.serve_history import SYSTEM_EVENTS_LIMIT
from niadra.config.models import SpaceConfig
from niadra.domain.common.errors import Unavailable
from niadra.domain.compile import CompileInput, MemorySnapshot, Variant, profile_target
from niadra.domain.compile.candidates import CandidateBuilder
from niadra.domain.compile.templates import Formatter
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.records import ProfileRecord
from niadra.domain.serve.history import history_docs
from niadra.domain.serve.relative_time import marks_from_events, marks_from_objects
from niadra.domain.serve.retrieval import (
    ChannelResult,
    ChannelStatus,
    ProfileEntry,
    build_entry,
    cosine_scores,
)
from niadra.ports.infra import Clock, ConfigProvider, NoTelemetry, QueryEncoder, RetrievalTelemetry
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

SEMANTIC = "semantic"
SEMANTIC_DEADLINE_S = 0.030
MAX_ENTRY_SHARE = 8
"""No entry may take more than an eighth of the cache: one very long history must not flush everyone."""
LOAD_CONCURRENCY = 2
"""Loads at once per process. Each holds a connection of the space's small pool for its transaction."""
PREPARED_QUERIES = 256
"""Encodings a prefetch made ahead of the read (11, 3.5), the newest kept: one per live voice turn."""
PREPARED_QUERY_TTL_S = 60.0
PREPARE_DEADLINE_S = 1.0
"""A prefetch encodes off the read path, so it may wait longer than a read would; not forever."""

_Key = tuple[UUID, UUID]


class RetrievalCache:
    """Profiles' entries by space and profile, least recently used first out, within `max_bytes`."""

    def __init__(self, max_bytes: int, telemetry: RetrievalTelemetry | None = None) -> None:
        self.max_bytes = max_bytes
        self._entries: OrderedDict[_Key, ProfileEntry] = OrderedDict()
        self._bytes = 0
        self._telemetry = telemetry or NoTelemetry()

    @property
    def bytes_used(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._entries)

    def get(
        self, space_id: UUID, profile: ProfileRecord, generation: int, min_privacy: int = 0
    ) -> ProfileEntry | None:
        key = (space_id, profile.profile_id)
        entry = self._entries.get(key)
        if entry is None:
            self._event("miss")
            return None
        if not _serves(entry, profile, generation, min_privacy):
            self._remove(key)
            self._event("stale")
            return None
        self._entries.move_to_end(key)
        self._event("hit")
        return entry

    def peek(self, space_id: UUID, profile_id: UUID) -> ProfileEntry | None:
        """The held entry, valid or not, without touching its place in the order."""
        return self._entries.get((space_id, profile_id))

    def put(self, space_id: UUID, entry: ProfileEntry) -> bool:
        if entry.nbytes > self.max_bytes // MAX_ENTRY_SHARE:
            self._event("too_large")
            return False
        key = (space_id, entry.profile_id)
        held = self._entries.get(key)
        if held is not None and held.generation > entry.generation:
            return False  # a slow load never replaces what a newer one already brought
        if held is not None:
            self._remove(key)
        self._entries[key] = entry
        self._bytes += entry.nbytes
        while self._bytes > self.max_bytes:
            oldest = next(iter(self._entries))
            self._remove(oldest)
            self._event("evicted")
        self._event("loaded")
        return True

    def drop(self, space_id: UUID, profile_id: UUID) -> None:
        self._remove((space_id, profile_id))

    def _remove(self, key: _Key) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry.nbytes

    def _event(self, event: str) -> None:
        self._telemetry.retrieval_cache(event, self._bytes, len(self._entries))


def _serves(entry: ProfileEntry, profile: ProfileRecord, generation: int, min_privacy: int) -> bool:
    return (
        entry.generation >= generation
        and entry.handles == frozenset(profile.handle_ids)
        and entry.privacy_version >= max(min_privacy, profile.privacy_version)
    )


@dataclass(frozen=True, slots=True)
class _Load:
    space_id: UUID
    profile: ProfileRecord
    snapshot: MemorySnapshot | None = None
    generation: int | None = None


class RetrievalService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        config: ConfigProvider,
        clock: Clock,
        cache: RetrievalCache,
        encoder: QueryEncoder | None = None,
        *,
        deadline_s: float = SEMANTIC_DEADLINE_S,
        telemetry: RetrievalTelemetry | None = None,
        load_concurrency: int = LOAD_CONCURRENCY,
    ) -> None:
        self._uow = uow
        self._config = config
        self._clock = clock
        self.cache = cache
        self._encoder = encoder
        self._deadline_s = deadline_s
        self._telemetry = telemetry or NoTelemetry()
        self._loads = asyncio.Semaphore(load_concurrency)
        self._loading: dict[_Key, asyncio.Task[ProfileEntry | None]] = {}
        # By space too: one space's traffic must never show in another's timings.
        self._queries: OrderedDict[tuple[UUID, str], tuple[float, list[float], str]] = OrderedDict()

    @property
    def semantic_on(self) -> bool:
        return self._encoder is not None

    async def entry(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        generation: int | None,
        *,
        min_privacy: int = 0,
        wait_s: float | None = None,
    ) -> ProfileEntry | None:
        """The profile's entry for `generation` (the one the read saw in the cache; None reads it from the
        database). A miss loads it; with `wait_s` the read waits at most that long and gets None while the
        load goes on for the next turn."""
        if generation is None:
            async with self._uow(space_id) as uow:
                generation = await uow.compile.current_generation(profile_target(profile.profile_id))
        found = self.cache.get(space_id, profile, generation, min_privacy)
        if found is not None and self._complete(found):
            return found
        task = self._start(_Load(space_id, profile))
        try:
            loaded = await asyncio.wait_for(asyncio.shield(task), wait_s)
        except TimeoutError:
            return None
        if loaded is None or not _serves(loaded, profile, generation, min_privacy):
            return None
        return loaded

    def _complete(self, entry: ProfileEntry) -> bool:
        """An entry without the customer's rows and their vectors serves every read of a process without
        the semantic channel, the only one that reads them."""
        return entry.with_vectors or not self.semantic_on

    def warm(self, space_id: UUID, profile: ProfileRecord, generation: int | None) -> None:
        """The first read of a conversation: load the entry in the background when it is missing or older
        than `generation`, so the turns that follow find it."""
        held = self.cache.peek(space_id, profile.profile_id)
        if held is not None and _serves(held, profile, generation or 0, 0) and self._complete(held):
            return
        self._start(_Load(space_id, profile))

    def offer(
        self, space_id: UUID, profile: ProfileRecord, generation: int, snapshot: MemorySnapshot
    ) -> None:
        """A compilation in this process already opened the profile's memory: build the entry from it.
        `generation` must have been read before the snapshot, so the entry never claims newer data."""
        held = self.cache.peek(space_id, profile.profile_id)
        if held is not None and _serves(held, profile, generation, 0) and self._complete(held):
            return
        self._start(_Load(space_id, profile, snapshot, generation))

    async def semantic_for(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        query: str,
        allowed: Collection[str] | None = None,
        *,
        generation: int,
        min_privacy: int = 0,
        deadline_s: float | None = None,
    ) -> ChannelResult:
        """The semantic channel of one turn, cache lookup included, for the slots (WP2): never waits for a
        load. A customer not in the cache yet is `skipped` (`cold`) and loads for the next turn."""
        if self._encoder is None:
            return self._done(ChannelResult(SEMANTIC, ChannelStatus.OFF), time.perf_counter())
        started = time.perf_counter()
        found = await self.entry(space_id, profile, generation, min_privacy=min_privacy, wait_s=0)
        if found is None:
            return self._done(ChannelResult(SEMANTIC, ChannelStatus.SKIPPED, reason="cold"), started)
        budget = self._deadline_s if deadline_s is None else deadline_s
        return await self.semantic(
            found,
            query,
            allowed,
            deadline_s=max(0.0, budget - (time.perf_counter() - started)),
            space_id=space_id,
        )

    async def prepare_query(
        self, space_id: UUID, query: str, *, deadline_s: float = PREPARE_DEADLINE_S
    ) -> bool:
        """Encode `query` ahead of the read that will ask it (a prefetch): the read's semantic channel then
        ranks with this vector instead of encoding within its own deadline. The vector depends on the text
        and the model alone, so the ranking is the one the read would make. True when it is held."""
        if self._encoder is None or not query.strip():
            return False
        if self._prepared_vector(space_id, query) is not None:
            return True
        try:
            async with asyncio.timeout(deadline_s):
                vector, model = await self._encoder.embed_query(query)
        except Exception as error:
            log.info("prefetch could not encode the turn (%s)", type(error).__name__)
            return False
        self._queries[(space_id, query)] = (time.monotonic(), vector, model)
        while len(self._queries) > PREPARED_QUERIES:
            self._queries.popitem(last=False)
        return True

    def _prepared_vector(self, space_id: UUID | None, query: str) -> tuple[list[float], str] | None:
        if space_id is None:
            return None
        key = (space_id, query)
        held = self._queries.get(key)
        if held is None:
            return None
        if time.monotonic() - held[0] > PREPARED_QUERY_TTL_S:
            del self._queries[key]
            return None
        return held[1], held[2]

    async def drain(self) -> None:
        """Waits for the loads in flight (tests, shutdown)."""
        if self._loading:
            await asyncio.gather(*self._loading.values(), return_exceptions=True)

    async def semantic(
        self,
        entry: ProfileEntry,
        query: str,
        allowed: Collection[str] | None = None,
        *,
        deadline_s: float | None = None,
        space_id: UUID | None = None,
    ) -> ChannelResult:
        """Encode the turn and rank the rows the reader may see by exact cosine, all inside the deadline.
        With `space_id`, an encoding a prefetch made for the same text stands in for the encoder."""
        budget = self._deadline_s if deadline_s is None else deadline_s
        started = time.perf_counter()
        if self._encoder is None:
            return self._done(ChannelResult(SEMANTIC, ChannelStatus.OFF), started)
        if not query.strip() or not entry.vector_rows:
            return self._done(ChannelResult(SEMANTIC, ChannelStatus.EMPTY), started)
        stop_at = started + budget
        try:
            if (prepared := self._prepared_vector(space_id, query)) is not None:
                vector, model = prepared
            else:
                async with asyncio.timeout(budget):
                    vector, model = await self._encoder.embed_query(query)
        except TimeoutError:
            return self._done(ChannelResult(SEMANTIC, ChannelStatus.SKIPPED, reason="deadline"), started)
        except Unavailable as error:
            reason = "busy" if "busy" in str(error) else "unavailable"
            return self._done(ChannelResult(SEMANTIC, ChannelStatus.SKIPPED, reason=reason), started)
        except Exception:
            log.warning("query encoder failed", exc_info=True)
            return self._done(ChannelResult(SEMANTIC, ChannelStatus.SKIPPED, reason="unavailable"), started)
        if model != entry.vector_model:
            # The rows were encoded by another model: they rank by words until the backfill runs.
            return self._done(ChannelResult(SEMANTIC, ChannelStatus.EMPTY, reason="encoder"), started)
        scores = cosine_scores(entry, vector, allowed, expired=lambda: time.perf_counter() > stop_at)
        if scores is None:
            return self._done(ChannelResult(SEMANTIC, ChannelStatus.SKIPPED, reason="deadline"), started)
        return self._done(ChannelResult(SEMANTIC, ChannelStatus.RAN, scores), started)

    # Internals.

    def _done(self, result: ChannelResult, started: float) -> ChannelResult:
        self._telemetry.channel(result.channel, result.status.value, time.perf_counter() - started)
        return result

    def _start(self, load: _Load) -> asyncio.Task[ProfileEntry | None]:
        key = (load.space_id, load.profile.profile_id)
        running = self._loading.get(key)
        if running is not None:
            return running
        task = asyncio.create_task(self._load(load), name="retrieval:load")
        self._loading[key] = task
        task.add_done_callback(lambda _: self._loading.pop(key, None))
        return task

    async def _load(self, load: _Load) -> ProfileEntry | None:
        try:
            async with self._loads:
                entry = await self._read(load)
        except Exception:
            # Availability fails open (L10): without an entry the read goes on without the channels.
            log.warning("retrieval cache load failed", exc_info=True)
            return None
        self.cache.put(load.space_id, entry)
        return entry

    async def _read(self, load: _Load) -> ProfileEntry:
        profile = load.profile
        snapshot, generation = load.snapshot, load.generation
        config = self._config.space(load.space_id)
        if not self.semantic_on:
            return await self._read_light(load, config)
        async with self._uow(load.space_id) as uow:
            if snapshot is None or generation is None:
                # The generation first: an entry may claim older data than it has, never newer.
                generation = await uow.compile.current_generation(profile_target(profile.profile_id))
                # `profile` is the reader's view (5.3): the entry holds only the rows it may see, and it
                # serves only a read whose view has the same handles (`_serves`).
                snapshot = await load_snapshot(uow, profile, None, timezone=config.settings.timezone)
            vectors = await _vectors(uow, profile, snapshot)
            values = await uow.memory.values_for(profile.handle_ids)
            # The same window and bound as the history's system event rows.
            since = self._clock.now() - timedelta(days=config.settings.events_hot_days)
            events = await uow.ingest.system_events_for_handles(
                profile.handle_ids, since, SYSTEM_EVENTS_LIMIT
            )
        settings = compile_settings(config)
        fmt = Formatter(settings.timezone, settings.locale)
        # The candidates' texts and policy attributes do not depend on the reader: one entry serves every
        # variant, and each read applies its own policy to the rows.
        candidates = CandidateBuilder(CompileInput(snapshot, Variant(), settings), fmt).build()
        docs = history_docs(snapshot, candidates)
        return build_entry(
            profile.profile_id,
            generation,
            profile.handle_ids,
            profile.privacy_version,
            docs,
            vectors,
            values,
            [*marks_from_events(events), *marks_from_objects(snapshot.objects)],
        )

    async def _read_light(self, load: _Load, config: SpaceConfig) -> ProfileEntry:
        """The values and the anchors only: what the values channel, the guards and the time words read."""
        profile = load.profile
        async with self._uow(load.space_id) as uow:
            generation = load.generation
            if generation is None:
                # The generation first: an entry may claim older data than it has, never newer.
                generation = await uow.compile.current_generation(profile_target(profile.profile_id))
            objects = (
                load.snapshot.objects
                if load.snapshot is not None
                else await uow.objects.objects_for(profile.handle_ids)
            )
            values = await uow.memory.values_for(profile.handle_ids)
            since = self._clock.now() - timedelta(days=config.settings.events_hot_days)
            events = await uow.ingest.system_events_for_handles(
                profile.handle_ids, since, SYSTEM_EVENTS_LIMIT
            )
        return build_entry(
            profile.profile_id,
            generation,
            profile.handle_ids,
            profile.privacy_version,
            (),
            (),
            values,
            [*marks_from_events(events), *marks_from_objects(objects)],
            with_vectors=False,
        )


async def _vectors(uow: UnitOfWork, profile: ProfileRecord, snapshot: MemorySnapshot) -> list[ItemVector]:
    """The stored vectors of the profile's rows, through the modules that own them (16.2)."""
    return [
        *await uow.memory.vectors_for(profile.handle_ids),
        *await uow.objects.vectors_of(
            [o.object_id for o in snapshot.objects], [a.action_id for a in snapshot.actions]
        ),
        *await uow.traits.vectors_of([t.trait_id for t in snapshot.traits]),
    ]
