"""In-memory `TypedRepository` for unit tests."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.records import BusinessObject
from niadra.domain.state.observed import stamps_part
from niadra.domain.state.shared import SharedKey
from niadra.domain.state.typed import META_KEY, STAMP_SEQ, NewValue, ScanMark, TimerWrite, stamp_seq
from niadra.ports.repos.typed import (
    FiredTimer,
    RefreshRow,
    ScanRow,
    SharedRow,
    SharedStamps,
    SharedWrite,
    WatchRow,
    content_ref,
)


@dataclass(frozen=True, slots=True)
class TimerRow:
    object_id: UUID
    write: TimerWrite


class InMemoryTypedRepository:
    def __init__(self, store: MemoryStore) -> None:
        objects = store.module("objects")
        self._objects: dict[UUID, BusinessObject] = objects.setdefault("objects", {})
        self.values: dict[tuple[UUID, str, int], NewValue] = objects.setdefault("values", {})
        self.timers: dict[UUID, TimerRow] = objects.setdefault("timers", {})
        self.dependencies: set[tuple[UUID, str, UUID]] = objects.setdefault("dependencies", set())
        self.shared: dict[UUID, SharedRow] = objects.setdefault("shared", {})
        self.watches: dict[UUID, WatchRow] = objects.setdefault("watches", {})
        self.refreshes: dict[tuple[UUID, str], tuple[RefreshRow, int]] = objects.setdefault("refreshes", {})
        """(object, reason) -> the request and how many times it was leased."""
        self.scans: dict[str, ScanRow] = objects.setdefault("scans", {})
        self.consolidations: list[int] = objects.setdefault("consolidations", [])
        """How many objects each consolidation carried: one entry per statement."""

    async def add_values(self, values: Mapping[UUID, Sequence[NewValue]], event_id: UUID | None) -> None:  # noqa: ARG002
        for object_id, found in values.items():
            for value in found:
                self.values.setdefault((object_id, value.name, value.version), value)

    async def values_named(self, rows: Sequence[tuple[UUID, str, int]]) -> dict[tuple[UUID, str, int], Any]:
        return {
            r: self.values[r].value for r in rows if r in self.values and self.values[r].value is not None
        }

    async def values_page(
        self, after: tuple[UUID, str, int] | None, limit: int
    ) -> list[tuple[tuple[UUID, str, int], Any]]:
        keys = sorted(
            k for k, v in self.values.items() if v.value is not None and (after is None or k > after)
        )
        return [(k, self.values[k].value) for k in keys[:limit]]

    async def rewrite_value(self, object_id: UUID, name: str, version: int, value: Any) -> None:
        if (key := (object_id, name, version)) in self.values:
            self.values[key] = replace(self.values[key], value=value)

    async def write_timers(self, writes: Mapping[UUID, Sequence[TimerWrite]], now: datetime) -> None:  # noqa: ARG002
        for object_id, found in writes.items():
            for write in found:
                current = self.timers.get(write.timer_id)
                if current is None or current.write.state == "armed":
                    self.timers[write.timer_id] = TimerRow(object_id, write)

    async def fire_due(self, now: datetime, *, limit: int) -> list[FiredTimer]:
        return self._fire(now, limit, shared=False)

    async def fire_shared(self, now: datetime, *, limit: int) -> list[FiredTimer]:
        fired = self._fire(now, limit, shared=True)
        for timer in fired:
            row = self.shared[timer.object_id]
            stamps = dict(row.meta.get(META_KEY) or {})
            marks = dict(stamps.get("t") or {})
            mark = marks.get(timer.name)
            if mark is not None and mark.get("id") == str(timer.timer_id):
                marks[timer.name] = {**mark, "st": "fired", "fired": now.isoformat()}
            stamps |= {"t": marks, STAMP_SEQ: stamp_seq(stamps) + 1}
            self.shared[timer.object_id] = replace(row, meta={**row.meta, META_KEY: stamps})
        return fired

    def _fire(self, now: datetime, limit: int, *, shared: bool) -> list[FiredTimer]:
        due = sorted(
            (
                row
                for row in self.timers.values()
                if row.write.state == "armed"
                and row.write.due_at is not None
                and (row.object_id in self.shared) == shared
            ),
            key=lambda row: row.write.due_at or now,
        )
        fired = []
        for row in [r for r in due if r.write.due_at is not None and r.write.due_at <= now][:limit]:
            write = replace(row.write, state="fired", fired_at=now)
            self.timers[write.timer_id] = TimerRow(row.object_id, write)
            fired.append(
                FiredTimer(
                    write.timer_id,
                    row.object_id,
                    write.name,
                    write.due_at,
                    now,
                    write.value_version,
                    write.supersedes_firing_id,
                )
            )
        return fired

    async def bind_inputs(
        self,
        dependent_id: UUID,
        inputs: Sequence[tuple[UUID, str]],
        now: datetime,  # noqa: ARG002
    ) -> None:
        for kept in [d for d in self.dependencies if d[2] == dependent_id]:
            self.dependencies.discard(kept)
        for input_id, name in inputs:
            self.dependencies.add((input_id, name, dependent_id))

    async def enter_working_set(
        self, entries: Sequence[tuple[UUID, SharedKey, datetime, datetime]]
    ) -> list[SharedRow]:
        rows = []
        for object_id, key, at, until in entries:
            current = self.shared.get(object_id)
            if current is None:
                current = SharedRow(object_id, key, {}, {}, until, at)
            else:
                later = max(current.hot_until or until, until)
                current = replace(
                    current, hot_until=later, last_referenced_at=max(current.last_referenced_at, at)
                )
            self.shared[object_id] = current
            rows.append(current)
        return rows

    async def consolidate(
        self, objects: Sequence[tuple[UUID, dict[str, Any], dict[str, Any]]]
    ) -> list[tuple[UUID, int, str]]:
        self.consolidations.append(len(objects))
        moved = []
        for object_id, state, meta in objects:
            row = self.shared.get(object_id)
            if row is None:
                continue
            new_state = {f: dict(by) for f, by in row.state.items()}
            new_meta = {f: dict(by) for f, by in row.meta.items()}
            changed = False
            for field, by in meta.items():
                for source, stamps in by.items():
                    held = new_meta.get(field, {}).get(source)
                    if held is None or int(stamps["version"]) > int(held["version"]):
                        new_meta.setdefault(field, {})[source] = stamps
                        new_state.setdefault(field, {})[source] = state[field][source]
                        changed = True
            if changed:
                self.shared[object_id] = replace(row, state=new_state, meta=new_meta)
                moved.append((object_id, row.watch_count, row.key.type))
        return moved

    async def write_shared(
        self,
        writes: Mapping[UUID, SharedWrite],
        values: Mapping[UUID, Sequence[NewValue]],
        timers: Mapping[UUID, Sequence[TimerWrite]],
        changed: Mapping[UUID, Sequence[str]],
        now: datetime,
    ) -> list[SharedStamps]:
        await self.add_values(values, None)
        await self.write_timers(timers, now)
        bound_before = set(self.dependencies)  # the statement's snapshot: bindings it makes do not count
        for object_id, write in writes.items():
            if write.bound is not None:
                await self.bind_inputs(object_id, write.bound, now)
        hit: dict[UUID, set[str]] = {
            object_id: set(write.expired_by) for object_id, write in writes.items() if write.expired_by
        }
        for input_id, name, dependent_id in bound_before:
            if name in changed.get(input_id, ()):
                hit.setdefault(dependent_id, set()).add(name)
        stored = []
        for object_id in [*writes, *(i for i in hit if i not in writes)]:
            row = self.shared.get(object_id)
            if row is None:
                continue
            stamps, status = merged_stamps(
                row.meta.get(META_KEY) or {},
                row.derived_status,
                writes.get(object_id),
                hit.get(object_id),
                now,
            )
            self.shared[object_id] = replace(row, meta={**row.meta, META_KEY: stamps}, derived_status=status)
            stored.append(SharedStamps(object_id, row.key, stamps, status, object_id in hit))
        return stored

    async def shared_by_id(self, object_ids: Sequence[UUID]) -> list[SharedRow]:
        return [self.shared[i] for i in object_ids if i in self.shared]

    async def working_set(self, now: datetime, *, after: UUID | None, limit: int) -> list[SharedRow]:
        rows = sorted(
            (r for r in self.shared.values() if r.hot_until and r.hot_until > now), key=lambda r: r.object_id
        )
        return [r for r in rows if after is None or r.object_id > after][:limit]

    async def leave_working_set(
        self, type_: str, unreferenced_since: datetime, *, limit: int, keep: Collection[UUID] = ()
    ) -> list[UUID]:
        gone = [
            r.object_id
            for r in self.shared.values()
            if r.key.type == type_
            and r.last_referenced_at < unreferenced_since
            and r.watch_count == 0
            and r.object_id not in keep
        ][:limit]
        for object_id in gone:
            del self.shared[object_id]
        for timer_id, row in list(self.timers.items()):
            if row.object_id in gone and row.write.state == "armed":
                del self.timers[timer_id]
        return gone

    async def watch(self, watches: Sequence[WatchRow], now: datetime) -> None:  # noqa: ARG002
        for w in watches:
            if w.watch_id not in self.watches and w.object_id in self.shared:
                row = self.shared[w.object_id]
                self.shared[w.object_id] = replace(row, watch_count=row.watch_count + 1)
            kept = self.watches.get(w.watch_id)
            self.watches[w.watch_id] = replace(w, last_fired_at=kept.last_fired_at if kept else None)

    async def unwatch(self, pairs: Sequence[tuple[UUID, UUID]]) -> None:
        for object_id, handle_id in pairs:
            for w in [
                w for w in self.watches.values() if (w.object_id, w.handle_id) == (object_id, handle_id)
            ]:
                del self.watches[w.watch_id]
                if object_id in self.shared:
                    row = self.shared[object_id]
                    self.shared[object_id] = replace(row, watch_count=max(0, row.watch_count - 1))

    async def watches_of(self, object_ids: Sequence[UUID], now: datetime) -> list[WatchRow]:
        return [w for w in self.watches.values() if w.object_id in object_ids and w.expires_at > now]

    async def watches_for(self, handle_ids: Sequence[UUID]) -> list[WatchRow]:
        wanted = set(handle_ids)
        return sorted((w for w in self.watches.values() if w.handle_id in wanted), key=lambda w: w.expires_at)

    async def watches_fired(self, watch_ids: Sequence[UUID], at: datetime) -> None:
        for watch_id in watch_ids:
            if watch_id in self.watches:
                self.watches[watch_id] = replace(self.watches[watch_id], last_fired_at=at)

    async def request_refreshes(self, rows: Sequence[RefreshRow]) -> None:
        for row in rows:
            self.refreshes[(row.object_id, row.reason)] = (replace(row, lease_until=None), 0)

    async def lease_refreshes(
        self,
        owner: str,  # noqa: ARG002
        now: datetime,
        lease_until: datetime,
        *,
        limit: int,
    ) -> list[RefreshRow]:
        rank = {"high": 0, "normal": 1, "low": 2}
        free = sorted(
            (
                (row, attempts)
                for row, attempts in self.refreshes.values()
                if row.lease_until is None or row.lease_until < now
            ),
            key=lambda found: (rank.get(found[0].priority, 3), found[0].requested_at),
        )[:limit]
        leased = []
        for row, attempts in free:
            kept = replace(row, lease_until=lease_until)
            self.refreshes[(row.object_id, row.reason)] = (kept, attempts + 1)
            leased.append(kept)
        return leased

    async def refreshed(self, object_ids: Sequence[UUID]) -> list[dict[str, Any]]:
        gone = [key for key in self.refreshes if key[0] in object_ids]
        return [r for key in gone if (r := self.refreshes.pop(key)[0].reservation)]

    async def give_up_refreshes(self, now: datetime, attempts: int, *, limit: int) -> list[RefreshRow]:
        gone = [
            key
            for key, (row, tried) in self.refreshes.items()
            if tried >= attempts and row.lease_until is not None and row.lease_until < now
        ][:limit]
        return [self.refreshes.pop(key)[0] for key in gone]

    async def settle_refreshes(self, request_ids: Sequence[UUID]) -> list[RefreshRow]:
        gone = [key for key, (row, _) in self.refreshes.items() if row.request_id in request_ids]
        return [self.refreshes.pop(key)[0] for key in gone]

    async def record_scans(
        self,
        object_id: UUID,
        owner: UUID | None,  # noqa: ARG002
        scans: Mapping[str, ScanMark],
        now: datetime,  # noqa: ARG002
    ) -> None:
        for name, mark in scans.items():
            ref = content_ref(object_id, name, mark.sha256)
            self.scans.setdefault(
                ref, ScanRow(ref, object_id, name, mark.sha256[:32], mark.state, mark.reason)
            )

    async def pending_scans(self, *, limit: int) -> list[ScanRow]:
        return [row for row in self.scans.values() if row.state == "pending"][:limit]

    async def decide_scans(self, decisions: Sequence[ScanRow], by: str, at: datetime) -> None:  # noqa: ARG002
        for decision in decisions:
            current = self.scans.get(decision.content_ref)
            if current is not None and current.state == "pending":
                self.scans[decision.content_ref] = replace(
                    current, state=decision.state, reason=decision.reason
                )

    async def release_scan(self, ref: str, by: str, reason: str, at: datetime) -> ScanRow | None:  # noqa: ARG002
        current = self.scans.get(ref)
        if current is None or current.state == "clean":
            return None
        self.scans[ref] = replace(current, state="clean", reason=reason)
        return self.scans[ref]

    async def field_stamps(self, types: Sequence[str], *, limit: int) -> list[tuple[str, dict[str, Any]]]:
        wanted = set(types)
        owned = [o for o in self._objects.values() if o.key.type in wanted][-limit:]
        shared = [r for r in self.shared.values() if r.key.type in wanted][-limit:]
        found = [
            (o.key.type, {n: {s: stamps_part(ob) for s, ob in by.items()} for n, by in o.fields.items()})
            for o in owned
        ]
        return found + [(r.key.type, dict(r.meta)) for r in shared]

    async def expire_versions(
        self, before: datetime, *, limit: int, keep_objects: Collection[UUID], keep_handles: Collection[UUID]
    ) -> int:
        owned = {o for o, obj in self._objects.items() if obj.origin_handle_id in keep_handles}
        latest: dict[tuple[UUID, str], int] = {}
        for object_id, name, version in self.values:
            latest[(object_id, name)] = max(version, latest.get((object_id, name), 0))
        doomed = [
            key
            for key, value in sorted(self.values.items(), key=lambda kv: str(kv[0]))
            if value.recorded_at < before
            and key[2] < latest[(key[0], key[1])]
            and key[0] not in keep_objects
            and key[0] not in owned
        ][:limit]
        for key in doomed:
            del self.values[key]
        return len(doomed)

    async def drop_orphans(self, *, limit: int) -> int:
        known = set(self._objects) | set(self.shared)
        doomed = [key for key in self.values if key[0] not in known][:limit]
        for key in doomed:
            del self.values[key]
        timers = [t for t, row in self.timers.items() if row.object_id not in known][: limit - len(doomed)]
        for timer_id in timers:
            del self.timers[timer_id]
        return len(doomed) + len(timers)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:  # noqa: ARG002
        return []  # the objects' own rows are covered on PostgreSQL, where the orphans are found


def merged_stamps(
    stored: Mapping[str, Any],
    status: str | None,
    write: SharedWrite | None,
    expired_by: Collection[str] | None,
    now: datetime,
) -> tuple[dict[str, Any], str | None]:
    """What PostgreSQL's `write_shared` leaves of a shared object's stamps: the pass's own members over the
    stored ones, a timer the stored stamps say fired under the same firing kept fired, an expiry cleared by a
    computation after it, a new expiry, and the sequence moved."""
    stamps = dict(stored)
    if write is not None:
        planned = {k: v for k, v in write.stamps.items() if k not in ("by", "at", STAMP_SEQ, "t")}
        stamps = {k: v for k, v in stamps.items() if k not in ("v", "t", "i", "l", "r", "p")} | planned
        held = stored.get("t") or {}
        stamps["t"] = {
            name: held[name]
            if held.get(name, {}).get("id") == mark.get("id") and held.get(name, {}).get("st") == "fired"
            else mark
            for name, mark in (write.stamps.get("t") or {}).items()
        }
        at = stored.get("at")
        if write.since is not None and (at is None or datetime.fromisoformat(at) <= write.since):
            stamps.pop("by", None)
            stamps.pop("at", None)
            status = "current"
    if expired_by:
        stamps |= {"by": sorted(expired_by), "at": now.isoformat()}
        status = "expired_by_input"
    stamps[STAMP_SEQ] = stamp_seq(stored) + 1
    return stamps, status
