"""In-memory `AgentStateRepository` for unit tests. Without the space's keys it finds no handle by the
customer's digest: a state keeps the `origin_handle_id` it was given."""

import dataclasses
from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from niadra.adapters.memory.mentions import index_in, mentions_of
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.privacy.mentions import MentionKey, MentionStore
from niadra.domain.state.agent_state import DurableState, PackagedState

Key = tuple[UUID | None, bytes, UUID, str]


class InMemoryAgentStateRepository:
    def __init__(self, store: MemoryStore, space_id: UUID | None) -> None:
        self._space_id = space_id
        self.rows: dict[Key, DurableState] = store.module("agent_state").setdefault("rows", {})
        self._mentions = mentions_of(store)

    def _key(self, scope_key: bytes, source_id: UUID, agent: str) -> Key:
        return (self._space_id, scope_key, source_id, agent)

    async def get(self, scope_key: bytes, source_id: UUID, agent: str) -> DurableState | None:
        return self.rows.get(self._key(scope_key, source_id, agent))

    async def upsert(self, states: Sequence[DurableState]) -> int:
        moved = 0
        for state in states:
            key = self._key(state.scope_key, state.source_id, state.agent)
            current = self.rows.get(key)
            if current is None or current.version < state.version:
                origin = state.origin_handle_id or (current.origin_handle_id if current else None)
                self.rows[key] = dataclasses.replace(state, origin_handle_id=origin)
                moved += 1
        named = [(MentionKey(MentionStore.STATE, s.row), s.named) for s in states]
        index_in(self._mentions["rows"], named, replace=False)
        return moved

    async def states_page(self, after: tuple[bytes, UUID, str] | None, limit: int) -> list[DurableState]:
        mine = sorted(
            (k[1:], s)
            for k, s in self.rows.items()
            if k[0] == self._space_id and (after is None or k[1:] > after)
        )
        return [s for _, s in mine[:limit]]

    async def for_handles(self, handle_ids: Sequence[UUID]) -> list[DurableState]:
        wanted = set(handle_ids)
        found = [
            s
            for (space, *_), s in self.rows.items()
            if space == self._space_id and s.origin_handle_id in wanted
        ]
        return sorted(found, key=lambda s: s.updated_at, reverse=True)

    async def package_for(self, handle_ids: Sequence[UUID]) -> list[PackagedState]:  # noqa: ARG002
        return []  # the bodies are sealed with the space's keys: covered on PostgreSQL

    async def resolve_owners(self, *, limit: int) -> int:  # noqa: ARG002
        return 0  # the keyed digests of handles live in the identity module's repository, on PostgreSQL

    async def older_than(
        self, cutoff: datetime, *, limit: int, keep: Collection[UUID] = ()
    ) -> list[DurableState]:
        found = [
            s
            for (space, *_), s in self.rows.items()
            if space == self._space_id and s.updated_at < cutoff and s.origin_handle_id not in keep
        ]
        return sorted(found, key=lambda s: s.updated_at)[:limit]

    async def replace_body(self, state: DurableState) -> bool:
        key = self._key(state.scope_key, state.source_id, state.agent)
        current = self.rows.get(key)
        if current is None or current.version != state.version:
            return False
        self.rows[key] = dataclasses.replace(
            current, body_ct=state.body_ct, dek_version=state.dek_version, body_bytes=state.body_bytes
        )
        return True

    async def expire(self, now: datetime, *, limit: int, keep: Collection[UUID] = ()) -> int:
        doomed = [
            k
            for k, s in self.rows.items()
            if k[0] == self._space_id
            and s.valid_until is not None
            and s.valid_until <= now
            and s.origin_handle_id not in keep
        ][:limit]
        for key in doomed:
            del self.rows[key]
        return len(doomed)

    async def for_scopes(self, scope_keys: Sequence[bytes]) -> list[DurableState]:
        wanted = set(scope_keys)
        return [s for k, s in self.rows.items() if k[0] == self._space_id and s.scope_key in wanted]

    async def erase_marked(self, marks: Sequence[bytes], *, limit: int) -> int:
        wanted = set(marks)
        doomed = [
            k
            for k, s in self.rows.items()
            if k[0] == self._space_id and (s.subject_key in wanted or s.scope_key in wanted)
        ][:limit]
        for key in doomed:
            del self.rows[key]
        return len(doomed)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids or scope.kind is TargetKind.CONVERSATION:
            return []  # a conversation's scope key needs the space's keys: covered on PostgreSQL
        handles = set(scope.handle_ids)
        doomed = [k for k, s in self.rows.items() if k[0] == self._space_id and s.origin_handle_id in handles]
        erased = []
        for key in doomed[:limit]:
            del self.rows[key]
            erased.append(
                ErasedRow(
                    "agent_state", {"scope_key": key[1].hex(), "source_id": str(key[2]), "agent": key[3]}
                )
            )
        return erased
