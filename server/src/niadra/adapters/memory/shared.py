"""In-memory hot copy of the shared objects' working set, for unit tests: the semantics of
`adapters.outbound.redis.shared`, without the server."""

from collections.abc import Mapping, Sequence
from uuid import UUID


class InMemorySharedObjects:
    def __init__(self) -> None:
        self.objects: dict[tuple[UUID, str], dict[str, bytes | str]] = {}
        self.dirty: dict[UUID, set[str]] = {}
        self.marked: set[UUID] = set()
        self.typed: dict[UUID, set[UUID]] = {}
        self.drift: dict[UUID, set[bytes]] = {}
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("cache store unreachable")

    async def push(self, space_id: UUID, items: Sequence[tuple[str, int, Mapping[str, bytes]]]) -> list[str]:
        self._check()
        out = []
        for digest, version, fields in items:
            held = self.objects.get((space_id, digest))
            if held is None or "#in" not in held:
                out.append("out_of_set")
                continue
            moved = False
            for slot, payload in sorted(fields.items()):
                current = held.get(f"v:{slot}")
                if current is None or version > int(current):
                    held[f"v:{slot}"], held[f"p:{slot}"] = str(version), payload
                    moved = True
            if moved:
                self.dirty.setdefault(space_id, set()).add(digest)
            out.append("applied" if moved else "stale_version")
        return out

    async def enter(
        self,
        space_id: UUID,
        entries: Sequence[tuple[str, UUID, Mapping[str, tuple[int, bytes]], int, bytes | None, int]],
    ) -> None:
        self._check()
        for digest, object_id, fields, _, typed, seq in entries:
            key = (space_id, digest)
            if key not in self.objects:
                held: dict[str, bytes | str] = {"#id": str(object_id)}
                if typed:
                    held["#t"], held["#tn"] = typed, str(seq)
                for slot, (version, payload) in fields.items():
                    held[f"v:{slot}"], held[f"p:{slot}"] = str(version), payload
                self.objects[key] = held
            self.objects[key]["#in"] = "1"

    async def load(
        self, space_id: UUID, digests: Sequence[str]
    ) -> list[tuple[UUID, dict[str, tuple[int, bytes]], bytes | None] | None]:
        self._check()
        found: list[tuple[UUID, dict[str, tuple[int, bytes]], bytes | None] | None] = []
        for digest in digests:
            held = self.objects.get((space_id, digest))
            if held is None or "#in" not in held:
                found.append(None)
                continue
            fields = {
                name[2:]: (int(str(held[name])), bytes(held[f"p:{name[2:]}"]))  # type: ignore[arg-type]
                for name in held
                if name.startswith("v:")
            }
            typed = held.get("#t")
            found.append((UUID(str(held["#id"])), fields, bytes(typed) if isinstance(typed, bytes) else None))
        return found

    async def take_dirty(self, space_id: UUID, limit: int) -> list[str]:
        self._check()
        pending = self.dirty.get(space_id, set())
        taken = sorted(pending)[:limit]
        pending.difference_update(taken)
        return taken

    async def mark_dirty(self, space_id: UUID, digests: Sequence[str]) -> None:
        self._check()
        self.dirty.setdefault(space_id, set()).update(digests)

    async def loaded(self, space_id: UUID) -> bool:
        self._check()
        return space_id in self.marked

    async def mark_loaded(self, space_id: UUID) -> None:
        self._check()
        self.marked.add(space_id)

    async def set_typed(self, space_id: UUID, entries: Sequence[tuple[str, bytes, int]]) -> None:
        self._check()
        for digest, typed, seq in entries:
            held = self.objects.get((space_id, digest))
            if held is not None and seq > int(str(held.get("#tn", "-1"))):
                held["#t"], held["#tn"] = typed, str(seq)

    async def mark_typed(self, space_id: UUID, object_ids: Sequence[UUID]) -> None:
        self.typed.setdefault(space_id, set()).update(object_ids)

    async def take_typed(self, space_id: UUID, limit: int) -> list[UUID]:
        pending = self.typed.get(space_id, set())
        taken = sorted(pending)[:limit]
        pending.difference_update(taken)
        return taken

    async def note_drift(self, space_id: UUID, entries: Sequence[bytes]) -> None:
        self.drift.setdefault(space_id, set()).update(entries)

    async def take_drift(self, space_id: UUID, limit: int) -> list[bytes]:
        pending = self.drift.get(space_id, set())
        taken = sorted(pending)[:limit]
        pending.difference_update(taken)
        return taken

    async def forget(self, space_id: UUID, digests: Sequence[str]) -> None:
        self._check()
        for digest in digests:
            self.objects.pop((space_id, digest), None)
