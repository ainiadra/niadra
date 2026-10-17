"""Stored packs and manifests (5.10)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from niadra.domain.compile import StoredPack
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope


class PackRepository(Protocol):
    """Owned by the `compile` module; no other module reads or writes these tables (4.3)."""

    async def save_bundle(self, target: str, generation: int, packs: Sequence[StoredPack]) -> None:
        """The target's stored variants become `packs`, all of `generation`, in one write, unless a newer
        generation is stored; a variant left out is no longer served, and the variants kept keep
        their last reads."""
        ...

    async def get_pack(self, pack_key: str) -> StoredPack | None: ...

    async def packs_for(self, target: str) -> list[StoredPack]:
        """Every stored variant of a target (`profile:<id>`), ordered by `pack_key`."""
        ...

    async def mark_read(self, reads: dict[str, datetime]) -> None:
        """The last time each pack was served, by `pack_key`, never moving back. The receipt writer
        calls it, so the read path writes nothing."""
        ...

    async def current_generation(self, target: str) -> int:
        """The highest generation stored for the target; 0 when none."""
        ...

    async def save_manifest(self, manifest_hash: str, manifest: dict[str, Any]) -> None:
        """Content-addressed and immutable: saving an existing hash is a no-op."""
        ...

    async def save_manifests(self, manifests: Mapping[str, dict[str, Any]]) -> None:
        """`save_manifest` for every manifest of a compilation, in one statement."""
        ...

    async def get_manifest(self, manifest_hash: str) -> dict[str, Any] | None:
        """`ManifestSource` for lineage (5.13): `items` and `excluded`, each entry with `id` (the public
        item id receipts carry), `kind` and `evidence_event_ids`."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): the profile's packs and their pins and agent watermarks; for a
        profile target also its manifests, which name its handles."""
        ...
