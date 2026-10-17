"""PostgreSQL implementation of `PackRepository`: one `pack_bundles` row per profile (5.10).

A transaction reads a profile's bundle once: `packs_for`, `get_pack` and `current_generation` of the same
target share the row.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, Text, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.current_space import CURRENT_SPACE
from niadra.adapters.outbound.postgres.privacy_rows import delete_where
from niadra.adapters.outbound.postgres.schema.read_path import PackBundle, PackManifest
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.adapters.outbound.postgres.serve import PgServeRepository
from niadra.domain.compile import StoredPack, profile_target
from niadra.domain.compile.bundle import bundle_bytes, bundle_packs, read_marks, target_of
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind

BUNDLES = cast(Table, PackBundle.__table__)
MANIFESTS = cast(Table, PackManifest.__table__)

_READ = text(
    "SELECT b.generation, b.body_ct, b.dek_version, b.last_read"
    " FROM (SELECT 1) AS one LEFT JOIN pack_bundles b ON b.target = :target"
)

# A later read never moves a variant's mark back; a mark for a variant the bundle no longer has is kept
# until the next compilation prunes it.
_MARK = text(
    "UPDATE pack_bundles SET last_read = last_read || coalesce(("
    " SELECT jsonb_object_agg(n.key, n.value) FROM jsonb_each(:marks) AS n"
    " WHERE coalesce((pack_bundles.last_read ->> n.key)::bigint, 0) < (n.value #>> '{}')::bigint),"
    " '{}'::jsonb)"
    " WHERE space_id = current_setting('niadra.space_id')::uuid AND target = :target"
).bindparams(bindparam("marks", type_=JSONB))


@dataclass(frozen=True, slots=True)
class _Bundle:
    generation: int
    packs: dict[str, StoredPack]


class PgPackRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:
        self._db = session
        self._sealer = sealer
        self._bundles: dict[str, _Bundle] = {}

    @property
    def _seal(self) -> Sealer:
        assert self._sealer is not None, "packs are read and written inside a space"
        return self._sealer

    async def save_bundle(self, target: str, generation: int, packs: Sequence[StoredPack]) -> None:
        ciphertext, dek = await self._seal.seal(
            bundle_bytes(packs), table="pack_bundles", row=target, column="body_ct"
        )
        kept = sorted(p.pack_key.split("/", 1)[1] for p in packs)
        stmt = insert(PackBundle).values(
            space_id=CURRENT_SPACE, target=target, generation=generation, body_ct=ciphertext, dek_version=dek
        )
        prune = text(
            "(SELECT coalesce(jsonb_object_agg(r.key, r.value), '{}'::jsonb)"
            " FROM jsonb_each(pack_bundles.last_read) AS r WHERE r.key = ANY(:kept))"
        ).bindparams(bindparam("kept", kept, type_=ARRAY(Text)))
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[PackBundle.space_id, PackBundle.target],
                set_={
                    "generation": stmt.excluded.generation,
                    "body_ct": stmt.excluded.body_ct,
                    "dek_version": stmt.excluded.dek_version,
                    "last_read": prune,
                },
                where=PackBundle.generation <= stmt.excluded.generation,
            )
        )
        self._bundles.pop(target, None)

    async def get_pack(self, pack_key: str) -> StoredPack | None:
        target = target_of(pack_key)
        if target is None:
            return None
        return (await self._bundle(target)).packs.get(pack_key)

    async def packs_for(self, target: str) -> list[StoredPack]:
        bundle = await self._bundle(target)
        return [bundle.packs[key] for key in sorted(bundle.packs)]

    async def mark_read(self, reads: dict[str, datetime]) -> None:
        for target, marks in sorted(read_marks(reads).items()):
            await self._db.execute(_MARK, {"marks": marks, "target": target})
            self._bundles.pop(target, None)

    async def current_generation(self, target: str) -> int:
        return (await self._bundle(target)).generation

    async def save_manifest(self, manifest_hash: str, manifest: dict[str, Any]) -> None:
        await self.save_manifests({manifest_hash: manifest})

    async def save_manifests(self, manifests: Mapping[str, dict[str, Any]]) -> None:
        if not manifests:
            return
        rows = [
            {
                "space_id": CURRENT_SPACE,
                "manifest_hash": manifest_hash,
                "manifest": manifest,
                "profile_id": UUID(manifest["profile_id"]) if manifest.get("profile_id") else None,
            }
            for manifest_hash, manifest in sorted(manifests.items())
        ]
        await self._db.execute(insert(PackManifest).values(rows).on_conflict_do_nothing())

    async def get_manifest(self, manifest_hash: str) -> dict[str, Any] | None:
        found = await self._db.scalar(
            select(PackManifest.manifest).where(PackManifest.manifest_hash == manifest_hash)
        )
        return dict(found) if found is not None else None

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids or scope.profile_id is None:
            return []
        profile_id = scope.profile_id
        target = profile_target(profile_id)
        erased = await delete_where(self._db, BUNDLES, ("target",), [BUNDLES.c.target == target], limit)
        self._bundles.pop(target, None)
        if scope.kind is TargetKind.PROFILE:
            erased += await delete_where(
                self._db,
                MANIFESTS,
                ("manifest_hash",),
                [MANIFESTS.c.profile_id == profile_id],
                limit - len(erased),
            )
        # A pin only names a pack; `serve` owns the pins, so its repository drops them in this transaction.
        return erased + await PgServeRepository(self._db, self._sealer).erase_for(
            scope, limit=limit - len(erased)
        )

    async def _bundle(self, target: str) -> _Bundle:
        cached = self._bundles.get(target)
        if cached is not None:
            return cached
        row = (await self._db.execute(_READ, {"target": target})).one()
        packs: dict[str, StoredPack] = {}
        if row.body_ct is not None:
            body = await self._seal.open_bytes(
                row.body_ct, row.dek_version, table="pack_bundles", row=target, column="body_ct"
            )
            packs = {p.pack_key: p for p in bundle_packs(body, row.last_read)}
        generation = row.generation or 0
        bundle = self._bundles[target] = _Bundle(generation, packs)
        return bundle
