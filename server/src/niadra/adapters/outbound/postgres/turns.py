"""The `turns` module on PostgreSQL (6.7): the intake of `POST /v1/turns`, the kept tier's index and the
day's totals, each write in one statement (24.6).

`PgTurnIntakeLog.accept` is the acknowledgement's only statement: `turn_accept` stores the intake and its
outbox copy in the space's own pool, autocommit, and a second call with the same intake id stores nothing
more. A worker then takes up to 50 intakes and writes what they add up to, with their removal, in one
statement of data-modifying CTEs; the space pin and the take are the other two.
"""

import typing
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

import orjson
from sqlalchemy import DateTime, Table, Text, delete, exc, func, literal, or_, select, text, tuple_, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.engines import raise_unreachable
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.schema.platform import Outbox
from niadra.adapters.outbound.postgres.schema.turns import (
    AgentBuild,
    ConfigHistory,
    DataIssue,
    Scenario,
    ScenarioRun,
    TurnDaily,
    TurnIntake,
    TurnKept,
    TurnTombstone,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.errors import Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.tasks import CATALOG, TaskName
from niadra.domain.turns.issues import MAX_REFS, DataIssueRow, IssueQuery, NewDataIssue
from niadra.domain.turns.records import (
    AppliedTurns,
    BuildSeen,
    DailyTotals,
    KeptTurn,
    NewTurnIntake,
    ScenarioChange,
    ScenarioRow,
    ScenarioRunRow,
    TakenIntake,
    TurnIntakeRow,
    TurnQuery,
)
from niadra.domain.turns.replay import ConfigSeen
from niadra.domain.turns.storage import KeptBy

# The space's apply lock: one worker at a time adds a space's intakes up, so a group is never counted twice.
APPLY_LOCK = 0x4E49_4144_5254_5531
_SPACE = "CAST(current_setting('niadra.space_id') AS uuid)"

ACCEPT = text(
    "SELECT turn_accept(CAST(:space AS uuid), CAST(:intake AS uuid), CAST(:received AS timestamptz),"
    " CAST(:source AS uuid), CAST(:turns AS integer), CAST(:body AS bytea), CAST(:dek AS integer),"
    " CAST(:queue AS text))"
)
TAKE = text(
    "WITH lock AS MATERIALIZED (SELECT pg_try_advisory_xact_lock(CAST(:lock AS bigint)) AS held),"
    " fenced AS MATERIALIZED (SELECT coalesce(array_agg(decode(substr(mark, 14), 'hex')), '{}'::bytea[])"
    "  AS hashes"
    f"  FROM turn_tombstones WHERE space_id = {_SPACE} AND mark LIKE 'conversation:%')"
    " SELECT lock.held, fenced.hashes, t.intake_id, t.received_at, t.source_id, t.body_ct, t.dek_version,"
    " t.attempts"
    " FROM lock CROSS JOIN fenced LEFT JOIN LATERAL ("
    "   SELECT intake_id, received_at, source_id, body_ct, dek_version, attempts, seq FROM turn_intake"
    f"  WHERE space_id = {_SPACE} AND failed_at IS NULL ORDER BY seq LIMIT :limit"
    " ) AS t ON lock.held ORDER BY t.seq"
)
_COUNTERS = (
    "turns",
    "incomplete",
    "tool_calls",
    "model_calls",
    "errors",
    "synthetic",
    "cache_hits",
    "tokens_in",
    "tokens_cached",
    "tokens_out",
    "cost_micros",
    "constraints_sent",
    "results_checked",
    "violations",
    "unverifiable",
    "relaxed_declared",
    "relaxed_suspected",
    "effects",
    "items_presented",
)
_KEPT_COLUMNS = (
    "turn_id text, conversation_hmac text, source_id uuid, agent text, kind text, started_at timestamptz,"
    " fidelity text, completeness text, content_mode text, replayable boolean, build_hash text,"
    " flags text[], kept_by text, kept_at timestamptz, kept_until timestamptz"
)
_KEPT_SELECT = (
    f"SELECT {_SPACE}, k.turn_id, decode(k.conversation_hmac, 'hex'), k.source_id, k.agent, k.kind,"
    " k.started_at, k.fidelity, k.completeness, k.content_mode, k.replayable, decode(k.build_hash, 'hex'),"
    " k.flags, k.kept_by, k.kept_at, k.kept_until"
    f" FROM jsonb_to_recordset(CAST(:kept AS jsonb)) AS k({_KEPT_COLUMNS})"
)
_KEPT_INSERT = (
    "INSERT INTO turns_kept (space_id, turn_id, conversation_hmac, source_id, agent, kind, started_at,"
    " fidelity, completeness, content_mode, replayable, build_hash, flags, kept_by, kept_at, kept_until) "
)
# Adds the group's counts to the day's rows; two rows of one key never meet in one statement.
APPLY = text(
    "WITH daily AS ("
    "  INSERT INTO turn_daily (space_id, day, source_id, agent, build_hash, "
    + ", ".join(_COUNTERS)
    + ", latency_buckets, claims)"
    f"  SELECT {_SPACE}, d.day, d.source_id, d.agent, decode(d.build_hash, 'hex'), "
    + ", ".join(f"d.{c}" for c in _COUNTERS)
    + ", d.latency_buckets, d.claims"
    "  FROM jsonb_to_recordset(CAST(:daily AS jsonb)) AS d(day date, source_id uuid, agent text,"
    "   build_hash text, "
    + ", ".join(f"{c} bigint" for c in _COUNTERS)
    + ", latency_buckets bigint[], claims jsonb)"
    "  ON CONFLICT (space_id, day, source_id, agent, build_hash) DO UPDATE SET "
    + ", ".join(f"{c} = turn_daily.{c} + excluded.{c}" for c in _COUNTERS)
    + ", latency_buckets = ARRAY(SELECT coalesce(a, 0) + coalesce(b, 0)"
    "     FROM unnest(turn_daily.latency_buckets, excluded.latency_buckets) WITH ORDINALITY AS u(a, b, i)"
    "     ORDER BY i),"
    "   claims = (SELECT coalesce(jsonb_object_agg(k, n), '{}'::jsonb) FROM ("
    "     SELECT k, sum(v::bigint) AS n FROM ("
    "       SELECT key AS k, value AS v FROM jsonb_each_text(turn_daily.claims)"
    "       UNION ALL SELECT key, value FROM jsonb_each_text(excluded.claims)) AS pairs GROUP BY k) AS sums)"
    "), builds AS ("
    "  INSERT INTO agent_builds (space_id, build_hash, source_id, agent, pins, sdk, adapter, first_seen_at,"
    "   last_seen_at)"
    f"  SELECT {_SPACE}, decode(b.build_hash, 'hex'), b.source_id, b.agent, b.pins, b.sdk, b.adapter,"
    "   b.at, b.at"
    "  FROM jsonb_to_recordset(CAST(:builds AS jsonb)) AS b(build_hash text, source_id uuid, agent text,"
    "   pins jsonb, sdk text, adapter text, at timestamptz)"
    "  ON CONFLICT (space_id, build_hash) DO UPDATE SET"
    "   first_seen_at = least(agent_builds.first_seen_at, excluded.first_seen_at),"
    "   last_seen_at = greatest(agent_builds.last_seen_at, excluded.last_seen_at)"
    "), kept AS ("
    f"  {_KEPT_INSERT}{_KEPT_SELECT} ON CONFLICT DO NOTHING"
    "), taken AS ("
    f"  DELETE FROM turn_intake WHERE space_id = {_SPACE} AND intake_id = ANY(CAST(:ids AS uuid[]))"
    "), relayed AS ("
    "  DELETE FROM outbox WHERE id = ANY(CAST(:ids AS uuid[]))"
    "), flagged AS ("
    "  INSERT INTO outbox (id, space_id, task, queue, payload)"
    f"  SELECT CAST(:flag_id AS uuid), {_SPACE}, :flag_task, :flag_queue, CAST(:flagged AS jsonb)"
    "  WHERE CAST(:flagged AS jsonb) IS NOT NULL"
    "), scanned AS ("
    "  INSERT INTO outbox (id, space_id, task, queue, payload)"
    f"  SELECT s.id, {_SPACE}, :scan_task, :scan_queue, s.payload"
    "  FROM jsonb_to_recordset(CAST(:scans AS jsonb)) AS s(id uuid, payload jsonb)"
    "), named AS ("
    "  INSERT INTO object_mentions (space_id, key, tokens)"
    f"  SELECT {_SPACE}, m.key, ARRAY(SELECT decode(t, 'hex') FROM jsonb_array_elements_text(m.tokens) AS t)"
    "  FROM jsonb_to_recordset(CAST(:mentions AS jsonb)) AS m(key text, tokens jsonb)"
    "  ON CONFLICT (space_id, key) DO UPDATE SET"
    "   tokens = ARRAY(SELECT DISTINCT t FROM unnest(object_mentions.tokens || excluded.tokens) AS t)"
    ") SELECT 1"
)
# The open issue of the fingerprint counts one more and takes the new references after its own, up to 50; a
# new row is the one whose `xmax` is still 0, and only that one gets the task that announces it.
OPEN_ISSUE = text(
    "WITH up AS ("
    "  INSERT INTO data_issues (space_id, issue_id, kind, fingerprint, object_type, field, source, refs,"
    "   occurrences, opened_at, last_seen_at)"
    f"  VALUES ({_SPACE}, CAST(:issue_id AS uuid), :kind, :fingerprint, :object_type, :field, :source,"
    "   CAST(:refs AS jsonb), 1, :at, :at)"
    "  ON CONFLICT (space_id, fingerprint) WHERE acknowledged_at IS NULL DO UPDATE SET"
    "   occurrences = data_issues.occurrences + 1,"
    "   last_seen_at = greatest(data_issues.last_seen_at, excluded.last_seen_at),"
    f"   refs = CASE WHEN jsonb_array_length(data_issues.refs) >= {MAX_REFS} THEN data_issues.refs ELSE ("
    "     SELECT coalesce(jsonb_agg(m.v ORDER BY m.o), '[]'::jsonb) FROM ("
    "       SELECT v, min(o) AS o FROM ("
    "         SELECT v, o FROM jsonb_array_elements(data_issues.refs) WITH ORDINALITY AS a(v, o)"
    "         UNION ALL"
    "         SELECT v, o + 1000 FROM jsonb_array_elements(excluded.refs) WITH ORDINALITY AS b(v, o)"
    f"       ) AS seen GROUP BY v ORDER BY min(o) LIMIT {MAX_REFS}"
    "     ) AS m) END"
    "  RETURNING issue_id, xmax = 0 AS opened"
    "), told AS ("
    "  INSERT INTO outbox (id, space_id, task, queue, payload)"
    f"  SELECT CAST(:task_id AS uuid), {_SPACE}, :task, :queue, jsonb_build_object('issue_id', up.issue_id)"
    "  FROM up WHERE up.opened AND CAST(:tell AS boolean)"
    ") SELECT issue_id FROM up"
)
# A section enters the history only when its digest is not the last one recorded for it.
RECORD_CONFIG = text(
    "INSERT INTO config_history (space_id, section, version, applied_at, digest)"
    f" SELECT {_SPACE}, s.section, :version, :at, decode(s.digest, 'hex')"
    " FROM jsonb_to_recordset(CAST(:sections AS jsonb)) AS s(section text, digest text)"
    " WHERE decode(s.digest, 'hex') IS DISTINCT FROM ("
    "   SELECT h.digest FROM config_history h"
    f"  WHERE h.space_id = {_SPACE} AND h.section = s.section ORDER BY h.version DESC LIMIT 1)"
    " ON CONFLICT DO NOTHING"
)
KEEP = text(f"{_KEPT_INSERT}{_KEPT_SELECT} ON CONFLICT DO NOTHING RETURNING turn_id")
# A turn a scenario takes stays at least as long as it was kept already; null (kept until a retention is set)
# outlasts any date.
KEEP_FOR_SCENARIO = text(
    f"{_KEPT_INSERT}{_KEPT_SELECT} ON CONFLICT (space_id, turn_id) DO UPDATE SET kept_by = 'scenario',"
    " kept_until = CASE WHEN turns_kept.kept_until IS NULL OR excluded.kept_until IS NULL THEN NULL"
    " ELSE greatest(turns_kept.kept_until, excluded.kept_until) END"
)


def accept_params(intake: NewTurnIntake) -> dict[str, Any]:
    return {
        "space": intake.space_id,
        "intake": intake.intake_id,
        "received": intake.received_at,
        "source": intake.source_id,
        "turns": intake.turns,
        "body": intake.body_ct,
        "dek": intake.dek_version,
        "queue": CATALOG[TaskName.APPLY_TURNS].queue.value,
    }


class PgTurnIntakeLog:
    """`turn_accept` as one autocommit statement on the space's pool; on a connection the pooler or the
    server closed, once more on a fresh one (the function is idempotent by intake id)."""

    def __init__(self, registry: EngineRegistry) -> None:
        self._registry = registry

    async def accept(self, intake: NewTurnIntake) -> None:
        for attempt in range(2):
            try:
                await self._accept(intake)
                return
            except exc.DBAPIError as error:
                if attempt or not error.connection_invalidated:
                    raise_unreachable(error)

    async def _accept(self, intake: NewTurnIntake) -> None:
        try:
            async with (
                self._registry.lease(intake.space_id, "app") as engine,
                engine.connect() as connection,
            ):
                autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
                await autocommit.execute(ACCEPT, accept_params(intake))
        except exc.TimeoutError:
            raise Unavailable("the database connection pool is busy", retry_after_s=1) from None


class PgTurnRepository:
    # Frames and bodies arrive sealed by the service; nothing here is sealed column by column.
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:  # noqa: ARG002
        self._db = session

    async def take_intake(self, limit: int) -> TakenIntake | None:
        rows = (await self._db.execute(TAKE, {"lock": APPLY_LOCK, "limit": limit})).all()
        if not rows or not rows[0].held:
            return None
        taken = [
            TurnIntakeRow(
                r.intake_id, r.received_at, r.source_id, bytes(r.body_ct), r.dek_version, r.attempts
            )
            for r in rows
            if r.intake_id is not None
        ]
        return TakenIntake(taken, frozenset(bytes(h) for h in rows[0].hashes))

    async def tombstone(self, marks: Sequence[str], until: datetime) -> None:
        if not marks:
            return
        stmt = insert(_TOMBSTONES).values(
            [{"space_id": CURRENT_SPACE, "mark": mark, "until": until} for mark in sorted(set(marks))]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["space_id", "mark"],
            set_={"until": func.greatest(_TOMBSTONES.c.until, stmt.excluded.until)},
        )
        await self._db.execute(stmt)

    async def wait_for_apply(self) -> None:
        await self._db.execute(
            text("SELECT pg_advisory_xact_lock(CAST(:lock AS bigint))"), {"lock": APPLY_LOCK}
        )

    async def purge_tombstones(self, before: datetime) -> int:
        result = await self._db.execute(
            delete(_TOMBSTONES).where(_TOMBSTONES.c.space_id == CURRENT_SPACE, _TOMBSTONES.c.until < before)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def apply(self, applied: AppliedTurns) -> None:
        daily = [
            {
                "day": d.day.isoformat(),
                "source_id": str(d.source_id),
                "agent": d.agent,
                "build_hash": d.build_hash.hex(),
                **{c: getattr(d, c) for c in _COUNTERS},
                "latency_buckets": d.latency_buckets,
                "claims": d.claims,
            }
            for d in applied.daily
        ]
        builds = [
            {
                "build_hash": b.build_hash.hex(),
                "source_id": str(b.source_id),
                "agent": b.agent,
                "pins": b.pins,
                "sdk": b.sdk,
                "adapter": b.adapter,
                "at": b.at.isoformat(),
            }
            for b in applied.builds
        ]
        await self._db.execute(
            APPLY,
            {
                "daily": _json(daily),
                "builds": _json(builds),
                "kept": _json([_kept_json(k) for k in applied.kept]),
                "ids": list(applied.intake_ids),
                "flag_id": uuid7(),
                "flag_task": TaskName.NOTIFY_FLAGGED_TURNS.value,
                "flag_queue": CATALOG[TaskName.NOTIFY_FLAGGED_TURNS].queue.value,
                "flagged": _json({"turns": list(applied.flagged)}) if applied.flagged else None,
                "scan_task": TaskName.SCAN_TURN_ARGUMENTS.value,
                "scan_queue": CATALOG[TaskName.SCAN_TURN_ARGUMENTS].queue.value,
                "scans": _json([{"id": str(uuid7()), "payload": p} for p in applied.scans]),
                "mentions": _json(
                    [
                        {"key": k, "tokens": sorted(t.hex() for t in tokens)}
                        for k, tokens in applied.mentions
                        if tokens
                    ]
                ),
            },
        )

    async def fail_intake(self, intake_id: UUID) -> int:
        stmt = (
            update(TurnIntake)
            .where(TurnIntake.space_id == CURRENT_SPACE, TurnIntake.intake_id == intake_id)
            .values(attempts=TurnIntake.attempts + 1)
            .returning(TurnIntake.attempts)
        )
        return int((await self._db.execute(stmt)).scalar_one_or_none() or 0)

    async def drop_intake(self, intake_ids: Sequence[UUID]) -> None:
        if not intake_ids:
            return
        await self._db.execute(
            delete(TurnIntake).where(
                TurnIntake.space_id == CURRENT_SPACE, TurnIntake.intake_id.in_(intake_ids)
            )
        )
        await self._db.execute(delete(Outbox).where(Outbox.id.in_(intake_ids)))

    async def set_aside_intake(self, intake_id: UUID, at: datetime) -> None:
        await self._db.execute(
            update(TurnIntake)
            .where(TurnIntake.space_id == CURRENT_SPACE, TurnIntake.intake_id == intake_id)
            .values(failed_at=at)
        )
        await self._db.execute(delete(Outbox).where(Outbox.id == intake_id))

    async def failed_intake(
        self, limit: int, *, before: datetime | None = None, after: UUID | None = None
    ) -> list[TurnIntakeRow]:
        stmt = select(
            TurnIntake.intake_id,
            TurnIntake.received_at,
            TurnIntake.source_id,
            TurnIntake.body_ct,
            TurnIntake.dek_version,
            TurnIntake.attempts,
            TurnIntake.failed_at,
        ).where(TurnIntake.space_id == CURRENT_SPACE, TurnIntake.failed_at.is_not(None))
        if before is not None:
            stmt = stmt.where(TurnIntake.received_at < before)
        if after is not None:
            mark = (
                select(TurnIntake.seq)
                .where(TurnIntake.space_id == CURRENT_SPACE, TurnIntake.intake_id == after)
                .scalar_subquery()
            )
            stmt = stmt.where(TurnIntake.seq > mark)
        rows = await self._db.execute(stmt.order_by(TurnIntake.seq).limit(limit))
        return [
            TurnIntakeRow(
                r.intake_id,
                r.received_at,
                r.source_id,
                bytes(r.body_ct),
                r.dek_version,
                r.attempts,
                r.failed_at,
            )
            for r in rows
        ]

    async def count_failed_intake(self) -> int:
        kept = (
            select(func.count())
            .select_from(TurnIntake)
            .where(TurnIntake.space_id == CURRENT_SPACE, TurnIntake.failed_at.is_not(None))
        )
        return int((await self._db.execute(kept)).scalar_one())

    async def retry_intake(self, intake_ids: Sequence[UUID] | None) -> list[UUID]:
        stmt = update(TurnIntake).where(
            TurnIntake.space_id == CURRENT_SPACE, TurnIntake.failed_at.is_not(None)
        )
        if intake_ids is not None:
            stmt = stmt.where(TurnIntake.intake_id.in_(intake_ids))
        put_back = stmt.values(failed_at=None, attempts=0).returning(TurnIntake.intake_id)
        return [r.intake_id for r in await self._db.execute(put_back)]

    async def rewrite_intake(self, intake_id: UUID, body_ct: bytes, dek_version: int, turns: int) -> None:
        await self._db.execute(
            update(TurnIntake)
            .where(TurnIntake.space_id == CURRENT_SPACE, TurnIntake.intake_id == intake_id)
            .values(body_ct=body_ct, dek_version=dek_version, turns=turns)
        )

    async def daily(self, since: date, until: date, source_id: UUID | None) -> list[DailyTotals]:
        stmt = select(_DAILY).where(_DAILY.c.space_id == CURRENT_SPACE, _DAILY.c.day.between(since, until))
        if source_id is not None:
            stmt = stmt.where(_DAILY.c.source_id == source_id)
        rows = await self._db.execute(stmt.order_by(_DAILY.c.day, _DAILY.c.source_id, _DAILY.c.agent))
        return [
            DailyTotals(
                day=r.day,
                source_id=r.source_id,
                agent=r.agent,
                build_hash=bytes(r.build_hash),
                **{c: int(getattr(r, c)) for c in _COUNTERS},
                latency_buckets=[int(b) for b in r.latency_buckets],
                claims={k: int(v) for k, v in r.claims.items()},
            )
            for r in rows
        ]

    async def keep(self, turns: Sequence[KeptTurn]) -> set[str]:
        if not turns:
            return set()
        rows = await self._db.execute(KEEP, {"kept": _json([_kept_json(k) for k in turns])})
        return {row.turn_id for row in rows}

    async def kept(self, turn_ids: Sequence[str]) -> list[KeptTurn]:
        if not turn_ids:
            return []
        rows = await self._db.execute(
            select(_KEPT).where(_KEPT.c.space_id == CURRENT_SPACE, _KEPT.c.turn_id.in_(list(turn_ids)))
        )
        return [_kept(row) for row in rows]

    async def kept_between(
        self, since: datetime, until: datetime, *, after: str | None, limit: int
    ) -> list[KeptTurn]:
        stmt = select(_KEPT).where(
            _KEPT.c.space_id == CURRENT_SPACE, _KEPT.c.kept_at >= since, _KEPT.c.kept_at < until
        )
        if after is not None:
            stmt = stmt.where(_KEPT.c.turn_id > after)
        return [_kept(row) for row in await self._db.execute(stmt.order_by(_KEPT.c.turn_id).limit(limit))]

    async def search(self, query: TurnQuery) -> list[KeptTurn]:
        stmt = select(_KEPT).where(_KEPT.c.space_id == CURRENT_SPACE)
        if query.conversation is not None:
            stmt = stmt.where(_KEPT.c.conversation_hmac == query.conversation)
        if query.agent is not None:
            stmt = stmt.where(_KEPT.c.agent == query.agent)
        if query.since is not None:
            stmt = stmt.where(_KEPT.c.started_at >= query.since)
        if query.until is not None:
            stmt = stmt.where(_KEPT.c.started_at < query.until)
        if query.flags:
            stmt = stmt.where(_KEPT.c.flags.overlap(list(query.flags)))
        if query.sources is not None:
            stmt = stmt.where(_KEPT.c.source_id.in_(list(query.sources)))
        if query.before is not None:
            at, turn_id = query.before
            after = tuple_(literal(at, DateTime(timezone=True)), literal(turn_id, Text))
            stmt = stmt.where(tuple_(_KEPT.c.started_at, _KEPT.c.turn_id) < after)
        stmt = stmt.order_by(_KEPT.c.started_at.desc(), _KEPT.c.turn_id.desc()).limit(query.limit)
        return [_kept(row) for row in await self._db.execute(stmt)]

    async def expire_kept(
        self,
        before: datetime,
        limit: int,
        *,
        keep: Collection[bytes] = (),
        kept_before: datetime | None = None,
        scenario_before: datetime | None = None,
    ) -> list[KeptTurn]:
        held = [_KEPT.c.conversation_hmac.not_in(list(keep))] if keep else []
        due = [_KEPT.c.kept_until < before]
        undated = _KEPT.c.kept_until.is_(None)
        if kept_before is not None:
            due.append(undated & (_KEPT.c.kept_by != KeptBy.SCENARIO.value) & (_KEPT.c.kept_at < kept_before))
        if scenario_before is not None:
            due.append(
                undated & (_KEPT.c.kept_by == KeptBy.SCENARIO.value) & (_KEPT.c.kept_at < scenario_before)
            )
        doomed = (
            select(_KEPT.c.turn_id)
            .where(_KEPT.c.space_id == CURRENT_SPACE, or_(*due), *held)
            .order_by(_KEPT.c.kept_at)
            .limit(limit)
        )
        stmt = (
            delete(_KEPT)
            .where(_KEPT.c.space_id == CURRENT_SPACE, _KEPT.c.turn_id.in_(doomed.scalar_subquery()))
            .returning(*_KEPT.c)
        )
        return [_kept(row) for row in await self._db.execute(stmt)]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """The objects go by the conversations' prefixes, after the step: the short tier has no rows."""
        if not scope.conversation_hashes:
            return []
        doomed = (
            select(_KEPT.c.turn_id)
            .where(
                _KEPT.c.space_id == CURRENT_SPACE,
                _KEPT.c.conversation_hmac.in_(list(scope.conversation_hashes)),
            )
            .limit(limit)
        )
        stmt = (
            delete(_KEPT)
            .where(_KEPT.c.space_id == CURRENT_SPACE, _KEPT.c.turn_id.in_(doomed.scalar_subquery()))
            .returning(_KEPT.c.turn_id)
        )
        return [ErasedRow("turns_kept", {"turn_id": row.turn_id}) for row in await self._db.execute(stmt)]

    async def keep_for_scenario(self, turns: Sequence[KeptTurn]) -> None:
        if turns:
            await self._db.execute(KEEP_FOR_SCENARIO, {"kept": _json([_kept_json(k) for k in turns])})

    async def renew_scenario_turns(self, turn_ids: Sequence[str], until: datetime | None) -> None:
        if not turn_ids:
            return
        later = _KEPT.c.kept_until.is_not(None) if until is None else _KEPT.c.kept_until < until
        await self._db.execute(
            update(_KEPT)
            .where(
                _KEPT.c.space_id == CURRENT_SPACE,
                _KEPT.c.kept_by == KeptBy.SCENARIO.value,
                _KEPT.c.turn_id.in_(list(turn_ids)),
                later,
            )
            .values(kept_until=until)
        )

    async def add_scenario(self, scenario: ScenarioRow) -> ScenarioRow:
        values = {
            "space_id": CURRENT_SPACE,
            "scenario_id": scenario.scenario_id,
            "name": scenario.name,
            "origin": scenario.origin,
            "turn_ids": list(scenario.turn_ids),
            "assertions": list(scenario.assertions),
            "status": scenario.status,
            "version": scenario.version,
            "created_at": scenario.created_at,
            "created_by": scenario.created_by,
            "updated_at": scenario.updated_at,
        }
        stmt = insert(_SCENARIOS).values(values).on_conflict_do_nothing().returning(*_SCENARIOS.c)
        row = (await self._db.execute(stmt)).first()
        if row is None:
            row = (await self._db.execute(_one_scenario(scenario.scenario_id))).one()
        return _scenario(row)

    async def scenarios(
        self,
        *,
        ids: Sequence[UUID] | None = None,
        status: str | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[ScenarioRow]:
        stmt = select(_SCENARIOS).where(_SCENARIOS.c.space_id == CURRENT_SPACE)
        if ids is not None:
            stmt = stmt.where(_SCENARIOS.c.scenario_id.in_(list(ids)))
        if status is not None:
            stmt = stmt.where(_SCENARIOS.c.status == status)
        if before is not None:
            at, scenario_id = before
            after = tuple_(literal(at, DateTime(timezone=True)), literal(scenario_id, postgresql.UUID()))
            stmt = stmt.where(tuple_(_SCENARIOS.c.created_at, _SCENARIOS.c.scenario_id) < after)
        order = (_SCENARIOS.c.created_at.desc(), _SCENARIOS.c.scenario_id.desc())
        return [_scenario(row) for row in await self._db.execute(stmt.order_by(*order).limit(limit))]

    async def change_scenario(
        self, scenario_id: UUID, change: ScenarioChange, at: datetime
    ) -> ScenarioRow | None:
        values: dict[str, Any] = {"version": _SCENARIOS.c.version + 1, "updated_at": at}
        if change.name is not None:
            values["name"] = change.name
        if change.assertions is not None:
            values["assertions"] = list(change.assertions)
        if change.status is not None:
            values["status"] = change.status
        stmt = (
            update(_SCENARIOS)
            .where(_SCENARIOS.c.space_id == CURRENT_SPACE, _SCENARIOS.c.scenario_id == scenario_id)
            .values(values)
            .returning(*_SCENARIOS.c)
        )
        row = (await self._db.execute(stmt)).first()
        return _scenario(row) if row is not None else None

    async def add_run(self, run: ScenarioRunRow) -> ScenarioRunRow:
        values = {
            "space_id": CURRENT_SPACE,
            "run_id": run.run_id,
            "scenario_ids": list(run.scenario_ids),
            "build": run.build,
            "mode": run.mode,
            "vary": list(run.vary),
            "status": run.status,
            "verdict": run.verdict,
            "summary": run.summary,
            "created_at": run.created_at,
            "decided_at": run.decided_at,
        }
        stmt = insert(_RUNS).values(values).on_conflict_do_nothing().returning(*_RUNS.c)
        row = (await self._db.execute(stmt)).first()
        if row is None:
            found = await self.run(run.run_id)
            assert found is not None
            return found
        return _run(row)

    async def run(self, run_id: UUID) -> ScenarioRunRow | None:
        stmt = select(_RUNS).where(_RUNS.c.space_id == CURRENT_SPACE, _RUNS.c.run_id == run_id)
        row = (await self._db.execute(stmt)).first()
        return _run(row) if row is not None else None

    async def runs_of(
        self, scenario_ids: Sequence[UUID], before: datetime, limit: int
    ) -> list[ScenarioRunRow]:
        stmt = (
            select(_RUNS)
            .where(
                _RUNS.c.space_id == CURRENT_SPACE,
                _RUNS.c.scenario_ids.overlap(list(scenario_ids)),
                _RUNS.c.created_at < before,
            )
            .order_by(_RUNS.c.created_at.desc(), _RUNS.c.run_id.desc())
            .limit(limit)
        )
        return [_run(row) for row in await self._db.execute(stmt)]

    async def build_pins(self, build_hashes: Sequence[bytes]) -> dict[bytes, dict[str, Any]]:
        if not build_hashes:
            return {}
        stmt = select(_BUILDS.c.build_hash, _BUILDS.c.pins).where(
            _BUILDS.c.space_id == CURRENT_SPACE, _BUILDS.c.build_hash.in_(list(build_hashes))
        )
        return {bytes(r.build_hash): r.pins for r in await self._db.execute(stmt)}

    async def builds(self, limit: int) -> list[BuildSeen]:
        stmt = (
            select(_BUILDS)
            .where(_BUILDS.c.space_id == CURRENT_SPACE)
            .order_by(_BUILDS.c.first_seen_at.desc(), _BUILDS.c.build_hash)
            .limit(limit)
        )
        return [
            BuildSeen(
                build_hash=bytes(r.build_hash),
                source_id=r.source_id,
                agent=r.agent,
                pins=r.pins,
                sdk=r.sdk,
                adapter=r.adapter,
                at=r.first_seen_at,
            )
            for r in await self._db.execute(stmt)
        ]

    async def record_config(self, sections: Mapping[str, bytes], version: int, at: datetime) -> None:
        rows = [{"section": name, "digest": digest.hex()} for name, digest in sorted(sections.items())]
        await self._db.execute(RECORD_CONFIG, {"sections": _json(rows), "version": version, "at": at})

    async def config_history(self, limit: int) -> list[ConfigSeen]:
        stmt = (
            select(_CONFIG)
            .where(_CONFIG.c.space_id == CURRENT_SPACE)
            .order_by(_CONFIG.c.version.desc(), _CONFIG.c.section)
            .limit(limit)
        )
        return [
            ConfigSeen(r.section, int(r.version), r.applied_at, bytes(r.digest))
            for r in await self._db.execute(stmt)
        ]

    async def open_issue(self, issue: NewDataIssue, *, tell: bool) -> UUID:
        params = {
            "issue_id": issue.issue_id,
            "kind": issue.kind,
            "fingerprint": issue.fingerprint,
            "object_type": issue.object_type,
            "field": issue.field,
            "source": issue.source,
            "refs": _json(list(issue.refs)),
            "at": issue.at,
            "task_id": uuid7(),
            "task": TaskName.NOTIFY_DATA_ISSUE.value,
            "queue": CATALOG[TaskName.NOTIFY_DATA_ISSUE].queue.value,
            "tell": tell,
        }
        issue_id: UUID = (await self._db.execute(OPEN_ISSUE, params)).scalar_one()
        return issue_id

    async def issues(self, query: IssueQuery) -> list[DataIssueRow]:
        stmt = select(_ISSUES).where(_ISSUES.c.space_id == CURRENT_SPACE)
        if query.open is not None:
            closed = _ISSUES.c.acknowledged_at.is_not(None)
            stmt = stmt.where(~closed if query.open else closed)
        if query.kind is not None:
            stmt = stmt.where(_ISSUES.c.kind == query.kind)
        if query.before is not None:
            at, issue_id = query.before
            after = tuple_(literal(at, DateTime(timezone=True)), literal(issue_id, postgresql.UUID()))
            stmt = stmt.where(tuple_(_ISSUES.c.opened_at, _ISSUES.c.issue_id) < after)
        order = (_ISSUES.c.opened_at.desc(), _ISSUES.c.issue_id.desc())
        return [_issue(row) for row in await self._db.execute(stmt.order_by(*order).limit(query.limit))]

    async def issue(self, issue_id: UUID) -> DataIssueRow | None:
        stmt = select(_ISSUES).where(_ISSUES.c.space_id == CURRENT_SPACE, _ISSUES.c.issue_id == issue_id)
        row = (await self._db.execute(stmt)).first()
        return _issue(row) if row is not None else None

    async def acknowledge_issue(self, issue_id: UUID, by: str, at: datetime) -> DataIssueRow | None:
        stmt = (
            update(_ISSUES)
            .where(
                _ISSUES.c.space_id == CURRENT_SPACE,
                _ISSUES.c.issue_id == issue_id,
                _ISSUES.c.acknowledged_at.is_(None),
            )
            .values(acknowledged_at=at, acknowledged_by=by)
            .returning(*_ISSUES.c)
        )
        row = (await self._db.execute(stmt)).first()
        return _issue(row) if row is not None else await self.issue(issue_id)


_KEPT = typing.cast(Table, TurnKept.__table__)
_DAILY = typing.cast(Table, TurnDaily.__table__)
_SCENARIOS = typing.cast(Table, Scenario.__table__)
_RUNS = typing.cast(Table, ScenarioRun.__table__)
_BUILDS = typing.cast(Table, AgentBuild.__table__)
_ISSUES = typing.cast(Table, DataIssue.__table__)
_CONFIG = typing.cast(Table, ConfigHistory.__table__)
_TOMBSTONES = typing.cast(Table, TurnTombstone.__table__)


def _one_scenario(scenario_id: UUID) -> Any:
    return select(_SCENARIOS).where(
        _SCENARIOS.c.space_id == CURRENT_SPACE, _SCENARIOS.c.scenario_id == scenario_id
    )


def _scenario(row: Any) -> ScenarioRow:
    return ScenarioRow(
        scenario_id=row.scenario_id,
        name=row.name,
        origin=row.origin,
        turn_ids=tuple(row.turn_ids),
        assertions=tuple(row.assertions),
        status=row.status,
        version=row.version,
        created_at=row.created_at,
        created_by=row.created_by,
        updated_at=row.updated_at,
    )


def _run(row: Any) -> ScenarioRunRow:
    return ScenarioRunRow(
        run_id=row.run_id,
        scenario_ids=tuple(row.scenario_ids),
        build=row.build,
        mode=row.mode,
        vary=tuple(row.vary),
        status=row.status,
        verdict=row.verdict,
        summary=row.summary,
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


def _issue(row: Any) -> DataIssueRow:
    return DataIssueRow(
        issue_id=row.issue_id,
        kind=row.kind,
        object_type=row.object_type,
        field=row.field,
        source=row.source,
        refs=tuple(row.refs),
        occurrences=int(row.occurrences),
        opened_at=row.opened_at,
        last_seen_at=row.last_seen_at,
        acknowledged_at=row.acknowledged_at,
        acknowledged_by=row.acknowledged_by,
    )


def _json(value: Any) -> str:
    return orjson.dumps(value).decode()


def _kept_json(k: KeptTurn) -> dict[str, Any]:
    return {
        "turn_id": k.turn_id,
        "conversation_hmac": k.conversation.hex(),
        "source_id": str(k.source_id),
        "agent": k.agent,
        "kind": k.kind,
        "started_at": k.started_at.isoformat(),
        "fidelity": k.fidelity,
        "completeness": k.completeness,
        "content_mode": k.content_mode,
        "replayable": k.replayable,
        "build_hash": k.build_hash.hex() if k.build_hash is not None else None,
        "flags": list(k.flags),
        "kept_by": k.kept_by.value,
        "kept_at": k.kept_at.isoformat(),
        "kept_until": k.kept_until.isoformat() if k.kept_until is not None else None,
    }


def _kept(row: Any) -> KeptTurn:
    return KeptTurn(
        turn_id=row.turn_id,
        conversation=bytes(row.conversation_hmac),
        source_id=row.source_id,
        agent=row.agent,
        kind=row.kind,
        started_at=row.started_at,
        fidelity=row.fidelity,
        completeness=row.completeness,
        content_mode=row.content_mode,
        replayable=row.replayable,
        build_hash=bytes(row.build_hash) if row.build_hash is not None else None,
        flags=tuple(row.flags),
        kept_by=KeptBy(row.kept_by),
        kept_at=row.kept_at,
        kept_until=row.kept_until,
    )
