"""PostgreSQL implementation of `AgentStateRepository`: the durable copy of the agents' working state.

The body is sealed whole (AAD `agent_state:<scope key>/<source>/<agent>:body_ct`); the scope is only its keyed
digest. The batch writer's statement finds the customer's handle by the keyed digest the identity module keeps
for it (`handles.value_hmac`), reading that table and nothing else of it.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import orjson
from sqlalchemy import Row, Table, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.mentions import with_entries
from niadra.adapters.outbound.postgres.privacy_rows import delete_where
from niadra.adapters.outbound.postgres.schema.state import AgentStateRow
from niadra.adapters.outbound.postgres.schema.write_path import HandleRow
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.privacy.mentions import MentionKey, MentionStore
from niadra.domain.state.agent_state import SCOPE_PURPOSE, DurableState, PackagedState, scope_text

_S = cast(Table, AgentStateRow.__table__)
_H = cast(Table, HandleRow.__table__)
_KEY = ("scope_key", "source_id", "agent")


class PgAgentStateRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    @property
    def _seal(self) -> Sealer:
        assert self._sealer is not None, "agent state repositories run in a space-scoped transaction"
        return self._sealer

    async def get(self, scope_key: bytes, source_id: UUID, agent: str) -> DurableState | None:
        row = (
            await self._db.execute(
                select(_S).where(
                    _S.c.space_id == CURRENT_SPACE,
                    _S.c.scope_key == scope_key,
                    _S.c.source_id == source_id,
                    _S.c.agent == agent,
                )
            )
        ).first()
        return _state(row) if row else None

    async def upsert(self, states: Sequence[DurableState]) -> int:
        if not states:
            return 0
        rows = [
            {
                "space_id": CURRENT_SPACE,
                "scope_key": s.scope_key,
                "source_id": s.source_id,
                "agent": s.agent,
                "scope_kind": s.scope_kind,
                "body_ct": s.body_ct,
                "dek_version": s.dek_version,
                "body_bytes": s.body_bytes,
                "schema_version": s.schema_version,
                "version": s.version,
                "updated_at": s.updated_at,
                "valid_until": s.valid_until,
                "subject_key": s.subject_key,
                "origin_handle_id": _handle_of(s.subject_key),
            }
            for s in _newest(states)
        ]
        named = [(MentionKey(MentionStore.STATE, s.row), s.named) for s in _newest(states)]
        stmt = insert(_S).values(rows)
        excluded = stmt.excluded
        moved = await self._db.execute(
            with_entries(
                stmt.on_conflict_do_update(
                    index_elements=["space_id", "scope_key", "source_id", "agent"],
                    set_={
                        "body_ct": excluded.body_ct,
                        "dek_version": excluded.dek_version,
                        "body_bytes": excluded.body_bytes,
                        "schema_version": excluded.schema_version,
                        "version": excluded.version,
                        "updated_at": excluded.updated_at,
                        "valid_until": excluded.valid_until,
                        "subject_key": func.coalesce(excluded.subject_key, _S.c.subject_key),
                        "origin_handle_id": func.coalesce(excluded.origin_handle_id, _S.c.origin_handle_id),
                    },
                    where=_S.c.version < excluded.version,
                ).returning(_S.c.version),
                named,
            )
        )
        return len(moved.all())

    async def states_page(self, after: tuple[bytes, UUID, str] | None, limit: int) -> list[DurableState]:
        stmt = select(_S).where(_S.c.space_id == CURRENT_SPACE)
        if after is not None:
            key = tuple_(*(literal(v) for v in after))
            stmt = stmt.where(tuple_(_S.c.scope_key, _S.c.source_id, _S.c.agent) > key)
        rows = await self._db.execute(stmt.order_by(_S.c.scope_key, _S.c.source_id, _S.c.agent).limit(limit))
        return [_state(r) for r in rows]

    async def for_handles(self, handle_ids: Sequence[UUID]) -> list[DurableState]:
        if not handle_ids:
            return []
        rows = await self._db.execute(
            select(_S)
            .where(_S.c.space_id == CURRENT_SPACE, _S.c.origin_handle_id.in_(list(handle_ids)))
            .order_by(_S.c.updated_at.desc())
        )
        return [_state(r) for r in rows]

    async def package_for(self, handle_ids: Sequence[UUID]) -> list[PackagedState]:
        packaged = []
        for state in await self.for_handles(handle_ids):
            row = f"{state.scope_key.hex()}/{state.source_id}/{state.agent}"
            document = orjson.loads(
                await self._seal.open_bytes(
                    state.body_ct, state.dek_version, table="agent_state", row=row, column="body_ct"
                )
            )
            packaged.append(
                PackagedState(
                    state.scope_kind,
                    state.agent,
                    state.version,
                    state.updated_at,
                    state.valid_until,
                    dict(document["fields"]),
                )
            )
        return packaged

    async def older_than(
        self, cutoff: datetime, *, limit: int, keep: Collection[UUID] = ()
    ) -> list[DurableState]:
        rows = await self._db.execute(
            select(_S)
            .where(_S.c.space_id == CURRENT_SPACE, _S.c.updated_at < cutoff, *_not_held(keep))
            .order_by(_S.c.updated_at)
            .limit(limit)
        )
        return [_state(r) for r in rows]

    async def replace_body(self, state: DurableState) -> bool:
        done = await self._db.execute(
            update(_S)
            .where(
                _S.c.space_id == CURRENT_SPACE,
                _S.c.scope_key == state.scope_key,
                _S.c.source_id == state.source_id,
                _S.c.agent == state.agent,
                _S.c.version == state.version,
            )
            .values(body_ct=state.body_ct, dek_version=state.dek_version, body_bytes=state.body_bytes)
            .returning(_S.c.version)
        )
        return done.first() is not None

    async def resolve_owners(self, *, limit: int) -> int:
        owner = _handle_of_row()
        page = (
            select(*[_S.c[k] for k in _KEY])
            .where(
                _S.c.space_id == CURRENT_SPACE, _S.c.origin_handle_id.is_(None), _S.c.subject_key.is_not(None)
            )
            .where(owner.is_not(None))
            .limit(limit)
        )
        resolved = await self._db.execute(
            update(_S)
            .where(_S.c.space_id == CURRENT_SPACE, tuple_(*[_S.c[k] for k in _KEY]).in_(page))
            .values(origin_handle_id=owner)
            .returning(_S.c.version)
        )
        return len(resolved.all())

    async def expire(self, now: datetime, *, limit: int, keep: Collection[UUID] = ()) -> int:
        return len(await delete_where(self._db, _S, _KEY, [_S.c.valid_until <= now, *_not_held(keep)], limit))

    async def for_scopes(self, scope_keys: Sequence[bytes]) -> list[DurableState]:
        if not scope_keys:
            return []
        rows = await self._db.execute(
            select(_S).where(_S.c.space_id == CURRENT_SPACE, _S.c.scope_key.in_(list(scope_keys)))
        )
        return [_state(r) for r in rows]

    async def erase_marked(self, marks: Sequence[bytes], *, limit: int) -> int:
        if not marks:
            return 0
        found = or_(_S.c.subject_key.in_(list(marks)), _S.c.scope_key.in_(list(marks)))
        return len(await delete_where(self._db, _S, _KEY, [found], limit))

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            return []
        where: list[Any] = []
        if scope.kind is TargetKind.CONVERSATION and scope.conversation_id:
            key = await self._seal.hmac(SCOPE_PURPOSE, scope_text("conversation", scope.conversation_id))
            where.append(_S.c.scope_key == key)
        if scope.kind is not TargetKind.CONVERSATION and scope.handle_ids:
            where.append(_S.c.origin_handle_id.in_(list(scope.handle_ids)))
        if not where:
            return []
        return await delete_where(self._db, _S, _KEY, [or_(*where)], limit)


def _not_held(keep: Collection[UUID]) -> list[Any]:
    if not keep:
        return []
    return [or_(_S.c.origin_handle_id.is_(None), _S.c.origin_handle_id.not_in(list(keep)))]


def _handle_of(subject_key: bytes | None) -> Any:
    if subject_key is None:
        return None
    return (
        select(_H.c.handle_id)
        .where(_H.c.space_id == CURRENT_SPACE, _H.c.value_hmac == subject_key)
        .order_by(_H.c.handle_id)
        .limit(1)
        .scalar_subquery()
    )


def _handle_of_row() -> Any:
    """The customer's handle of the row's own `subject_key`, as the batch writer finds it."""
    return (
        select(_H.c.handle_id)
        .where(_H.c.space_id == CURRENT_SPACE, _H.c.value_hmac == _S.c.subject_key)
        .order_by(_H.c.handle_id)
        .limit(1)
        .scalar_subquery()
    )


def _newest(states: Sequence[DurableState]) -> list[DurableState]:
    """One row per state in a statement: the greatest version a batch carries."""
    newest: dict[tuple[bytes, UUID, str], DurableState] = {}
    for s in states:
        key = (s.scope_key, s.source_id, s.agent)
        if key not in newest or newest[key].version < s.version:
            newest[key] = s
    return list(newest.values())


def _state(r: Row[Any]) -> DurableState:
    return DurableState(
        space_id=r.space_id,
        scope_key=r.scope_key,
        scope_kind=r.scope_kind,
        source_id=r.source_id,
        agent=r.agent,
        version=r.version,
        body_ct=r.body_ct,
        dek_version=r.dek_version,
        body_bytes=r.body_bytes,
        updated_at=r.updated_at,
        valid_until=r.valid_until,
        schema_version=r.schema_version,
        subject_key=r.subject_key,
        origin_handle_id=r.origin_handle_id,
    )
