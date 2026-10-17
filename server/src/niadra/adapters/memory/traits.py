"""In-memory `TraitRepository` for unit tests."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.change_seq import ChangeSeq
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.compile.model import ItemKind
from niadra.domain.exported import export_row
from niadra.domain.memory.erasure import plan_evidence_erasure
from niadra.domain.memory.vectors import ItemVector, PendingVector
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.records import Trait
from niadra.domain.traits.evidence import EvidenceRef


class InMemoryTraitRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("traits")
        self._traits: dict[UUID, Trait] = self._s.setdefault("traits", {})
        self._evidence: dict[UUID, tuple[EvidenceRef, ...]] = self._s.setdefault("evidence", {})
        self._opt_outs: dict[UUID, datetime] = self._s.setdefault("opt_outs", {})
        self._vectors: dict[UUID, tuple[tuple[float, ...], str]] = self._s.setdefault("vectors", {})
        self._seq = ChangeSeq(store, "traits")

    def _with_evidence(self, trait: Trait) -> Trait:
        refs = self._evidence.get(trait.trait_id, ())
        return replace(trait, evidence=tuple((e.kind, e.id) for e in refs))

    async def active_for(self, profile_id: UUID) -> list[Trait]:
        # PostgreSQL compares with the database clock; this mirrors `now()`.
        now = datetime.now(UTC)
        return [t for t in await self.traits_of(profile_id) if t.retracted_at is None and t.expires_at > now]

    async def traits_of(self, profile_id: UUID) -> list[Trait]:
        found = [self._with_evidence(t) for t in self._traits.values() if t.profile_id == profile_id]
        return sorted(found, key=lambda t: t.name)

    async def profiles_with_traits(self, *, limit: int) -> list[UUID]:
        found = {t.profile_id for t in self._traits.values() if t.retracted_at is None}
        return sorted(found)[:limit]

    async def rules_fingerprint(self) -> int | None:
        value: int | None = self._s.get("rules_fingerprint")
        return value

    async def set_rules_fingerprint(self, value: int) -> None:
        self._s["rules_fingerprint"] = value

    async def get_trait(self, trait_id: UUID) -> Trait | None:
        trait = self._traits.get(trait_id)
        return self._with_evidence(trait) if trait else None

    async def evidence_of(self, trait_ids: Sequence[UUID]) -> dict[UUID, tuple[EvidenceRef, ...]]:
        return {t: self._evidence.get(t, ()) for t in trait_ids if t in self._traits}

    async def save_trait(self, trait: Trait, evidence: Sequence[EvidenceRef]) -> None:
        stored = next(
            (t for t in self._traits.values() if t.profile_id == trait.profile_id and t.name == trait.name),
            None,
        )
        trait_id = stored.trait_id if stored else trait.trait_id
        self._traits[trait_id] = replace(trait, trait_id=trait_id, evidence=())
        self._vectors.pop(trait_id, None)
        self._seq.touch("traits", trait_id)
        for old in self._evidence.get(trait_id, ()):
            self._seq.forget("trait_evidence", _key(trait_id, old))
        self._evidence[trait_id] = tuple({(e.kind, e.id, e.origin_handle_id): e for e in evidence}.values())
        for new in self._evidence[trait_id]:
            self._seq.touch("trait_evidence", _key(trait_id, new))

    async def vectors_of(self, trait_ids: Sequence[UUID]) -> list[ItemVector]:
        return [
            ItemVector(ItemKind.TRAIT, t, list(stored[0]), stored[1])
            for t in trait_ids
            if (stored := self._vectors.get(t))
        ]

    async def unembedded(
        self, *, model: str | None, profile_id: UUID | None = None, limit: int
    ) -> list[PendingVector]:
        now = datetime.now(UTC)
        found = []
        for trait_id, trait in sorted(self._traits.items()):
            if trait.retracted_at is not None or trait.expires_at <= now:
                continue
            if profile_id is not None and trait.profile_id != profile_id:
                continue
            stored = self._vectors.get(trait_id)
            if stored is None or (model is not None and stored[1] != model):
                found.append(
                    PendingVector(self._with_evidence(trait), fence=self._seq.of("traits", trait_id))
                )
        return found[:limit]

    async def set_vector(self, pending: PendingVector, vector: Sequence[float], model: str) -> bool:
        trait_id = pending.item_id
        if trait_id not in self._traits or pending.fence != self._seq.of("traits", trait_id):
            return False
        self._vectors[trait_id] = (tuple(vector), model)
        self._seq.touch("traits", trait_id)
        return True

    async def delete_trait(self, trait_id: UUID) -> None:
        self._traits.pop(trait_id, None)
        self._vectors.pop(trait_id, None)
        self._seq.forget("traits", trait_id)
        for old in self._evidence.pop(trait_id, ()):
            self._seq.forget("trait_evidence", _key(trait_id, old))

    async def retract(self, trait_id: UUID, at: datetime) -> None:
        if trait_id in self._traits:
            self._traits[trait_id] = replace(self._traits[trait_id], retracted_at=at)
            self._seq.touch("traits", trait_id)

    async def expiring(self, now: datetime, *, limit: int = 500) -> list[Trait]:
        found = [t for t in self._traits.values() if t.retracted_at is None and t.expires_at <= now]
        return sorted(found, key=lambda t: (t.expires_at, t.trait_id))[:limit]

    async def traits_with_evidence_from(self, handle_ids: Sequence[UUID]) -> list[Trait]:
        handles = set(handle_ids)
        ids = {t for t, refs in self._evidence.items() if any(e.origin_handle_id in handles for e in refs)}
        return [self._with_evidence(self._traits[t]) for t in sorted(ids) if t in self._traits]

    async def opt_out(self, handle_ids: Sequence[UUID], at: datetime) -> None:
        for handle_id in handle_ids:
            self._opt_outs.setdefault(handle_id, at)

    async def opted_out(self, handle_ids: Sequence[UUID]) -> bool:
        return any(h in self._opt_outs for h in handle_ids)

    async def opt_outs(self, handle_ids: Sequence[UUID]) -> list[tuple[UUID, datetime]]:
        return sorted((h, self._opt_outs[h]) for h in set(handle_ids) if h in self._opt_outs)

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            events = set(scope.event_ids)
            return self._erase_evidence(lambda e: e.kind == "event" and e.id in events, limit)
        if scope.kind is TargetKind.CONVERSATION:
            return []
        handles = set(scope.handle_ids)
        erased = self._erase_evidence(lambda e: e.origin_handle_id in handles, limit) if handles else []
        own = sorted(t for t, trait in self._traits.items() if trait.profile_id == scope.profile_id)
        doomed = [(t, e) for t in own for e in self._evidence.get(t, ())]
        for trait_id, ref in doomed[: max(limit - len(erased), 0)]:
            erased.append(self._drop_evidence(trait_id, ref))
        for trait_id in own[: max(limit - len(erased), 0)]:
            erased.append(self._drop_trait(trait_id))
        for handle_id in sorted(h for h in self._opt_outs if h in handles)[: max(limit - len(erased), 0)]:
            del self._opt_outs[handle_id]
            erased.append(ErasedRow("trait_opt_outs", {"handle_id": str(handle_id)}))
        return erased

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table not in ("traits", "trait_evidence"):
            raise ValueError(f"traits does not export {table}")
        rows: list[dict[str, Any]] = []
        for seq, key in self._seq.between(table, after_seq, upto_seq, limit):
            if table == "traits":
                rows.append(export_row(self._traits[key], seq, drop=("evidence",)))
            else:
                rows.append({**dict(zip(_EVIDENCE_COLUMNS, key, strict=True)), "change_seq": seq})
        return rows

    def _erase_evidence(self, erased: Callable[[EvidenceRef], bool], limit: int) -> list[ErasedRow]:
        touched = {t: set(refs) for t, refs in self._evidence.items() if any(erased(e) for e in refs)}
        plan = plan_evidence_erasure(touched, erased, limit)
        rows = [self._drop_evidence(trait_id, ref) for trait_id, ref in plan.evidence]
        return rows + [self._drop_trait(trait_id) for trait_id in plan.items]

    def _drop_evidence(self, trait_id: UUID, ref: EvidenceRef) -> ErasedRow:
        self._evidence[trait_id] = tuple(e for e in self._evidence[trait_id] if e != ref)
        key = _key(trait_id, ref)
        seq = self._seq.forget("trait_evidence", key)
        return ErasedRow(
            "trait_evidence", {k: str(v) for k, v in zip(_EVIDENCE_COLUMNS, key, strict=True)}, change_seq=seq
        )

    def _drop_trait(self, trait_id: UUID) -> ErasedRow:
        del self._traits[trait_id]
        self._evidence.pop(trait_id, None)
        self._vectors.pop(trait_id, None)
        return ErasedRow(
            "traits", {"trait_id": str(trait_id)}, change_seq=self._seq.forget("traits", trait_id)
        )


_EVIDENCE_COLUMNS = ("trait_id", "origin_handle_id", "evidence_kind", "evidence_id")


def _key(trait_id: UUID, ref: EvidenceRef) -> tuple[UUID, UUID, str, UUID]:
    return (trait_id, ref.origin_handle_id, ref.kind, ref.id)
