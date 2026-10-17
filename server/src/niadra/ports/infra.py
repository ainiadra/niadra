"""Ports for everything outside the process that is not a cell or space database transaction.

Each port names a capability, not a product: the queue is RabbitMQ today, the cache is
Redis-compatible, object storage speaks the S3 protocol, and keys come from a vault that
can be a local key file or HashiCorp Vault. Swapping one is an adapter change.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from niadra.config.models import SpaceConfig
from niadra.domain.decide import Triage
from niadra.domain.ingest.media_text import ReadText
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.spaces.directory import DbStatus
from niadra.domain.tasks import Task
from niadra.domain.vocabulary import Verification


class Clock(Protocol):
    def now(self) -> datetime: ...


class KeyVault(Protocol):
    """Envelope encryption with one data key per space (5.14.1).

    `aad` binds a ciphertext to where it lives (table, row, column), never to the profile,
    so a merge or unmerge does not force re-encryption.
    """

    async def encrypt(self, space_id: UUID, plaintext: bytes, aad: bytes) -> tuple[bytes, int]: ...

    async def decrypt(self, space_id: UUID, ciphertext: bytes, aad: bytes, dek_version: int) -> bytes: ...

    async def hmac(self, space_id: UUID, purpose: str, value: bytes) -> bytes:
        """Keyed hash for lookups over short values (handles, fact values); plain hashes are reversible."""
        ...

    async def key_versions(self, space_id: UUID) -> list[int]:
        """The versions of the space's data keys that exist, for a deletion receipt."""
        ...

    async def destroy_space(self, space_id: UUID) -> list[int]:
        """Crypto-shredding when a space is deleted: the wrapped data keys go, so every copy of its
        ciphertext (dumps, instance snapshots) becomes unreadable. Returns the versions destroyed."""
        ...


class SecretStore(Protocol):
    async def get(self, ref: str) -> str: ...

    async def put(self, ref: str, value: str) -> int: ...

    async def destroy_space(self, space_id: UUID) -> int:
        """Deletes every version of every secret of a deleted space; returns how many rows went."""
        ...


class TaskBus(Protocol):
    """Publishes tasks after the outbox commit and delivers them to workers (5.5)."""

    async def publish(self, tasks: Sequence[Task]) -> None: ...

    def consume(self, queues: Sequence[str], prefetch: int) -> AsyncIterator["Delivery"]: ...


class Doorbell(Protocol):
    """Tells the loops process when a space's database has work due (a delayed task, an outbox row the
    publisher could not send), so its relay sleeps while the space is quiet instead of polling it (06, 6.3).
    Best effort: a ring that fails leaves the work to the relay's next pass."""

    async def ring(self, space_id: UUID, at: datetime) -> None: ...

    async def due(self, now: datetime) -> list[UUID]:
        """Spaces whose earliest ring is at or before `now`, taken off the doorbell."""
        ...


class Delivery(Protocol):
    task: Task
    attempt: int

    async def ack(self) -> None: ...

    async def retry_later(self, reason: str) -> None:
        """Negative ack: the broker redelivers, and after the limit it dead-letters."""
        ...


@dataclass(frozen=True, slots=True)
class HotTurn:
    event_id: UUID
    occurred_at_ms: int
    envelope_ct: bytes


class CacheUnavailable(ConnectionError):
    """The cache or the hot layer failed a command: down, full (`noeviction`) or timing out. A
    `ConnectionError`, so every read-path fallback that catches those fails open on it too."""


@dataclass(frozen=True, slots=True)
class HotRead:
    turns: list[HotTurn]
    whole: bool = True
    """False while the store's marker is not sealed: it restarted or was flushed and the turns written
    before that may be missing, until the rebuild from the event log seals it again (06, 3)."""
    by_handle: dict[UUID, list[HotTurn]] = field(default_factory=dict)
    """Each handle's own turns, oldest first: a turn of two handles is in both lists, once in `turns`."""


class HotLayerState(StrEnum):
    WHOLE = "whole"
    REBUILDING = "rebuilding"
    LOST = "lost"
    """No marker: the store came back empty (a restart without persistence) or was never sealed."""


class HotLayer(Protocol):
    """Recent turns per handle in a Redis-compatible store (5.4, 06 6.4).

    A marker key says whether the store still holds everything written to it. The store keeps nothing
    across a restart, so a missing marker means the turns may be gone; one rebuilder claims it, extends
    the claim while it re-adds turns from the event log, and seals it when done (06, 3)."""

    async def add(self, space_id: UUID, handle_id: UUID, turns: Sequence[HotTurn], cap_ms: int) -> None: ...

    async def read(
        self, space_id: UUID, handle_ids: Sequence[UUID], since_ms: int, *, newest: int | None = None
    ) -> HotRead:
        """Turns since `since_ms`, oldest first; with `newest`, only that many of the latest per handle.
        The marker is read in the same round trip."""
        ...

    async def state(self) -> HotLayerState: ...

    async def claim_rebuild(self, token: str, lease_ms: int) -> bool:
        """Only when the marker is missing: it becomes this rebuild's, expiring after `lease_ms`."""
        ...

    async def extend_rebuild(self, token: str, lease_ms: int) -> bool:
        """False once the marker is no longer this rebuild's: the store restarted or the lease lapsed."""
        ...

    async def seal(self, token: str) -> bool:
        """The marker becomes whole, with no expiry, only if it is still this rebuild's."""
        ...

    async def shorten(self, space_id: UUID, handle_id: UUID, covered_until_ms: int, keep_ms: int) -> None:
        """Lower the key's TTL to `keep_ms` after the last covered turn, if nothing newer is uncovered."""
        ...

    async def purge(self, space_id: UUID, handle_ids: Sequence[UUID]) -> None: ...

    async def retract(self, space_id: UUID, handle_id: UUID, event_id: UUID, occurred_at_ms: int) -> None:
        """Removes one turn from a handle's set: one the acknowledgement wrote under a handle or a
        conversation that storing the event resolved otherwise."""
        ...

    async def hold_proofs(self, held: Sequence[tuple[str, UUID, Verification]], ttl_ms: int) -> None:
        """What acknowledged turns prove for their conversation until they are stored (5.2): one field per
        turn, under the conversation's keyed name (`app.pending_proofs`), the name expiring after `ttl_ms`."""
        ...

    async def held_proof(self, name: str) -> Verification | None:
        """The highest level held under the name; None when nothing is."""
        ...

    async def release_proofs(self, held: Sequence[tuple[str, UUID]]) -> None:
        """Drops the fields of turns that are now stored."""
        ...


class KeyValueCache(Protocol):
    """Small derived state: key cache, handle -> profile map, proven level, T0 packs, generations."""

    async def get(self, key: str) -> bytes | None: ...

    async def get_many(self, keys: Sequence[str]) -> list[bytes | None]: ...

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None: ...

    async def delete(self, keys: Sequence[str]) -> None: ...

    async def put_and_get_many(
        self, key: str, value: bytes | None, ttl_s: int, keys: Sequence[str]
    ) -> tuple[bool, list[bytes | None]]:
        """In one round trip: stores `value` at `key` (or, with None, only renews its expiry) and reads
        `keys`. The flag is False when an expiry found no key to renew."""
        ...

    async def put_if_generation(
        self, pack_key: str, gen_key: str, value: bytes, generation: int, ttl_s: int
    ) -> bool:
        """Atomically store a pack only if `generation` >= the stored generation and the generation exists."""
        ...

    async def bump_generation(self, gen_key: str, generation: int, ttl_s: int) -> None: ...

    async def incr_window(self, key: str, window_s: int) -> int:
        """Fixed-window counter for rate limits."""
        ...

    async def purge_profile(self, space_id: UUID, profile_id: UUID) -> int:
        """Deletes every key of one profile (`...:<space_id>:{<profile_id>}...`): its packs, generation, pins,
        watermarks and wanted variants, whatever the cache epoch. For an erasure; returns how many went."""
        ...

    async def purge_space(self, space_id: UUID) -> int:
        """Deletes every key that names the space (`...:<space_id>:...`): packs, generations, pins, the
        hot layer. For a deleted space; returns how many keys went."""
        ...


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    url: str
    headers: Mapping[str, str]


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    async def put_stream(
        self, key: str, chunks: AsyncIterator[bytes], content_type: str = "application/octet-stream"
    ) -> int:
        """Uploads without holding the whole object in memory (a database dump); returns the size."""
        ...

    async def put_if_absent(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> bool:
        """A conditional write (`If-None-Match: *`): False, having written nothing, when the key exists."""
        ...

    async def copy(self, source: str, target: str) -> None:
        """A copy inside the store, without the bytes passing through the process; NotFound if `source`
        does not exist."""
        ...

    async def get(self, key: str) -> bytes: ...

    def get_stream(self, key: str) -> AsyncIterator[bytes]:
        """The object in chunks; raises NotFound when it does not exist."""
        ...

    async def delete(self, key: str) -> None:
        """One object by its exact key; nothing happens when it does not exist."""
        ...

    async def delete_prefix(self, prefix: str) -> int: ...

    async def list_keys(self, prefix: str) -> list[str]:
        """Every key under the prefix, in key order."""
        ...

    async def list_keys_before(self, prefix: str, before: datetime) -> list[str]:
        """The keys under the prefix last written before `before`, in key order: what a retention by age
        reaches when the key names no time."""
        ...

    async def presign_put(
        self,
        key: str,
        content_type: str,
        expires_s: int,
        *,
        size_bytes: int | None = None,
        sha256_hex: str | None = None,
    ) -> PresignedUpload:
        """A time-limited upload link. When size and digest are given the store refuses other bytes;
        the uploader must send `headers` exactly as returned."""
        ...

    async def presign_get(self, key: str, expires_s: int) -> str:
        """A time-limited download link; whoever holds it reads the object without credentials."""
        ...


class AnchorStore(Protocol):
    """Where the daily roots of the receipt chains are kept (5.13): write-once storage that nobody, the
    cell included, can delete or overwrite before the retention ends (Object Lock, compliance mode)."""

    @property
    def locked(self) -> bool:
        """Whether the store itself enforces the retention; False for the cell's own bucket."""
        ...

    async def put_anchor(self, key: str, data: bytes, retain_until: datetime) -> None: ...

    async def get_anchor(self, key: str) -> bytes:
        """Raises `NotFound` when the object is missing."""
        ...


@dataclass(frozen=True, slots=True)
class LlmUsage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    cost_micros: int = 0


@dataclass(frozen=True, slots=True)
class LlmResult:
    content: str
    model: str
    provider: str
    usage: LlmUsage


Reasoning = Literal["none", "minimal", "low", "medium", "high"]
"""How much the generation model thinks before it answers; its thinking is billed as output tokens."""


class LlmClient(Protocol):
    """Structured generation by the one generation model (the extraction model). Only the workers call it,
    never a synchronous path (L3)."""

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: Mapping[str, Any],
        reasoning: Reasoning,
        max_output_tokens: int,
        space_id: UUID,
        purpose: str,
    ) -> LlmResult: ...


@dataclass(frozen=True, slots=True)
class Label:
    label: str
    probability: float
    model_version: str


@dataclass(frozen=True, slots=True)
class PiiSpan:
    start: int
    end: int
    kind: str


class MediaTextReader(Protocol):
    """Local text extraction from media (10, D.1): nothing leaves the cell for it."""

    async def document_text(self, data: bytes, mime: str, first_page: int = 0) -> ReadText | None:
        """The text of a document whose type was sniffed from its bytes: a PDF (text layer, OCR of the
        pages without one), an image (OCR) or an Office file, a window of pages, slides or sheets from
        `first_page`, with where the next starts. Empty text when the file cannot be read; None when this
        cell has no reader for the type."""
        ...


class ModelsClient(Protocol):
    """The small in-cell models of `niadra-models` (5.6): embeddings and PII spans. Neither is a language
    model."""

    async def embed(self, texts: Sequence[str]) -> tuple[list[list[float]], str]: ...

    async def embed_query(self, text: str) -> tuple[list[float], str]:
        """One short text on the read path, under a deadline of its own: the caller ranks by keywords when
        the encoder is slow, so waiting for it would only make the answer late."""
        ...

    async def pii(self, texts: Sequence[str]) -> list[list[PiiSpan]]: ...


class DecisionModel(Protocol):
    """The decision model answering the model tasks of the decisions port: one label per
    text, from the task's closed labels, with its probability, or None for a text it did not answer in
    time. The texts arrive masked; the port records the task's conservative label for what is missing."""

    async def decide(self, space_id: UUID, task: str, texts: Sequence[str]) -> list[Label | None]: ...

    async def triage(
        self, space_id: UUID, transcripts: Sequence[str], categories: Sequence[str]
    ) -> Triage | None:
        """One call about a batch of one customer's closed sessions, their masked transcripts in order:
        each one answered on the spot or not and its category, any injection, any special category; None
        when the model did not answer."""
        ...


class QueryEncoder(Protocol):
    """What the semantic channel of the read path needs: the models client. The version it returns is
    compared with the stored vectors' before any cosine."""

    async def embed_query(self, text: str) -> tuple[list[float], str]: ...


class RetrievalTelemetry(Protocol):
    """The read path's retrieval signals: each channel's outcome and time, and the per-profile cache."""

    def channel(self, channel: str, status: str, seconds: float) -> None: ...

    def retrieval_cache(self, event: str, bytes_used: int, entries: int) -> None:
        """`event` is hit, miss, stale, loaded, evicted or too_large, or prewarm_loaded, prewarm_capped or
        prewarm_full for the predictive prewarm (13, 6.4); the gauges follow every event."""
        ...


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


class EgressClient(Protocol):
    """All outbound HTTP to customer endpoints: https only, private and metadata ranges blocked (7.5)."""

    async def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float
    ) -> HttpResponse: ...


class ConfigProvider(Protocol):
    def space(self, space_id: UUID) -> SpaceConfig:
        """The active configuration of a space; raises NotFound for unknown spaces."""
        ...

    def spaces(self) -> Sequence[SpaceConfig]: ...

    def removed(self) -> Mapping[UUID, datetime]:
        """Spaces the control plane deleted, with when: the snapshot names them so a cell can tell a
        deleted space from one that is only missing (a truncated snapshot must never delete)."""
        ...

    def moved(self) -> Mapping[UUID, str]:
        """Spaces that moved from this cell to another, with the cell they went to."""
        ...

    async def refresh_on_miss(self) -> bool:
        """For a credential that names a space or a source this copy does not know, or a feature it does not
        have on: reads the cell's copy now, at most once a second. True when a read that began after this call
        finished; False when the rate limit left the copy as it was."""
        ...


class SpaceDatabases(Protocol):
    """Where each space's database stands, as this process last read the cell's directory."""

    async def require_serving(self, space_id: UUID) -> None:
        """Raises `Unavailable` with a retry delay while the space's database is being created,
        restored or deleted, and for a space the directory does not know yet."""
        ...

    async def status(self, space_id: UUID) -> DbStatus | None:
        """None for a space the directory does not know, after one fresh read of the directory."""
        ...

    def active_spaces(self) -> list[UUID]:
        """Spaces this process has an open connection pool for: the ones it served recently."""
        ...

    def serving_spaces(self) -> list[UUID]:
        """Spaces whose database serves, as this process last read the directory."""
        ...

    async def refresh(self) -> None:
        """Reads the directory again now, instead of at the next tick."""
        ...

    async def dispose(self, space_id: UUID) -> None:
        """Closes this process's connections to the space's database (before a drop or a rename)."""
        ...


TaskHandler = Callable[["Task"], Awaitable[None]]


class ReceiptSink(Protocol):
    """Where read processes send receipts; durable and outside the cache (5.11)."""

    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        """True when the broker confirmed within the timeout; otherwise the caller buffers."""
        ...

    async def mark(self, mark: ProducerMark) -> None: ...


class ReceiptBatch(Protocol):
    drafts: list[ReceiptDraft]
    marks: list[ProducerMark]

    async def ack(self) -> None: ...

    async def retry_later(self) -> None: ...


class ReceiptSource(Protocol):
    def batches(self, max_items: int) -> AsyncIterator["ReceiptBatch"]: ...


class Telemetry(Protocol):
    """Operational signals the use cases raise; the adapter turns them into metrics (5.20)."""

    def receipts_lost(self, count: int) -> None: ...

    def llm_spent(self, purpose: str, cost_micros: int) -> None: ...

    def llm_budget_exhausted(self, purpose: str) -> None: ...

    def memory_ready(self, seconds: float) -> None:
        """From a session's end to its memory reconciled: the one-minute target."""
        ...

    def object_event_applied(self, seconds: float) -> None:
        """From an action's or system event's arrival to its effect on objects and open items."""
        ...

    def measurement_ready(self, seconds: float) -> None:
        """From a session's end to its first context-use measurement: the one-minute target."""
        ...


class MetricsReader(Protocol):
    """The cell's own metrics store (Prometheus), read-only, for the Console's reports."""

    async def scalars(self, queries: Mapping[str, str]) -> dict[str, float | None]:
        """Each instant query's single value by name: None when it has no data. Raises when the store does
        not answer."""
        ...


@dataclass(frozen=True, slots=True)
class DirectoryState:
    """What the reconciler saw in one pass, for the dashboard and the alerts."""

    by_status: Mapping[str, int]
    drift: int
    orphans: int
    failures: Mapping[UUID, int]
    pending_receipts: int


class SpaceTelemetry(Protocol):
    """Signals of the space databases: the directory, the per-space loops and the outbox lag."""

    def directory(self, state: DirectoryState) -> None: ...

    def loop_interval(self, space_id: UUID, loop: str, seconds: float) -> None: ...

    def outbox_lag(self, space_id: UUID, seconds: float) -> None: ...

    def forget_space(self, space_id: UUID) -> None: ...


class NoTelemetry:
    """For tests and tools that run without a metrics endpoint."""

    def receipts_lost(self, count: int) -> None:  # noqa: ARG002
        return None

    def llm_spent(self, purpose: str, cost_micros: int) -> None:  # noqa: ARG002
        return None

    def llm_budget_exhausted(self, purpose: str) -> None:  # noqa: ARG002
        return None

    def memory_ready(self, seconds: float) -> None:  # noqa: ARG002
        return None

    def object_event_applied(self, seconds: float) -> None:  # noqa: ARG002
        return None

    def measurement_ready(self, seconds: float) -> None:  # noqa: ARG002
        return None

    def channel(self, channel: str, status: str, seconds: float) -> None:  # noqa: ARG002
        return None

    def retrieval_cache(self, event: str, bytes_used: int, entries: int) -> None:  # noqa: ARG002
        return None

    def directory(self, state: DirectoryState) -> None:  # noqa: ARG002
        return None

    def loop_interval(self, space_id: UUID, loop: str, seconds: float) -> None:  # noqa: ARG002
        return None

    def outbox_lag(self, space_id: UUID, seconds: float) -> None:  # noqa: ARG002
        return None

    def forget_space(self, space_id: UUID) -> None:  # noqa: ARG002
        return None
