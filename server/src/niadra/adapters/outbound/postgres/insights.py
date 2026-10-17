"""PostgreSQL implementation of `InsightsRepository` (5.21).

The reads are the named read models of 4.3: one base selectable per entity, over the owner modules'
tables, exposing structured columns, the profile (through `profile_membership`) and the time column.
Text leaves sealed; the application opens it after the policy check. Every statement is pinned to the
transaction's space, on top of row-level security. A row anchored on a handle a claim joined and nobody
confirmed yet counts nowhere here, attributed or not, until the link is confirmed (5.3).
"""

from collections.abc import Collection, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, String, and_, case, cast, delete, exists, func, insert, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, Subquery

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.memory import (
    AgentActionRow,
    BusinessObjectRow,
    EpisodeRow,
    OpenItemRow,
    TraitEvidence,
    TraitRow,
)
from niadra.adapters.outbound.postgres.schema.operate import InsightReview, KnowledgeGapDay, TenantRollup
from niadra.adapters.outbound.postgres.schema.platform import SpaceState
from niadra.adapters.outbound.postgres.schema.write_path import HandleRow, Profile, ProfileMembership
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.insights.gaps import GapDay, GapKey, GapKind, Sketch
from niadra.domain.insights.model import (
    AGE_BANDS,
    OLDEST_AGE_BAND,
    SENTIMENT_NEGATIVE_BELOW,
    SENTIMENT_POSITIVE_ABOVE,
    AggregateQuery,
    AggregateRow,
    Entity,
    EpisodeText,
    Metric,
    ProfileMatch,
    ProfileRef,
    RollupRow,
    Sealed,
    Selection,
)

E = EpisodeRow.__table__
OI = OpenItemRow.__table__
T = TraitRow.__table__
A = AgentActionRow.__table__
B = BusinessObjectRow.__table__
P = Profile.__table__
M = ProfileMembership.__table__
H = HandleRow.__table__
TE = TraitEvidence.__table__


class PgInsightsRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer  # unused: text is opened by the application after the policy check

    async def aggregate(
        self, query: AggregateQuery, exclude_categories: Collection[str]
    ) -> list[AggregateRow]:
        base = _base(query.selection, exclude_categories)
        groups = [base.c[d] for d in query.group_by]
        columns: list[ColumnElement[Any]] = [func.count().label("n")]
        if Metric.PROFILES in query.metrics:
            columns.append(func.count(func.distinct(base.c.profile_id)).label("profiles"))
        if Metric.AVG_SENTIMENT in query.metrics:
            columns.append(func.avg(base.c.sentiment).label("avg_sentiment"))
        rows = await self._db.execute(select(*groups, *columns).select_from(base).group_by(*groups))
        return [
            AggregateRow(
                group=tuple(None if r[i] is None else str(r[i]) for i in range(len(groups))),
                count=int(r.n),
                profiles=int(r.profiles) if Metric.PROFILES in query.metrics else None,
                avg_sentiment=float(r.avg_sentiment)
                if Metric.AVG_SENTIMENT in query.metrics and r.avg_sentiment is not None
                else None,
            )
            for r in rows
        ]

    async def find_profiles(
        self,
        selection: Selection,
        exclude_categories: Collection[str],
        min_matches: int,
        after: str | None,
        limit: int,
    ) -> list[ProfileMatch]:
        base = _base(selection, exclude_categories)
        counted = (
            select(base.c.profile_id, func.count().label("n"))
            .where(base.c.profile_id.is_not(None))
            .group_by(base.c.profile_id)
            .having(func.count() >= min_matches)
            .subquery()
        )
        stmt = (
            select(P.c.pseudonym, counted.c.n)
            .join(counted, and_(P.c.space_id == CURRENT_SPACE, P.c.profile_id == counted.c.profile_id))
            .order_by(P.c.pseudonym)
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(P.c.pseudonym > after)
        return [ProfileMatch(r.pseudonym, int(r.n)) for r in await self._db.execute(stmt)]

    async def episode_texts(
        self, selection: Selection, exclude_categories: Collection[str], limit: int, *, newest_first: bool
    ) -> list[EpisodeText]:
        base = _base(selection, exclude_categories)
        stmt = (
            select(base, P.c.pseudonym)
            .join(P, and_(P.c.space_id == CURRENT_SPACE, P.c.profile_id == base.c.profile_id))
            .order_by(base.c.at.desc() if newest_first else func.random())
            .limit(limit)
        )
        return [
            EpisodeText(
                episode_id=r.episode_id,
                pseudonym=r.pseudonym,
                channel=r.channel,
                intent=r.intent,
                category=r.category,
                outcome=r.outcome,
                ended_at=r.at,
                summary=Sealed(r.summary_ct, r.dek_version, "episodes", str(r.episode_id), "summary_ct"),
                sentiment=r.sentiment,
            )
            for r in await self._db.execute(stmt)
        ]

    async def profile_by_pseudonym(self, pseudonym: str) -> ProfileRef | None:
        profile = (
            await self._db.execute(
                select(P.c.profile_id, P.c.pseudonym, P.c.kind).where(
                    P.c.space_id == CURRENT_SPACE, P.c.pseudonym == pseudonym, P.c.retired_at.is_(None)
                )
            )
        ).first()
        if profile is None:
            return None
        handles = await self._db.execute(
            select(M.c.handle_id, _unconfirmed(M.c.handle_id).label("unconfirmed"))
            .where(M.c.space_id == CURRENT_SPACE, M.c.profile_id == profile.profile_id)
            .order_by(M.c.handle_id)
        )
        members = handles.all()
        return ProfileRef(
            profile.profile_id,
            profile.pseudonym,
            profile.kind,
            tuple(h.handle_id for h in members if not h.unconfirmed),
            tuple(h.handle_id for h in members if h.unconfirmed),
        )

    async def write_rollups(self, day: date, rows: Sequence[RollupRow]) -> None:
        await self._db.execute(
            delete(TenantRollup).where(TenantRollup.space_id == CURRENT_SPACE, TenantRollup.day == day)
        )
        if rows:
            await self._db.execute(
                insert(TenantRollup).values(
                    [
                        {
                            "space_id": CURRENT_SPACE,
                            "day": day,
                            "dimension": r.dimension,
                            "bucket": r.bucket,
                            "value": r.value,
                        }
                        for r in rows
                    ]
                )
            )

    async def latest_rollups(self) -> tuple[date | None, list[RollupRow]]:
        day = (
            await self._db.execute(
                select(func.max(TenantRollup.day)).where(TenantRollup.space_id == CURRENT_SPACE)
            )
        ).scalar()
        if day is None:
            return None, []
        rows = await self._db.execute(
            select(TenantRollup.dimension, TenantRollup.bucket, TenantRollup.value)
            .where(TenantRollup.space_id == CURRENT_SPACE, TenantRollup.day == day)
            .order_by(TenantRollup.dimension, TenantRollup.bucket)
        )
        return day, [RollupRow(r.dimension, r.bucket, int(r.value)) for r in rows]

    async def write_gap_days(self, day: date, rows: Sequence[GapDay]) -> None:
        g = KnowledgeGapDay
        await self._db.execute(delete(g).where(g.space_id == CURRENT_SPACE, g.day == day))
        if rows:
            await self._db.execute(
                insert(g).values(
                    [
                        {
                            "space_id": CURRENT_SPACE,
                            "day": day,
                            "kind": r.key.kind.value,
                            "subject": r.key.subject,
                            "topic": r.key.topic,
                            "turns": r.turns,
                            "customers": r.customers.to_bytes(),
                        }
                        for r in rows
                    ]
                )
            )

    async def purge_gap_days(self, before: date) -> int:
        g = KnowledgeGapDay
        result = await self._db.execute(delete(g).where(g.space_id == CURRENT_SPACE, g.day < before))
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def gap_days(self, since: date, until: date) -> list[GapDay]:
        g = KnowledgeGapDay
        rows = await self._db.execute(
            select(g.day, g.kind, g.subject, g.topic, g.turns, g.customers)
            .where(g.space_id == CURRENT_SPACE, g.day >= since, g.day < until)
            .order_by(g.day, g.kind, g.subject, g.topic)
        )
        return [
            GapDay(r.day, GapKey(GapKind(r.kind), r.subject, r.topic), int(r.turns), Sketch(r.customers))
            for r in rows
        ]

    async def gaps_day(self) -> int | None:
        found: int | None = await self._db.scalar(select(SpaceState.value).where(SpaceState.key == GAPS_DAY))
        return found

    async def set_gaps_day(self, day: int) -> None:
        stmt = pg_insert(SpaceState).values(key=GAPS_DAY, value=day)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[SpaceState.key], set_={"value": day, "updated_at": func.now()}
            )
        )

    async def reviews(self) -> dict[str, tuple[str, str, datetime, str | None]]:
        r = InsightReview
        rows = await self._db.execute(
            select(r.key, r.decision, r.decided_by, r.decided_at, r.note).where(r.space_id == CURRENT_SPACE)
        )
        return {row.key: (row.decision, row.decided_by, row.decided_at, row.note) for row in rows}

    async def put_review(
        self, key: str, decision: str, decided_by: str, at: datetime, note: str | None
    ) -> None:
        values = {"decision": decision, "decided_by": decided_by, "decided_at": at, "note": note}
        stmt = pg_insert(InsightReview).values(space_id=CURRENT_SPACE, key=key, **values)
        await self._db.execute(stmt.on_conflict_do_update(index_elements=["space_id", "key"], set_=values))


GAPS_DAY = "insights_gaps_day"
"""A counter row of `space_state`: the last day the knowledge gaps pass counted."""


def _base(selection: Selection, exclude_categories: Collection[str]) -> Subquery:
    """The entity as rows of (dimensions..., profile_id, sentiment, at), filtered and windowed."""
    stmt = _BUILDERS[selection.entity](selection)
    inner = stmt.subquery()
    conditions = [inner.c.at >= selection.since, inner.c.at < selection.until]
    conditions += [inner.c[k].in_(values) for k, values in selection.filters.items()]
    if selection.entity is Entity.EPISODES and exclude_categories:
        excluded = list(exclude_categories)
        conditions += [inner.c.category.not_in(excluded), inner.c.normalized_category.not_in(excluded)]
    return select(inner).where(*conditions).subquery()


def _membership(handle: ColumnElement[UUID]) -> Any:
    return and_(M.c.space_id == CURRENT_SPACE, M.c.handle_id == handle)


def _unconfirmed(handle: ColumnElement[UUID]) -> ColumnElement[bool]:
    """The row's anchor is a handle a claim joined and nobody confirmed yet (5.3)."""
    return exists().where(H.c.space_id == CURRENT_SPACE, H.c.handle_id == handle, H.c.provisional)


def _episodes(_: Selection) -> Select[Any]:
    band = case(
        (E.c.sentiment.is_(None), "unknown"),
        (E.c.sentiment < SENTIMENT_NEGATIVE_BELOW, "negative"),
        (E.c.sentiment > SENTIMENT_POSITIVE_ABOVE, "positive"),
        else_="neutral",
    )
    return (
        select(
            E.c.episode_id,
            E.c.intent,
            E.c.category,
            E.c.normalized_category,
            E.c.outcome,
            E.c.channel,
            cast(E.c.source_id, String).label("source_id"),
            E.c.initiated_by,
            band.label("sentiment_band"),
            E.c.sentiment,
            E.c.summary_ct,
            E.c.dek_version,
            M.c.profile_id,
            E.c.ended_at.label("at"),
        )
        .select_from(E.outerjoin(M, _membership(E.c.origin_handle_id)))
        .where(E.c.space_id == CURRENT_SPACE, E.c.is_current.is_(True), ~_unconfirmed(E.c.origin_handle_id))
    )


def _open_items(selection: Selection) -> Select[Any]:
    age = func.date_part("day", literal(selection.until) - OI.c.created_at)
    band = case(*[(age <= limit, label) for limit, label in AGE_BANDS], else_=OLDEST_AGE_BAND)
    return (
        select(
            OI.c.owner,
            OI.c.status,
            OI.c.object_type,
            OI.c.expected_operation,
            band.label("age_band"),
            literal(None).label("sentiment"),
            M.c.profile_id,
            OI.c.created_at.label("at"),
        )
        .select_from(OI.outerjoin(M, _membership(OI.c.origin_handle_id)))
        .where(OI.c.space_id == CURRENT_SPACE, ~_unconfirmed(OI.c.origin_handle_id))
    )


def _traits(_: Selection) -> Select[Any]:
    return select(
        T.c.name,
        T.c.layer,
        T.c.rule_version,
        literal(None).label("sentiment"),
        T.c.profile_id,
        T.c.last_evidence_at.label("at"),
    ).where(
        T.c.space_id == CURRENT_SPACE,
        T.c.retracted_at.is_(None),
        # A pattern derived before the rule kept an unconfirmed link's rows apart (5.3).
        ~exists().where(
            TE.c.space_id == CURRENT_SPACE,
            TE.c.trait_id == T.c.trait_id,
            _unconfirmed(TE.c.origin_handle_id),
        ),
    )


def _actions(_: Selection) -> Select[Any]:
    return select(
        A.c.operation,
        A.c.status,
        cast(A.c.source_id, String).label("source_id"),
        A.c.purpose,
        literal(None).label("sentiment"),
        cast(literal(None), A.c.action_id.type).label("profile_id"),
        A.c.occurred_at.label("at"),
    ).where(A.c.space_id == CURRENT_SPACE)


def _objects(_: Selection) -> Select[Any]:
    return (
        select(
            B.c.type,
            B.c.namespace,
            cast(B.c.source_id, String).label("source_id"),
            literal(None).label("sentiment"),
            M.c.profile_id,
            B.c.as_of.label("at"),
        )
        .select_from(B.outerjoin(M, _membership(B.c.origin_handle_id)))
        .where(B.c.space_id == CURRENT_SPACE, ~_unconfirmed(B.c.origin_handle_id))
    )


def _profiles(_: Selection) -> Select[Any]:
    return select(
        P.c.kind,
        literal(None).label("sentiment"),
        P.c.profile_id,
        P.c.created_at.label("at"),
    ).where(P.c.space_id == CURRENT_SPACE, P.c.retired_at.is_(None))


_BUILDERS = {
    Entity.EPISODES: _episodes,
    Entity.OPEN_ITEMS: _open_items,
    Entity.TRAITS: _traits,
    Entity.ACTIONS: _actions,
    Entity.OBJECTS: _objects,
    Entity.PROFILES: _profiles,
}
