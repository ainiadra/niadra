"""In-memory `EventRepository` for unit tests."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.ingest.coverage import SourceDay
from niadra.domain.ingest.events import ConversationTouch, NewEvent, export_row
from niadra.domain.ingest.files import FileImport
from niadra.domain.ingest.intake import IntakeRow, NewIntake
from niadra.domain.ingest.storage import event_blob_keys
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.privacy.mentions import by_key
from niadra.domain.records import EventRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind

_TABLES = 3


class InMemoryEventRepository:
    def __init__(self, store: MemoryStore, space_id: UUID | None = None) -> None:
        self._space_id = space_id
        self._outbox: list[Task] = store.module("pipeline").setdefault("outbox", [])
        self._s = store.module("ingest")
        self._intake: dict[tuple[UUID | None, UUID], IntakeRow] = self._s.setdefault("intake", {})
        self._events: dict[UUID, NewEvent] = self._s.setdefault("events", {})
        self._keys: dict[int, dict[tuple[UUID, str], tuple[UUID, datetime]]] = self._s.setdefault(
            "event_keys", {i: {} for i in range(_TABLES)}
        )
        self._generation: dict[str, Any] = self._s.setdefault("generation", {"value": 0, "updated_at": None})
        self._conversations: dict[tuple[UUID, str], dict[str, Any]] = self._s.setdefault("conversations", {})
        self._heartbeats: dict[tuple[UUID, UUID, datetime], dict[str, Any]] = self._s.setdefault(
            "heartbeats", {}
        )
        self._imports: dict[UUID, tuple[UUID, FileImport]] = self._s.setdefault("imports", {})
        # Stands in for `change_seq`: bumped on every insert and update of an exported row.
        self._seq: dict[tuple[str, Any], int] = self._s.setdefault("change_seq", {})
        self._counter: dict[str, int] = self._s.setdefault("seq_counter", {"value": 0})

    @property
    def conversations(self) -> dict[tuple[UUID, str], dict[str, Any]]:
        return self._conversations

    @property
    def heartbeats(self) -> dict[tuple[UUID, UUID, datetime], dict[str, Any]]:
        return self._heartbeats

    @property
    def stored(self) -> list[NewEvent]:
        return list(self._events.values())

    async def get_events(self, event_ids: Sequence[UUID]) -> list[EventRecord]:
        return [self._events[i].record for i in event_ids if i in self._events]

    async def mark_content(self, event_ids: Sequence[UUID], marks: dict[str, Any]) -> None:
        for event_id in event_ids:
            stored = self._events.get(event_id)
            if stored is not None:
                meta = {**(stored.record.content_meta or {}), **marks}
                self._events[event_id] = replace(stored, record=replace(stored.record, content_meta=meta))
                self._stamp("events", event_id)

    async def events_after(
        self, after_id: UUID | None, before_id: UUID, limit: int
    ) -> list[tuple[EventRecord, str | None]]:
        found = sorted(
            (e for e in self._events.values() if e.record.event_id < before_id),
            key=lambda e: e.record.event_id,
        )
        if after_id is not None:
            found = [e for e in found if e.record.event_id > after_id]
        return [(e.record, e.raw_ref) for e in found[:limit]]

    async def rewrite_content(self, records: Sequence[EventRecord]) -> None:
        for record in records:
            stored = self._events.get(record.event_id)
            if stored is not None:
                meta = dict(stored.record.content_meta or {})
                if masked := (record.content_meta or {}).get("masked"):
                    meta["masked"] = masked
                rewritten = replace(
                    stored.record,
                    text=record.text,
                    fields=record.fields,
                    action=record.action,
                    content_meta=meta or None,
                )
                self._events[record.event_id] = replace(stored, record=rewritten)
                self._stamp("events", record.event_id)

    async def masked_counts(self, *, conversation_id: str | None, task_id: str | None) -> dict[str, int]:
        totals: dict[str, int] = {}
        for e in self._events.values():
            if conversation_id is not None and e.record.conversation_id != conversation_id:
                continue
            if task_id is not None and e.record.task_id != task_id:
                continue
            for kind, count in ((e.record.content_meta or {}).get("masked") or {}).items():
                totals[kind] = totals.get(kind, 0) + int(count)
        return dict(sorted(totals.items()))

    async def masking_state(self) -> int | None:
        value = self._s.setdefault("state", {}).get("masking")
        return int(value) if value is not None else None

    async def set_masking_state(self, fingerprint: int) -> None:
        self._s.setdefault("state", {})["masking"] = fingerprint

    async def events_between(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        since: datetime,
        until: datetime,
    ) -> list[EventRecord]:
        found = [
            e.record
            for e in self._events.values()
            if (conversation_id is None or e.record.conversation_id == conversation_id)
            and (task_id is None or e.record.task_id == task_id)
            and since <= e.record.occurred_at <= until
        ]
        return sorted(found, key=lambda r: (r.occurred_at, r.event_id))

    async def events_for_handles(
        self, handle_ids: Sequence[UUID], since: datetime, limit: int, *, before_id: UUID | None = None
    ) -> list[EventRecord]:
        wanted = set(handle_ids)
        found = [
            e.record
            for e in self._events.values()
            if e.record.received_at >= since
            and wanted.intersection(e.record.handle_ids)
            and (before_id is None or e.record.event_id < before_id)
        ]
        recent = sorted(found, key=lambda r: r.event_id, reverse=True)[:limit]
        return sorted(recent, key=lambda r: (r.occurred_at, r.event_id))

    async def system_events_for_handles(
        self, handle_ids: Sequence[UUID], since: datetime, limit: int
    ) -> list[EventRecord]:
        wanted = set(handle_ids)
        found = [
            e.record
            for e in self._events.values()
            if e.record.kind is EventKind.SYSTEM_EVENT
            and e.record.received_at >= since
            and wanted.intersection(e.record.handle_ids)
        ]
        recent = sorted(found, key=lambda r: r.event_id, reverse=True)[:limit]
        return sorted(recent, key=lambda r: (r.occurred_at, r.event_id), reverse=True)

    async def recent_events(
        self, since: datetime, limit: int, *, after: UUID | None = None
    ) -> list[EventRecord]:
        found = [
            e.record
            for e in self._events.values()
            if e.record.received_at >= since and (after is None or e.record.event_id > after)
        ]
        return sorted(found, key=lambda r: r.event_id)[:limit]

    async def source_last_event_at(self, since: datetime) -> dict[UUID, datetime]:
        latest: dict[UUID, datetime] = {}
        for (_, source_id, window_start), row in self._heartbeats.items():
            last = row["last_event_at"]
            if window_start >= since and last is not None:
                latest[source_id] = max(last, latest.get(source_id, last))
        return latest

    async def coverage(self, since: datetime) -> dict[UUID, list[SourceDay]]:
        totals: dict[UUID, dict[Any, dict[str, Any]]] = {}
        for (_, source_id, window_start), row in sorted(self._heartbeats.items(), key=lambda kv: kv[0][2]):
            if window_start < since:
                continue
            day = totals.setdefault(source_id, {}).setdefault(
                window_start.astimezone(UTC).date(),
                {"sent": 0, "received": 0, "rejected": 0, "unmapped": 0, "last_event_at": None},
            )
            for name in ("sent", "received", "rejected", "unmapped"):
                day[name] += row[name]
            if row["last_event_at"] is not None:
                day["last_event_at"] = max(filter(None, (day["last_event_at"], row["last_event_at"])))
        return {
            source: [SourceDay(day, **values) for day, values in sorted(days.items())]
            for source, days in totals.items()
        }

    async def received_since(self, since: datetime) -> bool:
        return any(
            window_start >= since and row["received"] > 0
            for (_, _, window_start), row in self._heartbeats.items()
        )

    async def claim_keys(
        self, space_id: UUID, keys: Sequence[tuple[str, UUID]], received_at: datetime
    ) -> tuple[int, set[str]]:
        generation = int(self._generation["value"])
        current, previous = self._keys[generation % _TABLES], self._keys[(generation - 1) % _TABLES]
        known: set[str] = set()
        for key, event_id in keys:
            if (space_id, key) in previous or (space_id, key) in current:
                known.add(key)
            else:
                current[(space_id, key)] = (event_id, received_at)
        return generation, known

    async def accept_intake(self, intake: NewIntake) -> set[str]:
        _, known = await self.claim_keys(intake.space_id, intake.claims, intake.received_at)
        row = IntakeRow(
            intake.intake_id,
            intake.received_at,
            intake.body_ct,
            intake.dek_version,
            frozenset(known),
            source_id=intake.source_id,
        )
        self._intake[(intake.space_id, intake.intake_id)] = row
        payload = {"intake_id": str(intake.intake_id)}
        self._outbox.append(Task(TaskName.APPLY_INTAKE, intake.space_id, payload, id=intake.intake_id))
        return known

    async def has_intake(self) -> bool:
        return any(space == self._space_id and r.failed_at is None for (space, _), r in self._intake.items())

    async def take_intake(self, limit: int) -> list[IntakeRow]:
        # Insertion order stands in for the database's sequence.
        return [
            r for (space, _), r in self._intake.items() if space == self._space_id and r.failed_at is None
        ][:limit]

    async def try_take_intake(self, limit: int) -> list[IntakeRow] | None:
        return await self.take_intake(limit)

    async def drop_intake(self, intake_ids: Sequence[UUID]) -> None:
        for intake_id in intake_ids:
            self._intake.pop((self._space_id, intake_id), None)
        self._outbox[:] = [t for t in self._outbox if t.id not in set(intake_ids)]

    async def fail_intake(self, intake_id: UUID) -> int:
        row = self._intake.get((self._space_id, intake_id))
        if row is None:
            return 0
        self._intake[(self._space_id, intake_id)] = replace(row, attempts=row.attempts + 1)
        return row.attempts + 1

    async def set_aside_intake(self, intake_id: UUID, at: datetime) -> None:
        row = self._intake.get((self._space_id, intake_id))
        if row is not None:
            self._intake[(self._space_id, intake_id)] = replace(row, failed_at=at)
        self._outbox[:] = [t for t in self._outbox if t.id != intake_id]

    async def failed_intake(
        self, limit: int, *, before: datetime | None = None, after: UUID | None = None
    ) -> list[IntakeRow]:
        kept = [
            r for (space, _), r in self._intake.items() if space == self._space_id and r.failed_at is not None
        ]
        if after is not None:
            # Insertion order stands in for the database's sequence.
            ids = [r.intake_id for r in kept]
            kept = kept[ids.index(after) + 1 :] if after in ids else []
        return [r for r in kept if before is None or r.received_at < before][:limit]

    async def count_failed_intake(self) -> int:
        mine = (r for (space, _), r in self._intake.items() if space == self._space_id)
        return sum(1 for r in mine if r.failed_at is not None)

    async def retry_intake(self, intake_ids: Sequence[UUID] | None) -> list[UUID]:
        put_back = []
        for (space, intake_id), row in list(self._intake.items()):
            if space != self._space_id or row.failed_at is None:
                continue
            if intake_ids is not None and intake_id not in intake_ids:
                continue
            self._intake[(space, intake_id)] = replace(row, failed_at=None, attempts=0)
            put_back.append(intake_id)
        return put_back

    async def rewrite_intake(self, intake_id: UUID, body_ct: bytes, dek_version: int) -> None:
        row = self._intake.get((self._space_id, intake_id))
        if row is not None:
            self._intake[(self._space_id, intake_id)] = replace(row, body_ct=body_ct, dek_version=dek_version)

    async def release_keys(self, space_id: UUID, generation: int, keys: Sequence[str]) -> None:
        current = self._keys[generation % _TABLES]
        for key in keys:
            current.pop((space_id, key), None)

    async def rotate_dedupe(self, now: datetime, min_age: timedelta) -> int | None:
        updated_at = self._generation["updated_at"]
        if updated_at is not None and now - updated_at < min_age:
            return None
        new = int(self._generation["value"]) + 1
        self._keys[(new + 1) % _TABLES].clear()
        self._generation.update(value=new, updated_at=now)
        return new

    async def insert_events(self, events: Sequence[NewEvent]) -> None:
        for event in events:
            self._events[event.record.event_id] = event
            self._stamp("events", event.record.event_id)

    async def events_naming(
        self, tokens: Sequence[bytes], *, besides: Sequence[UUID], after: UUID | None, limit: int
    ) -> list[UUID]:
        wanted, theirs = set(tokens), set(besides)
        found = sorted(
            e.record.event_id
            for e in self._events.values()
            if (self._space_id is None or e.space_id == self._space_id)
            and e.mentions & wanted
            and not theirs.intersection(e.record.handle_ids)
            and (after is None or e.record.event_id > after)
        )
        return found[:limit]

    async def index_mentions(self, mentions: Sequence[tuple[UUID, frozenset[bytes]]]) -> None:
        for event_id, tokens in by_key(mentions):
            if (stored := self._events.get(event_id)) is not None:
                self._events[event_id] = replace(stored, mentions=tokens)

    async def mentions_ready(self) -> bool:
        return bool(self._s.get("mentions_ready"))

    async def mark_mentions_ready(self) -> None:
        self._s["mentions_ready"] = True

    async def find_conversation(self, ids: Sequence[str]) -> str | None:
        wanted = set(ids)
        for (_, conversation_id), row in self._conversations.items():
            if conversation_id in wanted or wanted.intersection(row["aliases"]):
                return conversation_id
        return None

    async def known_conversation(self, conversation_id: str) -> tuple[UUID, ...] | None:
        for (_, stored), row in self._conversations.items():
            if stored == conversation_id:
                return (row["primary_handle"],) if row["primary_handle"] else ()
        return None

    async def primary_handles(self, conversation_ids: Sequence[str]) -> dict[str, UUID]:
        wanted = set(conversation_ids)
        return {
            stored: row["primary_handle"]
            for (_, stored), row in self._conversations.items()
            if stored in wanted and row["primary_handle"]
        }

    async def conversation_names(self, scope: ErasureScope) -> list[str]:
        names = {
            name
            for key in self._target_conversations(scope)
            for name in (key[1], *self._conversations[key]["aliases"])
        }
        return sorted(names)

    async def upsert_conversation(self, space_id: UUID, touch: ConversationTouch) -> None:
        key = (space_id, touch.conversation_id)
        row = self._conversations.get(key)
        if row is None:
            self._conversations[key] = {
                "aliases": set(touch.aliases),
                "channel": touch.channel,
                "primary_handle": touch.primary_handle,
                "first_event_at": touch.first_event_at,
                "last_event_at": touch.last_event_at,
                "dirty": touch.dirty,
            }
            self._stamp("conversations", key)
            return
        self._stamp("conversations", key)
        row["aliases"] |= touch.aliases
        row["primary_handle"] = row["primary_handle"] or touch.primary_handle
        row["first_event_at"] = min(row["first_event_at"], touch.first_event_at)
        row["last_event_at"] = max(row["last_event_at"], touch.last_event_at)
        row["dirty"] = row["dirty"] or touch.dirty

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
        row = self._heartbeats.setdefault(
            (space_id, source_id, window_start),
            {"sent": 0, "received": 0, "rejected": 0, "unmapped": 0, "last_event_at": None},
        )
        if sent is not None:
            row["sent"] = max(row["sent"], sent)
        row["received"] += received
        row["rejected"] += rejected
        row["unmapped"] += unmapped
        if last_event_at is not None:
            row["last_event_at"] = max(filter(None, (row["last_event_at"], last_event_at)))

    async def raw_refs(self, source_id: UUID, *, limit: int) -> list[tuple[UUID, str]]:
        newest: dict[str, UUID] = {}
        for event in sorted(self._events.values(), key=lambda e: e.record.event_id, reverse=True):
            if event.record.source_id == source_id and event.raw_ref and event.raw_ref not in newest:
                newest[event.raw_ref] = event.record.event_id
        return [(event_id, ref) for ref, event_id in newest.items()][:limit]

    async def recent_messages(self, source_id: UUID, *, limit: int) -> list[EventRecord]:
        found = [
            e.record
            for e in self._events.values()
            if e.record.source_id == source_id and e.record.kind is EventKind.MESSAGE
        ]
        return sorted(found, key=lambda r: r.event_id, reverse=True)[:limit]

    async def archivable_events(
        self, *, since: datetime, until: datetime, after_id: UUID | None, limit: int
    ) -> list[EventRecord]:
        found = [
            e.record
            for e in self._events.values()
            if since <= e.record.received_at < until and (after_id is None or e.record.event_id > after_id)
        ]
        return sorted(found, key=lambda r: r.event_id)[:limit]

    async def purge_archived(self, *, since: datetime, until: datetime, limit: int) -> int:
        doomed = sorted(
            e.record.event_id for e in self._events.values() if since <= e.record.received_at < until
        )[:limit]
        for event_id in doomed:
            del self._events[event_id]
        return len(doomed)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        rows = [self._erase_event(e) for e in self._target_events(scope)[:limit]]
        if len(rows) < limit:
            rows += [self._erase_conversation(key) for key in self._target_conversations(scope)]
        return rows[:limit]

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table not in ("events", "conversations"):
            raise ValueError(f"ingest does not export {table}")
        stamped = sorted(
            (seq, key)
            for (name, key), seq in self._seq.items()
            if name == table and after_seq < seq <= upto_seq
        )
        rows: list[dict[str, Any]] = []
        for seq, key in stamped[:limit]:
            if table == "events":
                rows.append({**export_row(self._events[key].record), "change_seq": seq})
            else:
                row = self._conversations[key]
                rows.append(
                    {
                        "conversation_id": key[1],
                        "aliases": sorted(row["aliases"]),
                        "channel": row["channel"],
                        "primary_handle": row["primary_handle"],
                        "first_event_at": row["first_event_at"],
                        "last_event_at": row["last_event_at"],
                        "change_seq": seq,
                    }
                )
        return rows

    async def create_import(self, space_id: UUID, record: FileImport) -> None:
        self._imports.setdefault(record.import_id, (space_id, record))

    async def get_import(self, import_id: UUID) -> FileImport | None:
        found = self._imports.get(import_id)
        return found[1] if found else None

    async def save_import(self, record: FileImport) -> None:
        space_id, _ = self._imports[record.import_id]
        self._imports[record.import_id] = (space_id, record)

    def _stamp(self, table: str, key: Any) -> None:
        self._counter["value"] += 1
        self._seq[(table, key)] = self._counter["value"]

    def _target_events(self, scope: ErasureScope) -> list[NewEvent]:
        handles = set(scope.handle_ids)
        found = [
            e
            for e in self._events.values()
            if (
                e.record.conversation_id == scope.conversation_id
                if scope.kind is TargetKind.CONVERSATION
                else bool(handles.intersection(e.record.handle_ids))
            )
        ]
        return sorted(found, key=lambda e: e.record.event_id)

    def _target_conversations(self, scope: ErasureScope) -> list[tuple[UUID, str]]:
        if scope.kind is TargetKind.CONVERSATION:
            return [key for key in self._conversations if key[1] == scope.conversation_id]
        handles = set(scope.handle_ids)
        return [key for key, row in self._conversations.items() if row["primary_handle"] in handles]

    def _erase_event(self, event: NewEvent) -> ErasedRow:
        event_id = event.record.event_id
        del self._events[event_id]
        return ErasedRow(
            "events",
            {"event_id": str(event_id)},
            event_blob_keys(event.space_id, event.raw_ref, event.record.content_meta),
            self._seq.pop(("events", event_id), None),
        )

    def _erase_conversation(self, key: tuple[UUID, str]) -> ErasedRow:
        del self._conversations[key]
        return ErasedRow(
            "conversations", {"conversation_id": key[1]}, (), self._seq.pop(("conversations", key), None)
        )
