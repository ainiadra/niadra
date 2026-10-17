"""PostgreSQL implementation of `ServeRepository`."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.privacy_rows import delete_where
from niadra.adapters.outbound.postgres.schema.read_path import AgentWatermark as WatermarkRow
from niadra.adapters.outbound.postgres.schema.read_path import ConversationPin as PinRow
from niadra.adapters.outbound.postgres.schema.read_path import ExperimentAssignment as AssignmentRow
from niadra.adapters.outbound.postgres.schema.read_path import ReadInvalidation as InvalidationRow
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.serve.records import AgentWatermark, ConversationPin

PINS = cast(Table, PinRow.__table__)
MARKS = cast(Table, WatermarkRow.__table__)
ASSIGNMENTS = cast(Table, AssignmentRow.__table__)
INVALIDATIONS = cast(Table, InvalidationRow.__table__)


class PgServeRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def get_pin(self, conversation_id: str, view: str) -> ConversationPin | None:
        row = (
            await self._db.execute(
                select(PinRow).where(PinRow.conversation_id == conversation_id, PinRow.view == view)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return ConversationPin(
            row.conversation_id,
            row.view,
            row.pack_key,
            row.pack_hash,
            row.generation,
            row.pinned_at,
            row.expires_at,
        )

    async def get_watermark(
        self, source_id: UUID, profile_id: UUID, thread: str = ""
    ) -> AgentWatermark | None:
        row = (
            await self._db.execute(
                select(WatermarkRow).where(
                    WatermarkRow.source_id == source_id,
                    WatermarkRow.profile_id == profile_id,
                    WatermarkRow.thread == thread,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return AgentWatermark(
            row.source_id, row.profile_id, row.manifest_hash, row.seen_at, row.thread, row.generation
        )

    async def latest_watermark(self, source_id: UUID, profile_id: UUID) -> AgentWatermark | None:
        row = (
            await self._db.execute(
                select(WatermarkRow)
                .where(WatermarkRow.source_id == source_id, WatermarkRow.profile_id == profile_id)
                .order_by(WatermarkRow.generation.desc(), WatermarkRow.seen_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return AgentWatermark(
            row.source_id, row.profile_id, row.manifest_hash, row.seen_at, row.thread, row.generation
        )

    async def project_pins(self, pins: Sequence[ConversationPin]) -> None:
        if not pins:
            return
        stmt = insert(PINS).values(
            [
                {
                    "space_id": CURRENT_SPACE,
                    "conversation_id": p.conversation_id,
                    "view": p.view,
                    "pack_key": p.pack_key,
                    "pack_hash": p.pack_hash,
                    "generation": p.generation,
                    "pinned_at": p.pinned_at,
                    "expires_at": p.expires_at,
                }
                for p in pins
            ]
        )
        new, old = stmt.excluded, PINS.c
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[old.space_id, old.conversation_id, old.view],
                set_={
                    "pack_key": new.pack_key,
                    "pack_hash": new.pack_hash,
                    "generation": new.generation,
                    "pinned_at": new.pinned_at,
                    "expires_at": func.greatest(old.expires_at, new.expires_at),
                },
                where=tuple_(old.generation, old.pinned_at) <= tuple_(new.generation, new.pinned_at),
            )
        )

    async def project_watermarks(self, marks: Sequence[AgentWatermark]) -> None:
        if not marks:
            return
        stmt = insert(MARKS).values(
            [
                {
                    "space_id": CURRENT_SPACE,
                    "source_id": m.source_id,
                    "profile_id": m.profile_id,
                    "thread": m.thread,
                    "manifest_hash": m.manifest_hash,
                    "seen_at": m.seen_at,
                    "generation": m.generation,
                }
                for m in marks
            ]
        )
        new, old = stmt.excluded, MARKS.c
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[old.space_id, old.source_id, old.profile_id, old.thread],
                set_={
                    "manifest_hash": new.manifest_hash,
                    "seen_at": new.seen_at,
                    "generation": new.generation,
                },
                where=tuple_(old.generation, old.seen_at) <= tuple_(new.generation, new.seen_at),
            )
        )

    async def get_assignments(self, profile_id: UUID) -> dict[str, str]:
        stmt = select(ASSIGNMENTS.c.experiment, ASSIGNMENTS.c.grp).where(
            ASSIGNMENTS.c.space_id == CURRENT_SPACE, ASSIGNMENTS.c.profile_id == profile_id
        )
        return {row.experiment: row.grp for row in await self._db.execute(stmt)}

    async def project_assignment(
        self,
        *,
        experiment: str,
        profile_id: UUID,
        group: str,
        assigned_at: datetime,
        kind: str = "memory",
    ) -> None:
        stmt = insert(ASSIGNMENTS).values(
            space_id=CURRENT_SPACE,
            experiment=experiment,
            profile_id=profile_id,
            grp=group,
            assigned_at=assigned_at,
            kind=kind,
        )
        await self._db.execute(stmt.on_conflict_do_nothing())

    async def invalidations_since(self, since: datetime) -> list[dict[str, Any]]:
        rows = INVALIDATIONS.c
        stmt = select(rows.id, rows.handle_ids, rows.profile_ids, rows.threads).where(
            rows.space_id == CURRENT_SPACE, rows.created_at > since
        )
        return [dict(row._mapping) for row in await self._db.execute(stmt)]

    async def purge_invalidations(self, before: datetime) -> int:
        stmt = delete(INVALIDATIONS).where(
            INVALIDATIONS.c.space_id == CURRENT_SPACE, INVALIDATIONS.c.created_at < before
        )
        return int((await self._db.execute(stmt)).rowcount or 0)  # type: ignore[attr-defined]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            return []
        pins: list[Any] = []
        if scope.profile_id is not None:
            pins.append(PINS.c.pack_key.startswith(f"{scope.profile_id}/"))
        if scope.kind is TargetKind.CONVERSATION and scope.conversation_id:
            pins.append(PINS.c.conversation_id == scope.conversation_id)
        erased = (
            await delete_where(self._db, PINS, ("conversation_id", "view"), [or_(*pins)], limit)
            if pins
            else []
        )
        if scope.kind is not TargetKind.CONVERSATION and scope.profile_id is not None:
            erased += await delete_where(
                self._db,
                MARKS,
                ("source_id", "profile_id", "thread"),
                [MARKS.c.profile_id == scope.profile_id],
                limit - len(erased),
            )
            erased += await delete_where(
                self._db,
                ASSIGNMENTS,
                ("experiment", "profile_id"),
                [ASSIGNMENTS.c.profile_id == scope.profile_id],
                limit - len(erased),
            )
        return erased
