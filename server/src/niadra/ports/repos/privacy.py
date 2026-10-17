"""Erasure requests, the privacy feed, export runs, tombstones and the erasure cascade (5.14, 22.3).

Besides its own repository, this file names the two roles the owners of personal data play for privacy:
each owner's repository port has `erase_for` and, for exported tables, `export_rows`, with these contracts.
"""

from collections.abc import Collection, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.config.models import ExportSchedule
from niadra.domain.privacy.erasure import ErasedRow, ErasureRequest, ErasureScope, ErasureStatus
from niadra.domain.privacy.export import ExportRun, TombstoneRecord
from niadra.domain.privacy.raw_index import RawObject


class PrivacyRepository(Protocol):
    """Owned by the `privacy` module."""

    async def record_feed(
        self, *, kind: str, profile_ids: Sequence[UUID], handle_ids: Sequence[UUID], privacy_version: int
    ) -> None:
        """Adds a privacy-feed row in the caller's transaction; identity writes it on merge, unmerge and
        retraction, privacy on erasure, edge on revocation (06, 3)."""
        ...

    async def feed_since(self, since: datetime) -> list[dict[str, object]]:
        """Rows created after `since` across the cell; read by every read process every 2 s."""
        ...

    async def create_erasure(self, space_id: UUID, request: ErasureRequest) -> tuple[ErasureRequest, bool]:
        """Inserts the request unless its id exists; returns the stored one and whether it is new."""
        ...

    async def get_erasure(self, space_id: UUID, request_id: UUID) -> ErasureRequest | None: ...

    async def save_erasure(self, space_id: UUID, request: ErasureRequest) -> None: ...

    async def list_erasures(
        self,
        space_id: UUID,
        *,
        status: ErasureStatus | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ErasureRequest]:
        """Newest first by (`requested_at`, `request_id`); request ids come from the Idempotency-Key and
        carry no time, so the keyset is the pair of the last row of the previous page."""
        ...

    async def add_tombstones(self, space_id: UUID, rows: Sequence[ErasedRow], at: datetime) -> None: ...

    async def tombstones_between(
        self, space_id: UUID, after_seq: int, upto_seq: int | None
    ) -> list[TombstoneRecord]:
        """Ordered by `change_seq`; `upto_seq=None` reads to the end."""
        ...

    async def current_change_seq(self) -> int:
        """The last value handed out by the cell's `change_seq` sequence."""
        ...

    async def runs_covering(self, space_id: UUID, change_seqs: Sequence[int]) -> list[UUID]:
        """Completed continuous export runs whose range holds one of the sequences."""
        ...

    async def create_export_run(self, space_id: UUID, run: ExportRun) -> None: ...

    async def save_export_run(self, space_id: UUID, run: ExportRun) -> None: ...

    async def get_export_run(self, space_id: UUID, run_id: UUID) -> ExportRun | None: ...

    async def export_runs(
        self, space_id: UUID, *, schedule_id: str | None, before_id: UUID | None, limit: int
    ) -> list[ExportRun]:
        """Newest first by `run_id`."""
        ...

    async def stored_packages(
        self,
        space_id: UUID,
        *,
        profile_id: UUID | None,
        created_before: datetime | None,
        limit: int,
    ) -> list[ExportRun]:
        """On-demand packages still in storage (no `deleted_at` in the manifest): of one profile, or
        created before an instant."""
        ...

    async def purge_feed(self, space_id: UUID, before: datetime) -> int: ...

    async def purge_export_runs(self, space_id: UUID, before: datetime) -> int: ...

    async def purge_tombstones(self, space_id: UUID, before: datetime) -> int: ...

    # The index of raw content (`domain.privacy.raw_index`).

    async def index_raw(self, space_id: UUID, objects: Sequence[RawObject]) -> None:
        """Indexes raw content as it is written; a key already indexed keeps its row."""
        ...

    async def raw_named(self, space_id: UUID, tokens: Collection[bytes]) -> list[RawObject]:
        """The rows of the payloads that name any of `tokens`, by the index alone, by key."""
        ...

    async def retoken_raw(self, space_id: UUID, key: str, tokens: Collection[bytes]) -> None:
        """What a payload names, once an erasure redacted someone out of it."""
        ...

    async def raw_written_before(
        self, space_id: UUID, before: datetime, *, keep: Collection[str], limit: int
    ) -> list[str]:
        """The keys of at most `limit` objects written before `before`, oldest first, but those whose owner is
        in `keep` (a handle under a legal hold)."""
        ...

    async def unindex_raw(self, space_id: UUID, keys: Collection[str]) -> None:
        """Rows of objects deleted from storage."""
        ...

    async def unindex_raw_owned(self, space_id: UUID, owners: Collection[str]) -> None:
        """Rows of the objects kept under these handles, once an erasure deleted their prefixes."""
        ...

    async def raw_index_ready(self, space_id: UUID) -> bool:
        """Whether what was written before the index existed is in it."""
        ...

    async def mark_raw_index_ready(self, space_id: UUID) -> None: ...


class ErasureParticipant(Protocol):
    """A step of the erasure cascade (8.4), implemented by the module that owns the rows.

    Erases at most `limit` rows for the scope, in the caller's short transaction, and returns them;
    fewer than `limit` means nothing is left. Must be idempotent: rows already erased are not returned.
    """

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]: ...


class ExportSource(Protocol):
    """From each owner of an exported table (22.3).

    Rows of `table` with `after_seq < change_seq <= upto_seq`, ordered by `change_seq`, decrypted, with a
    `change_seq` column. A row whose content was erased or whose key was destroyed comes as its key plus
    `erased: True`, and is listed in the manifest instead of exported.
    """

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]: ...


class ExportSink(Protocol):
    """Where the export writes its files; nothing is ever read back."""

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...


class ExportDestinations(Protocol):
    """Object storage in the customer's bucket, opened with the write-only credential from the vault.

    A context manager because the client holds connections that must be closed when the run ends.
    """

    def open(self, schedule: ExportSchedule, credential: str) -> AbstractAsyncContextManager[ExportSink]: ...

    async def check_write_only(self, schedule: ExportSchedule, credential: str) -> None:
        """Raises `InvalidInput` for a credential that can read or list the destination."""
        ...
