"""PostgreSQL implementation of `OutcomeRepository` (6.7, 24.7): outcome links and their daily totals,
interleaving, counterfactual runs and reports. Attributing a batch of objects is one statement, and adding
up the days it moved one more; an experiment's arms are one statement over the links."""

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import DateTime, Table, bindparam, func, insert, literal, select, text, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, DATE, REAL, SMALLINT, TEXT, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.privacy_rows import delete_where
from niadra.adapters.outbound.postgres.schema.signals import (
    CounterfactualRunRow,
    InterleaveDailyRow,
    MeasurementReportRow,
    OutcomeLinkRow,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.attribution import Link
from niadra.domain.measure.counterfactual import Engagement, Summary
from niadra.domain.measure.stats import Moments
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.signals.daily import Interleaved
from niadra.ports.repos.outcomes import ArmRow, StoredLink, StoredReport, StoredRun

L = cast(Table, OutcomeLinkRow.__table__)
R = cast(Table, MeasurementReportRow.__table__)
V = cast(Table, InterleaveDailyRow.__table__)
C = cast(Table, CounterfactualRunRow.__table__)
_UUIDS = ARRAY(PG_UUID(as_uuid=True))
_TIMES = ARRAY(TIMESTAMP(timezone=True))

_WRITE = text(
    """
    WITH fresh AS (
      SELECT * FROM unnest(
        :ids, :outcomes, :handles, :objects, :refs, :states, :lines, :turns, :actions, :exposures, :positions,
        :handoffs, :agents, :methods, :bands, :confidences, :finalities, :values, :currencies, :valid, :final,
        :orders
      ) AS f(link_id, outcome, handle, object_id, object_ref, state, line_ref, turn_id, action_id,
             exposure_id, position, handoff_id, agent, method, band, confidence, finality, value_minor,
             currency, valid_at, final_at, order_ref)
    ), stale AS (
      DELETE FROM outcome_links l
      WHERE l.space_id = current_setting('niadra.space_id')::uuid AND l.outcome_object_id = ANY(:object_ids)
        AND NOT EXISTS (SELECT 1 FROM fresh WHERE fresh.link_id = l.link_id)
      RETURNING l.valid_at
    ), kept AS (
      INSERT INTO outcome_links AS l
        (space_id, link_id, outcome, origin_handle_id, outcome_object_id, object_ref, outcome_state, line_ref,
         turn_id, action_id, exposure_id, position, handoff_id, agent, method, band, confidence, finality,
         value_minor, currency, valid_at, known_at, final_at, order_ref)
      SELECT current_setting('niadra.space_id')::uuid, link_id, outcome, handle, object_id, object_ref, state,
             line_ref, turn_id, action_id, exposure_id, position, handoff_id, agent, method, band, confidence,
             finality, value_minor, currency, valid_at, :now, final_at, order_ref
      FROM fresh
      ON CONFLICT (space_id, link_id) DO UPDATE SET
        outcome_state = EXCLUDED.outcome_state, turn_id = EXCLUDED.turn_id, agent = EXCLUDED.agent,
        confidence = EXCLUDED.confidence, finality = EXCLUDED.finality, value_minor = EXCLUDED.value_minor,
        currency = EXCLUDED.currency, known_at = EXCLUDED.known_at, order_ref = EXCLUDED.order_ref,
        valid_at = LEAST(l.valid_at, EXCLUDED.valid_at),
        final_at = CASE
          WHEN EXCLUDED.final_at IS NULL THEN NULL
          ELSE LEAST(l.valid_at, EXCLUDED.valid_at) + (EXCLUDED.final_at - EXCLUDED.valid_at)
        END
      WHERE (l.outcome_state, l.finality, l.value_minor, l.currency, l.turn_id, l.agent, l.confidence,
             l.order_ref)
            IS DISTINCT FROM (EXCLUDED.outcome_state, EXCLUDED.finality, EXCLUDED.value_minor,
                              EXCLUDED.currency, EXCLUDED.turn_id, EXCLUDED.agent, EXCLUDED.confidence,
                              EXCLUDED.order_ref)
      RETURNING l.valid_at
    )
    SELECT DISTINCT (valid_at AT TIME ZONE 'UTC')::date AS day FROM (
      SELECT valid_at FROM stale UNION ALL SELECT valid_at FROM kept
    ) AS moved
    """
).bindparams(
    bindparam("now", type_=TIMESTAMP(timezone=True)),
    bindparam("object_ids", type_=_UUIDS),
    bindparam("ids", type_=_UUIDS),
    bindparam("outcomes", type_=ARRAY(TEXT)),
    bindparam("handles", type_=_UUIDS),
    bindparam("objects", type_=_UUIDS),
    bindparam("refs", type_=ARRAY(TEXT)),
    bindparam("states", type_=ARRAY(TEXT)),
    bindparam("lines", type_=ARRAY(TEXT)),
    bindparam("turns", type_=ARRAY(TEXT)),
    bindparam("actions", type_=_UUIDS),
    bindparam("exposures", type_=ARRAY(TEXT)),
    bindparam("positions", type_=ARRAY(SMALLINT)),
    bindparam("handoffs", type_=_UUIDS),
    bindparam("agents", type_=ARRAY(TEXT)),
    bindparam("methods", type_=ARRAY(TEXT)),
    bindparam("bands", type_=ARRAY(TEXT)),
    bindparam("confidences", type_=ARRAY(REAL)),
    bindparam("finalities", type_=ARRAY(TEXT)),
    bindparam("values", type_=ARRAY(BIGINT)),
    bindparam("currencies", type_=ARRAY(TEXT)),
    bindparam("valid", type_=_TIMES),
    bindparam("final", type_=_TIMES),
    bindparam("orders", type_=ARRAY(TEXT)),
)

# Delete and upsert touch different rows, so the day's totals move in one statement.
_ROLL_UP = text(
    """
    WITH fresh AS (
      SELECT (valid_at AT TIME ZONE 'UTC')::date AS day, outcome, coalesce(agent, '') AS agent, method, band,
             finality, coalesce(currency, '') AS currency,
             count(DISTINCT coalesce(order_ref, outcome_object_id::text))::int AS outcomes,
             count(*)::int AS lines, coalesce(sum(value_minor), 0)::bigint AS value_minor
      FROM outcome_links
      WHERE space_id = current_setting('niadra.space_id')::uuid
        AND (valid_at AT TIME ZONE 'UTC')::date = ANY(:days)
      GROUP BY 1, 2, 3, 4, 5, 6, 7
    ), gone AS (
      DELETE FROM attribution_daily a
      WHERE a.space_id = current_setting('niadra.space_id')::uuid AND a.day = ANY(:days)
        AND NOT EXISTS (
          SELECT 1 FROM fresh f
          WHERE (f.day, f.outcome, f.agent, f.method, f.band, f.finality, f.currency)
              = (a.day, a.outcome, a.agent, a.method, a.band, a.finality, a.currency)
        )
    )
    INSERT INTO attribution_daily AS a
      (space_id, day, outcome, agent, method, band, finality, currency, outcomes, lines, value_minor)
    SELECT current_setting('niadra.space_id')::uuid, * FROM fresh
    ON CONFLICT (space_id, day, outcome, agent, method, band, finality, currency) DO UPDATE SET
      outcomes = EXCLUDED.outcomes, lines = EXCLUDED.lines, value_minor = EXCLUDED.value_minor
    """
).bindparams(bindparam("days", type_=ARRAY(DATE)))

_FINALIZE = text(
    """
    WITH due AS (
      SELECT link_id FROM outcome_links
      WHERE space_id = current_setting('niadra.space_id')::uuid AND finality = 'provisional'
        AND final_at <= :now
      ORDER BY final_at
      LIMIT :limit
    )
    UPDATE outcome_links l SET finality = 'final', known_at = :now, final_at = NULL
    FROM due
    WHERE l.space_id = current_setting('niadra.space_id')::uuid AND l.link_id = due.link_id
    RETURNING (l.valid_at AT TIME ZONE 'UTC')::date AS day
    """
).bindparams(bindparam("now", type_=TIMESTAMP(timezone=True)))

_NEXT_DUE = text(
    """
    SELECT min(final_at) FROM outcome_links
    WHERE space_id = current_setting('niadra.space_id')::uuid AND finality = 'provisional'
    """
)

_EXPIRE = text(
    """
    WITH old AS (
      SELECT link_id FROM outcome_links
      WHERE space_id = current_setting('niadra.space_id')::uuid AND valid_at < :before
      ORDER BY valid_at
      LIMIT :limit
    )
    DELETE FROM outcome_links l USING old
    WHERE l.space_id = current_setting('niadra.space_id')::uuid AND l.link_id = old.link_id
    RETURNING (l.valid_at AT TIME ZONE 'UTC')::date AS day
    """
).bindparams(bindparam("before", type_=TIMESTAMP(timezone=True)))

_LINE_VALUES = text(
    """
    SELECT DISTINCT ON (object_ref, coalesce(line_ref, '')) coalesce(order_ref, object_ref) AS order_ref,
           coalesce(line_ref, '') AS line, value_minor
    FROM outcome_links
    WHERE space_id = current_setting('niadra.space_id')::uuid AND band = 'deterministic'
      AND finality <> 'expired_without_outcome' AND value_minor IS NOT NULL
      AND (valid_at AT TIME ZONE 'UTC')::date BETWEEN :since AND :until
    ORDER BY object_ref, coalesce(line_ref, ''), known_at DESC
    """
).bindparams(bindparam("since", type_=DATE), bindparam("until", type_=DATE))

_AGREEMENT = text(
    """
    SELECT count(*) FILTER (WHERE t.exposure_id = i.exposure_id) AS same, count(*) AS both
    FROM outcome_links t
    JOIN outcome_links i ON i.space_id = t.space_id AND i.outcome_object_id = t.outcome_object_id
      AND i.line_ref IS NOT DISTINCT FROM t.line_ref AND i.method = 'identity'
    WHERE t.space_id = current_setting('niadra.space_id')::uuid AND t.method = 'line'
      AND (t.valid_at AT TIME ZONE 'UTC')::date BETWEEN :since AND :until
    """
).bindparams(bindparam("since", type_=DATE), bindparam("until", type_=DATE))


# Each person once, by the profile their assignment names and every handle it holds now; each line once,
# whichever methods attribute it, at the first moment it counted. `y` runs from the assignment to `until`,
# `x` over the covariate's days before the assignment.
_ARMS = text(
    """
    WITH assigned AS (
      SELECT profile_id, grp AS arm, assigned_at
      FROM experiment_assignments
      WHERE space_id = current_setting('niadra.space_id')::uuid AND experiment = :experiment AND kind = :kind
        AND assigned_at < :until
    ), lines AS (
      SELECT a.profile_id, coalesce(l.order_ref, l.outcome_object_id::text) AS order_key, l.outcome_object_id,
             coalesce(l.line_ref, '') AS line, max(l.value_minor) AS value, min(l.valid_at) AS at
      FROM assigned a
      JOIN profile_membership m
        ON m.space_id = current_setting('niadra.space_id')::uuid AND m.profile_id = a.profile_id
      JOIN outcome_links l
        ON l.space_id = current_setting('niadra.space_id')::uuid AND l.origin_handle_id = m.handle_id
      WHERE l.finality <> 'expired_without_outcome'
        AND (l.outcome, l.outcome_state) IN (SELECT * FROM unnest(:outcomes, :states))
        AND l.valid_at >= a.assigned_at - make_interval(days => :covariate_days) AND l.valid_at < :until
      GROUP BY 1, 2, 3, 4
    ), per AS (
      SELECT a.arm,
             count(DISTINCT k.order_key) FILTER (WHERE k.at >= a.assigned_at) AS outcomes,
             (count(k.at) FILTER (WHERE k.at >= a.assigned_at) > 0)::int AS yc,
             (count(k.at) FILTER (WHERE k.at < a.assigned_at) > 0)::int AS xc,
             coalesce(sum(k.value) FILTER (WHERE k.at >= a.assigned_at), 0)::numeric AS yv,
             coalesce(sum(k.value) FILTER (WHERE k.at < a.assigned_at), 0)::numeric AS xv
      FROM assigned a LEFT JOIN lines k ON k.profile_id = a.profile_id
      GROUP BY a.arm, a.profile_id
    )
    SELECT arm, count(*) AS people, sum(yc) AS converted, sum(outcomes) AS outcomes, sum(yv) AS value_minor,
           sum(xc) AS cx, sum(yc * xc) AS cxy,
           sum(yv * yv) AS vyy, sum(xv) AS vx, sum(xv * xv) AS vxx, sum(yv * xv) AS vxy
    FROM per GROUP BY arm ORDER BY arm
    """
).bindparams(
    bindparam("until", type_=TIMESTAMP(timezone=True)),
    bindparam("outcomes", type_=ARRAY(TEXT)),
    bindparam("states", type_=ARRAY(TEXT)),
)

_INFLOW = text(
    """
    SELECT experiment, kind, count(*) AS people FROM experiment_assignments
    WHERE space_id = current_setting('niadra.space_id')::uuid AND assigned_at >= :since
    GROUP BY experiment, kind ORDER BY experiment
    """
).bindparams(bindparam("since", type_=TIMESTAMP(timezone=True)))


class PgOutcomeRepository:
    def __init__(self, db: AsyncSession, sealer: Sealer | None = None) -> None:  # noqa: ARG002 (nothing sealed)
        self._db = db

    async def write(self, object_ids: Sequence[UUID], links: Sequence[Link], at: datetime) -> list[date]:
        if not object_ids:
            return []
        params: dict[str, Any] = {
            "now": at,
            "object_ids": list(object_ids),
            "ids": [x.link_id for x in links],
            "outcomes": [x.outcome for x in links],
            "handles": [x.handle_id for x in links],
            "objects": [x.object_id for x in links],
            "refs": [x.object_ref for x in links],
            "states": [x.state for x in links],
            "lines": [x.line_ref for x in links],
            "turns": [x.turn_id for x in links],
            "actions": [x.action_id for x in links],
            "exposures": [x.exposure_id for x in links],
            "positions": [x.position for x in links],
            "handoffs": [x.handoff_id for x in links],
            "agents": [x.agent for x in links],
            "methods": [x.method for x in links],
            "bands": [x.band for x in links],
            "confidences": [x.confidence for x in links],
            "finalities": [x.finality for x in links],
            "values": [x.value_minor for x in links],
            "currencies": [x.currency for x in links],
            "valid": [x.valid_at for x in links],
            "final": [x.final_at for x in links],
            "orders": [x.order_ref for x in links],
        }
        return sorted(set((await self._db.execute(_WRITE, params)).scalars()))

    async def roll_up(self, days: Sequence[date]) -> None:
        if days:
            await self._db.execute(_ROLL_UP, {"days": sorted(set(days))})

    async def finalize(self, now: datetime, *, limit: int) -> list[date]:
        return sorted(set((await self._db.execute(_FINALIZE, {"now": now, "limit": limit})).scalars()))

    async def next_due(self) -> datetime | None:
        found: datetime | None = await self._db.scalar(_NEXT_DUE)
        return found

    async def links(self, *, before: tuple[datetime, UUID] | None, limit: int) -> list[StoredLink]:
        stmt = select(L).where(L.c.space_id == CURRENT_SPACE)
        if before is not None:
            at, last = before
            after = tuple_(literal(at, DateTime(timezone=True)), literal(last, PG_UUID(as_uuid=True)))
            stmt = stmt.where(tuple_(L.c.known_at, L.c.link_id) < after)
        stmt = stmt.order_by(L.c.known_at.desc(), L.c.link_id.desc()).limit(limit)
        return [_stored(r) for r in await self._db.execute(stmt)]

    async def daily(self, since: date, until: date) -> list[dict[str, Any]]:
        stmt = text(
            "SELECT day, outcome, agent, method, band, finality, currency, outcomes, lines, value_minor"
            " FROM attribution_daily WHERE space_id = current_setting('niadra.space_id')::uuid"
            " AND day BETWEEN :since AND :until"
            " ORDER BY day, outcome, agent, method, band, finality, currency"
        ).bindparams(bindparam("since", type_=DATE), bindparam("until", type_=DATE))
        return [dict(r._mapping) for r in await self._db.execute(stmt, {"since": since, "until": until})]

    async def line_values(self, since: date, until: date) -> list[tuple[str, str, int]]:
        rows = await self._db.execute(_LINE_VALUES, {"since": since, "until": until})
        return [(r.order_ref, r.line, int(r.value_minor)) for r in rows]

    async def agreement(self, since: date, until: date) -> tuple[int, int]:
        row = (await self._db.execute(_AGREEMENT, {"since": since, "until": until})).one()
        return int(row.same), int(row.both)

    async def expire(self, before: datetime, *, limit: int) -> list[date]:
        return sorted(set((await self._db.execute(_EXPIRE, {"before": before, "limit": limit})).scalars()))

    async def put_interleaved(self, day: date, counts: Mapping[tuple[str, str], Interleaved]) -> None:
        if not counts:
            return
        rows = [
            {
                "space_id": CURRENT_SPACE, "day": day, "experiment": experiment, "tool": tool,
                "impressions": c.impressions, "wins_a": c.wins_a, "wins_b": c.wins_b, "ties": c.ties,
            }
            for (experiment, tool), c in sorted(counts.items())
        ]  # fmt: skip
        stmt = pg_insert(V).values(rows)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[V.c.space_id, V.c.day, V.c.experiment, V.c.tool],
                set_={
                    "impressions": stmt.excluded.impressions, "wins_a": stmt.excluded.wins_a,
                    "wins_b": stmt.excluded.wins_b, "ties": stmt.excluded.ties,
                },
            )
        )  # fmt: skip

    async def interleaved(self, since: date, until: date) -> list[dict[str, Any]]:
        stmt = (
            select(V.c.day, V.c.experiment, V.c.tool, V.c.impressions, V.c.wins_a, V.c.wins_b, V.c.ties)
            .where(V.c.space_id == CURRENT_SPACE, V.c.day >= since, V.c.day <= until)
            .order_by(V.c.day, V.c.experiment, V.c.tool)
        )
        return [dict(r._mapping) for r in await self._db.execute(stmt)]

    async def add_report(
        self, kind: str, since: date, until: date, body: dict[str, Any], at: datetime
    ) -> UUID:
        report_id = uuid7()
        await self._db.execute(
            insert(R).values(
                space_id=CURRENT_SPACE, report_id=report_id, kind=kind, period_start=since, period_end=until,
                body=body, created_at=at,
            )
        )  # fmt: skip
        return report_id

    async def reports(self, kind: str, since: date) -> list[StoredReport]:
        stmt = (
            select(R.c.period_start, R.c.period_end, R.c.body)
            .where(R.c.space_id == CURRENT_SPACE, R.c.kind == kind, R.c.period_end >= since)
            .order_by(R.c.period_end, R.c.report_id)
        )
        return [StoredReport(r.period_start, r.period_end, r.body) for r in await self._db.execute(stmt)]

    async def last_report(self, kind: str) -> date | None:
        found: date | None = await self._db.scalar(
            select(func.max(R.c.period_end)).where(R.c.space_id == CURRENT_SPACE, R.c.kind == kind)
        )
        return found

    async def arms(
        self,
        experiment: str,
        kind: str,
        until: datetime,
        covariate_days: int,
        success: Sequence[tuple[str, str]],
    ) -> list[ArmRow]:
        params = {
            "experiment": experiment, "kind": kind, "until": until, "covariate_days": covariate_days,
            "outcomes": [o for o, _ in success], "states": [s for _, s in success],
        }  # fmt: skip
        out = []
        for r in await self._db.execute(_ARMS, params):
            n, converted = int(r.people), int(r.converted)
            value = int(r.value_minor)
            out.append(
                ArmRow(
                    arm=r.arm, people=n, converted=converted, outcomes=int(r.outcomes), value_minor=value,
                    conversion=Moments(n, converted, converted, int(r.cx), int(r.cx), int(r.cxy)),
                    value=Moments(n, value, float(r.vyy), float(r.vx), float(r.vxx), float(r.vxy)),
                )
            )  # fmt: skip
        return out

    async def inflow(self, since: datetime) -> list[tuple[str, str, int]]:
        rows = await self._db.execute(_INFLOW, {"since": since})
        return [(r.experiment, r.kind, int(r.people)) for r in rows]

    async def add_run(self, run: StoredRun) -> None:
        x = run.summary
        await self._db.execute(
            insert(C).values(
                space_id=CURRENT_SPACE, run_id=run.run_id, tool=run.tool, element=run.element,
                cases=x.cases, overlap_at_k=x.overlap, noise_floor=x.noise_floor, limits=list(x.limits),
                detail=_run_detail(run), created_at=run.created_at,
            )
        )  # fmt: skip

    async def runs(self, *, before: tuple[datetime, UUID] | None, limit: int) -> list[StoredRun]:
        stmt = select(C).where(C.c.space_id == CURRENT_SPACE)
        if before is not None:
            at, last = before
            after = tuple_(literal(at, DateTime(timezone=True)), literal(last, PG_UUID(as_uuid=True)))
            stmt = stmt.where(tuple_(C.c.created_at, C.c.run_id) < after)
        stmt = stmt.order_by(C.c.created_at.desc(), C.c.run_id.desc()).limit(limit)
        return [_run(r) for r in await self._db.execute(stmt)]

    async def run(self, run_id: UUID) -> StoredRun | None:
        row = (
            await self._db.execute(select(C).where(C.c.space_id == CURRENT_SPACE, C.c.run_id == run_id))
        ).first()
        return _run(row) if row is not None else None

    async def written_since(self, since: datetime, *, limit: int) -> list[str]:
        stmt = (
            select(L.c.object_ref)
            .where(L.c.space_id == CURRENT_SPACE, L.c.known_at >= since)
            .group_by(L.c.object_ref)
            .limit(limit)
        )
        return list((await self._db.execute(stmt)).scalars())

    async def links_of(self, handle_ids: Sequence[UUID]) -> list[StoredLink]:
        if not handle_ids:
            return []
        stmt = (
            select(L)
            .where(L.c.space_id == CURRENT_SPACE, L.c.origin_handle_id.in_(list(handle_ids)))
            .order_by(L.c.valid_at, L.c.link_id)
        )
        return [_stored(r) for r in await self._db.execute(stmt)]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids or scope.kind is TargetKind.CONVERSATION or not scope.handle_ids:
            return []
        handles = list(scope.handle_ids)
        return await delete_where(self._db, L, ("link_id",), [L.c.origin_handle_id.in_(handles)], limit)


def _stored(r: Any) -> StoredLink:
    return StoredLink(
        Link(
            link_id=r.link_id, outcome=r.outcome, object_id=r.outcome_object_id, object_ref=r.object_ref,
            handle_id=r.origin_handle_id, state=r.outcome_state, line_ref=r.line_ref, method=r.method,
            band=r.band, finality=r.finality, valid_at=r.valid_at, value_minor=r.value_minor,
            currency=r.currency, exposure_id=r.exposure_id, position=r.position, turn_id=r.turn_id,
            action_id=r.action_id, handoff_id=r.handoff_id, agent=r.agent, confidence=r.confidence,
            final_at=r.final_at, order_ref=r.order_ref,
        ),
        r.known_at,
    )  # fmt: skip


def _run_detail(run: StoredRun) -> dict[str, Any]:
    x = run.summary
    return {
        "label": run.label, "completed": x.completed, "skipped": x.skipped, "effect": x.effect,
        "below_noise": x.below_noise, "above_noise": x.above_noise, "ties": x.ties, "p_value": x.p_value,
        "engaged": dataclasses.asdict(x.engaged),
    }  # fmt: skip


def _run(r: Any) -> StoredRun:
    d = r.detail
    summary = Summary(
        cases=r.cases, completed=d["completed"], skipped=d["skipped"], overlap=r.overlap_at_k,
        noise_floor=r.noise_floor, effect=d["effect"], below_noise=d["below_noise"],
        above_noise=d["above_noise"], ties=d["ties"], p_value=d["p_value"],
        engaged=Engagement(**d["engaged"]), limits=list(r.limits),
    )  # fmt: skip
    return StoredRun(r.run_id, r.tool, r.element, d.get("label"), r.created_at, summary)
