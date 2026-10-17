"""In-memory `PackRepository` for unit tests: one bundle per target, as on PostgreSQL."""

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from niadra.adapters.memory.serve import InMemoryServeRepository
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.compile import StoredPack, profile_target
from niadra.domain.compile.bundle import target_of
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind


@dataclass
class _Bundle:
    generation: int
    packs: dict[str, StoredPack]
    last_read: dict[str, datetime] = field(default_factory=dict)


class InMemoryPackRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._s = store.module("compile")
        self._bundles: dict[str, _Bundle] = self._s.setdefault("bundles", {})
        self._manifests: dict[str, dict[str, Any]] = self._s.setdefault("manifests", {})

    async def save_bundle(self, target: str, generation: int, packs: Sequence[StoredPack]) -> None:
        current = self._bundles.get(target)
        if current is not None and current.generation > generation:
            return
        kept = {p.pack_key: replace(p, last_read_at=None) for p in packs}
        reads = {k: at for k, at in (current.last_read if current else {}).items() if k in kept}
        self._bundles[target] = _Bundle(generation, kept, reads)

    async def get_pack(self, pack_key: str) -> StoredPack | None:
        target = target_of(pack_key)
        bundle = self._bundles.get(target) if target else None
        return self._with_read(bundle, pack_key) if bundle else None

    async def packs_for(self, target: str) -> list[StoredPack]:
        bundle = self._bundles.get(target)
        if bundle is None:
            return []
        return [p for key in sorted(bundle.packs) if (p := self._with_read(bundle, key)) is not None]

    async def mark_read(self, reads: dict[str, datetime]) -> None:
        for key, at in reads.items():
            target = target_of(key)
            bundle = self._bundles.get(target) if target else None
            if bundle is not None and (bundle.last_read.get(key) is None or bundle.last_read[key] < at):
                bundle.last_read[key] = at

    async def current_generation(self, target: str) -> int:
        bundle = self._bundles.get(target)
        return bundle.generation if bundle else 0

    async def save_manifest(self, manifest_hash: str, manifest: dict[str, Any]) -> None:
        self._manifests.setdefault(manifest_hash, copy.deepcopy(manifest))

    async def save_manifests(self, manifests: Mapping[str, dict[str, Any]]) -> None:
        for manifest_hash, manifest in manifests.items():
            await self.save_manifest(manifest_hash, manifest)

    async def get_manifest(self, manifest_hash: str) -> dict[str, Any] | None:
        found = self._manifests.get(manifest_hash)
        return copy.deepcopy(found) if found is not None else None

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids or scope.profile_id is None:
            return []
        target = profile_target(scope.profile_id)
        erased = []
        if limit > 0 and self._bundles.pop(target, None) is not None:
            erased.append(ErasedRow("pack_bundles", {"target": target}))
        if scope.kind is TargetKind.PROFILE:
            mine = sorted(
                h for h, m in self._manifests.items() if m.get("profile_id") == str(scope.profile_id)
            )
            for manifest_hash in mine[: max(limit - len(erased), 0)]:
                del self._manifests[manifest_hash]
                erased.append(ErasedRow("pack_manifests", {"manifest_hash": manifest_hash}))
        pins = InMemoryServeRepository(self._store)
        return erased + await pins.erase_for(scope, limit=max(limit - len(erased), 0))

    @staticmethod
    def _with_read(bundle: _Bundle, key: str) -> StoredPack | None:
        pack = bundle.packs.get(key)
        return replace(pack, last_read_at=bundle.last_read.get(key)) if pack is not None else None
