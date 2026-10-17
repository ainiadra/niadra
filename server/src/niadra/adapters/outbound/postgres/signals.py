"""PostgreSQL implementation of `SignalRepository` (6.7): `subject_signals`, `beneficiaries`, `signal_intake`
and `inference_suppressions`. Bodies arrive sealed; the derivation's read and write are one statement each
(24.6)."""

import json
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    Table,
    and_,
    bindparam,
    delete,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import (
    ARRAY,
    BIGINT,
    BYTEA,
    DOUBLE_PRECISION,
    INTEGER,
    INTERVAL,
    SMALLINT,
    TEXT,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.mentions import with_entries
from niadra.adapters.outbound.postgres.privacy_rows import delete_where
from niadra.adapters.outbound.postgres.schema.signals import (
    BeneficiaryRow as BeneficiaryTable,
)
from niadra.adapters.outbound.postgres.schema.signals import (
    InferenceSuppressionRow,
    MeasureSegmentRow,
    SignalIntakeRow,
    SignalPriorRow,
    SubjectSignalsRow,
)
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.privacy.mentions import MentionKey, MentionStore
from niadra.domain.signals.daily import Prior
from niadra.domain.signals.records import (
    RECHECK_LEAST,
    RECHECK_MOST,
    SELF,
    SESSION_PURPOSE,
    BeneficiaryRow,
    DueSnapshots,
    PendingRow,
    Segment,
    SignalRow,
    SignalWrite,
    Snapshot,
    SuppressionRow,
    session_digest,
)

S = cast(Table, SubjectSignalsRow.__table__)
B = cast(Table, BeneficiaryTable.__table__)
P = cast(Table, SignalIntakeRow.__table__)
X = cast(Table, InferenceSuppressionRow.__table__)
G = cast(Table, MeasureSegmentRow.__table__)
M = cast(Table, SignalPriorRow.__table__)
MAX_PENDING = 500
"""Pending interactions one derivation takes; the rest wait for the next pass."""

_SNAPSHOT = text(
    """
    SELECT 'row' AS kind, beneficiary_id::text AS key, body_ct, dek_version, body_version, size_bytes,
           derived_at AS at, last_activity_at, opted_out_at, NULL::text AS session
    FROM subject_signals
    WHERE space_id = current_setting('niadra.space_id')::uuid AND origin_handle_id = :handle
    UNION ALL
    SELECT * FROM (
      SELECT 'pending', entry_key, body_ct, dek_version, NULL::smallint, NULL::int, received_at,
             NULL::timestamptz, NULL::timestamptz, session
      FROM signal_intake
      WHERE space_id = current_setting('niadra.space_id')::uuid AND origin_handle_id = :handle
      ORDER BY received_at, entry_key
      LIMIT :pending
    ) AS p
    """
)

_DUE = text(
    """
    WITH due AS (
      SELECT origin_handle_id AS handle
      FROM signal_intake
      WHERE space_id = current_setting('niadra.space_id')::uuid
        AND (origin_handle_id IS NOT NULL OR checked_at IS NULL
             OR checked_at + LEAST(GREATEST(checked_at - received_at, :least), :most) <= :now)
      GROUP BY origin_handle_id
      HAVING min(received_at) <= :ready OR max(received_at) <= :quiet
      ORDER BY min(received_at)
      LIMIT :limit
    )
    SELECT 'row' AS kind, s.origin_handle_id AS handle, s.beneficiary_id::text AS key, s.body_ct,
           s.dek_version, s.body_version, s.size_bytes, s.derived_at AS at, s.last_activity_at,
           s.opted_out_at, NULL::text AS session
    FROM subject_signals s JOIN due ON s.origin_handle_id = due.handle
    WHERE s.space_id = current_setting('niadra.space_id')::uuid
    UNION ALL
    SELECT 'pending', p.origin_handle_id, p.entry_key, p.body_ct, p.dek_version, NULL::smallint, NULL::int,
           p.received_at, NULL::timestamptz, NULL::timestamptz, p.session
    FROM (
      SELECT i.*,
             row_number() OVER (PARTITION BY i.origin_handle_id ORDER BY i.received_at, i.entry_key) AS n
      FROM signal_intake i JOIN due ON i.origin_handle_id = due.handle
      WHERE i.space_id = current_setting('niadra.space_id')::uuid
    ) AS p
    WHERE p.n <= :pending
    UNION ALL
    SELECT 'suppressed', x.origin_handle_id, x.beneficiary_id::text, x.inference_key_hmac, NULL::int,
           NULL::smallint, NULL::int, x.evidence_until, NULL::timestamptz, x.created_at, x.kind
    FROM inference_suppressions x JOIN due ON x.origin_handle_id = due.handle
    WHERE x.space_id = current_setting('niadra.space_id')::uuid
    UNION ALL
    SELECT 'unresolved', NULL::uuid, NULL::text, NULL::bytea, NULL::int, NULL::smallint, NULL::int,
           NULL::timestamptz, NULL::timestamptz, NULL::timestamptz, NULL::text
    FROM due WHERE due.handle IS NULL
    """
)

_STORE = text(
    """
    WITH open AS (
      SELECT DISTINCT w.handle FROM unnest(:handles) AS w(handle)
      WHERE NOT EXISTS (
        SELECT 1 FROM subject_signals s
        WHERE s.space_id = current_setting('niadra.space_id')::uuid AND s.origin_handle_id = w.handle
          AND s.opted_out_at IS NOT NULL
      )
    ), gone AS (
      DELETE FROM signal_intake
      WHERE space_id = current_setting('niadra.space_id')::uuid AND entry_key = ANY(:folded)
    ), beneficiaries_seen AS (
      INSERT INTO beneficiaries
        (space_id, origin_handle_id, beneficiary_id, relation, label_ct, dek_version, status, created_at,
         updated_at)
      SELECT current_setting('niadra.space_id')::uuid, b.handle, b.id, b.relation, b.label, b.dek, 'active',
             :now, :now
      FROM unnest(:b_handles, :b_ids, :b_relations, :b_labels, :b_deks) AS b(handle, id, relation, label, dek)
      JOIN open ON open.handle = b.handle
      ON CONFLICT (space_id, origin_handle_id, beneficiary_id) DO UPDATE SET updated_at = EXCLUDED.updated_at
    ), bodies AS (
      INSERT INTO subject_signals
        (space_id, origin_handle_id, beneficiary_id, body_ct, body_version, size_bytes, derived_at,
         last_activity_at, dek_version)
      SELECT current_setting('niadra.space_id')::uuid, r.handle, r.id, r.body, r.version, r.size, r.derived,
             r.activity, r.dek
      FROM unnest(:r_handles, :r_ids, :r_bodies, :r_versions, :r_sizes, :r_derived, :r_activity, :r_deks)
        AS r(handle, id, body, version, size, derived, activity, dek)
      JOIN open ON open.handle = r.handle
      ON CONFLICT (space_id, origin_handle_id, beneficiary_id) DO UPDATE SET
        body_ct = EXCLUDED.body_ct, body_version = EXCLUDED.body_version, size_bytes = EXCLUDED.size_bytes,
        derived_at = EXCLUDED.derived_at, last_activity_at = EXCLUDED.last_activity_at,
        dek_version = EXCLUDED.dek_version
      WHERE subject_signals.opted_out_at IS NULL
    ), raw_kept AS (
      INSERT INTO raw_objects (space_id, object_key, owner, written_at)
      SELECT current_setting('niadra.space_id')::uuid, k.key, k.owner, :now
      FROM unnest(:raw_keys, :raw_owners) AS k(key, owner)
      ON CONFLICT DO NOTHING
    ), named AS (
      INSERT INTO text_mentions (space_id, store, row_key, tokens)
      SELECT current_setting('niadra.space_id')::uuid, 'signals', m.handle || ':' || m.beneficiary,
             ARRAY(SELECT decode(t, 'hex') FROM jsonb_array_elements_text(m.tokens) AS t)
      FROM jsonb_to_recordset(CAST(:named AS jsonb)) AS m(handle text, beneficiary text, tokens jsonb)
      JOIN open ON open.handle = CAST(m.handle AS uuid)
      ON CONFLICT (space_id, store, row_key) DO UPDATE SET
        tokens = ARRAY(SELECT DISTINCT t FROM unnest(text_mentions.tokens || EXCLUDED.tokens) AS t)
    )
    SELECT handle FROM open
    """
).bindparams(
    bindparam("now", type_=TIMESTAMP(timezone=True)),
    bindparam("handles", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("folded", type_=ARRAY(TEXT)),
    bindparam("b_handles", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("b_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("b_relations", type_=ARRAY(TEXT)),
    bindparam("b_labels", type_=ARRAY(BYTEA)),
    bindparam("b_deks", type_=ARRAY(INTEGER)),
    bindparam("r_handles", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("r_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("r_bodies", type_=ARRAY(BYTEA)),
    bindparam("r_versions", type_=ARRAY(SMALLINT)),
    bindparam("r_sizes", type_=ARRAY(INTEGER)),
    bindparam("r_derived", type_=ARRAY(TIMESTAMP(timezone=True))),
    bindparam("r_activity", type_=ARRAY(TIMESTAMP(timezone=True))),
    bindparam("r_deks", type_=ARRAY(INTEGER)),
    bindparam("raw_keys", type_=ARRAY(TEXT)),
    bindparam("raw_owners", type_=ARRAY(TEXT)),
    bindparam("named", type_=TEXT),
)


_DECAY = text(
    """
    UPDATE subject_signals s
    SET body_ct = d.body, body_version = d.version, size_bytes = d.size, dek_version = d.dek, decayed_at = :at
    FROM unnest(:handles, :ids, :bodies, :versions, :sizes, :deks, :derived)
      AS d(handle, id, body, version, size, dek, derived)
    WHERE s.space_id = current_setting('niadra.space_id')::uuid
      AND s.origin_handle_id = d.handle AND s.beneficiary_id = d.id AND s.derived_at = d.derived
      AND s.opted_out_at IS NULL
    RETURNING s.origin_handle_id
    """
).bindparams(
    bindparam("at", type_=TIMESTAMP(timezone=True)),
    bindparam("handles", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("ids", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("bodies", type_=ARRAY(BYTEA)),
    bindparam("versions", type_=ARRAY(SMALLINT)),
    bindparam("sizes", type_=ARRAY(INTEGER)),
    bindparam("deks", type_=ARRAY(INTEGER)),
    bindparam("derived", type_=ARRAY(TIMESTAMP(timezone=True))),
)
"""The retention pass's rewrite: only a row not derived again since it was read (`derived_at`)."""

_DROP_UNTOLD = text(
    """
    DELETE FROM subject_signals s
    USING unnest(:handles, :derived) AS d(handle, derived)
    WHERE s.space_id = current_setting('niadra.space_id')::uuid
      AND s.origin_handle_id = d.handle AND s.beneficiary_id = '00000000-0000-0000-0000-000000000000'::uuid
      AND s.derived_at = d.derived AND s.opted_out_at IS NULL
    RETURNING s.origin_handle_id, s.beneficiary_id, s.change_seq
    """
).bindparams(
    bindparam("handles", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("derived", type_=ARRAY(TIMESTAMP(timezone=True))),
)
"""A subject's own row that held only derived accumulators: nothing the person said goes with it."""


def _signal_row(r: Any) -> SignalRow:
    return SignalRow(
        handle_id=r.origin_handle_id,
        beneficiary_id=r.beneficiary_id,
        body_ct=bytes(r.body_ct),
        dek_version=r.dek_version,
        body_version=r.body_version,
        size_bytes=r.size_bytes,
        derived_at=r.derived_at,
        last_activity_at=r.last_activity_at,
        opted_out_at=r.opted_out_at,
    )


def _pending(r: Any) -> PendingRow:
    return PendingRow(
        r.entry_key,
        r.origin_handle_id,
        r.received_at,
        bytes(r.body_ct),
        r.dek_version,
        r.session,
        r.checked_at,
    )


_PRIORS = text(
    """
    WITH fresh AS (
      SELECT * FROM unnest(:segments, :attrs, :values, :priors, :exposures, :signals)
        AS f(segment, attr, value, prior, exposures, signals)
    ), gone AS (
      DELETE FROM signal_priors p
      WHERE p.space_id = current_setting('niadra.space_id')::uuid
        AND NOT EXISTS (
          SELECT 1 FROM fresh f WHERE f.segment = p.segment AND f.attr = p.attr AND f.value = p.value
        )
    )
    INSERT INTO signal_priors AS p (space_id, segment, attr, value, prior, exposures, signals, computed_at)
    SELECT current_setting('niadra.space_id')::uuid, segment, attr, value, prior, exposures, signals, :at
    FROM fresh
    ON CONFLICT (space_id, segment, attr, value) DO UPDATE SET
      prior = EXCLUDED.prior, exposures = EXCLUDED.exposures, signals = EXCLUDED.signals,
      computed_at = EXCLUDED.computed_at
    """
).bindparams(
    bindparam("at", type_=TIMESTAMP(timezone=True)),
    bindparam("segments", type_=ARRAY(TEXT)),
    bindparam("attrs", type_=ARRAY(TEXT)),
    bindparam("values", type_=ARRAY(TEXT)),
    bindparam("priors", type_=ARRAY(DOUBLE_PRECISION)),
    bindparam("exposures", type_=ARRAY(BIGINT)),
    bindparam("signals", type_=ARRAY(DOUBLE_PRECISION)),
)


class PgSignalRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    async def add_pending(self, rows: Sequence[PendingRow]) -> None:
        if not rows:
            return
        values = [
            {
                "space_id": CURRENT_SPACE,
                "entry_key": r.entry_key,
                "origin_handle_id": r.handle_id,
                "session": r.session,
                "received_at": r.received_at,
                "body_ct": r.body_ct,
                "dek_version": r.dek_version,
            }
            for r in rows
        ]
        named = [(MentionKey(MentionStore.INTAKE, r.entry_key), r.named) for r in rows]
        await self._db.execute(with_entries(pg_insert(P).values(values).on_conflict_do_nothing(), named))

    async def due_snapshots(
        self, now: datetime, *, window: timedelta, quiet: timedelta, limit: int
    ) -> DueSnapshots:
        params = {
            "ready": now - window, "quiet": now - quiet, "limit": limit, "pending": MAX_PENDING, "now": now,
            "least": RECHECK_LEAST, "most": RECHECK_MOST,
        }  # fmt: skip
        rows: dict[UUID, list[SignalRow]] = {}
        pending: dict[UUID, list[PendingRow]] = {}
        suppressed: dict[UUID, list[SuppressionRow]] = {}
        unresolved = False
        for r in await self._db.execute(_DUE, params):
            if r.kind == "unresolved":
                unresolved = True
            elif r.kind == "suppressed":
                # The columns of the union: the tombstone's hash rides in `body_ct`, its kind in `session`.
                suppressed.setdefault(r.handle, []).append(
                    SuppressionRow(r.handle, UUID(r.key), bytes(r.body_ct), r.session, r.at, r.opted_out_at)
                )
            elif r.kind == "row":
                rows.setdefault(r.handle, []).append(
                    SignalRow(
                        r.handle, UUID(r.key), bytes(r.body_ct), r.dek_version, r.body_version, r.size_bytes,
                        r.at, r.last_activity_at, r.opted_out_at,
                    )
                )  # fmt: skip
            else:
                pending.setdefault(r.handle, []).append(
                    PendingRow(r.key, r.handle, r.at, bytes(r.body_ct), r.dek_version, r.session)
                )
        snapshots = {
            handle: Snapshot(tuple(rows.get(handle, ())), tuple(found), tuple(suppressed.get(handle, ())))
            for handle, found in pending.items()
        }
        return DueSnapshots(snapshots, unresolved)

    async def unresolved(self, now: datetime, limit: int) -> list[PendingRow]:
        waited = func.greatest(P.c.checked_at - P.c.received_at, bindparam("least", RECHECK_LEAST, INTERVAL))
        wait = func.least(waited, bindparam("most", RECHECK_MOST, INTERVAL), type_=INTERVAL)
        stmt = (
            select(P)
            .where(
                P.c.space_id == CURRENT_SPACE,
                P.c.origin_handle_id.is_(None),
                or_(P.c.checked_at.is_(None), P.c.checked_at + wait <= now),
            )
            .order_by(P.c.received_at, P.c.entry_key)
            .limit(limit)
        )
        return [_pending(r) for r in await self._db.execute(stmt)]

    async def resolve(self, found: Mapping[str, UUID]) -> None:
        if not found:
            return
        pairs = func.unnest(
            bindparam("keys", list(found), type_=ARRAY(TEXT)),
            bindparam("handles", list(found.values()), type_=ARRAY(PG_UUID(as_uuid=True))),
        ).table_valued("entry_key", "handle_id")
        await self._db.execute(
            update(P)
            .where(P.c.space_id == CURRENT_SPACE, P.c.entry_key == pairs.c.entry_key)
            .values(origin_handle_id=pairs.c.handle_id)
        )

    async def checked(self, entry_keys: Sequence[str], at: datetime) -> None:
        if entry_keys:
            await self._db.execute(
                update(P)
                .where(
                    P.c.space_id == CURRENT_SPACE,
                    P.c.entry_key.in_(list(entry_keys)),
                    P.c.origin_handle_id.is_(None),
                )
                .values(checked_at=at)
            )

    async def drop_unattributed(self, sessions: Sequence[str]) -> list[ErasedRow]:
        if not sessions:
            return []
        stmt = (
            delete(P)
            .where(
                P.c.space_id == CURRENT_SPACE,
                P.c.origin_handle_id.is_(None),
                P.c.session.in_(list(sessions)),
            )
            .returning(P.c.entry_key)
        )
        return [ErasedRow(P.name, {"entry_key": k}) for k in (await self._db.execute(stmt)).scalars()]

    async def put_segment(self, segment: Segment, at: datetime) -> None:
        values = {
            "space_id": CURRENT_SPACE, "segment_id": segment.segment_id, "kind": segment.kind,
            "starts_at": segment.starts_at, "object_key": segment.object_key, "rows": segment.rows,
            "size_bytes": segment.size_bytes, "sha256": segment.sha256, "created_at": at,
            "expires_at": segment.expires_at,
        }  # fmt: skip
        stmt = pg_insert(G).values(**values)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[G.c.space_id, G.c.kind, G.c.starts_at],
                set_={
                    "object_key": stmt.excluded.object_key, "rows": stmt.excluded.rows,
                    "size_bytes": stmt.excluded.size_bytes, "sha256": stmt.excluded.sha256,
                    "rewritten_at": stmt.excluded.created_at, "expires_at": stmt.excluded.expires_at,
                },
            )
        )  # fmt: skip

    async def segments(self, kind: str, since: datetime) -> list[Segment]:
        stmt = (
            select(G)
            .where(G.c.space_id == CURRENT_SPACE, G.c.kind == kind, G.c.starts_at >= since)
            .order_by(G.c.starts_at)
        )
        return [
            Segment(r.segment_id, r.kind, r.starts_at, r.object_key, r.rows, r.size_bytes, bytes(r.sha256),
                    r.expires_at)
            for r in await self._db.execute(stmt)
        ]  # fmt: skip

    async def rewrite_segment(self, segment: Segment, at: datetime) -> None:
        await self._db.execute(
            update(G)
            .where(G.c.space_id == CURRENT_SPACE, G.c.segment_id == segment.segment_id)
            .values(rows=segment.rows, size_bytes=segment.size_bytes, sha256=segment.sha256, rewritten_at=at)
        )

    async def expire_segments(self, now: datetime) -> list[str]:
        stmt = delete(G).where(G.c.space_id == CURRENT_SPACE, G.c.expires_at <= now).returning(G.c.object_key)
        return list((await self._db.execute(stmt)).scalars())

    async def replace_priors(self, priors: Sequence[Prior], at: datetime) -> None:
        await self._db.execute(
            _PRIORS,
            {
                "at": at, "segments": [p.segment for p in priors], "attrs": [p.attr for p in priors],
                "values": [p.value for p in priors], "priors": [p.prior for p in priors],
                "exposures": [p.exposures for p in priors], "signals": [p.signals for p in priors],
            },
        )  # fmt: skip

    async def priors(self) -> list[Prior]:
        stmt = select(M).where(M.c.space_id == CURRENT_SPACE).order_by(M.c.segment, M.c.attr, M.c.value)
        return [
            Prior(r.attr, r.value, r.prior, r.exposures, r.signals, 0, r.segment)
            for r in await self._db.execute(stmt)
        ]

    async def pending_page(self, after: str | None, limit: int) -> list[PendingRow]:
        stmt = select(P).where(P.c.space_id == CURRENT_SPACE, P.c.origin_handle_id.is_not(None))
        if after is not None:
            stmt = stmt.where(P.c.entry_key > after)
        stmt = stmt.order_by(P.c.entry_key).limit(limit)
        return [_pending(r) for r in await self._db.execute(stmt)]

    async def intake_page(self, after: str | None, limit: int) -> list[PendingRow]:
        stmt = select(P).where(P.c.space_id == CURRENT_SPACE)
        if after is not None:
            stmt = stmt.where(P.c.entry_key > after)
        return [_pending(r) for r in await self._db.execute(stmt.order_by(P.c.entry_key).limit(limit))]

    async def pending_by_keys(self, entry_keys: Sequence[str]) -> list[PendingRow]:
        if not entry_keys:
            return []
        stmt = select(P).where(P.c.space_id == CURRENT_SPACE, P.c.entry_key.in_(list(entry_keys)))
        return [_pending(r) for r in await self._db.execute(stmt)]

    async def rewrite_pending(self, rows: Sequence[PendingRow]) -> None:
        for r in rows:
            await self._db.execute(
                update(P)
                .where(P.c.space_id == CURRENT_SPACE, P.c.entry_key == r.entry_key)
                .values(body_ct=r.body_ct, dek_version=r.dek_version)
            )

    async def pending_of(self, handle_ids: Sequence[UUID]) -> list[PendingRow]:
        if not handle_ids:
            return []
        stmt = (
            select(P)
            .where(P.c.space_id == CURRENT_SPACE, P.c.origin_handle_id.in_(list(handle_ids)))
            .order_by(P.c.received_at, P.c.entry_key)
            .limit(MAX_PENDING)
        )
        return [_pending(r) for r in await self._db.execute(stmt)]

    async def snapshot(self, handle_id: UUID) -> Snapshot:
        result = await self._db.execute(_SNAPSHOT, {"handle": handle_id, "pending": MAX_PENDING})
        rows: list[SignalRow] = []
        pending: list[PendingRow] = []
        for r in result:
            if r.kind == "row":
                rows.append(
                    SignalRow(
                        handle_id, UUID(r.key), bytes(r.body_ct), r.dek_version, r.body_version, r.size_bytes,
                        r.at, r.last_activity_at, r.opted_out_at,
                    )
                )  # fmt: skip
            else:
                pending.append(PendingRow(r.key, handle_id, r.at, bytes(r.body_ct), r.dek_version, r.session))
        return Snapshot(tuple(rows), tuple(pending))

    async def store(self, write: SignalWrite) -> bool:
        return write.handle_id in await self.store_many([write])

    async def store_many(self, writes: Sequence[SignalWrite]) -> set[UUID]:
        if not writes:
            return set()
        rows = [r for w in writes for r in w.rows]
        bens = [b for w in writes for b in w.beneficiaries]
        params = {
            "now": max(w.at for w in writes),
            "handles": [w.handle_id for w in writes],
            "folded": [key for w in writes for key in w.folded],
            "b_handles": [b.handle_id for b in bens],
            "b_ids": [b.beneficiary_id for b in bens],
            "b_relations": [b.relation for b in bens],
            "b_labels": [b.label_ct for b in bens],
            "b_deks": [b.dek_version for b in bens],
            "r_handles": [r.handle_id for r in rows],
            "r_ids": [r.beneficiary_id for r in rows],
            "r_bodies": [r.body_ct for r in rows],
            "r_versions": [r.body_version for r in rows],
            "r_sizes": [r.size_bytes for r in rows],
            "r_derived": [r.derived_at for r in rows],
            "r_activity": [r.last_activity_at for r in rows],
            "r_deks": [r.dek_version for r in rows],
            # The raw interactions kept for measurement (`domain.privacy.raw_index`), whoever objected.
            "raw_keys": [w.raw_key for w in writes if w.raw_key],
            "raw_owners": [str(w.handle_id) for w in writes if w.raw_key],
            # What each body names a person by (`domain.privacy.mentions`), indexed with it.
            "named": json.dumps(
                [
                    {
                        "handle": str(w.handle_id),
                        "beneficiary": str(b),
                        "tokens": sorted(t.hex() for t in tokens),
                    }
                    for w in writes
                    for b, tokens in w.named
                    if tokens
                ]
            ),
        }
        return set((await self._db.execute(_STORE, params)).scalars())

    async def rows_for(self, handle_ids: Sequence[UUID]) -> list[SignalRow]:
        if not handle_ids:
            return []
        stmt = select(S).where(S.c.space_id == CURRENT_SPACE, S.c.origin_handle_id.in_(list(handle_ids)))
        return [_signal_row(r) for r in await self._db.execute(stmt)]

    signals_for = rows_for

    async def rows_page(self, after: tuple[UUID, UUID] | None, limit: int) -> list[SignalRow]:
        stmt = select(S).where(S.c.space_id == CURRENT_SPACE)
        if after is not None:
            key = tuple_(literal(after[0], PG_UUID(as_uuid=True)), literal(after[1], PG_UUID(as_uuid=True)))
            stmt = stmt.where(tuple_(S.c.origin_handle_id, S.c.beneficiary_id) > key)
        rows = await self._db.execute(stmt.order_by(S.c.origin_handle_id, S.c.beneficiary_id).limit(limit))
        return [_signal_row(r) for r in rows]

    async def beneficiaries_for(self, handle_ids: Sequence[UUID]) -> list[BeneficiaryRow]:
        if not handle_ids:
            return []
        stmt = select(B).where(B.c.space_id == CURRENT_SPACE, B.c.origin_handle_id.in_(list(handle_ids)))
        return [
            BeneficiaryRow(
                r.origin_handle_id, r.beneficiary_id, r.relation, bytes(r.label_ct) if r.label_ct else None,
                r.dek_version, r.status, r.created_at, r.updated_at,
            )
            for r in await self._db.execute(stmt)
        ]  # fmt: skip

    async def withdraw(self, handle_ids: Sequence[UUID], markers: Sequence[SignalRow]) -> list[ErasedRow]:
        handles = list(handle_ids)
        erased: list[ErasedRow] = []
        for table, key in (
            (S, ("origin_handle_id", "beneficiary_id")),
            (B, ("origin_handle_id", "beneficiary_id")),
        ):
            erased += await _delete_all(self._db, table, key, table.c.origin_handle_id.in_(handles))
        erased += await _delete_all(self._db, P, ("entry_key",), P.c.origin_handle_id.in_(handles))
        if markers:
            await self._db.execute(
                insert(S).values(
                    [
                        {
                            "space_id": CURRENT_SPACE,
                            "origin_handle_id": m.handle_id,
                            "beneficiary_id": m.beneficiary_id,
                            "body_ct": m.body_ct,
                            "body_version": m.body_version,
                            "size_bytes": m.size_bytes,
                            "derived_at": m.derived_at,
                            "last_activity_at": m.last_activity_at,
                            "dek_version": m.dek_version,
                            "opted_out_at": m.opted_out_at,
                        }
                        for m in markers
                    ]
                )
            )
        return erased

    async def inactive(
        self,
        before: datetime,
        *,
        limit: int,
        keep: Collection[UUID] = (),
        after: tuple[UUID, UUID] | None = None,
    ) -> list[SignalRow]:
        where = [
            S.c.space_id == CURRENT_SPACE,
            S.c.last_activity_at < before,
            S.c.opted_out_at.is_(None),
            or_(S.c.decayed_at.is_(None), S.c.decayed_at < S.c.last_activity_at),
        ]
        if keep:
            where.append(S.c.origin_handle_id.not_in(list(keep)))
        if after is not None:
            last = tuple_(literal(after[0]), literal(after[1]))
            where.append(tuple_(S.c.origin_handle_id, S.c.beneficiary_id) > last)
        stmt = select(S).where(*where).order_by(S.c.origin_handle_id, S.c.beneficiary_id).limit(limit)
        return [_signal_row(r) for r in await self._db.execute(stmt)]

    async def decay(self, rows: Sequence[SignalRow], at: datetime) -> int:
        if not rows:
            return 0
        result = await self._db.execute(
            _DECAY,
            {
                "at": at,
                "handles": [r.handle_id for r in rows],
                "ids": [r.beneficiary_id for r in rows],
                "bodies": [r.body_ct for r in rows],
                "versions": [r.body_version for r in rows],
                "sizes": [r.size_bytes for r in rows],
                "deks": [r.dek_version for r in rows],
                "derived": [r.derived_at for r in rows],
            },
        )
        return len(result.all())

    async def drop_untold(self, rows: Sequence[SignalRow]) -> list[ErasedRow]:
        mine = [r for r in rows if r.beneficiary_id == SELF]
        if not mine:
            return []
        found = await self._db.execute(
            _DROP_UNTOLD,
            {"handles": [r.handle_id for r in mine], "derived": [r.derived_at for r in mine]},
        )
        return [
            ErasedRow(
                S.name,
                {"origin_handle_id": str(r.origin_handle_id), "beneficiary_id": str(r.beneficiary_id)},
                change_seq=r.change_seq,
            )
            for r in found
        ]

    async def add_suppressions(self, rows: Sequence[SuppressionRow]) -> None:
        if not rows:
            return
        stmt = pg_insert(X).values(
            [
                {
                    "space_id": CURRENT_SPACE,
                    "origin_handle_id": r.handle_id,
                    "beneficiary_id": r.beneficiary_id,
                    "inference_key_hmac": r.key_hmac,
                    "kind": r.kind,
                    "evidence_until": r.evidence_until,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
        )
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    X.c.space_id,
                    X.c.origin_handle_id,
                    X.c.beneficiary_id,
                    X.c.inference_key_hmac,
                ],
                set_={"evidence_until": func.greatest(X.c.evidence_until, stmt.excluded.evidence_until)},
            )
        )

    async def suppressions_for(self, handle_ids: Sequence[UUID]) -> list[SuppressionRow]:
        if not handle_ids:
            return []
        stmt = select(X).where(X.c.space_id == CURRENT_SPACE, X.c.origin_handle_id.in_(list(handle_ids)))
        return [
            SuppressionRow(
                r.origin_handle_id, r.beneficiary_id, bytes(r.inference_key_hmac), r.kind, r.evidence_until,
                r.created_at,
            )
            for r in await self._db.execute(stmt)
        ]  # fmt: skip

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.kind is TargetKind.CONVERSATION:
            if scope.conversation_id is None or self._sealer is None:
                return []
            session = session_digest(await self._sealer.hmac(SESSION_PURPOSE, scope.conversation_id))
            return await delete_where(self._db, P, ("entry_key",), [P.c.session == session], limit)
        if not scope.handle_ids:
            return []
        handles = list(scope.handle_ids)
        rows: list[ErasedRow] = []
        steps = (
            (S, ("origin_handle_id", "beneficiary_id")),
            (B, ("origin_handle_id", "beneficiary_id")),
            (X, ("origin_handle_id", "beneficiary_id", "inference_key_hmac")),
        )
        for table, key in steps:
            rows += await delete_where(
                self._db, table, key, [table.c.origin_handle_id.in_(handles)], limit - len(rows)
            )
        rows += await delete_where(
            self._db, P, ("entry_key",), [P.c.origin_handle_id.in_(handles)], limit - len(rows)
        )
        return rows


async def _delete_all(db: AsyncSession, table: Table, key: Sequence[str], where: Any) -> list[ErasedRow]:
    stmt = (
        delete(table)
        .where(and_(table.c.space_id == CURRENT_SPACE, where))
        .returning(*(table.c[k] for k in key))
    )
    return [
        ErasedRow(table.name, {k: str(v) for k, v in zip(key, row, strict=True)})
        for row in (await db.execute(stmt)).all()
    ]
