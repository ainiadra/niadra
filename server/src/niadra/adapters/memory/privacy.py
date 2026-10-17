"""In-memory `PrivacyRepository` for unit tests.

The in-memory unit of work does not know its space, so `record_feed` rows carry no space; everything
else takes the space explicitly, like the PostgreSQL adapter's filters.
"""

from collections.abc import Collection, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasedRow, ErasureRequest, ErasureStatus
from niadra.domain.privacy.export import ON_DEMAND, ExportRun, ExportStatus, TombstoneRecord
from niadra.domain.privacy.raw_index import RawObject


class InMemoryPrivacyRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("privacy")
        self._feed: list[dict[str, object]] = self._s.setdefault("feed", [])
        self._erasures: dict[tuple[UUID, UUID], ErasureRequest] = self._s.setdefault("erasures", {})
        self._tombstones: dict[UUID, list[TombstoneRecord]] = self._s.setdefault("tombstones", {})
        self._runs: dict[tuple[UUID, UUID], ExportRun] = self._s.setdefault("runs", {})
        # Stands in for the cell's `change_seq` sequence; tests move it to simulate other modules' writes.
        self._seq: dict[str, int] = self._s.setdefault("change_seq", {"value": 0})
        self._raw: dict[str, RawObject] = self._s.setdefault("raw_objects", {})
        self._raw_ready: set[UUID] = self._s.setdefault("raw_index_ready", set())

    async def record_feed(
        self, *, kind: str, profile_ids: Sequence[UUID], handle_ids: Sequence[UUID], privacy_version: int
    ) -> None:
        self._feed.append(
            {
                "id": uuid7(),
                "space_id": None,
                "kind": kind,
                "profile_ids": list(profile_ids),
                "handle_ids": list(handle_ids),
                "privacy_version": privacy_version,
                "created_at": datetime.now(UTC),  # PostgreSQL stamps it with now() too
            }
        )

    async def feed_since(self, since: datetime) -> list[dict[str, object]]:
        return [row for row in self._feed if row["created_at"] > since]  # type: ignore[operator]

    async def create_erasure(self, space_id: UUID, request: ErasureRequest) -> tuple[ErasureRequest, bool]:
        key = (space_id, request.request_id)
        if key in self._erasures:
            return self._erasures[key], False
        self._erasures[key] = request
        return request, True

    async def get_erasure(self, space_id: UUID, request_id: UUID) -> ErasureRequest | None:
        return self._erasures.get((space_id, request_id))

    async def save_erasure(self, space_id: UUID, request: ErasureRequest) -> None:
        self._erasures[(space_id, request.request_id)] = request

    async def list_erasures(
        self,
        space_id: UUID,
        *,
        status: ErasureStatus | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ErasureRequest]:
        found = [
            r
            for (s, _), r in self._erasures.items()
            if s == space_id
            and (status is None or r.status is status)
            and (before is None or (r.requested_at, r.request_id) < before)
        ]
        return sorted(found, key=lambda r: (r.requested_at, r.request_id), reverse=True)[:limit]

    async def add_tombstones(self, space_id: UUID, rows: Sequence[ErasedRow], at: datetime) -> None:
        bucket = self._tombstones.setdefault(space_id, [])
        for row in rows:
            bucket.append(TombstoneRecord(self._next_seq(), row.table, dict(row.key), at))

    async def tombstones_between(
        self, space_id: UUID, after_seq: int, upto_seq: int | None
    ) -> list[TombstoneRecord]:
        return [
            t
            for t in self._tombstones.get(space_id, [])
            if t.change_seq > after_seq and (upto_seq is None or t.change_seq <= upto_seq)
        ]

    async def current_change_seq(self) -> int:
        return self._seq["value"]

    async def runs_covering(self, space_id: UUID, change_seqs: Sequence[int]) -> list[UUID]:
        return [
            run.run_id
            for (space, _), run in sorted(self._runs.items())
            if space == space_id
            and run.status is ExportStatus.COMPLETED
            and run.schedule_id != ON_DEMAND
            and any(run.from_seq < seq <= run.to_seq for seq in change_seqs)
        ]

    async def create_export_run(self, space_id: UUID, run: ExportRun) -> None:
        self._runs[(space_id, run.run_id)] = run

    async def save_export_run(self, space_id: UUID, run: ExportRun) -> None:
        self._runs[(space_id, run.run_id)] = run

    async def get_export_run(self, space_id: UUID, run_id: UUID) -> ExportRun | None:
        return self._runs.get((space_id, run_id))

    async def export_runs(
        self, space_id: UUID, *, schedule_id: str | None, before_id: UUID | None, limit: int
    ) -> list[ExportRun]:
        runs = [
            run
            for (space, run_id), run in self._runs.items()
            if space == space_id
            and (schedule_id is None or run.schedule_id == schedule_id)
            and (before_id is None or run_id < before_id)
        ]
        return sorted(runs, key=lambda r: r.run_id, reverse=True)[:limit]

    async def stored_packages(
        self,
        space_id: UUID,
        *,
        profile_id: UUID | None,
        created_before: datetime | None,
        limit: int,
    ) -> list[ExportRun]:
        found = [
            run
            for (space, _), run in sorted(self._runs.items())
            if space == space_id
            and run.schedule_id == ON_DEMAND
            and run.manifest is not None
            and not run.manifest.get("deleted_at")
            and (profile_id is None or run.manifest.get("profile_id") == str(profile_id))
            and (created_before is None or run.started_at < created_before)
        ]
        return found[:limit]

    async def purge_feed(self, space_id: UUID, before: datetime) -> int:  # noqa: ARG002 (rows carry no space here)
        kept = [row for row in self._feed if row["created_at"] >= before]  # type: ignore[operator]
        purged = len(self._feed) - len(kept)
        self._feed[:] = kept
        return purged

    async def purge_export_runs(self, space_id: UUID, before: datetime) -> int:
        doomed = [key for key, run in self._runs.items() if key[0] == space_id and run.started_at < before]
        for key in doomed:
            del self._runs[key]
        return len(doomed)

    async def purge_tombstones(self, space_id: UUID, before: datetime) -> int:
        bucket = self._tombstones.get(space_id, [])
        kept = [t for t in bucket if t.deleted_at >= before]
        self._tombstones[space_id] = kept
        return len(bucket) - len(kept)

    async def index_raw(self, space_id: UUID, objects: Sequence[RawObject]) -> None:  # noqa: ARG002
        index_raw_in(self._raw, objects)

    async def raw_named(self, space_id: UUID, tokens: Collection[bytes]) -> list[RawObject]:
        wanted = set(tokens)
        return sorted((r for r in self._raw_of(space_id) if r.tokens & wanted), key=lambda r: r.key)

    async def retoken_raw(self, space_id: UUID, key: str, tokens: Collection[bytes]) -> None:  # noqa: ARG002
        if (row := self._raw.get(key)) is not None:
            self._raw[key] = replace(row, tokens=frozenset(tokens))

    async def raw_written_before(
        self, space_id: UUID, before: datetime, *, keep: Collection[str], limit: int
    ) -> list[str]:
        rows = self._raw_of(space_id)
        due = sorted((r for r in rows if r.written_at < before and r.owner not in keep), key=_oldest)
        return [r.key for r in due[:limit]]

    async def unindex_raw(self, space_id: UUID, keys: Collection[str]) -> None:  # noqa: ARG002
        for key in keys:
            self._raw.pop(key, None)

    async def unindex_raw_owned(self, space_id: UUID, owners: Collection[str]) -> None:
        for row in [r for r in self._raw_of(space_id) if r.owner in owners]:
            del self._raw[row.key]

    def _raw_of(self, space_id: UUID) -> list[RawObject]:
        """Every raw key names its space as a segment (`<kind>/<space>/...`)."""
        return [r for r in self._raw.values() if str(space_id) in r.key.split("/")]

    async def raw_index_ready(self, space_id: UUID) -> bool:
        return space_id in self._raw_ready

    async def mark_raw_index_ready(self, space_id: UUID) -> None:
        self._raw_ready.add(space_id)

    def _next_seq(self) -> int:
        self._seq["value"] += 1
        return self._seq["value"]


def _oldest(row: RawObject) -> tuple[datetime, str]:
    return row.written_at, row.key


def index_raw_in(index: dict[str, RawObject], objects: Sequence[RawObject]) -> None:
    """A key already indexed keeps its row, as `ON CONFLICT DO NOTHING` does."""
    for row in objects:
        index.setdefault(row.key, row)
