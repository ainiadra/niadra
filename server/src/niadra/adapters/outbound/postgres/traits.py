"""PostgreSQL implementation of `TraitRepository`.

`traits.value_ct` is encrypted (5.14.1). `trait_opt_outs` was added by the revision
`traits: trait opt-outs` and is declared here until the shared schema models catch up.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Row, Select, Table, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.privacy_rows import changed_rows, delete_keys, delete_where
from niadra.adapters.outbound.postgres.schema.memory import TraitEvidence, TraitOptOut, TraitRow
from niadra.adapters.outbound.postgres.schema.platform import SpaceState
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.adapters.outbound.postgres.vectors import (
    NO_VECTOR,
    needs_vector,
    opened_vector,
    sealed_vector,
    store,
    vector_columns,
)
from niadra.domain.compile.model import ItemKind
from niadra.domain.exported import export_row
from niadra.domain.memory.erasure import plan_evidence_erasure
from niadra.domain.memory.vectors import ItemVector, PendingVector
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.records import Trait
from niadra.domain.traits.evidence import EvidenceRef

_T = cast(Table, TraitRow.__table__)
RULES_FINGERPRINT = "trait_rules_fingerprint"
"""A counter row of `space_state`: the pattern rules a space's profiles were last derived with."""
_EV = cast(Table, TraitEvidence.__table__)
OPT_OUTS = cast(Table, TraitOptOut.__table__)
_EVIDENCE_KEY = ("trait_id", "origin_handle_id", "evidence_kind", "evidence_id")
type _Evidence = tuple[UUID, str, UUID]
"""(origin handle, kind, id) of one evidence row."""


class PgTraitRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer

    @property
    def _seal(self) -> Sealer:
        assert self._sealer is not None, "trait repositories run in a space-scoped transaction"
        return self._sealer

    async def active_for(self, profile_id: UUID) -> list[Trait]:
        return await self._traits(
            select(_T)
            .where(
                _T.c.space_id == CURRENT_SPACE,
                _T.c.profile_id == profile_id,
                _T.c.retracted_at.is_(None),
                _T.c.expires_at > func.now(),
            )
            .order_by(_T.c.name)
        )

    async def traits_of(self, profile_id: UUID) -> list[Trait]:
        return await self._traits(
            select(_T)
            .where(_T.c.space_id == CURRENT_SPACE, _T.c.profile_id == profile_id)
            .order_by(_T.c.name)
        )

    async def profiles_with_traits(self, *, limit: int) -> list[UUID]:
        stmt = (
            select(_T.c.profile_id)
            .where(_T.c.space_id == CURRENT_SPACE, _T.c.retracted_at.is_(None))
            .group_by(_T.c.profile_id)
            .order_by(_T.c.profile_id)
            .limit(limit)
        )
        return [row[0] for row in await self._db.execute(stmt)]

    async def rules_fingerprint(self) -> int | None:
        found: int | None = await self._db.scalar(
            select(SpaceState.value).where(SpaceState.key == RULES_FINGERPRINT)
        )
        return found

    async def set_rules_fingerprint(self, value: int) -> None:
        stmt = insert(SpaceState).values(key=RULES_FINGERPRINT, value=value)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[SpaceState.key], set_={"value": value, "updated_at": func.now()}
            )
        )

    async def get_trait(self, trait_id: UUID) -> Trait | None:
        found = await self._traits(
            select(_T).where(_T.c.space_id == CURRENT_SPACE, _T.c.trait_id == trait_id)
        )
        return found[0] if found else None

    async def evidence_of(self, trait_ids: Sequence[UUID]) -> dict[UUID, tuple[EvidenceRef, ...]]:
        if not trait_ids:
            return {}
        rows = await self._db.execute(
            select(_EV)
            .where(_EV.c.space_id == CURRENT_SPACE, _EV.c.trait_id.in_(list(trait_ids)))
            .order_by(_EV.c.trait_id, _EV.c.evidence_kind, _EV.c.evidence_id)
        )
        found: dict[UUID, list[EvidenceRef]] = {}
        for r in rows:
            found.setdefault(r.trait_id, []).append(
                EvidenceRef(r.evidence_kind, r.evidence_id, r.origin_handle_id)
            )
        return {t: tuple(refs) for t, refs in found.items()}

    async def save_trait(self, trait: Trait, evidence: Sequence[EvidenceRef]) -> None:
        stored = (
            await self._db.execute(
                select(_T.c.trait_id).where(
                    _T.c.space_id == CURRENT_SPACE,
                    _T.c.profile_id == trait.profile_id,
                    _T.c.name == trait.name,
                )
            )
        ).scalar()
        # The ciphertext is bound to the row it lives in, so it is sealed for the id that will hold it.
        trait_id = stored or trait.trait_id
        value_ct, dek = await self._seal.seal(trait.value, table="traits", row=trait_id, column="value_ct")
        values = {
            "membership_version": trait.membership_version,
            "value_ct": value_ct,
            "layer": trait.layer,
            "rule_version": trait.rule_version,
            "first_seen": trait.first_seen,
            "last_evidence_at": trait.last_evidence_at,
            "expires_at": trait.expires_at,
            "confidence": trait.confidence,
            "retracted_at": trait.retracted_at,
            "dek_version": dek,
            **NO_VECTOR,
        }
        await self._db.execute(
            insert(_T)
            .values(
                space_id=CURRENT_SPACE,
                trait_id=trait_id,
                profile_id=trait.profile_id,
                name=trait.name,
                **values,
            )
            .on_conflict_do_update(index_elements=["space_id", "profile_id", "name"], set_=values)
        )
        await self._db.execute(delete(_EV).where(_EV.c.space_id == CURRENT_SPACE, _EV.c.trait_id == trait_id))
        rows = {
            (e.kind, e.id, e.origin_handle_id): {
                "space_id": CURRENT_SPACE,
                "trait_id": trait_id,
                "origin_handle_id": e.origin_handle_id,
                "evidence_kind": e.kind,
                "evidence_id": e.id,
            }
            for e in evidence
        }
        if rows:
            await self._db.execute(insert(_EV).values(list(rows.values())).on_conflict_do_nothing())

    async def vectors_of(self, trait_ids: Sequence[UUID]) -> list[ItemVector]:
        if not trait_ids:
            return []
        rows = await self._db.execute(
            select(_T.c.trait_id, *vector_columns(_T)).where(
                _T.c.space_id == CURRENT_SPACE,
                _T.c.trait_id.in_(list(trait_ids)),
                _T.c.embedding_ct.is_not(None),
            )
        )
        return [await opened_vector(self._seal, ItemKind.TRAIT, "traits", r, r.trait_id) for r in rows]

    async def unembedded(
        self, *, model: str | None, profile_id: UUID | None = None, limit: int
    ) -> list[PendingVector]:
        query = select(_T).where(
            _T.c.space_id == CURRENT_SPACE,
            _T.c.retracted_at.is_(None),
            _T.c.expires_at > func.now(),
            needs_vector(_T, model),
        )
        if profile_id is not None:
            query = query.where(_T.c.profile_id == profile_id)
        rows = (await self._db.execute(query.order_by(_T.c.trait_id).limit(limit))).all()
        evidence = await self.evidence_of([r.trait_id for r in rows])
        # A new derivation rewrites the row in place, value included.
        return [
            PendingVector(await self._trait(r, evidence.get(r.trait_id, ())), fence=r.change_seq)
            for r in rows
        ]

    async def set_vector(self, pending: PendingVector, vector: Sequence[float], model: str) -> bool:
        values = await sealed_vector(self._seal, "traits", pending.item_id, vector, model)
        where = [_T.c.space_id == CURRENT_SPACE, _T.c.trait_id == pending.item_id]
        return await store(self._db, _T, where, values, pending.fence)

    async def delete_trait(self, trait_id: UUID) -> None:
        await self._db.execute(delete(_EV).where(_EV.c.space_id == CURRENT_SPACE, _EV.c.trait_id == trait_id))
        await self._db.execute(delete(_T).where(_T.c.space_id == CURRENT_SPACE, _T.c.trait_id == trait_id))

    async def retract(self, trait_id: UUID, at: datetime) -> None:
        await self._db.execute(
            update(_T)
            .where(_T.c.space_id == CURRENT_SPACE, _T.c.trait_id == trait_id)
            .values(retracted_at=at)
        )

    async def expiring(self, now: datetime, *, limit: int = 500) -> list[Trait]:
        return await self._traits(
            select(_T)
            .where(
                _T.c.space_id == CURRENT_SPACE,
                _T.c.retracted_at.is_(None),
                _T.c.expires_at <= now,
            )
            .order_by(_T.c.expires_at, _T.c.trait_id)
            .limit(limit)
        )

    async def traits_with_evidence_from(self, handle_ids: Sequence[UUID]) -> list[Trait]:
        from_handles = select(_EV.c.trait_id).where(
            _EV.c.space_id == CURRENT_SPACE, _EV.c.origin_handle_id.in_(list(handle_ids))
        )
        return await self._traits(
            select(_T)
            .where(_T.c.space_id == CURRENT_SPACE, _T.c.trait_id.in_(from_handles))
            .order_by(_T.c.trait_id)
        )

    async def opt_out(self, handle_ids: Sequence[UUID], at: datetime) -> None:
        if handle_ids:
            rows = [{"space_id": CURRENT_SPACE, "handle_id": h, "opted_out_at": at} for h in handle_ids]
            await self._db.execute(insert(OPT_OUTS).values(rows).on_conflict_do_nothing())

    async def opted_out(self, handle_ids: Sequence[UUID]) -> bool:
        query = select(
            exists().where(OPT_OUTS.c.space_id == CURRENT_SPACE, OPT_OUTS.c.handle_id.in_(list(handle_ids)))
        )
        return bool((await self._db.execute(query)).scalar())

    async def opt_outs(self, handle_ids: Sequence[UUID]) -> list[tuple[UUID, datetime]]:
        if not handle_ids:
            return []
        query = (
            select(OPT_OUTS.c.handle_id, OPT_OUTS.c.opted_out_at)
            .where(OPT_OUTS.c.space_id == CURRENT_SPACE, OPT_OUTS.c.handle_id.in_(list(handle_ids)))
            .order_by(OPT_OUTS.c.handle_id)
        )
        return [(row.handle_id, row.opted_out_at) for row in await self._db.execute(query)]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        ev = _EV.c
        if scope.event_ids:
            events = set(scope.event_ids)
            by_event = (ev.evidence_kind == "event", ev.evidence_id.in_(list(events)))
            return await self._erase_evidence(by_event, lambda e: e[1] == "event" and e[2] in events, limit)
        if scope.kind is TargetKind.CONVERSATION:
            return []  # the evidence step already took what pointed at the conversation's events
        handles = set(scope.handle_ids)
        erased: list[ErasedRow] = []
        if handles:
            # Evidence from these handles may back traits of another profile after an unmerge (20.2).
            by_handle = (ev.origin_handle_id.in_(list(handles)),)
            erased += await self._erase_evidence(by_handle, lambda e: e[0] in handles, limit)
        if scope.profile_id is not None:
            own = select(_T.c.trait_id).where(
                _T.c.space_id == CURRENT_SPACE, _T.c.profile_id == scope.profile_id
            )
            erased += await delete_where(
                self._db, _EV, _EVIDENCE_KEY, [ev.trait_id.in_(own)], limit - len(erased)
            )
            erased += await delete_where(
                self._db, _T, ("trait_id",), [_T.c.profile_id == scope.profile_id], limit - len(erased)
            )
        if handles:
            erased += await delete_where(
                self._db,
                OPT_OUTS,
                ("handle_id",),
                [OPT_OUTS.c.handle_id.in_(list(handles))],
                limit - len(erased),
            )
        return erased

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table not in ("traits", "trait_evidence"):
            raise ValueError(f"traits does not export {table}")
        source = _T if table == "traits" else _EV
        rows = await changed_rows(self._db, source, after_seq=after_seq, upto_seq=upto_seq, limit=limit)
        if table == "traits":
            return [export_row(await self._trait(r, ()), r.change_seq, drop=("evidence",)) for r in rows]
        return [{**{k: r._mapping[k] for k in _EVIDENCE_KEY}, "change_seq": r.change_seq} for r in rows]

    async def _erase_evidence(
        self, touched: tuple[Any, ...], erased: Callable[[_Evidence], bool], limit: int
    ) -> list[ErasedRow]:
        """Evidence rows that match go, and a trait goes with its last evidence row (L17)."""
        ev = _EV.c
        hit = select(ev.trait_id).where(ev.space_id == CURRENT_SPACE, *touched).distinct().limit(limit)
        rows = await self._db.execute(
            select(ev.trait_id, ev.origin_handle_id, ev.evidence_kind, ev.evidence_id).where(
                ev.space_id == CURRENT_SPACE, ev.trait_id.in_(hit)
            )
        )
        evidence: dict[UUID, set[_Evidence]] = {}
        for trait_id, origin, kind, evidence_id in rows:
            evidence.setdefault(trait_id, set()).add((origin, kind, evidence_id))
        plan = plan_evidence_erasure(evidence, erased, limit)
        found = await delete_keys(self._db, _EV, _EVIDENCE_KEY, [(t, *e) for t, e in plan.evidence])
        return found + await delete_keys(self._db, _T, ("trait_id",), [(t,) for t in plan.items])

    async def _traits(self, query: Select[Any]) -> list[Trait]:
        rows = (await self._db.execute(query)).all()
        evidence = await self.evidence_of([r.trait_id for r in rows])
        return [await self._trait(r, evidence.get(r.trait_id, ())) for r in rows]

    async def _trait(self, r: Row[Any], evidence: Sequence[EvidenceRef]) -> Trait:
        value = await self._seal.open_text(
            r.value_ct, r.dek_version, table="traits", row=r.trait_id, column="value_ct"
        )
        return Trait(
            trait_id=r.trait_id,
            profile_id=r.profile_id,
            membership_version=r.membership_version,
            name=r.name,
            value=value,
            layer=r.layer,
            rule_version=r.rule_version,
            first_seen=r.first_seen,
            last_evidence_at=r.last_evidence_at,
            expires_at=r.expires_at,
            confidence=r.confidence,
            evidence=tuple((e.kind, e.id) for e in evidence),
            retracted_at=r.retracted_at,
        )
