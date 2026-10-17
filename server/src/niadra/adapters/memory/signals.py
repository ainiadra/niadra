"""In-memory `SignalRepository` for unit tests."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from niadra.adapters.memory.mentions import index_in, mentions_of
from niadra.adapters.memory.privacy import index_raw_in
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.privacy.mentions import MentionKey, MentionStore, signals_key
from niadra.domain.privacy.raw_index import RawObject
from niadra.domain.signals.daily import Prior
from niadra.domain.signals.records import (
    SELF,
    BeneficiaryRow,
    DueSnapshots,
    PendingRow,
    Segment,
    SignalRow,
    SignalWrite,
    Snapshot,
    SuppressionRow,
    recheck_due,
)
from niadra.ports.infra import CacheUnavailable

MAX_PENDING = 500


class InMemorySignalRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("signals")
        self._raw: dict[str, RawObject] = store.module("privacy").setdefault("raw_objects", {})
        self.rows: dict[tuple[UUID, UUID], SignalRow] = self._s.setdefault("rows", {})
        self.decayed: dict[tuple[UUID, UUID], datetime] = self._s.setdefault("decayed", {})
        self.beneficiaries: dict[tuple[UUID, UUID], BeneficiaryRow] = self._s.setdefault("beneficiaries", {})
        self.pending: dict[str, PendingRow] = self._s.setdefault("pending", {})
        self.statements: list[str] = self._s.setdefault("statements", [])
        self._mentions = mentions_of(store)
        """What each call would run as one statement, for the budget tests."""
        self.suppressed: dict[tuple[UUID, UUID, bytes], SuppressionRow] = self._s.setdefault("suppressed", {})
        self.sessions: dict[str, str] = self._s.setdefault("sessions", {})
        self.segment_rows: dict[UUID, Segment] = self._s.setdefault("segments", {})
        self.prior_rows: dict[str, list[Prior]] = self._s.setdefault("priors", {})
        """A conversation's digest by its id, as a conversation erasure computes it."""

    async def add_pending(self, rows: Sequence[PendingRow]) -> None:
        self.statements.append("add_pending")
        for r in rows:
            self.pending.setdefault(r.entry_key, r)
        named = [(MentionKey(MentionStore.INTAKE, r.entry_key), r.named) for r in rows]
        index_in(self._mentions["rows"], named, replace=False)

    async def intake_page(self, after: str | None, limit: int) -> list[PendingRow]:
        keys = sorted(k for k in self.pending if after is None or k > after)
        return [self.pending[k] for k in keys[:limit]]

    async def pending_by_keys(self, entry_keys: Sequence[str]) -> list[PendingRow]:
        return [self.pending[k] for k in entry_keys if k in self.pending]

    async def rewrite_pending(self, rows: Sequence[PendingRow]) -> None:
        for r in rows:
            if r.entry_key in self.pending:
                self.pending[r.entry_key] = replace(
                    self.pending[r.entry_key], body_ct=r.body_ct, dek_version=r.dek_version
                )

    async def due_snapshots(
        self, now: datetime, *, window: timedelta, quiet: timedelta, limit: int
    ) -> DueSnapshots:
        self.statements.append("due_snapshots")
        by_handle: dict[UUID | None, list[datetime]] = {}
        for r in self.pending.values():
            if r.handle_id is not None or recheck_due(r, now):
                by_handle.setdefault(r.handle_id, []).append(r.received_at)
        ready = [
            (min(t), str(h), h)
            for h, t in by_handle.items()
            if min(t) <= now - window or max(t) <= now - quiet
        ]
        due = [h for _, _, h in sorted(ready)][:limit]
        snapshots = {h: self._snapshot(h) for h in due if h is not None}
        return DueSnapshots(snapshots, None in due)

    async def unresolved(self, now: datetime, limit: int) -> list[PendingRow]:
        found = sorted(
            (r for r in self.pending.values() if r.handle_id is None and recheck_due(r, now)),
            key=lambda r: (r.received_at, r.entry_key),
        )
        return found[:limit]

    async def resolve(self, found: Mapping[str, UUID]) -> None:
        for key, handle in found.items():
            if key in self.pending:
                self.pending[key] = replace(self.pending[key], handle_id=handle)

    async def checked(self, entry_keys: Sequence[str], at: datetime) -> None:
        for key in entry_keys:
            if key in self.pending and self.pending[key].handle_id is None:
                self.pending[key] = replace(self.pending[key], checked_at=at)

    async def drop_unattributed(self, sessions: Sequence[str]) -> list[ErasedRow]:
        wanted = set(sessions)
        keys = [k for k, r in self.pending.items() if r.handle_id is None and r.session in wanted]
        for k in keys:
            del self.pending[k]
        return [ErasedRow("signal_intake", {"entry_key": k}) for k in keys]

    async def put_segment(self, segment: Segment, at: datetime) -> None:  # noqa: ARG002
        self.statements.append("put_segment")
        for key, found in list(self.segment_rows.items()):
            if (found.kind, found.starts_at) == (segment.kind, segment.starts_at):
                del self.segment_rows[key]
        self.segment_rows[segment.segment_id] = segment

    async def segments(self, kind: str, since: datetime) -> list[Segment]:
        found = [s for s in self.segment_rows.values() if s.kind == kind and s.starts_at >= since]
        return sorted(found, key=lambda s: s.starts_at)

    async def rewrite_segment(self, segment: Segment, at: datetime) -> None:  # noqa: ARG002
        self.segment_rows[segment.segment_id] = segment

    async def expire_segments(self, now: datetime) -> list[str]:
        gone = [s for s in self.segment_rows.values() if s.expires_at <= now]
        for s in gone:
            del self.segment_rows[s.segment_id]
        return [s.object_key for s in gone]

    async def replace_priors(self, priors: Sequence[Prior], at: datetime) -> None:  # noqa: ARG002
        self.statements.append("replace_priors")
        self.prior_rows.clear()
        for prior in priors:
            self.prior_rows.setdefault(prior.segment, []).append(prior)

    async def priors(self) -> list[Prior]:
        return [p for segment in sorted(self.prior_rows) for p in self.prior_rows[segment]]

    async def pending_page(self, after: str | None, limit: int) -> list[PendingRow]:
        found = sorted(
            (
                r
                for r in self.pending.values()
                if r.handle_id is not None and (after is None or r.entry_key > after)
            ),
            key=lambda r: r.entry_key,
        )
        return found[:limit]

    async def pending_of(self, handle_ids: Sequence[UUID]) -> list[PendingRow]:
        self.statements.append("pending_of")
        found = sorted(
            (r for r in self.pending.values() if r.handle_id in handle_ids),
            key=lambda r: (r.received_at, r.entry_key),
        )
        return found[:MAX_PENDING]

    async def snapshot(self, handle_id: UUID) -> Snapshot:
        self.statements.append("snapshot")
        return self._snapshot(handle_id)

    def _snapshot(self, handle_id: UUID) -> Snapshot:
        rows = tuple(
            r for (h, _), r in sorted(self.rows.items(), key=lambda kv: str(kv[0])) if h == handle_id
        )
        pending = sorted(
            (r for r in self.pending.values() if r.handle_id == handle_id),
            key=lambda r: (r.received_at, r.entry_key),
        )
        suppressed = tuple(r for (h, _, _), r in self.suppressed.items() if h == handle_id)
        return Snapshot(rows, tuple(pending[:MAX_PENDING]), suppressed)

    async def store(self, write: SignalWrite) -> bool:
        return write.handle_id in await self.store_many([write])

    async def store_many(self, writes: Sequence[SignalWrite]) -> set[UUID]:
        self.statements.append("store_many")
        kept = [RawObject(w.raw_key, str(w.handle_id), w.at) for w in writes if w.raw_key]
        index_raw_in(self._raw, kept)
        written: set[UUID] = set()
        for write in writes:
            for key in write.folded:
                self.pending.pop(key, None)
            if any(r.opted_out_at for (h, _), r in self.rows.items() if h == write.handle_id):
                continue
            for b in write.beneficiaries:
                pair = (write.handle_id, b.beneficiary_id)
                before = self.beneficiaries.get(pair)
                self.beneficiaries[pair] = replace(before, updated_at=write.at) if before else b
            for r in write.rows:
                self.rows[(write.handle_id, r.beneficiary_id)] = r
            index_in(
                self._mentions["rows"],
                [(signals_key(write.handle_id, b), t) for b, t in write.named],
                replace=False,
            )
            written.add(write.handle_id)
        return written

    async def rows_for(self, handle_ids: Sequence[UUID]) -> list[SignalRow]:
        self.statements.append("rows_for")
        wanted = set(handle_ids)
        return [r for (h, _), r in self.rows.items() if h in wanted]

    async def rows_page(self, after: tuple[UUID, UUID] | None, limit: int) -> list[SignalRow]:
        found = sorted(self.rows.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
        wanted = (str(after[0]), str(after[1])) if after is not None else None
        return [r for (h, b), r in found if wanted is None or (str(h), str(b)) > wanted][:limit]

    async def signals_for(self, handle_ids: Sequence[UUID]) -> list[SignalRow]:
        wanted = set(handle_ids)
        return [r for (h, _), r in self.rows.items() if h in wanted]

    async def beneficiaries_for(self, handle_ids: Sequence[UUID]) -> list[BeneficiaryRow]:
        wanted = set(handle_ids)
        return [b for (h, _), b in self.beneficiaries.items() if h in wanted]

    async def withdraw(self, handle_ids: Sequence[UUID], markers: Sequence[SignalRow]) -> list[ErasedRow]:
        wanted = set(handle_ids)
        erased = self._drop(wanted, limit=None)
        for m in markers:
            self.rows[(m.handle_id, m.beneficiary_id)] = m
        return erased

    async def inactive(
        self,
        before: datetime,
        *,
        limit: int,
        keep: Collection[UUID] = (),
        after: tuple[UUID, UUID] | None = None,
    ) -> list[SignalRow]:
        found = [
            r
            for key, r in sorted(self.rows.items())
            if r.last_activity_at < before
            and r.opted_out_at is None
            and key[0] not in keep
            and (key not in self.decayed or self.decayed[key] < r.last_activity_at)
            and (after is None or key > after)
        ]
        return found[:limit]

    async def decay(self, rows: Sequence[SignalRow], at: datetime) -> int:
        done = 0
        for row in rows:
            key = (row.handle_id, row.beneficiary_id)
            held = self.rows.get(key)
            if held is None or held.derived_at != row.derived_at or held.opted_out_at is not None:
                continue
            self.rows[key] = row
            self.decayed[key] = at
            done += 1
        return done

    async def drop_untold(self, rows: Sequence[SignalRow]) -> list[ErasedRow]:
        erased = []
        for row in rows:
            key = (row.handle_id, row.beneficiary_id)
            held = self.rows.get(key)
            if row.beneficiary_id != SELF or held is None or held.derived_at != row.derived_at:
                continue
            if held.opted_out_at is not None:
                continue
            del self.rows[key]
            self.decayed.pop(key, None)
            erased.append(_row("subject_signals", key))
        return erased

    async def add_suppressions(self, rows: Sequence[SuppressionRow]) -> None:
        self.statements.append("add_suppressions")
        for r in rows:
            key = (r.handle_id, r.beneficiary_id, r.key_hmac)
            before = self.suppressed.get(key)
            if before is None or before.evidence_until < r.evidence_until:
                self.suppressed[key] = replace(r, created_at=before.created_at) if before else r

    async def suppressions_for(self, handle_ids: Sequence[UUID]) -> list[SuppressionRow]:
        wanted = set(handle_ids)
        return [r for (h, _, _), r in self.suppressed.items() if h in wanted]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.kind is TargetKind.CONVERSATION:
            session = self.sessions.get(scope.conversation_id or "")
            keys = [k for k, r in self.pending.items() if session is not None and r.session == session][
                :limit
            ]
            for k in keys:
                del self.pending[k]
            return [ErasedRow("signal_intake", {"entry_key": k}) for k in keys]
        erased = self._drop(set(scope.handle_ids), limit=limit)
        return erased + self._forget_suppressions(set(scope.handle_ids), limit=limit - len(erased))

    def _drop(self, handles: set[UUID], *, limit: int | None) -> list[ErasedRow]:
        erased: list[ErasedRow] = []
        for table, rows in (("subject_signals", self.rows), ("beneficiaries", self.beneficiaries)):
            for key in [k for k in rows if k[0] in handles]:
                if limit is not None and len(erased) >= limit:
                    return erased
                del rows[key]
                erased.append(_row(table, key))
        for entry_key in [k for k, r in self.pending.items() if r.handle_id in handles]:
            if limit is not None and len(erased) >= limit:
                return erased
            del self.pending[entry_key]
            erased.append(ErasedRow("signal_intake", {"entry_key": entry_key}))
        return erased

    def _forget_suppressions(self, handles: set[UUID], *, limit: int) -> list[ErasedRow]:
        erased = []
        for key in [k for k in self.suppressed if k[0] in handles][:limit]:
            del self.suppressed[key]
            erased.append(
                ErasedRow(
                    "inference_suppressions",
                    {
                        "origin_handle_id": str(key[0]),
                        "beneficiary_id": str(key[1]),
                        "inference_key_hmac": key[2].hex(),
                    },
                )
            )
        return erased


def _row(table: str, key: tuple[UUID, UUID]) -> ErasedRow:
    return ErasedRow(table, {"origin_handle_id": str(key[0]), "beneficiary_id": str(key[1])})


class InMemorySignalOverlay:
    """The state store's overlay, in memory; `down` makes every call fail as a lost store would."""

    def __init__(self) -> None:
        self.entries: dict[tuple[UUID, UUID], dict[str, bytes]] = {}
        self.folded: dict[tuple[UUID, UUID], set[str]] = {}
        self.down = False
        self.marker: str | None = None

    def restart(self) -> None:
        """The store came back empty."""
        self.entries.clear()
        self.folded.clear()
        self.marker = None

    def _check(self) -> None:
        if self.down:
            raise CacheUnavailable("state store down")

    async def add(self, space_id: UUID, entries: Mapping[UUID, Mapping[str, bytes]], ttl_s: int) -> None:  # noqa: ARG002
        self._check()
        for handle_id, by_key in entries.items():
            done = self.folded.get((space_id, handle_id), set())
            mine = self.entries.setdefault((space_id, handle_id), {})
            mine.update({k: v for k, v in by_key.items() if k not in done})

    async def read(self, space_id: UUID, handle_ids: Sequence[UUID]) -> dict[UUID, dict[str, bytes]]:
        self._check()
        return {h: dict(self.entries[(space_id, h)]) for h in handle_ids if self.entries.get((space_id, h))}

    async def drop(self, space_id: UUID, handle_id: UUID, entry_keys: Sequence[str], ttl_s: int) -> None:  # noqa: ARG002
        self._check()
        mine = self.entries.get((space_id, handle_id), {})
        for key in entry_keys:
            mine.pop(key, None)
        self.folded.setdefault((space_id, handle_id), set()).update(entry_keys)

    async def whole(self) -> bool:
        self._check()
        return self.marker == "whole"

    async def claim_rebuild(self, token: str, lease_ms: int) -> bool:  # noqa: ARG002
        self._check()
        if self.marker is not None:
            return False
        self.marker = f"rebuilding:{token}"
        return True

    async def seal(self, token: str) -> bool:
        self._check()
        if self.marker != f"rebuilding:{token}":
            return False
        self.marker = "whole"
        return True

    async def purge(self, space_id: UUID, handle_ids: Sequence[UUID]) -> None:
        self._check()
        for handle_id in handle_ids:
            self.entries.pop((space_id, handle_id), None)
            self.folded.pop((space_id, handle_id), None)


class InMemoryTurnCounters:
    """`TurnCounterStore` for unit tests; `down` makes it fail like a lost state store."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], dict[str, str]] = {}
        self.down = False

    async def add(self, space_id: UUID, counts: Mapping[str, Mapping[str, str]], ttl_s: int) -> None:  # noqa: ARG002
        self._check()
        for key, fields in counts.items():
            self.rows.setdefault((space_id, key), {}).update(fields)

    async def read(self, space_id: UUID, keys: Sequence[str]) -> list[dict[str, str]]:
        self._check()
        return [dict(self.rows.get((space_id, key), {})) for key in keys]

    async def forget(self, space_id: UUID, keys: Sequence[str]) -> None:
        self._check()
        for key in keys:
            self.rows.pop((space_id, key), None)

    def _check(self) -> None:
        if self.down:
            raise CacheUnavailable("state store down")
