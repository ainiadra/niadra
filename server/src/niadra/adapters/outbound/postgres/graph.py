"""PostgreSQL implementation of `GraphRepository`: the relationship graph's read model (4.3, 5.22).

Ids, counts and times only: no sealed column is read here. Each method is one statement, a UNION ALL of the
parts it was asked for, every part bounded by an index on the ids it is given, so a graph costs the same few
statements whatever the size of the space.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Float, String, and_, cast, func, literal, null, or_, select, text, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.schema.base import SpaceBase
from niadra.adapters.outbound.postgres.schema.write_path import (
    IdentitySuggestion,
    ProfileLink,
    ProfileMembership,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.records import AgentAction
from niadra.domain.vocabulary import ActionStatus
from niadra.ports.repos.graph import Activity, Acts, Handoffs, Reports, Talk, Tie

L = SpaceBase.metadata.tables[ProfileLink.__tablename__]
S = SpaceBase.metadata.tables[IdentitySuggestion.__tablename__]
PM = SpaceBase.metadata.tables[ProfileMembership.__tablename__]
_SPACE = "CAST(current_setting('niadra.space_id') AS uuid)"
_LATEST = "ORDER BY a.occurred_at DESC, a.action_id DESC"
_NOTHING = (
    "NULL::uuid AS action_id, NULL::uuid AS event_id, NULL::text AS operation, NULL::text AS status,"
    " NULL::text AS target, NULL::text AS outcome, NULL::bytea AS subject"
)
_ACTED = (
    "count(*) AS n, max(a.occurred_at) AS last_at, array_agg(DISTINCT a.operation) AS labels,"
    f" (array_agg(a.action_id {_LATEST}))[1] AS action_id, (array_agg(a.event_id {_LATEST}))[1] AS event_id,"
    f" (array_agg(a.operation {_LATEST}))[1] AS operation, (array_agg(a.status {_LATEST}))[1] AS status,"
    " NULL::text AS target, NULL::text AS outcome, NULL::bytea AS subject"
)


def _ids(kind: str, source: str, profile: str, handle: str, obj: str) -> str:
    return (
        f"'{kind}' AS kind, {source} AS source_id, {profile} AS profile_id, {handle} AS handle_id,"
        f" {obj} AS object_id"
    )


# Every part names the same columns in the same order, so any of them may come first in the union.
_PARTS = {
    "talks": f"""
        SELECT {_ids("talk", "s.source_id", "s.profile_id", "NULL::uuid", "NULL::uuid")},
            count(*) AS n, max(s.last_event_at) AS last_at, array_agg(DISTINCT s.channel) AS labels,
            {_NOTHING}
        FROM sessions s
        WHERE s.space_id = {_SPACE} AND s.profile_id = ANY(CAST(:profiles AS uuid[]))
        GROUP BY s.source_id, s.profile_id""",
    "acts_on_objects": f"""
        SELECT {_ids("acts", "a.source_id", "NULL::uuid", "NULL::uuid", "a.object_id")}, {_ACTED}
        FROM agent_actions a
        WHERE a.space_id = {_SPACE} AND a.object_id = ANY(CAST(:objects AS uuid[]))
        GROUP BY a.source_id, a.object_id""",
    "acts_on_handles": f"""
        SELECT {_ids("acts", "a.source_id", "NULL::uuid", "a.closes_handle_id", "NULL::uuid")}, {_ACTED}
        FROM agent_actions a
        WHERE a.space_id = {_SPACE} AND a.object_id IS NULL
            AND a.closes_handle_id = ANY(CAST(:handles AS uuid[]))
        GROUP BY a.source_id, a.closes_handle_id""",
    "acts_by_owner": f"""
        SELECT {_ids("acts", "a.source_id", "NULL::uuid", "o.origin_handle_id", "NULL::uuid")}, {_ACTED}
        FROM business_objects o
        JOIN agent_actions a ON a.space_id = o.space_id AND a.object_id = o.object_id
        WHERE o.space_id = {_SPACE} AND o.origin_handle_id = ANY(CAST(:owners AS uuid[]))
        GROUP BY a.source_id, o.origin_handle_id""",
    "reports_on_objects": f"""
        SELECT {_ids("reports", "e.source_id", "NULL::uuid", "NULL::uuid", "oe.object_id")},
            count(*) AS n, max(oe.occurred_at) AS last_at, NULL::text[] AS labels, {_NOTHING}
        FROM object_events oe
        JOIN events e ON e.space_id = oe.space_id AND e.event_id = oe.event_id
        WHERE oe.space_id = {_SPACE} AND oe.object_id = ANY(CAST(:objects AS uuid[]))
            AND e.kind = 'system_event'
        GROUP BY e.source_id, oe.object_id""",
    "reports_by_owner": f"""
        SELECT {_ids("reports", "e.source_id", "NULL::uuid", "o.origin_handle_id", "NULL::uuid")},
            count(*) AS n, max(oe.occurred_at) AS last_at, NULL::text[] AS labels, {_NOTHING}
        FROM business_objects o
        JOIN object_events oe ON oe.space_id = o.space_id AND oe.object_id = o.object_id
        JOIN events e ON e.space_id = oe.space_id AND e.event_id = oe.event_id
        WHERE o.space_id = {_SPACE} AND o.origin_handle_id = ANY(CAST(:owners AS uuid[]))
            AND e.kind = 'system_event'
        GROUP BY e.source_id, o.origin_handle_id""",
    "owned": f"""
        SELECT {_ids("owned", "NULL::uuid", "NULL::uuid", "o.origin_handle_id", "NULL::uuid")},
            count(*) AS n, max(o.as_of) AS last_at, NULL::text[] AS labels, {_NOTHING}
        FROM business_objects o
        WHERE o.space_id = {_SPACE} AND o.origin_handle_id = ANY(CAST(:owners AS uuid[]))
        GROUP BY o.origin_handle_id""",
    "involved": f"""
        SELECT {_ids("involved", "NULL::uuid", "g.profile_id", "NULL::uuid", "NULL::uuid")},
            count(DISTINCT x.object_id) AS n, NULL::timestamptz AS last_at, NULL::text[] AS labels, {_NOTHING}
        FROM unnest(CAST(:customer_ids AS uuid[]), CAST(:customer_handles AS uuid[]))
            AS g(profile_id, handle_id)
        CROSS JOIN LATERAL (
            SELECT o.object_id FROM business_objects o
            WHERE o.space_id = {_SPACE} AND o.origin_handle_id = g.handle_id
            UNION ALL
            SELECT p.object_id FROM object_participants p
            WHERE p.space_id = {_SPACE} AND p.handle_id = g.handle_id
        ) AS x
        GROUP BY g.profile_id""",
    "handoffs": f"""
        SELECT {_ids("handoff", "h.source_id", "NULL::uuid", "NULL::uuid", "NULL::uuid")},
            count(*) AS n, max(h.created_at) AS last_at, NULL::text[] AS labels,
            NULL::uuid AS action_id, NULL::uuid AS event_id, NULL::text AS operation,
            (array_agg(h.status ORDER BY h.created_at DESC, h.handoff_id DESC))[1] AS status,
            h.target AS target,
            (array_agg(h.outcome ORDER BY h.created_at DESC, h.handoff_id DESC))[1] AS outcome,
            h.subject_hmac AS subject
        FROM handoffs h
        WHERE h.space_id = {_SPACE} AND h.subject_hmac = ANY(CAST(:subjects AS bytea[]))
        GROUP BY h.source_id, h.subject_hmac, h.target""",
}


class PgGraphRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer  # unused: the read model reads no sealed column

    async def ties(self, handle_ids: Sequence[UUID]) -> list[Tie]:
        ids = sorted(set(handle_ids))
        if not ids:
            return []
        no_role, no_score = cast(null(), String), cast(null(), Float)
        links = select(
            literal("link").label("kind"),
            L.c.link_id.label("tie_id"),
            L.c.person_handle_id.label("a"),
            L.c.org_handle_id.label("b"),
            L.c.role.label("role"),
            L.c.valid_from.label("since"),
            no_score.label("score"),
        ).where(
            L.c.space_id == CURRENT_SPACE,
            or_(L.c.person_handle_id.in_(ids), L.c.org_handle_id.in_(ids)),
            or_(L.c.valid_to.is_(None), L.c.valid_to > func.now()),
            # A link that starts later is not in force yet (21.4).
            L.c.valid_from <= func.now(),
        )
        suggestions = select(
            literal("suggestion"),
            S.c.suggestion_id,
            S.c.handle_a,
            S.c.handle_b,
            no_role,
            S.c.created_at,
            S.c.score,
        ).where(
            S.c.space_id == CURRENT_SPACE,
            S.c.status == "open",
            or_(S.c.handle_a.in_(ids), S.c.handle_b.in_(ids)),
        )
        tied = union_all(links, suggestions).subquery("tied")
        side_a, side_b = PM.alias("side_a"), PM.alias("side_b")
        stmt = (
            select(tied, side_a.c.profile_id.label("a_profile"), side_b.c.profile_id.label("b_profile"))
            .select_from(
                tied.outerjoin(
                    side_a, and_(side_a.c.space_id == CURRENT_SPACE, side_a.c.handle_id == tied.c.a)
                ).outerjoin(side_b, and_(side_b.c.space_id == CURRENT_SPACE, side_b.c.handle_id == tied.c.b))
            )
            .order_by(tied.c.kind, tied.c.tie_id)
        )
        return [
            Tie(r.kind, r.tie_id, r.a, r.b, r.a_profile, r.b_profile, r.since, r.role, r.score)
            for r in await self._db.execute(stmt)
        ]

    async def activity(
        self,
        *,
        profile_ids: Sequence[UUID] = (),
        object_ids: Sequence[UUID] = (),
        handle_ids: Sequence[UUID] = (),
        subject_hmacs: Sequence[bytes] = (),
        owner_handle_ids: Sequence[UUID] = (),
        customers: Mapping[UUID, Sequence[UUID]] | None = None,
    ) -> Activity:
        pairs = sorted({(p, h) for p, handles in (customers or {}).items() for h in handles})
        params: dict[str, Any] = {
            "customer_ids": [p for p, _ in pairs],
            "customer_handles": [h for _, h in pairs],
            "profiles": sorted(set(profile_ids)),
            "objects": sorted(set(object_ids)),
            "handles": sorted(set(handle_ids)),
            "subjects": sorted(set(subject_hmacs)),
            "owners": sorted(set(owner_handle_ids)),
        }
        wanted = {
            "talks": params["profiles"],
            "acts_on_objects": params["objects"],
            "reports_on_objects": params["objects"],
            "acts_on_handles": params["handles"],
            "handoffs": params["subjects"],
            "acts_by_owner": params["owners"],
            "reports_by_owner": params["owners"],
            "owned": params["owners"],
            "involved": pairs,
        }
        parts = [_PARTS[name] for name, ids in wanted.items() if ids]
        found = Activity()
        if not parts:
            return found
        rows = await self._db.execute(text(" UNION ALL ".join(parts)), params)
        for r in rows:
            match r.kind:
                case "talk":
                    found.talks.append(
                        Talk(r.source_id, r.profile_id, r.n, r.last_at, tuple(sorted(r.labels)))
                    )
                case "acts":
                    found.acts.append(
                        Acts(
                            r.source_id,
                            r.object_id,
                            r.handle_id,
                            r.n,
                            r.last_at,
                            tuple(sorted(r.labels)),
                            _latest(r.action_id, r.event_id, r.source_id, r.operation, r.status, r.last_at),
                        )
                    )
                case "reports":
                    found.reports.append(Reports(r.source_id, r.object_id, r.handle_id, r.n, r.last_at))
                case "handoff":
                    found.handoffs.append(
                        Handoffs(r.source_id, bytes(r.subject), r.target, r.n, r.last_at, r.status, r.outcome)
                    )
                case "owned":
                    found.owned[r.handle_id] = r.n
                case "involved":
                    found.involved[r.profile_id] = r.n
        return found


def _latest(
    action_id: UUID, event_id: UUID, source_id: UUID, operation: str, status: str, at: datetime
) -> AgentAction:
    return AgentAction(action_id, event_id, source_id, operation, ActionStatus(status), at)
