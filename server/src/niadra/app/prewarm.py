"""Predictive prewarm across channels (13, 6.4): the voice context of a customer who is likely
to call, ready before the call.

A customer whose history says they come back by voice within 72 hours of a contact on another channel
(`domain.traits.recontact`) and who now reads on that other channel is watched for 72 hours. While
watched, the read process keeps the customer's retrieval entry loaded at the profile's current
generation: when the conversation that brought them in is extracted and compiled, the next tick loads
the new generation, so the first voice turn finds the rows opened, the values indexed and the vectors
ready. The voice pack itself is already compiled on every change (every source's usual view is in the
eager set, 5.10) and published to the shared cache.

The loads run on the prefetch machinery (`PrefetchQueue`: one job per customer, bounded concurrency, a
deadline) into the same byte-capped retrieval cache, and three bounds keep the read node safe:

- a space loads at most `prewarm_per_minute` entries per minute across the read processes (a counter in
  the shared cache; 0, the default, turns prewarm off);
- a prewarm never loads while the retrieval cache is above `HEADROOM` of its bytes, so it never evicts
  the entry of a customer being served, and the cache's own cap bounds what prewarm can hold;
- a process watches at most `MAX_WATCHED` customers, the oldest first out.

Only a read by a confirmed handle can start a watch, and the entry loaded is the confirmed view of the
profile: rows of a link nobody confirmed never ride a prediction into another reader's cache. No model
runs, nothing is written to the database, and the read that triggers a watch pays one dictionary lookup
or, once per customer and generation, a pass over its episode index.
"""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from niadra.app.prefetch import PrefetchQueue
from niadra.app.retrieval import RetrievalCache
from niadra.config.models import SpaceConfig
from niadra.domain.common.errors import NotFound
from niadra.domain.compile.model import EpisodeRef
from niadra.domain.traits.recontact import RECONTACT_WINDOW, recontact_pattern
from niadra.domain.vocabulary import Audience
from niadra.ports.infra import Clock, ConfigProvider, KeyValueCache, NoTelemetry, RetrievalTelemetry

log = logging.getLogger(__name__)

PREDICTED_CHANNEL = "voice"
"""The channel prewarm prepares for: where the customer waits on the line while memory is read."""
MAX_WATCHED = 2048
"""Customers one read process watches; about 200 bytes each, the entries themselves live in the cache."""
HEADROOM = 0.75
"""A prewarm loads only while the retrieval cache holds less than this share of its bytes."""
TICK_S = 30.0
PREWARM_DEADLINE_S = 5.0
"""A load off the read path may wait for its turn; not forever."""
PREWARM_CONCURRENCY = 1
"""Loads at once per process: prewarm never competes with reads for more than one connection."""
CHECKED = 4096
"""Customers and generations already checked for a pattern, the newest kept."""
GET_MANY = 256

type Loader = Callable[[UUID, UUID], Awaitable[int | None]]
"""Loads the confirmed view's retrieval entry of (space, profile); returns the generation loaded."""
type Generations = Callable[[Sequence[tuple[UUID, UUID]]], Awaitable[list[int | None]]]
"""The current generation of each (space, profile) in the shared cache; None when unknown."""


def cap_key(space_id: UUID, minute: int) -> str:
    """The shared per-space counter of one minute's loads; it names the space as every key the space's
    deletion purges does."""
    return f"prewarm:{minute}:{space_id}:loads"


@dataclass
class _Watch:
    until: datetime
    loaded: int | None = None
    """The generation the last prewarm loaded."""


@dataclass
class PrewarmStats:
    watched: int = 0
    loaded: int = 0
    capped: int = 0
    full: int = 0
    failed: int = 0
    expired: int = 0

    def as_dict(self) -> dict[str, int]:
        return {k: getattr(self, k) for k in ("watched", "loaded", "capped", "full", "failed", "expired")}


@dataclass
class Prewarmer:
    cache: KeyValueCache
    retrieval_cache: RetrievalCache
    config: ConfigProvider
    clock: Clock
    load: Loader
    generations: Generations
    telemetry: RetrievalTelemetry = field(default_factory=NoTelemetry)
    max_watched: int = MAX_WATCHED
    headroom: float = HEADROOM

    def __post_init__(self) -> None:
        self._watched: OrderedDict[tuple[UUID, UUID], _Watch] = OrderedDict()
        self._checked: OrderedDict[tuple[UUID, UUID, int], bool] = OrderedDict()
        self.stats = PrewarmStats()
        self.queue = PrefetchQueue(
            self._run,
            concurrency=PREWARM_CONCURRENCY,
            max_threads=self.max_watched,
            deadline_s=PREWARM_DEADLINE_S,
        )

    def __len__(self) -> int:
        return len(self._watched)

    def consider(
        self,
        config: SpaceConfig,
        *,
        profile_id: UUID,
        generation: int,
        reader_channel: str | None,
        confirmed_reader: bool,
        episodes: Sequence[EpisodeRef],
        now: datetime,
    ) -> bool:
        """Called by a read (any channel): starts a 72-hour watch when the customer's recontact pattern says
        voice and this read is on another channel. True when a watch started."""
        settings = config.settings
        if not (settings.prewarm_per_minute > 0 and confirmed_reader):
            return False
        if reader_channel == PREDICTED_CHANNEL or not _serves_channel(config, PREDICTED_CHANNEL):
            return False
        key = (config.space_id, profile_id)
        if key in self._watched:
            return False
        checked = (config.space_id, profile_id, generation)
        if checked in self._checked:
            return False
        self._checked[checked] = True
        while len(self._checked) > CHECKED:
            self._checked.popitem(last=False)
        pattern = recontact_pattern(episodes, now)
        if pattern is None or pattern.channel != PREDICTED_CHANNEL:
            return False
        self._watched[key] = _Watch(now + RECONTACT_WINDOW)
        while len(self._watched) > self.max_watched:
            self._watched.popitem(last=False)
        self.stats.watched += 1
        self.queue.submit(key, key)
        return True

    async def tick(self) -> int:
        """Reload the watched customers whose generation moved past what prewarm loaded; drop the expired
        and those of spaces that turned prewarm off. Returns how many loads it queued."""
        moment = self.clock.now()
        for key in [k for k, w in self._watched.items() if w.until <= moment or not self._on(k[0])]:
            del self._watched[key]
            self.stats.expired += 1
        keys = list(self._watched)
        queued = 0
        for start in range(0, len(keys), GET_MANY):
            chunk = keys[start : start + GET_MANY]
            try:
                current = await self.generations(chunk)
            except (ConnectionError, TimeoutError, OSError):
                return queued
            for key, generation in zip(chunk, current, strict=True):
                watch = self._watched.get(key)
                if watch is None or generation is None:
                    continue
                held = self.retrieval_cache.peek(*key)
                if (watch.loaded is not None and watch.loaded >= generation) and held is not None:
                    continue
                if held is not None and held.generation >= generation:
                    watch.loaded = held.generation
                    continue
                if self.queue.submit(key, key):
                    queued += 1
        return queued

    async def drain(self) -> None:
        await self.queue.drain()

    async def _run(self, key: tuple[UUID, UUID]) -> None:
        space_id, profile_id = key
        watch = self._watched.get(key)
        if watch is None:
            return
        if not self._on(space_id):
            self._watched.pop(key, None)
            return
        cache = self.retrieval_cache
        if cache.bytes_used > cache.max_bytes * self.headroom:
            self.stats.full += 1
            self.telemetry.retrieval_cache("prewarm_full", cache.bytes_used, len(cache))
            return
        if not await self._within_cap(space_id):
            self.stats.capped += 1
            self.telemetry.retrieval_cache("prewarm_capped", cache.bytes_used, len(cache))
            return
        try:
            loaded = await self.load(space_id, profile_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A prewarm never fails anything (L10): the call reads cold, as it would have.
            self.stats.failed += 1
            log.warning("prewarm load failed", exc_info=True)
            return
        if loaded is None:
            self._watched.pop(key, None)  # the customer or the space is gone
            return
        watch.loaded = loaded
        self.stats.loaded += 1
        self.telemetry.retrieval_cache("prewarm_loaded", cache.bytes_used, len(cache))

    def _on(self, space_id: UUID) -> bool:
        try:
            settings = self.config.space(space_id).settings
        except NotFound:
            return False
        return settings.prewarm_per_minute > 0

    async def _within_cap(self, space_id: UUID) -> bool:
        cap = self.config.space(space_id).settings.prewarm_per_minute
        minute = int(self.clock.now().timestamp() // 60)
        try:
            count = await self.cache.incr_window(cap_key(space_id, minute), 60)
        except (ConnectionError, TimeoutError, OSError):
            return False  # without the shared count, no process may assume it is under the cap
        return count <= cap

    def forget(self, space_id: UUID, profile_id: UUID) -> None:
        self._watched.pop((space_id, profile_id), None)


def _serves_channel(config: SpaceConfig, channel: str) -> bool:
    return any(s.channel == channel and s.audience is Audience.CUSTOMER_AGENT for s in config.sources)
