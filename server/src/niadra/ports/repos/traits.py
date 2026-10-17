"""Traits and their evidence (20)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.memory.vectors import ItemVector, PendingVector
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.records import Trait
from niadra.domain.traits.evidence import EvidenceRef


class TraitRepository(Protocol):
    """Owned by the `traits` module."""

    async def active_for(self, profile_id: UUID) -> list[Trait]:
        """Traits not retracted and not expired."""
        ...

    async def traits_of(self, profile_id: UUID) -> list[Trait]:
        """Every row of the profile, retracted and expired included, with evidence."""
        ...

    async def get_trait(self, trait_id: UUID) -> Trait | None: ...

    async def profiles_with_traits(self, *, limit: int) -> list[UUID]:
        """Profiles holding a pattern: a rule turned off or narrowed must reach them too."""
        ...

    async def rules_fingerprint(self) -> int | None:
        """What the space's pattern rules were at the last re-derivation (`space_state`)."""
        ...

    async def set_rules_fingerprint(self, value: int) -> None: ...

    async def evidence_of(self, trait_ids: Sequence[UUID]) -> dict[UUID, tuple[EvidenceRef, ...]]: ...

    async def save_trait(self, trait: Trait, evidence: Sequence[EvidenceRef]) -> None:
        """Insert or replace the row of (profile, name) and its evidence; `trait.trait_id` is kept on
        replace only when it matches the stored row. A replaced row drops its vector."""
        ...

    async def delete_trait(self, trait_id: UUID) -> None: ...

    async def retract(self, trait_id: UUID, at: datetime) -> None: ...

    async def expiring(self, now: datetime, *, limit: int = 500) -> list[Trait]:
        """Not retracted and `expires_at <= now`: the sweep re-derives their profiles (20.2)."""
        ...

    async def traits_with_evidence_from(self, handle_ids: Sequence[UUID]) -> list[Trait]:
        """Traits whose evidence comes from any of the handles, whatever profile holds them now."""
        ...

    async def opt_out(self, handle_ids: Sequence[UUID], at: datetime) -> None: ...

    async def opted_out(self, handle_ids: Sequence[UUID]) -> bool: ...

    async def opt_outs(self, handle_ids: Sequence[UUID]) -> list[tuple[UUID, datetime]]:
        """(handle, when) of each objection to profiling among the handles, for the access package."""
        ...

    async def vectors_of(self, trait_ids: Sequence[UUID]) -> list[ItemVector]:
        """Stored vectors of these traits, for history search (16.2)."""
        ...

    async def unembedded(
        self, *, model: str | None, profile_id: UUID | None = None, limit: int
    ) -> list[PendingVector]:
        """Active traits without a vector or, with `model`, with one of another encoder: the profile's, or
        the whole space's. Fenced, since a new derivation rewrites the row."""
        ...

    async def set_vector(self, pending: PendingVector, vector: Sequence[float], model: str) -> bool:
        """Stores the vector sealed; False when the row is gone or changed since `pending` was read."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4). With `scope.event_ids`: evidence pointing at those events and the
        traits left without evidence. Otherwise: the profile's traits, evidence that came from the
        target's handles (and traits it leaves without evidence) and the handles' opt-outs."""
        ...

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        """`ExportSource` (22.3) for `traits` and `trait_evidence`: decrypted rows by `change_seq`."""
        ...
