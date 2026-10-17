"""PostgreSQL implementation of `ExtractionRunRepository`.

The validated result carries customer content, so it is sealed into `result_ct`. Rows written
before revision b2c0de000002 kept the ciphertext inside the `result` JSONB as
`{"ct": <base64>, "dek_version": n}`; they are still read, and rewritten in the new shape on save.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Any, cast
from uuid import UUID

import orjson
from sqlalchemy import Row, String, Table, func, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.platform import SpaceState
from niadra.adapters.outbound.postgres.schema.write_path import ExtractionRun as RunRow
from niadra.adapters.outbound.postgres.schema.write_path import LlmSpend
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.extract.result import ExtractionResult, ExtractionRun, RunStatus
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope

TABLE = "extraction_runs"
EXTRACTOR_FINGERPRINT = "extract_extractor_fingerprint"
EXTRACTOR_CHANGED_AT = "extract_extractor_changed_at"
RUNS = cast(Table, RunRow.__table__)
SPEND = cast(Table, LlmSpend.__table__)
_COLUMNS = (
    RUNS.c.session_id,
    RUNS.c.versions_key,
    RUNS.c.model,
    RUNS.c.provider,
    RUNS.c.input_tokens,
    RUNS.c.output_tokens,
    RUNS.c.cached_tokens,
    RUNS.c.cost_micros,
    RUNS.c.status,
    RUNS.c.dropped_items,
    RUNS.c.created_at,
    RUNS.c.applied_profile_id,
    RUNS.c.applied_work,
)
_RESULT = (RUNS.c.result_ct, RUNS.c.dek_version)


class PgExtractionRunRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def save_run(self, run: ExtractionRun) -> None:
        values: dict[str, Any] = {
            "space_id": CURRENT_SPACE,
            "session_id": run.session_id,
            "versions_key": run.versions_key,
            "model": run.model,
            "provider": run.provider,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "cached_tokens": run.cached_tokens,
            "cost_micros": run.cost_micros,
            "status": run.status.value,
            "dropped_items": run.dropped_items,
            "result_ct": None,
            "dek_version": None,
            "created_at": run.created_at,
            "applied_profile_id": run.applied_profile_id,
            "applied_work": run.applied_work,
        }
        if run.result is not None:
            values["result_ct"], values["dek_version"] = await self._seal(run)
        stmt = insert(RUNS).values(values)
        added = ("input_tokens", "output_tokens", "cached_tokens", "cost_micros")
        replaced = (
            "model",
            "provider",
            "status",
            "dropped_items",
            "result_ct",
            "dek_version",
            "created_at",
            "applied_profile_id",
            "applied_work",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[RUNS.c.space_id, RUNS.c.session_id, RUNS.c.versions_key],
            set_={
                **{c: RUNS.c[c] + stmt.excluded[c] for c in added},
                **{c: stmt.excluded[c] for c in replaced},
            },
        )
        await self._db.execute(stmt)

    async def get_run(self, session_id: UUID, versions_key: str) -> ExtractionRun | None:
        rows = await self._db.execute(
            select(*_COLUMNS, *_RESULT).where(
                RUNS.c.space_id == CURRENT_SPACE,
                RUNS.c.session_id == session_id,
                RUNS.c.versions_key == versions_key,
            )
        )
        row = rows.one_or_none()
        return await self._run(row, with_result=True) if row else None

    async def mark_applied(
        self, session_id: UUID, versions_key: str, profile_id: UUID | None, work: int
    ) -> None:
        await self._db.execute(
            update(RUNS)
            .where(
                RUNS.c.space_id == CURRENT_SPACE,
                RUNS.c.session_id == session_id,
                RUNS.c.versions_key == versions_key,
            )
            .values(applied_profile_id=profile_id, applied_work=work)
        )

    async def latest_run(self, session_id: UUID) -> ExtractionRun | None:
        rows = await self._db.execute(
            select(*_COLUMNS, *_RESULT)
            .where(RUNS.c.space_id == CURRENT_SPACE, RUNS.c.session_id == session_id)
            .order_by(RUNS.c.created_at.desc(), RUNS.c.versions_key.desc())
            .limit(1)
        )
        row = rows.one_or_none()
        return await self._run(row, with_result=True) if row else None

    async def runs_page(self, after: tuple[UUID, str] | None, limit: int) -> list[ExtractionRun]:
        stmt = select(*_COLUMNS, *_RESULT).where(
            RUNS.c.space_id == CURRENT_SPACE, RUNS.c.result_ct.is_not(None)
        )
        if after is not None:
            key = tuple_(literal(after[0], PG_UUID(as_uuid=True)), literal(after[1], String))
            stmt = stmt.where(tuple_(RUNS.c.session_id, RUNS.c.versions_key) > key)
        rows = await self._db.execute(stmt.order_by(RUNS.c.session_id, RUNS.c.versions_key).limit(limit))
        return [await self._run(row, with_result=True) for row in rows]

    async def rewrite_result(self, run: ExtractionRun) -> None:
        if run.result is None:
            return
        ct, version = await self._seal(run)
        await self._db.execute(
            update(RUNS)
            .where(
                RUNS.c.space_id == CURRENT_SPACE,
                RUNS.c.session_id == run.session_id,
                RUNS.c.versions_key == run.versions_key,
            )
            .values(result_ct=ct, dek_version=version, dropped_items=run.dropped_items)
        )

    async def run_keys(self, session_ids: Sequence[UUID]) -> dict[UUID, list[tuple[str, RunStatus]]]:
        found: dict[UUID, list[tuple[str, RunStatus]]] = {}
        if not session_ids:
            return found
        rows = await self._db.execute(
            select(RUNS.c.session_id, RUNS.c.versions_key, RUNS.c.status).where(
                RUNS.c.space_id == CURRENT_SPACE, RUNS.c.session_id.in_(list(session_ids))
            )
        )
        for session_id, versions_key, status in rows:
            found.setdefault(session_id, []).append((versions_key, RunStatus(status)))
        return found

    async def extractor_state(self) -> tuple[int | None, datetime | None]:
        rows = await self._db.execute(
            select(SpaceState.key, SpaceState.value).where(
                SpaceState.key.in_([EXTRACTOR_FINGERPRINT, EXTRACTOR_CHANGED_AT])
            )
        )
        values = {key: value for key, value in rows}
        changed = values.get(EXTRACTOR_CHANGED_AT)
        return values.get(EXTRACTOR_FINGERPRINT), (
            datetime.fromtimestamp(changed, UTC) if changed is not None else None
        )

    async def set_extractor_state(self, fingerprint: int, changed_at: datetime | None) -> None:
        values = {EXTRACTOR_FINGERPRINT: fingerprint}
        if changed_at is not None:
            values[EXTRACTOR_CHANGED_AT] = int(changed_at.timestamp())
        for key, value in values.items():
            stmt = insert(SpaceState).values(key=key, value=value)
            await self._db.execute(
                stmt.on_conflict_do_update(
                    index_elements=[SpaceState.key], set_={"value": value, "updated_at": func.now()}
                )
            )

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if not scope.session_ids:
            return []
        doomed = (
            select(RUNS.c.session_id, RUNS.c.versions_key)
            .where(
                RUNS.c.space_id == CURRENT_SPACE,
                RUNS.c.session_id.in_(scope.session_ids),
                RUNS.c.result_ct.is_not(None),
            )
            .order_by(RUNS.c.session_id, RUNS.c.versions_key)
            .limit(limit)
        )
        stmt = (
            update(RUNS)
            .where(
                RUNS.c.space_id == CURRENT_SPACE,
                tuple_(RUNS.c.session_id, RUNS.c.versions_key).in_(doomed),
            )
            .values(result_ct=None, dek_version=None)
            .returning(RUNS.c.session_id, RUNS.c.versions_key)
        )
        gone = sorted((await self._db.execute(stmt)).all())
        return [
            ErasedRow("extraction_runs", {"session_id": str(r.session_id), "versions_key": r.versions_key})
            for r in gone
        ]

    async def spent(self, day: date, purpose: str) -> int:
        spent = await self._db.scalar(
            select(SPEND.c.cost_micros).where(
                SPEND.c.space_id == CURRENT_SPACE, SPEND.c.day == _midnight(day), SPEND.c.purpose == purpose
            )
        )
        return int(spent or 0)

    async def add_spend(self, day: date, purpose: str, cost_micros: int, *, calls: int = 1) -> None:
        stmt = insert(SPEND).values(
            space_id=CURRENT_SPACE, day=_midnight(day), purpose=purpose, cost_micros=cost_micros, calls=calls
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[SPEND.c.space_id, SPEND.c.day, SPEND.c.purpose],
                set_={
                    "cost_micros": SPEND.c.cost_micros + stmt.excluded.cost_micros,
                    "calls": SPEND.c.calls + stmt.excluded.calls,
                },
            )
        )

    async def reserve_spend(self, day: date, purpose: str, cost_micros: int, cap_micros: int) -> bool:
        # The upsert locks the day's row, and under READ COMMITTED its WHERE is checked again against the
        # version a concurrent reservation committed: two reservations never both see room that one took.
        stmt = insert(SPEND).values(
            space_id=CURRENT_SPACE, day=_midnight(day), purpose=purpose, cost_micros=cost_micros, calls=0
        )
        held = stmt.on_conflict_do_update(
            index_elements=[SPEND.c.space_id, SPEND.c.day, SPEND.c.purpose],
            set_={"cost_micros": SPEND.c.cost_micros + stmt.excluded.cost_micros},
            where=SPEND.c.cost_micros < cap_micros,
        ).returning(SPEND.c.cost_micros)
        return (await self._db.execute(held)).scalar_one_or_none() is not None

    async def mark_budget_notice(self, day: date, purpose: str) -> bool:
        # A row of the spend table under `<purpose>:notice`, never counted as spend: the table is the
        # budget's own, and the insert races safely between workers.
        stmt = (
            insert(SPEND)
            .values(
                space_id=CURRENT_SPACE,
                day=_midnight(day),
                purpose=f"{purpose}:notice",
                cost_micros=0,
                calls=0,
            )
            .on_conflict_do_nothing()
            .returning(SPEND.c.purpose)
        )
        return (await self._db.execute(stmt)).scalar_one_or_none() is not None

    async def _seal(self, run: ExtractionRun) -> tuple[bytes, int]:
        assert run.result is not None
        return await self._s().seal(
            orjson.dumps(run.result.to_dict()),
            table=TABLE,
            row=_row_id(run.session_id, run.versions_key),
            column="result_ct",
        )

    async def _run(self, row: Row[Any], *, with_result: bool) -> ExtractionRun:
        result = None
        if with_result:
            plain = await self._open_result(row)
            result = ExtractionResult.from_dict(orjson.loads(plain)) if plain is not None else None
        return ExtractionRun(
            session_id=row.session_id,
            versions_key=row.versions_key,
            status=RunStatus(row.status),
            created_at=row.created_at,
            model=row.model,
            provider=row.provider,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cached_tokens=row.cached_tokens,
            cost_micros=row.cost_micros,
            dropped_items=row.dropped_items,
            result=result,
            applied_profile_id=row.applied_profile_id,
            applied_work=row.applied_work,
        )

    async def _open_result(self, row: Row[Any]) -> bytes | None:
        if row.result_ct is None:
            return None
        return await self._s().open_bytes(
            row.result_ct,
            row.dek_version,
            table=TABLE,
            row=_row_id(row.session_id, row.versions_key),
            column="result_ct",
        )

    def _s(self) -> Sealer:
        assert self._sealer is not None, "extraction runs are read and written inside a space transaction"
        return self._sealer


def _row_id(session_id: UUID, versions_key: str) -> str:
    return f"{session_id}:{versions_key}"


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=UTC)
