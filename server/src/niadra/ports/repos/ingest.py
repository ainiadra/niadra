"""Raw events, dedupe keys, event_handles, conversations, source heartbeats and file imports (5.2)."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.ingest.coverage import SourceDay
from niadra.domain.ingest.events import ConversationTouch, NewEvent
from niadra.domain.ingest.files import FileImport
from niadra.domain.ingest.intake import IntakeRow, NewIntake
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.records import EventRecord


class EventRepository(Protocol):
    """Owned by the `ingest` module; the reads below are used by sessions, extract, serve,
    objects, measure and privacy."""

    async def get_events(self, event_ids: Sequence[UUID]) -> list[EventRecord]: ...

    async def events_between(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        since: datetime,
        until: datetime,
    ) -> list[EventRecord]:
        """Events of one conversation or task in a time window, ordered by `occurred_at`."""
        ...

    async def events_for_handles(
        self, handle_ids: Sequence[UUID], since: datetime, limit: int, *, before_id: UUID | None = None
    ) -> list[EventRecord]:
        """The newest `limit` events of some handles through `event_handles`, returned by `occurred_at`;
        the hot layer's fallback (06, 6.4). `before_id` pages back from the smallest id already read."""
        ...

    async def system_events_for_handles(
        self, handle_ids: Sequence[UUID], since: datetime, limit: int
    ) -> list[EventRecord]:
        """The newest `limit` system events of some handles received since `since`, newest first: the
        rows history navigation shows for them."""
        ...

    async def recent_events(
        self, since: datetime, limit: int, *, after: UUID | None = None
    ) -> list[EventRecord]:
        """Events received since `since`, by id, after `after`; the hot layer rebuild re-adds them."""
        ...

    async def source_last_event_at(self, since: datetime) -> dict[UUID, datetime]:
        """Latest event per source among heartbeat windows since `since` (5.2 coverage); sources
        with no event in that span are absent."""
        ...

    async def coverage(self, since: datetime) -> dict[UUID, list[SourceDay]]:
        """Heartbeat counters of the pinned space summed per source and UTC day since `since`, days in
        order; sources without a heartbeat in the window are absent."""
        ...

    async def received_since(self, since: datetime) -> bool:
        """Whether any source of the pinned space had an event accepted in a heartbeat window since
        `since`: one statement that stops at the first one."""
        ...

    async def claim_keys(
        self, space_id: UUID, keys: Sequence[tuple[str, UUID]], received_at: datetime
    ) -> tuple[int, set[str]]:
        """Under the shared transaction lock of the space's dedupe rotation, in one round trip: the current
        generation (`space_state`), and the keys already seen in the current or the previous table, after
        inserting the others' `(idempotency_key, event_id)` into the current one (5.2)."""
        ...

    async def has_intake(self) -> bool:
        """Whether any batch of the space was acknowledged and not stored yet."""
        ...

    async def take_intake(self, limit: int) -> list[IntakeRow]:
        """Takes the space's intake lock for the transaction, then its oldest rows, in arrival order.
        Whoever applies them holds the lock until they are gone, so rows are stored in that order."""
        ...

    async def try_take_intake(self, limit: int) -> list[IntakeRow] | None:
        """`take_intake` without waiting for the lock: None while another transaction holds it."""
        ...

    async def drop_intake(self, intake_ids: Sequence[UUID]) -> None:
        """Deletes applied rows and their copies in the outbox."""
        ...

    async def fail_intake(self, intake_id: UUID) -> int:
        """Counts a failed application of the row; returns the count."""
        ...

    async def set_aside_intake(self, intake_id: UUID, at: datetime) -> None:
        """Keeps a row that failed every attempt out of the space's order (`failed_at`), sealed as it came,
        and deletes its copy in the outbox so the relay stops bringing it back. Never deletes the row."""
        ...

    async def failed_intake(
        self, limit: int, *, before: datetime | None = None, after: UUID | None = None
    ) -> list[IntakeRow]:
        """Rows set aside, oldest first; with `before`, only those received before it; with `after`, only
        those that came after that row (a cursor)."""
        ...

    async def count_failed_intake(self) -> int:
        """How many rows are set aside, every one, whatever a listing showed."""
        ...

    async def retry_intake(self, intake_ids: Sequence[UUID] | None) -> list[UUID]:
        """Puts rows set aside back in the space's order with no attempts counted, all of them when
        `intake_ids` is None; returns the ids put back."""
        ...

    async def rewrite_intake(self, intake_id: UUID, body_ct: bytes, dek_version: int) -> None:
        """Replaces a row's sealed body: an erasure took some of its events and kept the others."""
        ...

    async def release_keys(self, space_id: UUID, generation: int, keys: Sequence[str]) -> None:
        """Drops keys claimed in this transaction for items rejected afterwards, so a retry is not a
        duplicate."""
        ...

    async def rotate_dedupe(self, now: datetime, min_age: timedelta) -> int | None:
        """The space's rotation, as the system role: bumps the generation under the exclusive lock and
        truncates the retired table. None when the current generation is younger than `min_age`."""
        ...

    async def mark_content(self, event_ids: Sequence[UUID], marks: dict[str, Any]) -> None:
        """Merges `marks` into `content_meta`, e.g. the gate's `{"injection": true}` (18.4). The log stays
        append-only for content; only this metadata changes."""
        ...

    async def events_after(
        self, after_id: UUID | None, before_id: UUID, limit: int
    ) -> list[tuple[EventRecord, str | None]]:
        """Stored events with ids in `(after_id, before_id)`, oldest first, each with its raw payload key:
        the pages of the masking pass over what was stored before a rule existed (WP-A)."""
        ...

    async def rewrite_content(self, records: Sequence[EventRecord]) -> None:
        """Seals `text`, `fields` and the action's free text of stored events again, and merges
        `content_meta.masked` into their metadata, as the system role. Only the masking pass and the erasure
        cascade call it: the log is append-only for content, and a held value leaves it the way the erasure
        cascade removes an event; the cascade also leaves the company's feedback on an erased fact
        with ids and kinds only (8.4)."""
        ...

    async def masked_counts(self, *, conversation_id: str | None, task_id: str | None) -> dict[str, int]:
        """What was held back from one conversation's or task's events, by type, from `content_meta`."""
        ...

    async def masking_state(self) -> int | None:
        """The fingerprint of the masking rules the stored events were last masked with (`space_state`)."""
        ...

    async def set_masking_state(self, fingerprint: int) -> None: ...

    async def insert_events(self, events: Sequence[NewEvent]) -> None:
        """The events and their `event_handles` rows, content encrypted by the adapter, and what each names
        (`NewEvent.mentions`) beside it."""
        ...

    async def events_naming(
        self, tokens: Sequence[bytes], *, besides: Sequence[UUID], after: UUID | None, limit: int
    ) -> list[UUID]:
        """At most `limit` ids of events whose text names any of `tokens`, by `event_mentions` alone, but
        those kept under any of the handles in `besides`, after `after`, by id."""
        ...

    async def index_mentions(self, mentions: Sequence[tuple[UUID, frozenset[bytes]]]) -> None:
        """What these events name now: a row set again, or gone when they name nothing (system role)."""
        ...

    async def mentions_ready(self) -> bool:
        """Whether the events stored before `event_mentions` existed are in it (`space_state`)."""
        ...

    async def mark_mentions_ready(self) -> None: ...

    async def find_conversation(self, ids: Sequence[str]) -> str | None:
        """The stored conversation whose id or aliases contain any of `ids`."""
        ...

    async def upsert_conversation(self, space_id: UUID, touch: ConversationTouch) -> None: ...

    async def known_conversation(self, conversation_id: str) -> tuple[UUID, ...] | None:
        """The primary handle of a stored conversation (empty when it has none), or `None` when no
        conversation has that id. The row outlives the events that left for the cold archive, so a
        `forget` finds a conversation whose events are all archived (8.4)."""
        ...

    async def primary_handles(self, conversation_ids: Sequence[str]) -> dict[str, UUID]:
        """The primary handle of each stored conversation that has one, in one statement: whom the
        interactions of a turn record are about (24.3.2)."""
        ...

    async def conversation_names(self, scope: ErasureScope) -> list[str]:
        """The ids and aliases of the conversations `erase_for(scope)` takes: what other modules may
        still hold of them, a phone number or an e-mail often (8.4)."""
        ...

    async def bump_heartbeat(
        self,
        space_id: UUID,
        source_id: UUID,
        window_start: datetime,
        *,
        sent: int | None = None,
        received: int = 0,
        rejected: int = 0,
        unmapped: int = 0,
        last_event_at: datetime | None = None,
    ) -> None:
        """Per-source counters for coverage; `sent` comes from the SDK, the rest from the cell (5.2)."""
        ...

    async def raw_refs(self, source_id: UUID, *, limit: int) -> list[tuple[UUID, str]]:
        """Object keys of the source's latest raw payloads, one per payload, newest first, each with the
        newest event it produced. The bodies stay in object storage (5.2), so the caller reads them."""
        ...

    async def recent_messages(self, source_id: UUID, *, limit: int) -> list[EventRecord]:
        """The source's latest message events, newest first (22.4)."""
        ...

    async def archivable_events(
        self, *, since: datetime, until: datetime, after_id: UUID | None, limit: int
    ) -> list[EventRecord]:
        """`EventArchiveSource`: events received in `[since, until)` after `after_id`, oldest first."""
        ...

    async def purge_archived(self, *, since: datetime, until: datetime, limit: int) -> int:
        """Deletes up to `limit` events received in `[since, until)`, with their handle index rows,
        once the cold archive holds them (5.14.0); returns how many went."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): deletes up to `limit` events of the target, with their
        `event_handles`, then the target's conversations. Event rows carry their raw payload and media
        keys in `blob_keys`. Dedupe keys stay until their table rotates, so a provider resending the
        webhook cannot bring an erased event back."""
        ...

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        """`ExportSource` for `events` and `conversations` (22.3), decrypted, ordered by `change_seq`."""
        ...

    async def create_import(self, space_id: UUID, record: FileImport) -> None: ...

    async def get_import(self, import_id: UUID) -> FileImport | None: ...

    async def save_import(self, record: FileImport) -> None:
        """Status, progress, counters and errors of an existing import."""
        ...


class IntakeLog(Protocol):
    """Claims the intake's keys like `claim_keys`, stores its row with the keys found claimed already as
    `skip`, and the relay's copy of it in the outbox, as a transaction of its own in a single round trip: the
    only database write an acknowledgement waits for (5.2). Returns those keys."""

    async def accept(self, intake: NewIntake) -> set[str]: ...
